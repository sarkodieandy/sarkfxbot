"""Deterministic setup-quality scoring.

Confidence is a rules-based quality score and explicitly is not a probability
of winning, a forecast, or a promise of profitability.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ConfidenceWeights:
    h1_trend: int = 30
    m15_pullback: int = 25
    m5_confirmation: int = 25
    structure_alignment: int = 10
    spread_quality: int = 5
    volatility_quality: int = 5

    def __post_init__(self) -> None:
        values = (
            self.h1_trend,
            self.m15_pullback,
            self.m5_confirmation,
            self.structure_alignment,
            self.spread_quality,
            self.volatility_quality,
        )
        if any(value < 0 for value in values) or sum(values) != 100:
            raise ValueError("confidence weights must be non-negative and total 100")


@dataclass(frozen=True, slots=True)
class ConfidenceFactors:
    """Normalized deterministic factor strengths between zero and one."""

    h1_trend: float
    m15_pullback: float
    m5_confirmation: float
    structure_alignment: float
    spread_quality: float
    volatility_quality: float

    def __post_init__(self) -> None:
        for value in (
            self.h1_trend,
            self.m15_pullback,
            self.m5_confirmation,
            self.structure_alignment,
            self.spread_quality,
            self.volatility_quality,
        ):
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("confidence factors must be finite values from 0 to 1")


@dataclass(frozen=True, slots=True)
class ConfidenceBreakdown:
    score: int
    components: dict[str, int]
    interpretation: str = "deterministic setup-quality score; not win probability"


def score_confidence(
    factors: ConfidenceFactors, weights: ConfidenceWeights | None = None
) -> ConfidenceBreakdown:
    """Return a stable integer score and auditable weighted components."""

    selected_weights = weights or ConfidenceWeights()
    components = {
        "h1_trend": round(factors.h1_trend * selected_weights.h1_trend),
        "m15_pullback": round(factors.m15_pullback * selected_weights.m15_pullback),
        "m5_confirmation": round(factors.m5_confirmation * selected_weights.m5_confirmation),
        "structure_alignment": round(
            factors.structure_alignment * selected_weights.structure_alignment
        ),
        "spread_quality": round(factors.spread_quality * selected_weights.spread_quality),
        "volatility_quality": round(
            factors.volatility_quality * selected_weights.volatility_quality
        ),
    }
    return ConfidenceBreakdown(sum(components.values()), components)
