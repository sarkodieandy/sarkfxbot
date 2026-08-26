"""Read-only verification that the connected MT5 account is safe for demo testing."""

from __future__ import annotations

import asyncio
import sys

from app.domain.enums import AccountType
from app.domain.errors import GoldFlowError
from scripts._mt5_env import broker_from_environment, masked_account


async def verify() -> int:
    try:
        broker = broker_from_environment()
        await broker.connect()
        try:
            account = await broker.get_account()
            health = await broker.health_check()
            symbol = await broker.resolve_symbol("XAUUSD")
            tick = await broker.get_tick(symbol.name)
            if account.account_type is not AccountType.DEMO:
                print(
                    "REFUSED: connected account is not independently identified as DEMO",
                    file=sys.stderr,
                )
                return 4
            if not health.healthy or not health.connected:
                print("REFUSED: MT5 connection is unhealthy", file=sys.stderr)
                return 5
            print("demo verification passed (read-only; no order was submitted)")
            print(f"account: {masked_account(account.account_id)}")
            print(f"server: {account.server}")
            print(f"symbol: {symbol.name}")
            print(f"tick_time_utc: {tick.timestamp.isoformat()}")
            print(f"spread: {tick.spread}")
            return 0
        finally:
            await broker.disconnect()
    except (GoldFlowError, ValueError) as exc:
        print(f"MT5 demo verification failed: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    return asyncio.run(verify())


if __name__ == "__main__":
    raise SystemExit(main())
