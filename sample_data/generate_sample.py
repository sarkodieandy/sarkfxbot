"""Regenerate GoldFlow's deterministic synthetic XAUUSD fixture.

This data is deliberately labelled synthetic.  It exists for repeatable tests
and demonstration only and must not be represented as broker history.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Row:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float


def _m5_rows(count: int = 1_320) -> list[Row]:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    slopes = (0.020, -0.024, 0.022, -0.018, 0.021)
    segment_size = 264
    trend = 2_630.0
    previous = trend
    rows: list[Row] = []
    for index in range(count):
        segment = min(index // segment_size, len(slopes) - 1)
        trend += slopes[segment]
        wave = 1.35 * math.sin(index / 21.0) + 0.38 * math.sin(index / 5.0)
        close = trend + wave
        open_price = previous
        body = abs(close - open_price)
        upper = 0.16 + 0.05 * (1.0 + math.sin(index / 7.0))
        lower = 0.16 + 0.05 * (1.0 + math.cos(index / 9.0))
        if index % 47 == 0:
            lower += max(body, 0.08) * 1.8
        if index % 53 == 0:
            upper += max(body, 0.08) * 1.8
        rows.append(
            Row(
                timestamp=start + timedelta(minutes=5 * index),
                open=round(open_price, 5),
                high=round(max(open_price, close) + upper, 5),
                low=round(min(open_price, close) - lower, 5),
                close=round(close, 5),
                volume=float(700 + ((index * 37) % 500)),
                spread=round(0.17 + ((index * 11) % 7) * 0.01, 5),
            )
        )
        previous = close
    return rows


def _aggregate(rows: Sequence[Row], size: int) -> list[Row]:
    output: list[Row] = []
    for offset in range(0, len(rows) - size + 1, size):
        group = rows[offset : offset + size]
        output.append(
            Row(
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum(item.volume for item in group),
                spread=sum(item.spread for item in group) / size,
            )
        )
    return output


def generate(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    m5 = _m5_rows()
    series = {"H1": _aggregate(m5, 12), "M15": _aggregate(m5, 3), "M5": m5}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "symbol",
                "timeframe",
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "spread",
                "complete",
            )
        )
        for timeframe, rows in series.items():
            for row in rows:
                writer.writerow(
                    (
                        "XAUUSDm",
                        timeframe,
                        row.timestamp.isoformat().replace("+00:00", "Z"),
                        f"{row.open:.5f}",
                        f"{row.high:.5f}",
                        f"{row.low:.5f}",
                        f"{row.close:.5f}",
                        f"{row.volume:.2f}",
                        f"{row.spread:.5f}",
                        "true",
                    )
                )


def main() -> int:
    destination = Path(__file__).with_name("xauusd_synthetic.csv")
    generate(destination)
    print(f"generated {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
