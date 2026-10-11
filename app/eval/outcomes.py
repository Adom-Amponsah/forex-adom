"""
Scenario outcome evaluation.

Takes a scenario the LLM emitted (typed conditions) and walks the candles that
came *after* the analysis time to decide: did it activate? did it invalidate?
when? and how far did price move in each direction (MFE / MAE)?

This is pure, deterministic arithmetic over candles - no LLM involved.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict

from app.data.models import Candle
from app.llm.schema import Scenario, ScenarioCondition


class ScenarioOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_name: str
    direction: str
    entry_price: Optional[float] = None

    activated: bool = False
    activated_at: Optional[datetime] = None
    bars_to_activation: Optional[int] = None

    invalidated: bool = False
    invalidated_at: Optional[datetime] = None

    # Max favorable / adverse excursion from entry, in the scenario's direction
    mfe: Optional[float] = None
    mae: Optional[float] = None
    horizon_bars: int = 0
    resolved: bool = False


def _condition_met(cond: ScenarioCondition, candle: Candle) -> bool:
    """Evaluate a single typed condition against one closed candle."""
    t = cond.type
    if cond.price is None:
        return False
    if t in ("closes_above", "price_above"):
        return candle.close > cond.price
    if t in ("closes_below", "price_below"):
        return candle.close < cond.price
    # event_occurs and anything else are not mechanically checkable here
    return False


def _all_met(conditions: list[ScenarioCondition], candle: Candle) -> bool:
    return bool(conditions) and all(_condition_met(c, candle) for c in conditions)


def evaluate_scenario(
    scenario: Scenario,
    candles: list[Candle],
    as_of: datetime,
    horizon_bars: int = 50,
) -> ScenarioOutcome:
    """Resolve one scenario against the candles after `as_of`."""
    future = [c for c in candles if c.time_close > as_of][:horizon_bars]

    entry = next((c.close for c in candles if c.time_close == as_of), None)

    activated_at = None
    invalidated_at = None

    for i, c in enumerate(future):
        if _all_met(scenario.invalidation, c):
            invalidated_at = c.time_close
            break
        if _all_met(scenario.conditions, c):
            activated_at = c.time_close
            break

    mfe = mae = None
    if entry is not None and future:
        highs = [c.high for c in future]
        lows = [c.low for c in future]
        if scenario.direction == "bearish":
            mfe = entry - min(lows)
            mae = max(highs) - entry
        else:  # bullish / neutral treated as long
            mfe = max(highs) - entry
            mae = entry - min(lows)

    bars_to_activation = None
    if activated_at is not None:
        bars_to_activation = sum(
            1 for c in future if c.time_close <= activated_at
        )

    return ScenarioOutcome(
        scenario_name=scenario.name,
        direction=scenario.direction,
        entry_price=entry,
        activated=activated_at is not None,
        activated_at=activated_at,
        bars_to_activation=bars_to_activation,
        invalidated=invalidated_at is not None,
        invalidated_at=invalidated_at,
        mfe=mfe,
        mae=mae,
        horizon_bars=len(future),
        resolved=activated_at is not None or invalidated_at is not None,
    )
