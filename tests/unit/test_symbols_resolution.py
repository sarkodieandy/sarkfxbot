from decimal import Decimal

import pytest

from app.brokers.symbols import resolve_symbol
from app.domain.errors import SymbolResolutionError
from app.domain.models import SymbolSpecification


def spec(
    name: str,
    *,
    base: str = "XAU",
    quote: str = "USD",
    enabled: bool = True,
) -> SymbolSpecification:
    return SymbolSpecification(
        name=name,
        canonical_symbol="XAUUSD",
        base_currency=base,
        quote_currency=quote,
        digits=2,
        point=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("1"),
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
        trade_enabled=enabled,
    )


def test_exact_symbol_wins_over_suffixes() -> None:
    resolved = resolve_symbol("GOLD", (spec("XAUUSDm"), spec("XAUUSD")))
    assert resolved.name == "XAUUSD"


def test_metadata_resolves_unknown_broker_suffix() -> None:
    resolved = resolve_symbol("XAUUSD", (spec("GOLDmicro"),))
    assert resolved.name == "GOLDmicro"


def test_ambiguous_metadata_is_rejected() -> None:
    with pytest.raises(SymbolResolutionError, match="ambiguous"):
        resolve_symbol("XAUUSD", (spec("XAUUSDm"), spec("XAUUSDc")))


def test_disabled_exact_symbol_is_not_selected() -> None:
    resolved = resolve_symbol("XAUUSD", (spec("XAUUSD", enabled=False), spec("XAUUSDm")))
    assert resolved.name == "XAUUSDm"


def test_fuzzy_substring_is_not_accepted_without_matching_metadata() -> None:
    with pytest.raises(SymbolResolutionError, match="no tradeable"):
        resolve_symbol("XAUUSD", (spec("INDEX-XAUUSD-FAKE", base="", quote=""),))
