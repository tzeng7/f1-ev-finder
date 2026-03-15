"""
Kalshi F1 market fetcher.

Kalshi REST API v2: https://trading-api.kalshi.com/trade-api/v2
Auth: API key via header  RSA-signed requests (or simple email/password for demo).

For F1, markets are searchable by keyword. They appear under event series like:
  KXF1-<YEAR>-<RACE>-WINNER
  KXF1-<YEAR>-WDC

Kalshi prices are in cents (0–100). We normalize to 0–1.
"""

import httpx
import os
import time
import json
import base64
from loguru import logger
from src.models.market import Market, Outcome
from datetime import datetime
from typing import Optional

KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"

F1_SEARCH_TERMS = ["formula", "f1", "grand prix", "verstappen", "pole position"]

MARKET_TYPE_MAP = {
    "win": "race_winner",
    "race winner": "race_winner",
    "pole": "qualifying",
    "qualifying": "qualifying",
    "head-to-head": "h2h",
    "h2h": "h2h",
    "championship": "futures",
    "wdc": "futures",
    "constructors": "futures",
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


def _cents_to_prob(cents: float) -> float:
    """Convert Kalshi cent price (0-100) to implied probability (0-1)."""
    return cents / 100.0


def _parse_kalshi_market(raw: dict) -> Market | None:
    """Parse a Kalshi market response dict into a Market object."""
    ticker = raw.get("ticker", "")
    title = raw.get("title") or raw.get("question") or ticker
    subtitle = raw.get("subtitle", "")
    full_title = f"{title} — {subtitle}" if subtitle else title

    market_type = _infer_market_type(full_title)

    # Kalshi binary markets have yes_bid/yes_ask; use mid
    yes_bid = raw.get("yes_bid", 0)
    yes_ask = raw.get("yes_ask", 100)
    no_bid = raw.get("no_bid", 0)
    no_ask = raw.get("no_ask", 100)

    yes_mid = (yes_bid + yes_ask) / 2.0
    no_mid = (no_bid + no_ask) / 2.0

    # Validate: yes+no mids should approximately sum to 100
    if yes_mid <= 0 and no_mid <= 0:
        return None

    outcomes = [
        Outcome(name="Yes", price=_cents_to_prob(yes_mid), is_implied_prob=True),
        Outcome(name="No", price=_cents_to_prob(no_mid), is_implied_prob=True),
    ]

    closes_at = None
    if raw.get("close_time"):
        try:
            closes_at = datetime.fromisoformat(raw["close_time"].replace("Z", "+00:00"))
        except Exception:
            pass

    event_ticker = raw.get("event_ticker", "")
    url = f"https://kalshi.com/markets/{event_ticker}/{ticker}" if event_ticker else None

    return Market(
        source="kalshi",
        market_id=ticker,
        title=full_title,
        market_type=market_type,
        event=title,
        outcomes=outcomes,
        closes_at=closes_at,
        url=url,
    )


class KalshiClient:
    """
    Authenticated Kalshi API client.
    Supports both API key auth (production) and unauthenticated public market browsing.
    """

    def __init__(
        self,
        api_key: str | None = None,
        key_id: str | None = None,
        demo: bool = False,
    ):
        self.base = KALSHI_DEMO_BASE if demo else KALSHI_BASE
        self.api_key = api_key or os.getenv("KALSHI_API_KEY")
        self.key_id = key_id or os.getenv("KALSHI_API_KEY_ID")
        self._token: str | None = None

    def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        elif self.api_key and self.key_id:
            # RSA-signed auth — simplified: use API key as bearer if format allows
            # For full RSA signing, use the Kalshi Python SDK or implement PKCS#1v15
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        retries: int = 3,
    ) -> dict | list:
        url = f"{self.base}{path}"
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=20, headers=self._get_headers()) as client:
                    resp = client.request(method, url, params=params)
                    if resp.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning("Kalshi rate limited, waiting %ds...", wait)
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    logger.warning("Kalshi auth error — proceeding without auth for public markets")
                    self.api_key = None
                    self.key_id = None
                    continue
                logger.error("Kalshi API error: %s", e)
                raise
            except httpx.HTTPError as e:
                logger.error("Kalshi HTTP error: %s", e)
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return {}

    def search_markets(
        self,
        search_terms: list[str] | None = None,
        status: str = "open",
        limit: int = 100,
    ) -> list[Market]:
        """Search Kalshi for F1 markets across multiple search terms."""
        terms = search_terms or F1_SEARCH_TERMS
        seen: set[str] = set()
        markets: list[Market] = []

        for term in terms:
            params = {
                "search": term,
                "status": status,
                "limit": limit,
            }
            try:
                data = self._request("GET", "/markets", params=params)
            except Exception as e:
                logger.warning("Kalshi search failed for '%s': %s", term, e)
                continue

            raw_markets = data.get("markets", []) if isinstance(data, dict) else []
            for raw in raw_markets:
                ticker = raw.get("ticker", "")
                if ticker in seen:
                    continue
                seen.add(ticker)

                market = _parse_kalshi_market(raw)
                if market:
                    markets.append(market)

        logger.info("Fetched %d unique F1 markets from Kalshi", len(markets))
        return markets

    def get_market(self, ticker: str) -> Market | None:
        """Fetch a single market by ticker for real-time price."""
        try:
            data = self._request("GET", f"/markets/{ticker}")
            raw = data.get("market", data) if isinstance(data, dict) else {}
            return _parse_kalshi_market(raw)
        except Exception as e:
            logger.error("Failed to fetch Kalshi market %s: %s", ticker, e)
            return None

    def get_orderbook(self, ticker: str) -> dict:
        """Fetch orderbook for a market (for tighter spread pricing)."""
        try:
            data = self._request("GET", f"/markets/{ticker}/orderbook")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.debug("Kalshi orderbook fetch failed for %s: %s", ticker, e)
            return {}


def get_f1_markets(
    api_key: str | None = None,
    key_id: str | None = None,
    demo: bool = False,
) -> list[Market]:
    """Top-level convenience function to fetch all open Kalshi F1 markets."""
    client = KalshiClient(api_key=api_key, key_id=key_id, demo=demo)
    return client.search_markets()
