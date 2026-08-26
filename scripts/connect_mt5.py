"""Read-only MT5 connectivity and XAUUSD resolution check."""

from __future__ import annotations

import asyncio
import sys

from app.domain.errors import GoldFlowError
from scripts._mt5_env import broker_from_environment, masked_account


async def check_connection() -> int:
    try:
        broker = broker_from_environment()
        await broker.connect()
        try:
            account = await broker.get_account()
            symbol = await broker.resolve_symbol("XAUUSD")
            print("connected")
            print(f"broker: {account.broker}")
            print(f"server: {account.server}")
            print(f"account: {masked_account(account.account_id)}")
            print(f"account_type: {account.account_type.value}")
            print(f"balance: {account.balance} {account.currency}")
            print(f"equity: {account.equity} {account.currency}")
            print(f"resolved_symbol: {symbol.name}")
            return 0
        finally:
            await broker.disconnect()
    except (GoldFlowError, ValueError) as exc:
        print(f"MT5 connection validation failed: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    return asyncio.run(check_connection())


if __name__ == "__main__":
    raise SystemExit(main())
