"""
EV calculation and vig removal.

Sharp line = Pinnacle (primary), then Bet365/Circa consensus as fallback.
Vig removal uses the multiplicative (power) method which distributes vig
proportionally rather than the simple additive method.
"""

from src.models.market import Market, Outcome, EVOpportunity
from loguru import logger
import math


def remove_vig_multiplicative(outcomes: list[Outcome]) -> dict[str, float]:
    """
    Remove vig using the multiplicative method.
    Each implied prob is divided by the overround (sum of all raw implied probs).
    This is the standard method used by Pinnacle themselves.

    Returns dict of outcome_name -> fair_probability
    """
    raw_probs = {o.name: o.implied_prob for o in outcomes}
    overround = sum(raw_probs.values())

    if overround <= 0:
        logger.warning("Invalid overround: %s", overround)
        return raw_probs

    fair = {name: prob / overround for name, prob in raw_probs.items()}
    logger.debug("Overround: %.4f  |  Fair probs: %s", overround, fair)
    return fair


def remove_vig_power(outcomes: list[Outcome]) -> dict[str, float]:
    """
    Remove vig using the power (Shin) method.
    Solves for k such that sum((p_i)^k) = 1.
    More accurate for large fields (e.g. race winner with 20 drivers).
    """
    raw_probs = [o.implied_prob for o in outcomes]
    overround = sum(raw_probs)

    if abs(overround - 1.0) < 1e-6:
        return {o.name: o.implied_prob for o in outcomes}

    # Binary search for k
    lo, hi = 0.5, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        s = sum(p ** mid for p in raw_probs)
        if s > 1.0:
            lo = mid
        else:
            hi = mid

    k = (lo + hi) / 2
    fair_probs = [p ** k for p in raw_probs]
    total = sum(fair_probs)
    fair_probs = [p / total for p in fair_probs]

    return {o.name: p for o, p in zip(outcomes, fair_probs)}


def calculate_ev(market_price: float, fair_prob: float) -> float:
    """
    EV for a binary (yes/no) contract priced at market_price (0-1 implied prob).
    Assumes $1 bet pays $1 profit on win.

    EV = fair_prob * (1 - market_price) - (1 - fair_prob) * market_price
       = fair_prob - market_price
    """
    return fair_prob - market_price


def find_opportunities(
    sharp_market: Market,
    prediction_market: Market,
    matcher_fn,
    ev_threshold: float = 0.03,
    use_power_method: bool = True,
) -> list[EVOpportunity]:
    """
    Compare a sharp sportsbook market against a prediction market and return +EV outcomes.

    matcher_fn(name_a, name_b) -> bool: fuzzy name match function
    """
    opportunities = []

    # Build fair probs from sharp market
    if not sharp_market.outcomes:
        return []

    vig_fn = remove_vig_power if use_power_method else remove_vig_multiplicative
    fair_probs = vig_fn(sharp_market.outcomes)

    # For each outcome on the prediction market, find the matching sharp fair prob
    for pm_outcome in prediction_market.outcomes:
        best_match = None
        best_prob = None

        for sharp_name, fair_prob in fair_probs.items():
            if matcher_fn(pm_outcome.name, sharp_name):
                best_match = sharp_name
                best_prob = fair_prob
                break

        if best_match is None:
            logger.debug("No sharp match for PM outcome: %s", pm_outcome.name)
            continue

        pm_price = pm_outcome.implied_prob
        ev = calculate_ev(pm_price, best_prob)

        if ev >= ev_threshold:
            # Find the raw sharp decimal odds for display
            sharp_outcome = next(
                (o for o in sharp_market.outcomes if o.name == best_match), None
            )
            sharp_decimal = sharp_outcome.decimal_odds if sharp_outcome else 0.0

            opp = EVOpportunity(
                market_title=prediction_market.title,
                outcome=pm_outcome.name,
                platform=prediction_market.source,
                platform_price=pm_price,
                sharp_fair_prob=best_prob,
                ev_pct=ev,
                sharp_source=sharp_market.source,
                sharp_decimal_odds=sharp_decimal,
                platform_url=prediction_market.url,
            )
            opportunities.append(opp)
            logger.info("Found +EV: %s | EV=%.1f%%", pm_outcome.name, ev * 100)

    return sorted(opportunities, key=lambda x: x.ev_pct, reverse=True)
