# F1 EV Finder

Identifies positive expected value (+EV) betting opportunities on Formula 1 by comparing sharp sportsbook implied probabilities against live Polymarket and Kalshi contract prices.

---

## How It Works

The core insight: sharp sportsbooks (Pinnacle, Bet365, Circa) have the most accurate implied probabilities for F1 events. Prediction markets (Polymarket, Kalshi) are often mispriced relative to those sharp lines. When the prediction market is offering a higher probability than the sharp fair line implies, that's a +EV opportunity.

```
Sharp book odds  →  remove vig  →  fair implied probability
Polymarket/Kalshi price         →  implied probability
EV = fair_prob - market_price
```

Any market where `EV > threshold (default 3%)` gets surfaced.

---

## Workflow

```
┌─────────────────────────────────────┐
│         1. FETCH SHARP ODDS         │
│  The Odds API → Pinnacle, Bet365,   │
│  Circa, DraftKings (race winner +   │
│  H2H markets for F1)                │
└──────────────────┬──────────────────┘
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
┌────────────────┐  ┌─────────────────┐
│  2a. POLYMARKET│  │  2b. KALSHI     │
│  Gamma API     │  │  REST API v2    │
│  (discovery)   │  │  search: F1,    │
│  + CLOB API    │  │  formula,       │
│  (live prices) │  │  grand prix     │
└────────┬───────┘  └────────┬────────┘
         └─────────┬─────────┘
                   ▼
┌─────────────────────────────────────┐
│         3. REMOVE VIG               │
│  Power (Shin) method on sharp odds  │
│  → fair implied probability per     │
│  outcome, sums to exactly 1.0       │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│         4. MATCH MARKETS            │
│  Fuzzy match event + outcome names  │
│  across sources (rapidfuzz).        │
│  Canonical alias table handles      │
│  "Verstappen" = "Max Verstappen"    │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│         5. CALCULATE EV             │
│  EV = fair_prob - market_price      │
│  Filter to EV ≥ threshold (3%)      │
│  Sort by EV descending              │
└──────────────────┬──────────────────┘
                   ▼
┌─────────────────────────────────────┐
│         6. OUTPUT                   │
│  Rich table in terminal             │
│  output/opportunities.json          │
└─────────────────────────────────────┘
```

---

## Technologies

| Layer | Tool | Why |
|---|---|---|
| HTTP client | `httpx` | Sync and async, connection pooling, clean API |
| CLI | `typer` | Declarative CLI with `--help` auto-generation |
| Terminal output | `rich` | Colored tables, readable EV results |
| Fuzzy matching | `rapidfuzz` | Fast Levenshtein/token matching for name normalization |
| Config/env | `python-dotenv` | `.env` file support for API keys |
| Logging | `loguru` | Structured, leveled logging with minimal boilerplate |
| Testing | `pytest` | Unit tests for EV math, vig removal, name matching |
| Sharp odds source | [The Odds API](https://the-odds-api.com) | Aggregates Pinnacle, Bet365, DraftKings, Circa — one API key |

---

## Data Sources

### Sharp Sportsbooks (reference line)

Accessed via **The Odds API** (`ODDS_API_KEY` required, free tier: 500 req/month):

| Book | Role |
|---|---|
| **Pinnacle** | Primary sharp line — lowest margin, sharpest F1 odds globally |
| **Bet365** | Secondary sharp reference, high limits |
| **Circa Sports** | Sharp US book with minimal vig |
| **DraftKings** | Included for H2H driver matchup liquidity |

Market types fetched: race winner outrights, head-to-head driver matchups.

### Prediction Markets (where EV is found)

**Polymarket**
- Discovery: `Gamma API` — searches for active F1 markets by keyword
- Live prices: `CLOB API` — orderbook mid-price for binary markets
- No API key required for public markets
- Prices in USDC (0.0–1.0)

**Kalshi**
- REST API v2 — searches open markets for F1/formula/grand prix keywords
- Auth via `KALSHI_API_KEY` + `KALSHI_API_KEY_ID` (gracefully degrades without auth)
- Prices in cents (0–100), normalized to 0–1
- Exponential backoff on 429 rate limit responses

---

## Market Types

| Type | Description | Sources |
|---|---|---|
| `race_winner` | Who wins the Grand Prix | Pinnacle, Polymarket, Kalshi |
| `qualifying` | Pole position / fastest qualifier | Pinnacle, Polymarket, Kalshi |
| `h2h` | Driver A finishes ahead of Driver B | DraftKings, Polymarket, Kalshi |
| `futures` | Season-long WDC / WCC | Pinnacle, Polymarket, Kalshi |
| `prop` | Fastest lap, podium, points finish | Polymarket, Kalshi |

---

## EV Math

### Vig Removal — Power (Shin) Method

For a market with outcomes `p_1, p_2, ..., p_n` (raw implied probs from decimal odds):

1. Find `k` such that `Σ(p_i^k) = 1` via binary search
2. Fair probability for outcome `i` = `p_i^k / Σ(p_j^k)`

This distributes vig proportionally and is more accurate than the simple multiplicative method for large fields (20-driver race winner markets). Pinnacle themselves use a similar approach.

### EV Formula

For a prediction market contract priced at `m` (implied prob 0–1):

```
EV = fair_prob - market_price
```

Positive EV means the market is underpricing the outcome relative to what sharp books imply.

---

## Project Structure

```
f1-ev-finder/
├── src/
│   ├── scrapers/
│   │   └── odds_api.py        # The Odds API — sharp book odds (Pinnacle, Bet365, DraftKings, Circa)
│   ├── markets/
│   │   ├── polymarket.py      # Gamma API (discovery) + CLOB API (live prices)
│   │   └── kalshi.py          # Kalshi REST API v2 client with auth + rate limiting
│   ├── ev/
│   │   ├── calculator.py      # Vig removal (multiplicative + power), EV calculation
│   │   └── matcher.py         # Fuzzy name matching, canonical driver alias table
│   ├── models/
│   │   └── market.py          # Market, Outcome, EVOpportunity dataclasses
│   └── main.py                # CLI: `scan` and `markets` commands
├── tests/
│   └── test_ev.py             # 14 unit tests (EV math, vig removal, matching)
├── output/
│   └── opportunities.json     # latest scan results (gitignored)
├── .env.example
├── requirements.txt
└── CLAUDE.md
```

---

## Setup

```bash
git clone <repo>
cd f1-ev-finder

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — add ODDS_API_KEY at minimum
```

### Environment Variables

```bash
# Required
ODDS_API_KEY=        # https://the-odds-api.com — free tier sufficient

# Optional (Kalshi falls back to public browsing without these)
KALSHI_API_KEY=
KALSHI_API_KEY_ID=

# Tuning
EV_THRESHOLD=0.03    # minimum EV to surface (default: 3%)
LOG_LEVEL=INFO
```

---

## Usage

```bash
# Scan all F1 markets for +EV vs sharp books
python src/main.py scan

# Filter to a specific race
python src/main.py scan --race "Australian"

# Raise EV threshold to 5%
python src/main.py scan --min-ev 0.05

# Only check Polymarket, skip Kalshi
python src/main.py scan --no-kalshi

# List all available F1 markets without EV calc
python src/main.py markets
python src/main.py markets --source polymarket
python src/main.py markets --source kalshi

# Run tests
pytest tests/ -v
```

### Sample Output

```
╭──────────────────────────────────────────────────────────────────────────────╮
│     F1 +EV Opportunities (2025-03-14 10:00 UTC)                              │
├────────────┬────────────────────────────┬─────────────┬──────────┬──────────┤
│ Platform   │ Market                     │ Outcome     │ Mkt Price│ EV       │
├────────────┼────────────────────────────┼─────────────┼──────────┼──────────┤
│ POLYMARKET │ 2025 Australian GP Winner  │ Norris      │ 22.0%    │ +6.1%    │
│ KALSHI     │ Australian GP - Pole Pos.  │ Verstappen  │ 38.0%    │ +4.2%    │
│ POLYMARKET │ 2025 Bahrain GP H2H        │ Leclerc     │ 44.0%    │ +3.5%    │
╰────────────┴────────────────────────────┴─────────────┴──────────┴──────────╯
```

Results also saved to `output/opportunities.json`.

---

## Output JSON Schema

```json
{
  "market_title": "2025 Australian GP - Race Winner",
  "outcome": "Lando Norris",
  "platform": "polymarket",
  "platform_price": 0.22,
  "sharp_fair_prob": 0.281,
  "ev_pct": 0.061,
  "sharp_source": "pinnacle",
  "sharp_decimal_odds": 3.55,
  "platform_url": "https://polymarket.com/event/...",
  "scanned_at": "2025-03-14T10:00:00Z"
}
```

---

## Limitations & Notes

- **The Odds API free tier** is 500 requests/month. Each `scan` uses 2–4 requests (outrights + H2H). Budget accordingly or upgrade.
- **Polymarket multi-outcome markets** (race winner with 10+ drivers) use Gamma API prices rather than CLOB, which may lag by a few minutes. CLOB enrichment is applied to binary (Yes/No) markets only.
- **Market matching** is fuzzy — a mismatch between a sharp market and a PM/Kalshi market can produce false positives. Always verify before acting.
- **Kalshi F1 liquidity** is thin. Wide bid/ask spreads mean the mid-price overstates true entry price. Factor in ~2–3¢ of slippage.
- This tool is for informational and research purposes.
