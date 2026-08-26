import platform
from types import SimpleNamespace

import pytest

from app.brokers.mt5 import MT5BrokerAdapter
from app.domain.enums import Timeframe
from app.domain.errors import BrokerOperationUnsupported


@pytest.mark.asyncio
async def test_mt5_fails_cleanly_off_windows_without_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    broker = MT5BrokerAdapter(login=123, server="demo", password="not-printed")
    with pytest.raises(BrokerOperationUnsupported, match="Windows") as error:
        await broker.connect()
    assert "not-printed" not in str(error.value)


class FakeMT5Candles:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TIMEFRAME_M5 = 5

    @staticmethod
    def initialize(**_: object) -> bool:
        return True

    @staticmethod
    def shutdown() -> bool:
        return True

    @staticmethod
    def account_info() -> SimpleNamespace:
        return SimpleNamespace(login=123, trade_mode=0)

    @staticmethod
    def symbol_info(_: str) -> SimpleNamespace:
        return SimpleNamespace(point=0.01)

    @staticmethod
    def copy_rates_from_pos(
        symbol: str, timeframe: int, start: int, count: int
    ) -> list[dict[str, int | float]]:
        del symbol, timeframe, start, count
        return [
            {
                "time": 1_700_000_000,
                "open": 2000.0,
                "high": 2001.0,
                "low": 1999.0,
                "close": 2000.5,
                "tick_volume": 100,
                "spread": 25,
            }
        ]


@pytest.mark.asyncio
async def test_mt5_candle_spread_points_are_normalized_to_price_units() -> None:
    broker = MT5BrokerAdapter(
        login=123,
        server="demo",
        password="not-printed",
        mt5_module=FakeMT5Candles(),
    )
    await broker.connect()
    candles = await broker.get_candles("XAUUSDm", Timeframe.M5, 1)
    assert candles[0].spread == 0.25
    await broker.disconnect()
