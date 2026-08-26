from decimal import Decimal

from app.risk.demo_validation import DemoPerformance, evaluate_demo_performance


def test_demo_threshold_boundaries_pass_without_promising_future_results() -> None:
    result = evaluate_demo_performance(
        DemoPerformance("1.0.0", 100, Decimal("0.10"), Decimal("1.2")),
        strategy_version="1.0.0",
    )
    assert result.allowed
    assert "do not guarantee" in result.disclaimer


def test_missing_demo_evidence_fails_closed() -> None:
    result = evaluate_demo_performance(None, strategy_version="1.0.0")
    assert not result.allowed
    assert result.reasons == ("DEMO_VALIDATION_EVIDENCE_MISSING",)


def test_every_failed_demo_metric_is_reported() -> None:
    result = evaluate_demo_performance(
        DemoPerformance("0.9.0", 99, Decimal("0.1001"), Decimal("1.199")),
        strategy_version="1.0.0",
    )
    assert not result.allowed
    assert set(result.reasons) == {
        "DEMO_STRATEGY_VERSION_MISMATCH",
        "DEMO_TRADE_COUNT_BELOW_MINIMUM",
        "DEMO_DRAWDOWN_ABOVE_MAXIMUM",
        "DEMO_PROFIT_FACTOR_BELOW_MINIMUM",
    }
