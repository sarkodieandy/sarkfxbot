"""JSON and standalone HTML backtest reporting."""

from __future__ import annotations

import html
import json
from pathlib import Path

from app.backtesting.models import BacktestResult


def write_json_report(result: BacktestResult, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    return destination


def _format(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def write_html_report(result: BacktestResult, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metric_rows = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(_format(value))}</td></tr>"
        for key, value in result.metrics.items()
        if key not in {"daily_returns", "monthly_returns"}
    )
    trade_rows = "".join(
        "<tr>"
        f"<td>{html.escape(trade.opened_at.isoformat())}</td>"
        f"<td>{trade.direction.value}</td>"
        f"<td>{trade.entry_price:.5f}</td>"
        f"<td>{trade.exit_price:.5f}</td>"
        f"<td>{trade.net_pnl:.2f}</td>"
        f"<td>{trade.r_multiple:.3f}</td>"
        f"<td>{html.escape(trade.exit_reason)}</td>"
        "</tr>"
        for trade in result.trades
    )
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>GoldFlow Backtest Report</title>
<style>
body{{font-family:system-ui;margin:2rem;max-width:1100px}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border:1px solid #ccc;padding:.45rem;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
.warning{{background:#fff3cd;padding:1rem}}
</style>
</head><body>
<h1>GoldFlow Backtest Report</h1>
<p><strong>{html.escape(result.strategy_id)} {html.escape(result.strategy_version)}</strong><br>
{result.started_at.isoformat()} to {result.ended_at.isoformat()}</p>
<p class="warning">
Backtests and confidence scores do not predict or guarantee future profitability.
</p>
<h2>Metrics</h2><table>{metric_rows}</table>
<h2>Trades</h2>
<table><thead><tr>
<th>Opened</th><th>Direction</th><th>Entry</th><th>Exit</th>
<th>Net P&amp;L</th><th>R</th><th>Reason</th>
</tr></thead><tbody>{trade_rows}</tbody></table>
</body></html>"""
    destination.write_text(body, encoding="utf-8")
    return destination
