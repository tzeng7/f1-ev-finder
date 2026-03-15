"""
F1 EV Finder — main orchestrator.

Usage:
  python src/main.py scan                    # scan all F1 markets
  python src/main.py scan --race "Bahrain"   # filter to a specific race
  python src/main.py scan --min-ev 0.05      # only show >= 5% EV
  python src/main.py markets                 # list available markets without EV calc
"""

import os
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import typer
from dotenv import load_dotenv
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich import box

from src.markets.polymarket import search_f1_markets
from src.markets.kalshi import get_f1_markets
from src.scrapers.odds_api import (
    get_race_winner_odds,
    get_h2h_odds,
    get_best_sharp_line,
    SHARP_BOOKS,
)
from src.ev.calculator import find_opportunities
from src.ev.matcher import names_match, best_match
from src.models.market import Market, EVOpportunity

load_dotenv()

app = typer.Typer(help="F1 prediction market EV finder")
console = Console()

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def _configure_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")


def _matcher(a: str, b: str) -> bool:
    return names_match(a, b, threshold=78)


def _group_sharp_markets_by_event(markets: list[Market]) -> dict[str, list[Market]]:
    """Group sharp markets by normalized event title."""
    groups: dict[str, list[Market]] = {}
    for m in markets:
        key = m.event.lower().strip()
        groups.setdefault(key, []).append(m)
    return groups


def _find_best_sharp_for_pm(
    pm_market: Market,
    sharp_groups: dict[str, list[Market]],
    market_type_filter: str | None = None,
) -> Market | None:
    """Find the best matching sharp market for a given prediction market."""
    pm_title = pm_market.title.lower()

    # Try exact or fuzzy event match
    for key, candidates in sharp_groups.items():
        if market_type_filter and all(c.market_type != market_type_filter for c in candidates):
            continue
        # Fuzzy match the title against sharp event keys
        score_threshold = 70
        from rapidfuzz import fuzz
        score = fuzz.partial_ratio(pm_title, key)
        if score >= score_threshold:
            type_filtered = [c for c in candidates if not market_type_filter or c.market_type == market_type_filter]
            return get_best_sharp_line(type_filtered or candidates)

    return None


def _print_opportunities(opportunities: list[EVOpportunity]) -> None:
    if not opportunities:
        console.print("[yellow]No +EV opportunities found above threshold.[/yellow]")
        return

    table = Table(
        title=f"F1 +EV Opportunities ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Platform", style="cyan", width=12)
    table.add_column("Market", style="white", max_width=40)
    table.add_column("Outcome", style="bright_white", max_width=25)
    table.add_column("Mkt Price", justify="right", style="yellow")
    table.add_column("Sharp Fair", justify="right", style="blue")
    table.add_column("EV", justify="right", style="bright_green")
    table.add_column("Sharp Book", style="dim")

    for opp in opportunities:
        ev_str = f"+{opp.ev_pct:.1%}" if opp.ev_pct >= 0 else f"{opp.ev_pct:.1%}"
        table.add_row(
            opp.platform.upper(),
            opp.market_title[:40],
            opp.outcome[:25],
            f"{opp.platform_price:.1%}",
            f"{opp.sharp_fair_prob:.1%}",
            ev_str,
            opp.sharp_source,
        )

    console.print(table)
    console.print(f"\n[dim]Found {len(opportunities)} +EV opportunities[/dim]")


def _save_opportunities(opportunities: list[EVOpportunity], path: Path) -> None:
    data = [
        {
            "market_title": o.market_title,
            "outcome": o.outcome,
            "platform": o.platform,
            "platform_price": round(o.platform_price, 4),
            "sharp_fair_prob": round(o.sharp_fair_prob, 4),
            "ev_pct": round(o.ev_pct, 4),
            "sharp_source": o.sharp_source,
            "sharp_decimal_odds": round(o.sharp_decimal_odds, 3),
            "platform_url": o.platform_url,
            "scanned_at": o.scanned_at.isoformat(),
        }
        for o in opportunities
    ]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    console.print(f"[dim]Results saved to {path}[/dim]")


@app.command()
def scan(
    race: str = typer.Option(None, "--race", "-r", help="Filter by race name (partial match)"),
    min_ev: float = typer.Option(None, "--min-ev", help="Minimum EV threshold (overrides .env)"),
    no_kalshi: bool = typer.Option(False, "--no-kalshi", help="Skip Kalshi markets"),
    no_polymarket: bool = typer.Option(False, "--no-polymarket", help="Skip Polymarket markets"),
    save: bool = typer.Option(True, "--save/--no-save", help="Save results to output/opportunities.json"),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Scan F1 prediction markets for +EV opportunities vs sharp sportsbooks."""
    _configure_logging(log_level)

    odds_api_key = os.getenv("ODDS_API_KEY")
    ev_threshold = min_ev if min_ev is not None else float(os.getenv("EV_THRESHOLD", "0.03"))

    if not odds_api_key:
        console.print("[red]Error: ODDS_API_KEY not set. Add it to .env (get a free key at https://the-odds-api.com)[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]F1 EV Scanner[/bold] | threshold: [green]+{ev_threshold:.0%}[/green]")
    console.print("")

    # ── 1. Fetch sharp sportsbook odds ───────────────────────────────────────
    console.print("[bold blue]Fetching sharp sportsbook odds...[/bold blue]")
    sharp_markets: list[Market] = []

    try:
        sharp_markets += get_race_winner_odds(odds_api_key)
        sharp_markets += get_h2h_odds(odds_api_key)
    except Exception as e:
        console.print(f"[red]Failed to fetch sharp odds: {e}[/red]")
        raise typer.Exit(1)

    if race:
        sharp_markets = [m for m in sharp_markets if race.lower() in m.event.lower()]
        console.print(f"Filtered to {len(sharp_markets)} markets matching '{race}'")

    sharp_groups = _group_sharp_markets_by_event(sharp_markets)
    console.print(f"[green]Got {len(sharp_markets)} sharp markets across {len(sharp_groups)} events[/green]\n")

    all_opportunities: list[EVOpportunity] = []

    # ── 2. Polymarket ────────────────────────────────────────────────────────
    if not no_polymarket:
        console.print("[bold blue]Fetching Polymarket F1 markets...[/bold blue]")
        try:
            pm_markets = search_f1_markets()
            if race:
                pm_markets = [m for m in pm_markets if race.lower() in m.title.lower()]
            console.print(f"[green]Got {len(pm_markets)} Polymarket markets[/green]")

            pm_hits = 0
            for pm_market in pm_markets:
                sharp = _find_best_sharp_for_pm(pm_market, sharp_groups, pm_market.market_type)
                if not sharp:
                    logger.debug("No sharp match for PM market: %s", pm_market.title)
                    continue

                opps = find_opportunities(
                    sharp_market=sharp,
                    prediction_market=pm_market,
                    matcher_fn=_matcher,
                    ev_threshold=ev_threshold,
                )
                all_opportunities.extend(opps)
                pm_hits += len(opps)

            console.print(f"Polymarket: [green]{pm_hits} +EV opportunities[/green]\n")
        except Exception as e:
            console.print(f"[yellow]Polymarket fetch failed: {e}[/yellow]\n")

    # ── 3. Kalshi ────────────────────────────────────────────────────────────
    if not no_kalshi:
        console.print("[bold blue]Fetching Kalshi F1 markets...[/bold blue]")
        try:
            kalshi_markets = get_f1_markets(
                api_key=os.getenv("KALSHI_API_KEY"),
                key_id=os.getenv("KALSHI_API_KEY_ID"),
            )
            if race:
                kalshi_markets = [m for m in kalshi_markets if race.lower() in m.title.lower()]
            console.print(f"[green]Got {len(kalshi_markets)} Kalshi markets[/green]")

            kalshi_hits = 0
            for k_market in kalshi_markets:
                sharp = _find_best_sharp_for_pm(k_market, sharp_groups, k_market.market_type)
                if not sharp:
                    logger.debug("No sharp match for Kalshi market: %s", k_market.title)
                    continue

                opps = find_opportunities(
                    sharp_market=sharp,
                    prediction_market=k_market,
                    matcher_fn=_matcher,
                    ev_threshold=ev_threshold,
                )
                all_opportunities.extend(opps)
                kalshi_hits += len(opps)

            console.print(f"Kalshi: [green]{kalshi_hits} +EV opportunities[/green]\n")
        except Exception as e:
            console.print(f"[yellow]Kalshi fetch failed: {e}[/yellow]\n")

    # ── 4. Output ─────────────────────────────────────────────────────────────
    all_opportunities.sort(key=lambda x: x.ev_pct, reverse=True)
    _print_opportunities(all_opportunities)

    if save and all_opportunities:
        _save_opportunities(all_opportunities, OUTPUT_DIR / "opportunities.json")


@app.command()
def markets(
    source: str = typer.Option("all", "--source", "-s", help="polymarket | kalshi | all"),
    log_level: str = typer.Option("WARNING", "--log-level"),
) -> None:
    """List available F1 prediction markets without running EV calculation."""
    _configure_logging(log_level)

    all_markets: list[Market] = []

    if source in ("polymarket", "all"):
        console.print("[bold blue]Polymarket F1 markets:[/bold blue]")
        pm = search_f1_markets()
        all_markets.extend(pm)

    if source in ("kalshi", "all"):
        console.print("[bold blue]Kalshi F1 markets:[/bold blue]")
        k = get_f1_markets(
            api_key=os.getenv("KALSHI_API_KEY"),
            key_id=os.getenv("KALSHI_API_KEY_ID"),
        )
        all_markets.extend(k)

    table = Table(title=f"F1 Prediction Markets ({len(all_markets)} total)", box=box.SIMPLE)
    table.add_column("Source", style="cyan", width=12)
    table.add_column("Type", style="yellow", width=14)
    table.add_column("Market", max_width=55)
    table.add_column("Outcomes", justify="right")
    table.add_column("Closes", width=12)

    for m in sorted(all_markets, key=lambda x: (x.source, x.market_type)):
        closes = m.closes_at.strftime("%Y-%m-%d") if m.closes_at else "—"
        table.add_row(
            m.source,
            m.market_type,
            m.title[:55],
            str(len(m.outcomes)),
            closes,
        )

    console.print(table)


if __name__ == "__main__":
    app()
