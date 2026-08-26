"""Read-only broker symbol discovery for canonical gold."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.brokers.symbols import canonicalize_symbol
from app.domain.errors import GoldFlowError
from scripts._mt5_env import broker_from_environment


async def discover(canonical_symbol: str = "XAUUSD") -> int:
    try:
        canonical = canonicalize_symbol(canonical_symbol)
        base = canonical[:3]
        quote = canonical[3:]
        broker = broker_from_environment()
        await broker.connect()
        try:
            symbols = await broker.get_symbols()
            gold = tuple(
                spec
                for spec in symbols
                if (spec.base_currency.upper(), spec.quote_currency.upper()) == (base, quote)
                or canonicalize_symbol(spec.name).startswith(canonical)
                or (canonical == "XAUUSD" and "GOLD" in spec.name.upper())
            )
            if not gold:
                print("No XAU/USD candidates were reported by MT5", file=sys.stderr)
                return 3
            for spec in sorted(gold, key=lambda item: item.name):
                print(
                    f"{spec.name} base={spec.base_currency or '?'} "
                    f"quote={spec.quote_currency or '?'} enabled={spec.trade_enabled} "
                    f"digits={spec.digits} point={spec.point} "
                    f"tick_size={spec.tick_size} tick_value={spec.tick_value} "
                    f"contract_size={spec.contract_size} "
                    f"volume={spec.volume_min}..{spec.volume_max} step={spec.volume_step} "
                    f"stops_level_points={spec.stops_level_points}"
                )
            resolved = await broker.resolve_symbol(canonical)
            print(f"resolved_symbol: {resolved.name}")
            return 0
        finally:
            await broker.disconnect()
    except (GoldFlowError, ValueError) as exc:
        print(f"MT5 symbol discovery failed: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover a canonical symbol in MT5")
    parser.add_argument("canonical_symbol", nargs="?", default="XAUUSD")
    args = parser.parse_args()
    return asyncio.run(discover(args.canonical_symbol))


if __name__ == "__main__":
    raise SystemExit(main())
