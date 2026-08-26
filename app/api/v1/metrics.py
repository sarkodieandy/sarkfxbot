"""Read-only trading performance summary."""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.auth import ViewerPrincipal
from app.api.dependencies import SessionDependency
from app.db.models import Trade

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=dict[str, Any])
def trading_metrics(
    session: SessionDependency,
    principal: ViewerPrincipal,
) -> dict[str, Any]:
    del principal
    total = session.scalar(select(func.count(Trade.id))) or 0
    closed = session.scalar(select(func.count(Trade.id)).where(Trade.closed_at.is_not(None))) or 0
    wins = (
        session.scalar(
            select(func.count(Trade.id)).where(Trade.closed_at.is_not(None), Trade.realized_pnl > 0)
        )
        or 0
    )
    pnl = session.scalar(select(func.coalesce(func.sum(Trade.realized_pnl), 0))) or Decimal("0")
    gross_profit = session.scalar(
        select(func.coalesce(func.sum(Trade.realized_pnl), 0)).where(Trade.realized_pnl > 0)
    ) or Decimal("0")
    gross_loss = abs(
        session.scalar(
            select(func.coalesce(func.sum(Trade.realized_pnl), 0)).where(Trade.realized_pnl < 0)
        )
        or Decimal("0")
    )
    return {
        "total_trades": int(total),
        "closed_trades": int(closed),
        "wins": int(wins),
        "losses": int(closed - wins),
        "win_rate": float(wins / closed) if closed else 0.0,
        "realized_pnl": str(pnl),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else None,
        "note": "Win rate alone is not a strategy-quality measure.",
    }
