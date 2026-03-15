from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Outcome:
    name: str                    # canonical driver/team name
    price: float                 # decimal odds (sportsbook) or implied prob (0-1 for markets)
    is_implied_prob: bool = False  # True for Polymarket/Kalshi prices

    @property
    def implied_prob(self) -> float:
        if self.is_implied_prob:
            return self.price
        return 1.0 / self.price if self.price > 0 else 0.0

    @property
    def decimal_odds(self) -> float:
        if not self.is_implied_prob:
            return self.price
        return 1.0 / self.price if self.price > 0 else 0.0


@dataclass
class Market:
    source: str                  # e.g. "pinnacle", "polymarket", "kalshi"
    market_id: str
    title: str
    market_type: str             # "race_winner", "h2h", "qualifying", "futures", "prop"
    event: str                   # e.g. "2025 Australian Grand Prix"
    outcomes: list[Outcome] = field(default_factory=list)
    closes_at: Optional[datetime] = None
    url: Optional[str] = None


@dataclass
class EVOpportunity:
    market_title: str
    outcome: str
    platform: str                # "polymarket" or "kalshi"
    platform_price: float        # implied prob on the market
    sharp_fair_prob: float       # de-vigged prob from sharp books
    ev_pct: float                # (sharp_prob - platform_price) / platform_price ... or direct
    sharp_source: str
    sharp_decimal_odds: float
    platform_url: Optional[str]
    scanned_at: datetime = field(default_factory=datetime.utcnow)

    def __str__(self) -> str:
        direction = "+" if self.ev_pct >= 0 else ""
        return (
            f"[{self.platform.upper()}] {self.market_title} | {self.outcome}\n"
            f"  Market price: {self.platform_price:.1%}  |  Sharp fair: {self.sharp_fair_prob:.1%}  |  EV: {direction}{self.ev_pct:.1%}\n"
            f"  Sharp: {self.sharp_source} @ {self.sharp_decimal_odds:.3f}  |  {self.platform_url or ''}"
        )
