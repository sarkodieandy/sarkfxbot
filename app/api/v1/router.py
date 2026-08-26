"""Aggregate all stable v1 routers."""

from fastapi import APIRouter

from app.api.v1 import account, admin, metrics, positions, risk, signals, strategy, trades

router = APIRouter(prefix="/api/v1")
router.include_router(account.router)
router.include_router(signals.router)
router.include_router(positions.router)
router.include_router(trades.router)
router.include_router(metrics.router)
router.include_router(strategy.router)
router.include_router(risk.router)
router.include_router(admin.router)
