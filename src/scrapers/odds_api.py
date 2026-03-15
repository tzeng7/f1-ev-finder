"""
The Odds API scraper — https://the-odds-api.com

Aggregates odds from Pinnacle, Bet365, DraftKings, Circa and others.
F1 sport key: "motorsport_formula_one"
Free tier: 500 requests/month. Each call costs 1–N requests depending on bookmakers/markets requested.

Relevant market keys:
  h2h       — head-to-head (2-outcome: driver A vs driver B)
  outrights — futures / race winner (multi-outcome)
"""

import httpx
import os
from loguru import logger
from src.models.market import Market, Outcome

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
F1_SPORT_KEY = "motorsport_formula_one"

# Sharp books to pull. Pinnacle is primary; others used for consensus.
SHARP_BOOKS = ["pinnacle", "betfair_ex_eu", "bet365", "draftkings", "circa_sports"]


def _american_to_decimal(american: int) -> float:
    if american > 0:
        return american / 100 + 1
    else:
        return 100 / abs(american) + 1


def _parse_outcomes(raw_outcomes: list[dict], source: str) -> list[Outcome]:
    outcomes = []
    for o in raw_outcomes:
        price = o.get("price", 0)
        # Odds API returns decimal odds for most books, American for some US books
        # Field "price" is always decimal in v4
        if price <= 0:
            continue
        outcomes.append(Outcome(name=o["name"], price=float(price), is_implied_prob=False))
    return outcomes


def get_f1_events(api_key: str) -> list[dict]:
    """Return list of upcoming F1 events from The Odds API."""
    url = f"{ODDS_API_BASE}/sports/{F1_SPORT_KEY}/events"
    params = {"apiKey": api_key}
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        events = resp.json()
        logger.info("Fetched %d F1 events from Odds API", len(events))
        remaining = resp.headers.get("x-requests-remaining", "?")
        logger.debug("Odds API requests remaining: %s", remaining)
        return events


def get_race_winner_odds(
    api_key: str,
    event_id: str | None = None,
    bookmakers: list[str] | None = None,
) -> list[Market]:
    """
    Fetch outrights (race winner / futures) odds.
    If event_id is None, fetches all upcoming F1 outrights.
    """
    books = ",".join(bookmakers or SHARP_BOOKS)
    url = f"{ODDS_API_BASE}/sports/{F1_SPORT_KEY}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us,eu,uk",
        "markets": "outrights",
        "bookmakers": books,
        "oddsFormat": "decimal",
    }
    if event_id:
        params["eventIds"] = event_id

    with httpx.Client(timeout=20) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    markets = []
    for event in data:
        event_name = f"{event.get('home_team', '')} {event.get('away_team', '')}".strip()
        # For outrights, home_team is the race name
        event_label = event.get("home_team") or event.get("sport_title", "F1")

        for bookmaker in event.get("bookmakers", []):
            book_key = bookmaker["key"]
            for mkt in bookmaker.get("markets", []):
                outcomes = _parse_outcomes(mkt.get("outcomes", []), book_key)
                if not outcomes:
                    continue
                markets.append(
                    Market(
                        source=book_key,
                        market_id=f"{event['id']}_{book_key}_{mkt['key']}",
                        title=event_label,
                        market_type="race_winner",
                        event=event_label,
                        outcomes=outcomes,
                        url=None,
                    )
                )

    logger.info("Fetched %d sharp outright markets for F1", len(markets))
    return markets


def get_h2h_odds(
    api_key: str,
    event_id: str | None = None,
    bookmakers: list[str] | None = None,
) -> list[Market]:
    """
    Fetch head-to-head (driver matchup) odds.
    Note: The Odds API h2h key for F1 is "h2h" — maps to 2-way driver matchups on DraftKings etc.
    """
    books = ",".join(bookmakers or SHARP_BOOKS)
    url = f"{ODDS_API_BASE}/sports/{F1_SPORT_KEY}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us,eu,uk",
        "markets": "h2h",
        "bookmakers": books,
        "oddsFormat": "decimal",
    }
    if event_id:
        params["eventIds"] = event_id

    with httpx.Client(timeout=20) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    markets = []
    for event in data:
        event_label = f"{event.get('home_team', '')} vs {event.get('away_team', '')}"
        for bookmaker in event.get("bookmakers", []):
            book_key = bookmaker["key"]
            for mkt in bookmaker.get("markets", []):
                outcomes = _parse_outcomes(mkt.get("outcomes", []), book_key)
                if not outcomes:
                    continue
                markets.append(
                    Market(
                        source=book_key,
                        market_id=f"{event['id']}_{book_key}_h2h",
                        title=event_label,
                        market_type="h2h",
                        event=event_label,
                        outcomes=outcomes,
                        url=None,
                    )
                )

    logger.info("Fetched %d sharp H2H markets for F1", len(markets))
    return markets


def get_best_sharp_line(markets: list[Market], prefer: str = "pinnacle") -> Market | None:
    """
    Given a list of markets for the same event from multiple books,
    return the sharpest single-book line (preferring `prefer` book).
    Falls back to first available sharp book.
    """
    priority = [prefer] + [b for b in SHARP_BOOKS if b != prefer]
    by_source = {m.source: m for m in markets}

    for book in priority:
        if book in by_source:
            return by_source[book]

    return markets[0] if markets else None
