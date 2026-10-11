"""
Previous day/week levels and round numbers.

Previous-period levels are derived from *closed* D1 candles relative to the
decision time, using the broker's day/week boundaries. Nothing is read from the
wall clock, and the "current" (incomplete) day is never included.
"""
from datetime import datetime, timedelta
from typing import Optional
from app.data.models import Candle, SymbolMetadata
from pydantic import BaseModel, ConfigDict


class PreviousLevel(BaseModel):
    """High/low for a single completed period (day or week)."""
    model_config = ConfigDict(frozen=True)

    high: Optional[float] = None
    low: Optional[float] = None
    source_candle_id: Optional[str] = None
    source_time: Optional[datetime] = None


def _broker_open_date(candle: Candle, broker_offset_hours: float):
    """Broker-local date of a candle's open (the trading day it belongs to)."""
    return (candle.time_open + timedelta(hours=broker_offset_hours)).date()


def calculate_previous_levels(
    d1_candles: list[Candle],
    as_of: datetime,
    broker_offset_hours: float = 0.0,
) -> tuple[PreviousLevel, PreviousLevel]:
    """
    Compute the previous completed day and previous completed week from D1
    candles that have already closed at `as_of`.

    Returns (previous_day, previous_week).
    """
    if not d1_candles:
        return PreviousLevel(), PreviousLevel()

    # Only candles already closed at the decision time (causal).
    closed = [c for c in d1_candles if c.time_close <= as_of]
    if not closed:
        return PreviousLevel(), PreviousLevel()

    current_date = (as_of + timedelta(hours=broker_offset_hours)).date()

    # --- previous day: most recent bar whose broker date precedes today ---
    prior_days = [c for c in closed if _broker_open_date(c, broker_offset_hours) < current_date]
    previous_day = PreviousLevel()
    if prior_days:
        bar = prior_days[-1]
        previous_day = PreviousLevel(
            high=bar.high,
            low=bar.low,
            source_candle_id=bar.id,
            source_time=bar.time_close,
        )

    # --- previous week: Mon-Fri before the current week ---
    current_week_start = current_date - timedelta(days=current_date.weekday())
    prev_week_start = current_week_start - timedelta(days=7)
    week_bars = [
        c for c in closed
        if prev_week_start <= _broker_open_date(c, broker_offset_hours) < current_week_start
    ]
    previous_week = PreviousLevel()
    if week_bars:
        previous_week = PreviousLevel(
            high=max(c.high for c in week_bars),
            low=min(c.low for c in week_bars),
            source_candle_id=week_bars[0].id,
            source_time=week_bars[0].time_close,
        )

    return previous_day, previous_week


class RoundNumberLevels(BaseModel):
    """Round number levels around current price."""
    model_config = ConfigDict(frozen=True)

    levels: list[float]
    current_price: float
    step_pips: int
    symbol_point: float


def calculate_round_numbers(
    current_price: float,
    symbol_meta: SymbolMetadata,
    step_pips: int = 100,
    levels_around: int = 5,
) -> RoundNumberLevels:
    """
    Calculate round number levels.

    For 5-digit pairs (point=0.00001), 100 pips = 0.0100
    For 3-digit JPY pairs (point=0.001), 100 pips = 0.100
    """
    point = symbol_meta.point
    digits = symbol_meta.digits

    pip_size = point * 10 if digits in (3, 5) else point
    step = step_pips * pip_size

    base = int(current_price / step) * step

    levels = [round(base + (i * step), digits) for i in range(-levels_around, levels_around + 1)]

    return RoundNumberLevels(
        levels=levels,
        current_price=current_price,
        step_pips=step_pips,
        symbol_point=point,
    )
