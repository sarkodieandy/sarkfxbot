"""Strict CSV market-data loading."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.domain.enums import Timeframe
from app.domain.models import Candle
from app.market.candles import validate_candle_sequence

_REQUIRED_COLUMNS = frozenset(
    {"symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume", "spread"}
)


def _parse_complete(value: str | None) -> bool:
    if value is None or not value.strip():
        return True
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"invalid complete flag {value!r}")


def load_candle_csv(path: str | Path) -> dict[Timeframe, tuple[Candle, ...]]:
    """Load the deterministic multi-timeframe GoldFlow CSV format."""

    source = Path(path)
    grouped: defaultdict[Timeframe, list[Candle]] = defaultdict(list)
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = _REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"candle CSV is missing columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                timeframe = Timeframe(row["timeframe"])
                candle = Candle(
                    symbol=row["symbol"],
                    timeframe=timeframe,
                    timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    spread=float(row["spread"]),
                    complete=_parse_complete(row.get("complete")),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid candle CSV row {line_number}: {exc}") from exc
            grouped[timeframe].append(candle)
    output: dict[Timeframe, tuple[Candle, ...]] = {}
    for timeframe, values in grouped.items():
        values.sort(key=lambda item: item.timestamp)
        output[timeframe] = validate_candle_sequence(values)
    return output
