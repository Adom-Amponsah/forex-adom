"""
Previous day/week levels and round numbers.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional
from app.data.models import Candle, Timeframe, SymbolMetadata
from app.config.settings import config
from pydantic import BaseModel, ConfigDict


class PreviousLevels(BaseModel):
    """Previous day and week high/low levels."""
    model_config = ConfigDict(frozen=True)
    
    previous_day_high: Optional[float] = None
    previous_day_low: Optional[float] = None
    previous_week_high: Optional[float] = None
    previous_week_low: Optional[float] = None
    # Source info
    day_source_candle_id: Optional[str] = None
    day_source_time: Optional[datetime] = None
    week_source_candle_id: Optional[str] = None
    week_source_time: Optional[datetime] = None


def get_broker_day_boundaries(
    candles: list[Candle],
    broker_offset_hours: float = 0.0,
) -> list[tuple[datetime, datetime]]:
    """
    Get broker day boundaries (midnight in broker time).
    
    Returns list of (day_start_utc, day_end_utc) for each broker day.
    """
    if not candles:
        return []
    
    boundaries = []
    current_day = None
    day_start = None
    
    for candle in candles:
        # Convert candle close to broker time
        broker_time = candle.time_close + timedelta(hours=broker_offset_hours)
        candle_day = broker_time.date()
        
        if current_day is None:
            current_day = candle_day
            day_start = candle.time_close - timedelta(seconds=candle.timeframe.to_seconds())
        elif candle_day != current_day:
            # Day changed - record previous day boundary
            day_end = candle.time_close - timedelta(seconds=candle.timeframe.to_seconds())
            boundaries.append((day_start, day_end))
            current_day = candle_day
            day_start = candle.time_close - timedelta(seconds=candle.timeframe.to_seconds())
    
    # Add final day if we have candles
    if day_start and candles:
        day_end = candles[-1].time_close
        boundaries.append((day_start, day_end))
    
    return boundaries


def calculate_previous_levels(
    d1_candles: list[Candle],
    broker_offset_hours: float = 0.0,
) -> PreviousLevels:
    """
    Calculate previous day and week high/low from D1 candles.
    
    Uses broker's daily candles (D1 timeframe) which should align with broker's day.
    """
    if not d1_candles:
        return PreviousLevels()
    
    # D1 candles should represent broker days
    # Previous day = second to last D1 candle (last is current/forming)
    # Previous week = find Monday of previous week
    
    prev_day_high = None
    prev_day_low = None
    prev_week_high = None
    prev_week_low = None
    day_source_id = None
    day_source_time = None
    week_source_id = None
    week_source_time = None
    
    # Previous day
    if len(d1_candles) >= 2:
        prev_day_candle = d1_candles[-2]  # -1 is current day, -2 is previous
        prev_day_high = prev_day_candle.high
        prev_day_low = prev_day_candle.low
        day_source_id = prev_day_candle.id
        day_source_time = prev_day_candle.time_close
    
    # Previous week - find the D1 candle for Friday of previous week
    # or calculate from last 5 D1 candles (Mon-Fri)
    if len(d1_candles) >= 6:
        # Last 5 complete days (excluding current)
        week_candles = d1_candles[-6:-1]  # Previous 5 days
        prev_week_high = max(c.high for c in week_candles)
        prev_week_low = min(c.low for c in week_candles)
        week_source_id = week_candles[0].id  # Monday
        week_source_time = week_candles[0].time_close
    
    return PreviousLevels(
        previous_day_high=prev_day_high,
        previous_day_low=prev_day_low,
        previous_week_high=prev_week_high,
        previous_week_low=prev_week_low,
        day_source_candle_id=day_source_id,
        day_source_time=day_source_time,
        week_source_candle_id=week_source_id,
        week_source_time=week_source_time,
    )


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
    
    # Calculate pip size
    if digits in (3, 5):
        pip_size = point * 10
    else:
        pip_size = point
    
    step = step_pips * pip_size
    
    # Find nearest round number below current price
    base = int(current_price / step) * step
    
    levels = []
    for i in range(-levels_around, levels_around + 1):
        level = base + (i * step)
        levels.append(round(level, digits))
    
    return RoundNumberLevels(
        levels=levels,
        current_price=current_price,
        step_pips=step_pips,
        symbol_point=point,
    )