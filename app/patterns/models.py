"""
Pattern feature models.

Patterns are *deterministic geometric facts* derived from confirmed swings and
candles - never interpreted. Each pattern carries the evidence (swing/candle
IDs) that produced it so the LLM can cite it and the validator can check it.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, computed_field
from app.data.models import Timeframe


class PatternClass(str, Enum):
    CONTINUATION = "continuation"
    REVERSAL = "reversal"
    NEUTRAL = "neutral"


class PatternType(str, Enum):
    DOUBLE_TOP = "double_top"
    DOUBLE_BOTTOM = "double_bottom"
    HEAD_SHOULDERS = "head_and_shoulders"
    INVERSE_HEAD_SHOULDERS = "inverse_head_and_shoulders"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    SYMMETRICAL_TRIANGLE = "symmetrical_triangle"
    RISING_WEDGE = "rising_wedge"
    FALLING_WEDGE = "falling_wedge"
    RECTANGLE = "rectangle"
    CHANNEL = "channel"


# How a trader reads each shape: does it continue the move or reverse it?
PATTERN_CLASS: dict[PatternType, PatternClass] = {
    PatternType.DOUBLE_TOP: PatternClass.REVERSAL,
    PatternType.DOUBLE_BOTTOM: PatternClass.REVERSAL,
    PatternType.HEAD_SHOULDERS: PatternClass.REVERSAL,
    PatternType.INVERSE_HEAD_SHOULDERS: PatternClass.REVERSAL,
    PatternType.RISING_WEDGE: PatternClass.REVERSAL,
    PatternType.FALLING_WEDGE: PatternClass.REVERSAL,
    PatternType.ASCENDING_TRIANGLE: PatternClass.CONTINUATION,
    PatternType.DESCENDING_TRIANGLE: PatternClass.CONTINUATION,
    PatternType.SYMMETRICAL_TRIANGLE: PatternClass.CONTINUATION,
    PatternType.RECTANGLE: PatternClass.CONTINUATION,
    PatternType.CHANNEL: PatternClass.CONTINUATION,
}


def pattern_class_of(pattern_type: PatternType) -> PatternClass:
    return PATTERN_CLASS.get(pattern_type, PatternClass.NEUTRAL)


class PatternStatus(str, Enum):
    FORMING = "forming"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class PatternDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class PatternPoint(BaseModel):
    """A named pivot that defines the pattern (e.g. 'high_1', 'trough', 'head')."""
    model_config = ConfigDict(frozen=True)

    role: str
    price: float
    time: datetime
    swing_id: Optional[str] = None


class TrendLine(BaseModel):
    """A fitted boundary line, expressed in index space for reproducibility."""
    model_config = ConfigDict(frozen=True)

    start_index: int
    end_index: int
    start_price: float
    end_price: float
    slope_per_bar: float

    def price_at(self, index: int) -> float:
        return self.start_price + self.slope_per_bar * (index - self.start_index)


class PatternFeature(BaseModel):
    """A single detected pattern with lifecycle state and evidence."""
    model_config = ConfigDict(frozen=True)

    id: str
    symbol: str
    timeframe: Timeframe
    pattern_type: PatternType
    status: PatternStatus = PatternStatus.FORMING
    direction: PatternDirection = PatternDirection.NEUTRAL

    points: list[PatternPoint] = []

    resistance_line: Optional[TrendLine] = None
    support_line: Optional[TrendLine] = None

    # Key levels
    upper_bound: Optional[float] = None
    lower_bound: Optional[float] = None
    neckline: Optional[float] = None

    # Lifecycle thresholds
    confirmation_price: Optional[float] = None
    invalidation_price: Optional[float] = None
    confirmation_time: Optional[datetime] = None
    confirmation_candle_id: Optional[str] = None
    measured_move_target: Optional[float] = None

    evidence_ids: list[str] = []

    # Internal: candle index from which the pattern may be confirmed (excluded from JSON)
    formed_at_index: int = Field(default=0, exclude=True)

    notes: str = ""

    @computed_field
    @property
    def pattern_class(self) -> str:
        return pattern_class_of(self.pattern_type).value
