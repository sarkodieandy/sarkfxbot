"""Pure symbol canonicalization and broker-candidate ranking."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.errors import SymbolResolutionError
from app.domain.models import SymbolSpecification

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_GOLD_ALIASES = frozenset({"GOLD", "XAUUSD"})


def canonicalize_symbol(value: str) -> str:
    """Normalize recognized gold aliases to their canonical FX pair."""

    normalized = _NON_ALNUM.sub("", value.upper())
    if normalized in _GOLD_ALIASES:
        return "XAUUSD"
    return normalized


def _candidate_score(specification: SymbolSpecification, canonical: str) -> int:
    name = specification.name.upper()
    compact = _NON_ALNUM.sub("", name)
    metadata_match = (
        specification.base_currency.upper() == canonical[:3]
        and specification.quote_currency.upper() == canonical[3:6]
    )
    score = 0
    if canonicalize_symbol(specification.canonical_symbol) == canonical:
        score += 100
    if compact == canonical:
        score += 80
    elif compact.startswith(canonical):
        score += 50
    if metadata_match:
        score += 60
    if "GOLD" in name or "GOLD" in specification.description.upper():
        score += 20
    if specification.trade_enabled:
        score += 10
    if specification.visible:
        score += 2
    return score


def rank_symbol_candidates(
    canonical_symbol: str, symbols: Iterable[SymbolSpecification]
) -> tuple[SymbolSpecification, ...]:
    """Rank plausible broker symbols without assuming a suffix convention."""

    canonical = canonicalize_symbol(canonical_symbol)
    if len(canonical) < 6:
        raise SymbolResolutionError(f"cannot derive base/quote from {canonical_symbol!r}")
    scored = [
        (_candidate_score(specification, canonical), specification) for specification in symbols
    ]
    plausible = [(score, item) for score, item in scored if score >= 50]
    plausible.sort(key=lambda pair: (-pair[0], len(pair[1].name), pair[1].name))
    return tuple(item for _, item in plausible)


def resolve_symbol(
    canonical_symbol: str, symbols: Iterable[SymbolSpecification]
) -> SymbolSpecification:
    """Return the highest-quality enabled symbol or fail closed."""

    ranked = rank_symbol_candidates(canonical_symbol, symbols)
    enabled = [item for item in ranked if item.trade_enabled]
    if not enabled:
        raise SymbolResolutionError(f"no tradable broker symbol resolves {canonical_symbol!r}")
    return enabled[0]
