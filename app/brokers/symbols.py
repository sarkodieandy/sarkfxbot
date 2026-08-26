"""Conservative canonical-to-broker symbol resolution."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.errors import SymbolResolutionError
from app.domain.models import SymbolSpecification

_CANONICAL_ALIASES = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
}
_KNOWN_EXACT_ALIASES = {
    "XAUUSD": frozenset({"GOLD", "XAUUSDM", "XAUUSDC", "XAUUSD247M", "XAUUSD.PRO"}),
}


def canonicalize_symbol(value: str) -> str:
    normalized = re.sub(r"[\s/_-]", "", value).upper()
    canonical = _CANONICAL_ALIASES.get(normalized, normalized)
    if not canonical:
        raise SymbolResolutionError("canonical symbol is empty")
    return canonical


def _score(spec: SymbolSpecification, canonical: str, aliases: frozenset[str]) -> int | None:
    name = spec.name.upper()
    compact_name = re.sub(r"[\s/_-]", "", name)
    if name == canonical:
        return 400
    if name in aliases:
        return 350
    metadata_matches = (
        spec.base_currency.upper() == canonical[:3] and spec.quote_currency.upper() == canonical[3:]
    )
    if metadata_matches:
        return 310 if compact_name.startswith(canonical) else 300
    # Fallback is intentionally narrow. It supports broker suffixes but not arbitrary
    # substring/fuzzy matches such as XAUEUR or synthetic names containing XAUUSD.
    if re.fullmatch(rf"{re.escape(canonical)}(?:[A-Z0-9.]*)", compact_name):
        return 200
    return None


def resolve_symbol(
    canonical_symbol: str,
    symbols: Iterable[SymbolSpecification],
    *,
    aliases: Iterable[str] = (),
) -> SymbolSpecification:
    """Resolve one unique tradeable symbol, rejecting ambiguity and missing metadata."""

    canonical = canonicalize_symbol(canonical_symbol)
    accepted_aliases = frozenset(
        {item.upper() for item in aliases} | set(_KNOWN_EXACT_ALIASES.get(canonical, ()))
    )
    ranked: list[tuple[int, SymbolSpecification]] = []
    for spec in symbols:
        if not spec.trade_enabled:
            continue
        score = _score(spec, canonical, accepted_aliases)
        if score is not None:
            ranked.append((score, spec))
    if not ranked:
        raise SymbolResolutionError(f"no tradeable broker symbol resolves to {canonical}")
    highest = max(score for score, _ in ranked)
    winners = [spec for score, spec in ranked if score == highest]
    if len(winners) != 1:
        names = ", ".join(sorted(spec.name for spec in winners))
        raise SymbolResolutionError(f"ambiguous broker symbols for {canonical}: {names}")
    return winners[0]
