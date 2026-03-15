"""
Fuzzy name matching for driver/team/race names across sportsbooks and prediction markets.

Known quirks:
- Pinnacle: "Max Verstappen"
- Polymarket: "Max Verstappen", "Verstappen", "M. Verstappen"
- Kalshi: "MAX VERSTAPPEN", "Verstappen"
- DraftKings: "Max Verstappen"
"""

from rapidfuzz import fuzz, process
from loguru import logger

# Manual overrides for known mismatches
CANONICAL_ALIASES: dict[str, list[str]] = {
    "Max Verstappen": ["verstappen", "max v", "m. verstappen", "ver"],
    "Lewis Hamilton": ["hamilton", "lewis h", "l. hamilton", "ham"],
    "Charles Leclerc": ["leclerc", "charles l", "c. leclerc", "lec"],
    "Carlos Sainz": ["sainz", "carlos s", "c. sainz", "sai"],
    "Lando Norris": ["norris", "lando n", "l. norris", "nor"],
    "Oscar Piastri": ["piastri", "oscar p", "o. piastri", "pia"],
    "George Russell": ["russell", "george r", "g. russell", "rus"],
    "Fernando Alonso": ["alonso", "fernando a", "f. alonso", "alo"],
    "Sergio Perez": ["perez", "checo", "sergio p", "s. perez", "per"],
    "Lance Stroll": ["stroll", "lance s", "l. stroll", "str"],
    "Valtteri Bottas": ["bottas", "valtteri b", "v. bottas", "bot"],
    "Esteban Ocon": ["ocon", "esteban o", "e. ocon", "ocn"],
    "Pierre Gasly": ["gasly", "pierre g", "p. gasly", "gas"],
    "Kevin Magnussen": ["magnussen", "kevin m", "k. magnussen", "mag"],
    "Nico Hulkenberg": ["hulkenberg", "hulk", "nico h", "n. hulkenberg", "hul"],
    "Yuki Tsunoda": ["tsunoda", "yuki t", "y. tsunoda", "tsu"],
    "Alexander Albon": ["albon", "alex albon", "a. albon", "alb"],
    "Logan Sargeant": ["sargeant", "logan s", "l. sargeant", "sar"],
    "Guanyu Zhou": ["zhou", "guanyu z", "g. zhou", "zho"],
    "Oliver Bearman": ["bearman", "oliver b", "o. bearman", "bea"],
    "Kimi Antonelli": ["antonelli", "kimi a", "k. antonelli", "ant"],
    "Jack Doohan": ["doohan", "jack d", "j. doohan", "doo"],
    "Isack Hadjar": ["hadjar", "isack h", "i. hadjar", "had"],
    "Liam Lawson": ["lawson", "liam l", "l. lawson", "law"],
    "Gabriel Bortoleto": ["bortoleto", "gabriel b", "g. bortoleto", "bor"],
    "Nico Hulkenberg": ["hulkenberg", "hulk", "nico h"],
}

# Build reverse lookup: alias -> canonical
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in CANONICAL_ALIASES.items():
    _ALIAS_TO_CANONICAL[canonical.lower()] = canonical
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def normalize(name: str) -> str:
    """Lowercase, strip extra whitespace."""
    return " ".join(name.lower().split())


def canonicalize(name: str) -> str:
    """Return canonical name if known alias, otherwise return normalized name."""
    key = normalize(name)
    return _ALIAS_TO_CANONICAL.get(key, name)


def names_match(name_a: str, name_b: str, threshold: int = 80) -> bool:
    """
    Return True if two driver/outcome names refer to the same entity.
    First checks canonical alias table, then falls back to fuzzy matching.
    """
    canon_a = canonicalize(name_a)
    canon_b = canonicalize(name_b)

    if normalize(canon_a) == normalize(canon_b):
        return True

    score = fuzz.token_sort_ratio(canon_a, canon_b)
    matched = score >= threshold
    if matched:
        logger.debug("Fuzzy match: '%s' <-> '%s' (score=%d)", name_a, name_b, score)
    return matched


def best_match(
    query: str,
    candidates: list[str],
    threshold: int = 80,
) -> tuple[str | None, int]:
    """
    Return (best_matching_candidate, score) or (None, 0) if no match above threshold.
    """
    canon_query = canonicalize(query)
    canon_candidates = {c: canonicalize(c) for c in candidates}

    result = process.extractOne(
        canon_query,
        list(canon_candidates.values()),
        scorer=fuzz.token_sort_ratio,
    )
    if result is None or result[1] < threshold:
        return None, 0

    # Map back to original candidate name
    matched_canon = result[0]
    for orig, canon in canon_candidates.items():
        if canon == matched_canon:
            return orig, result[1]

    return None, 0
