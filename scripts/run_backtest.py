"""Run GoldFlow's closed-candle strategy against a multi-timeframe CSV."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.backtesting.data import load_candle_csv  # noqa: E402
from app.backtesting.engine import BacktestEngine  # noqa: E402
from app.backtesting.models import BacktestConfig  # noqa: E402
from app.backtesting.reports import write_html_report, write_json_report  # noqa: E402
from app.strategies.gold_h1_m15_m5 import GoldTrendPullbackStrategy  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a deterministic, cost-aware GoldFlow backtest."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Multi-timeframe candle CSV",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Use the bundled deterministic synthetic XAUUSD fixture",
    )
    parser.add_argument(
        "--output-dir",
        "--output",
        type=Path,
        default=_PROJECT_ROOT / "backtest_reports",
        help="Directory for JSON and HTML reports",
    )
    parser.add_argument("--initial-balance", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.01, help="Fraction per trade")
    parser.add_argument("--fixed-spread", type=float, default=0.20)
    parser.add_argument("--slippage", type=float, default=0.03)
    parser.add_argument("--commission", type=float, default=3.5, help="Per lot per side")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.sample and args.data is not None:
        parser.error("--sample and --data are mutually exclusive")
    data_path = args.data or _PROJECT_ROOT / "sample_data" / "xauusd_synthetic.csv"
    candles = load_candle_csv(data_path)
    config = BacktestConfig(
        initial_balance=args.initial_balance,
        risk_fraction=args.risk,
        fixed_spread=args.fixed_spread,
        slippage=args.slippage,
        commission_per_lot_per_side=args.commission,
    )
    result = BacktestEngine(GoldTrendPullbackStrategy(), config).run(candles)
    json_path = write_json_report(result, args.output_dir / "goldflow-backtest.json")
    html_path = write_html_report(result, args.output_dir / "goldflow-backtest.html")
    summary = {
        "strategy": f"{result.strategy_id} {result.strategy_version}",
        "data": str(data_path),
        "json_report": str(json_path),
        "html_report": str(html_path),
        "initial_balance": result.initial_balance,
        "final_balance": result.final_balance,
        "metrics": result.metrics,
        "warning": "Backtests do not guarantee future results.",
    }
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
