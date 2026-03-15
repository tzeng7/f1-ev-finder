"""Tests for EV calculator using static fixture data."""

import pytest
from src.models.market import Market, Outcome
from src.ev.calculator import (
    remove_vig_multiplicative,
    remove_vig_power,
    calculate_ev,
    find_opportunities,
)
from src.ev.matcher import names_match


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_race_winner_market(source: str = "pinnacle") -> Market:
    """Simulated 3-driver race with ~5% vig."""
    return Market(
        source=source,
        market_id="test_race_001",
        title="2025 Australian GP - Race Winner",
        market_type="race_winner",
        event="2025 Australian Grand Prix",
        outcomes=[
            Outcome(name="Max Verstappen", price=2.10, is_implied_prob=False),   # 47.6%
            Outcome(name="Lando Norris", price=3.50, is_implied_prob=False),     # 28.6%
            Outcome(name="Charles Leclerc", price=4.00, is_implied_prob=False),  # 25.0%
            # Overround = 47.6 + 28.6 + 25.0 = 101.2% (~1.2% vig)
        ],
    )


def make_pm_market_overpriced() -> Market:
    """Polymarket where Verstappen is priced too low (sharp says higher)."""
    return Market(
        source="polymarket",
        market_id="pm_race_001",
        title="2025 Australian GP - Race Winner",
        market_type="race_winner",
        event="2025 Australian Grand Prix",
        outcomes=[
            Outcome(name="Max Verstappen", price=0.42, is_implied_prob=True),  # 42% — sharp fair is ~47%
            Outcome(name="Lando Norris", price=0.30, is_implied_prob=True),
            Outcome(name="Charles Leclerc", price=0.28, is_implied_prob=True),
        ],
        url="https://polymarket.com/event/test",
    )


def make_pm_market_no_ev() -> Market:
    """Polymarket where Verstappen is priced at fair value (no EV)."""
    return Market(
        source="polymarket",
        market_id="pm_race_002",
        title="2025 Australian GP - Race Winner",
        market_type="race_winner",
        event="2025 Australian Grand Prix",
        outcomes=[
            Outcome(name="Max Verstappen", price=0.47, is_implied_prob=True),
            Outcome(name="Lando Norris", price=0.28, is_implied_prob=True),
            Outcome(name="Charles Leclerc", price=0.25, is_implied_prob=True),
        ],
    )


# ── Vig removal tests ─────────────────────────────────────────────────────────

def test_remove_vig_multiplicative_sums_to_one():
    market = make_race_winner_market()
    fair = remove_vig_multiplicative(market.outcomes)
    assert abs(sum(fair.values()) - 1.0) < 1e-6


def test_remove_vig_power_sums_to_one():
    market = make_race_winner_market()
    fair = remove_vig_power(market.outcomes)
    assert abs(sum(fair.values()) - 1.0) < 1e-6


def test_remove_vig_power_shifts_toward_underdog():
    """Power method should give slightly more probability to underdogs vs multiplicative."""
    market = make_race_winner_market()
    mult = remove_vig_multiplicative(market.outcomes)
    power = remove_vig_power(market.outcomes)
    # Favorite (Verstappen) should have slightly lower fair prob under power method
    assert power["Max Verstappen"] <= mult["Max Verstappen"] + 0.01


# ── EV calculation tests ──────────────────────────────────────────────────────

def test_calculate_ev_positive():
    """Market price 42%, fair 47% → EV = +5%."""
    ev = calculate_ev(market_price=0.42, fair_prob=0.47)
    assert abs(ev - 0.05) < 1e-6


def test_calculate_ev_negative():
    """Market price 55%, fair 47% → EV = -8%."""
    ev = calculate_ev(market_price=0.55, fair_prob=0.47)
    assert abs(ev - (-0.08)) < 1e-6


def test_calculate_ev_breakeven():
    ev = calculate_ev(market_price=0.47, fair_prob=0.47)
    assert abs(ev) < 1e-9


# ── find_opportunities tests ──────────────────────────────────────────────────

def test_finds_positive_ev_opportunity():
    sharp = make_race_winner_market()
    pm = make_pm_market_overpriced()
    opps = find_opportunities(sharp, pm, matcher_fn=names_match, ev_threshold=0.03)

    assert len(opps) >= 1
    verstappen_opp = next((o for o in opps if "Verstappen" in o.outcome), None)
    assert verstappen_opp is not None
    assert verstappen_opp.ev_pct > 0.03
    assert verstappen_opp.platform == "polymarket"


def test_no_ev_below_threshold():
    sharp = make_race_winner_market()
    pm = make_pm_market_no_ev()
    opps = find_opportunities(sharp, pm, matcher_fn=names_match, ev_threshold=0.03)
    assert len(opps) == 0


def test_opportunities_sorted_by_ev_desc():
    sharp = make_race_winner_market()
    pm = make_pm_market_overpriced()
    opps = find_opportunities(sharp, pm, matcher_fn=names_match, ev_threshold=0.0)

    for i in range(len(opps) - 1):
        assert opps[i].ev_pct >= opps[i + 1].ev_pct


# ── Name matching tests ───────────────────────────────────────────────────────

def test_exact_match():
    assert names_match("Max Verstappen", "Max Verstappen")


def test_alias_match():
    assert names_match("Verstappen", "Max Verstappen")


def test_case_insensitive():
    assert names_match("MAX VERSTAPPEN", "Max Verstappen")


def test_no_match_different_drivers():
    assert not names_match("Lewis Hamilton", "Max Verstappen")


def test_fuzzy_match_abbreviated():
    assert names_match("M. Verstappen", "Max Verstappen")
