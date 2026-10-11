"""
Top-down analysis models.

These mirror how a discretionary trader reads a chart:
  1. higher timeframes set context and key S/R
  2. structure timeframe refines it
  3. decision timeframe times the entry
  4. across timeframes, look for confirmation (or conflict)
  5. classify patterns as continuation vs reversal and check which confirm
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class TimeframeBias(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeframe: str
    role: str  # "context" | "structure" | "decision"
    direction: str  # bullish | bearish | neutral
    structure_direction: str
    key_resistance: Optional[float] = None
    resistance_id: Optional[str] = None
    key_support: Optional[float] = None
    support_id: Optional[str] = None
    confirmed_patterns: int = 0
    forming_patterns: int = 0


class ConfluenceLevel(BaseModel):
    """A support/resistance level that appears on more than one timeframe."""
    model_config = ConfigDict(frozen=True)

    price: float
    low: float
    high: float
    role: str  # support | resistance
    timeframes: list[str]
    strength: int  # number of distinct timeframes
    evidence_ids: list[str]


class Confirmation(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str  # structure_alignment | pattern_confirmation | break_of_structure
    direction: str
    timeframes: list[str]
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class Alignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    htf_bias: str
    structure_bias: str
    decision_bias: str
    aligned: bool
    direction: Optional[str] = None
    confirmations: list[Confirmation] = []
    conflicts: list[str] = []


class Setup(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern_id: str
    timeframe: str
    pattern_type: str
    pattern_class: str  # continuation | reversal
    direction: str
    status: str  # forming | confirmed | failed
    key_level: Optional[float] = None  # neckline / breakout level
    invalidation: Optional[float] = None
    target: Optional[float] = None
    evidence_ids: list[str] = []


class Playbook(BaseModel):
    """Workflow-ordered brief: the chart, read the way a trader reads it."""
    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    price: float

    higher_timeframes: list[TimeframeBias]
    confluence_levels: list[ConfluenceLevel]
    alignment: Alignment
    continuation_setups: list[Setup]
    reversal_setups: list[Setup]
    steps: list[str]
