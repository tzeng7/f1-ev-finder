"""
Polymarket F1 market fetcher.

Uses two APIs:
  Gamma API  — market discovery, metadata, current prices
  CLOB API   — real-time orderbook mid-price (more accurate for liquid markets)

Gamma API docs: https://docs.polymarket.com/#gamma-markets-api
CLOB docs:      https://docs.polymarket.com/#clob-api
"""

import httpx
from loguru import logger
from src.models.market import Market, Outcome
from datetime import datetime

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

F1_SEARCH_TERMS = ["formula 1", "formula one", "f1", "grand prix", "gp", "verstappen"]

MARKET_TYPE_MAP = {
    "race winner": "race_winner",
    "win the race": "race_winner",
    "pole position": "qualifying",
    "qualifying": "qualifying",
    "head-to-head": "h2h",
    "h2h": "h2h",
    "world championship": "futures",
    "wdc": "futures",
    "constructors": "futures",
    "wcc": "futures",
    "fastest lap": "prop",
    "podium": "prop",
    "points": "prop",
}


def _infer_market_type(title: str) -> str:
    lower = title.lower()
    for keyword, mtype in MARKET_TYPE_MAP.items():
        if keyword in lower:
            return mtype
    return "unknown"


def _parse_gamma_market(raw: dict) -> Market | None:
    """Parse a single Gamma API market response into a Market object."""
    market_id = raw.get("id", "")
    title = raw.get("question") or raw.get("title") or ""
    slug = raw.get("slug", "")
    outcomes_raw = raw.get("outcomes", [])         # ["Yes", "No"] or ["Driver A", "Driver B", ...]
    prices_raw = raw.get("outcomePrices", [])       # ["0.45", "0.55"] — current mid prices

    if not outcomes_raw or not prices_raw:
        return None

    # outcomePrices may be a string-encoded list or actual list
    if isinstance(prices_raw, str):
        import json
        try:
            prices_raw = json.loads(prices_raw)
        except Exception:
            return None

    if isinstance(outcomes_raw, str):
        import json
        try:
            outcomes_raw = json.loads(outcomes_raw)
        except Exception:
            return None

    outcomes = []
    for name, price_str in zip(outcomes_raw, prices_raw):
        try:
            price = float(price_str)
        except (ValueError, TypeError):
            continue
        outcomes.append(Outcome(name=name, price=price, is_implied_prob=True))

    if not outcomes:
        return None

    closes_at = None
    if raw.get("endDate"):
        try:
            closes_at = datetime.fromisoformat(raw["endDate"].replace("Z", "+00:00"))
        except Exception:
            pass

    return Market(
        source="polymarket",
        market_id=str(market_id),
        title=title,
        market_type=_infer_market_type(title),
        event=title,  # refined by caller if needed
        outcomes=outcomes,
        closes_at=closes_at,
        url=f"https://polymarket.com/event/{slug}" if slug else None,
    )


def search_f1_markets(
    search_terms: list[str] | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> list[Market]:
    """
    Search Polymarket for F1-related markets using the Gamma API.
    Returns all unique markets found across search terms.
    """
    terms = search_terms or F1_SEARCH_TERMS
    seen_ids: set[str] = set()
    all_markets: list[Market] = []

    with httpx.Client(timeout=20, headers={"User-Agent": "f1-ev-finder/1.0"}) as client:
        for term in terms:
            params: dict = {
                "search": term,
                "limit": limit,
                "active": str(active_only).lower(),
                "closed": "false",
            }
            try:
                resp = client.get(f"{GAMMA_BASE}/markets", params=params)
                resp.raise_for_status()
                raw_markets = resp.json()
            except httpx.HTTPError as e:
                logger.warning("Polymarket Gamma API error for '%s': %s", term, e)
                continue

            for raw in raw_markets:
                mid = str(raw.get("id", ""))
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)

                market = _parse_gamma_market(raw)
                if market:
                    all_markets.append(market)

    logger.info("Fetched %d unique F1 markets from Polymarket", len(all_markets))
    return all_markets


def get_clob_midprice(condition_id: str, client: httpx.Client) -> float | None:
    """
    Fetch real-time mid-price from CLOB orderbook for a given condition_id (token_id on Polymarket).
    Returns implied probability (0–1) or None on failure.
    """
    try:
        resp = client.get(f"{CLOB_BASE}/midpoint", params={"token_id": condition_id})
        resp.raise_for_status()
        data = resp.json()
        mid = data.get("mid")
        return float(mid) if mid is not None else None
    except Exception as e:
        logger.debug("CLOB midprice fetch failed for %s: %s", condition_id, e)
        return None


def enrich_with_clob_prices(markets: list[Market]) -> list[Market]:
    """
    For binary (Yes/No) markets, replace Gamma prices with live CLOB mid-prices.
    Skips multi-outcome markets (CLOB is per condition_id, complex to map for outrights).
    """
    with httpx.Client(timeout=15, headers={"User-Agent": "f1-ev-finder/1.0"}) as client:
        for market in markets:
            if len(market.outcomes) != 2:
                continue
            # For binary markets we only need the "Yes" price; "No" = 1 - Yes
            # Polymarket condition_id is the market_id for simple binary markets
            mid = get_clob_midprice(market.market_id, client)
            if mid is not None:
                yes_outcome = next((o for o in market.outcomes if o.name.lower() == "yes"), None)
                no_outcome = next((o for o in market.outcomes if o.name.lower() == "no"), None)
                if yes_outcome:
                    yes_outcome.price = mid
                if no_outcome:
                    no_outcome.price = 1.0 - mid

    return markets
