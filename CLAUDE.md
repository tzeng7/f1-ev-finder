# F1 EV Finder

## Project Overview

This project scrapes Formula 1 odds from sharp sportsbooks and prediction markets, then identifies positive expected value (+EV) betting opportunities by comparing implied probabilities across sources.

## Architecture

### Data Sources

**Sharp Sportsbooks (consensus/reference lines)**
- Pinnacle — sharpest book worldwide, used as primary reference line
- Circa Sports — sharp US book, minimal margin
- Bet365 — high limits, sharp on F1 markets
- DraftKings — included primarily for head-to-head markets (H2H driver matchups)

**Prediction Markets (target markets for +EV)**
- Polymarket — decentralized, event-based markets
- Kalshi — regulated US prediction market

**Market Types Covered**
- Race winner (individual GP)
- Qualifying winner / pole position
- Podium finish
- Season-long futures (WDC, WCC)
- Head-to-head driver matchups
- Fastest lap, points finish, DNF props

### EV Calculation

The core EV formula compares the implied probability from sharp sportsbooks against the price offered on Polymarket/Kalshi:

```
Sharp Implied Prob = 1 / (decimal odds)  [after removing vig]
Market Price       = contract price (0–1 on Polymarket, 0–100¢ on Kalshi)

EV = (Sharp Prob × Profit) - ((1 - Sharp Prob) × Cost)
```

Vig removal (worst-case / power method preferred): use `implied_prob / sum(all_implied_probs)` to get fair probabilities from a multi-outcome market.

**EV threshold for surfacing opportunities:** configurable, default `> 3%`.

### Project Structure

```
f1-ev-finder/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   └── markets.yaml          # market mappings across books
├── src/
│   ├── scrapers/
│   │   ├── pinnacle.py       # Pinnacle API / scraper
│   │   ├── circa.py
│   │   ├── bet365.py
│   │   └── draftkings.py
│   ├── markets/
│   │   ├── polymarket.py     # Polymarket API (Gamma API + CLOB)
│   │   └── kalshi.py         # Kalshi REST API
│   ├── ev/
│   │   ├── calculator.py     # EV and vig-removal logic
│   │   └── matcher.py        # fuzzy match market names across sources
│   ├── models/
│   │   ├── market.py         # Market, Outcome, OddsLine dataclasses
│   │   └── opportunity.py    # EVOpportunity dataclass
│   └── main.py               # orchestrator / CLI entry point
├── output/
│   └── opportunities.json    # latest EV scan results
└── tests/
    ├── test_ev.py
    └── test_matcher.py
```

## Key Implementation Notes

### Polymarket
- Use the **Gamma API** (`https://gamma-api.polymarket.com`) for market discovery and metadata
- Use the **CLOB API** (`https://clob.polymarket.com`) for real-time orderbook prices
- F1 markets are tagged — filter by tag slug `formula-1` or `sports`
- Prices are in USDC (0.0–1.0 range)

### Kalshi
- REST API requires API key auth (`KALSHI_API_KEY`, `KALSHI_API_KEY_ID`)
- F1 markets live under series/event slugs — search for `F1` or `FORMULA`
- Prices are in cents (0–100)
- Rate limits: respect `429` responses with exponential backoff

### Pinnacle
- Pinnacle has a public odds API; no auth required for public feeds
- Endpoint: `https://pinnacle.com/sports/` — scrape or use `odds-api` style wrapper
- F1 is under "Motor Sports" category
- Use decimal odds; convert to implied probability

### DraftKings
- Scrape or use the DraftKings public odds feed
- Focus on H2H driver matchup markets (most liquid on DK for F1)
- American odds format — convert: positive `100/(odds+100)`, negative `|odds|/(|odds|+100)`

### Market Matching
- Driver and race names differ across books (e.g. "Max Verstappen" vs "M. Verstappen" vs "Verstappen, Max")
- Use fuzzy matching (`rapidfuzz`) with a confidence threshold (default 85)
- Maintain a `config/markets.yaml` canonical name mapping for known mismatches

### Sharp Line Construction
- When multiple sharp books are available, use the **Pinnacle line as primary**
- Optional: build a consensus line by averaging de-vigged probabilities across sharp books
- Flag any line where the sharp book spread vs consensus is > 3% — may indicate stale line

## Environment Variables

```
KALSHI_API_KEY=
KALSHI_API_KEY_ID=
POLYMARKET_API_KEY=       # optional, for higher rate limits
ODDS_API_KEY=             # optional, for The Odds API aggregator as fallback
EV_THRESHOLD=0.03         # minimum EV to surface an opportunity
LOG_LEVEL=INFO
```

## Running the Scanner

```bash
# Install deps
pip install -r requirements.txt

# Run full scan
python src/main.py scan

# Run for a specific upcoming race
python src/main.py scan --race "Bahrain GP"

# Run continuously (every 10 min)
python src/main.py watch --interval 600
```

## Output Format

Each opportunity in `output/opportunities.json`:
```json
{
  "market": "2025 Australian GP - Race Winner",
  "outcome": "Max Verstappen",
  "platform": "polymarket",
  "platform_price": 0.41,
  "sharp_fair_prob": 0.47,
  "ev_pct": 0.061,
  "sharp_source": "pinnacle",
  "sharp_decimal_odds": 2.10,
  "scanned_at": "2025-03-14T10:00:00Z"
}
```

## Testing

```bash
pytest tests/
```

Tests use recorded fixtures (VCR cassettes) to avoid live network calls. Do not mock sportsbook API responses with hardcoded values — use cassette recordings to ensure realistic data shapes.

## Conventions

- Python 3.11+
- Type hints on all public functions
- `dataclasses` or `pydantic` models for structured data
- `httpx` for async HTTP (preferred over `requests`)
- `loguru` for logging
- No hardcoded driver lists — pull dynamically from source APIs where possible
- Respect `robots.txt` and rate limits; add `time.sleep` jitter between scraper calls
