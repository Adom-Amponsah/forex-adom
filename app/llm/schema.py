"""
Structured schema for LLM interpretation.

The model must return exactly this shape. Anything it claims must be traceable
to evidence IDs from the canonical market state - the validator enforces that.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    CONDITIONAL = "conditional"


class MarketPhase(str, Enum):
    TREND = "trend"
    PULLBACK = "pullback"
    RANGE = "range"
    CONSOLIDATION = "consolidation"
    BREAKOUT = "breakout"
    RETEST = "retest"
    POSSIBLE_REVERSAL = "possible_reversal"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class TimeframeRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    interpretation: str
    references: list[str] = Field(default_factory=list)


class ScenarioCondition(BaseModel):
    """A typed, machine-checkable condition (not free text)."""
    model_config = ConfigDict(frozen=True)

    type: str  # e.g. "price_above", "price_below", "closes_above", "event_occurs"
    price: Optional[float] = None
    reference_id: Optional[str] = None
    description: str = ""


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    direction: str  # "bullish" | "bearish" | "neutral"
    conditions: list[ScenarioCondition] = Field(default_factory=list)
    invalidation: list[ScenarioCondition] = Field(default_factory=list)
    target: Optional[float] = None
    references: list[str] = Field(default_factory=list)


class KeyLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: float
    role: str  # "support" | "resistance" | "target" | "invalidation" | ...
    references: list[str] = Field(default_factory=list)


class AIAnalysis(BaseModel):
    """The LLM's interpretation of a single canonical market state."""
    model_config = ConfigDict(frozen=True)

    bias: Bias
    market_phase: MarketPhase
    summary: str

    timeframe_read: dict[str, TimeframeRead] = Field(default_factory=dict)

    patterns: list[str] = Field(default_factory=list)          # pattern IDs
    key_levels: list[KeyLevel] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    what_changes_the_read: list[str] = Field(default_factory=list)
    unclear: list[str] = Field(default_factory=list)


def ai_analysis_json_schema() -> dict:
    """JSON schema to hand to the model / API as a structured-output contract."""
    return AIAnalysis.model_json_schema()
