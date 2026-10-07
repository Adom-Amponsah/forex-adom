"""
Swing detection with confirmation logic.
Implements 5-left / 5-right symmetric pivot window with explicit equal high/low policy.
"""
from datetime import datetime, timezone
from typing import Optional
from enum import Enum
import numpy as np
from app.data.models import Candle, Timeframe, make_swing_id
from app.config.settings import config
from pydantic import BaseModel, ConfigDict


class SwingType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class EqualPolicy(str, Enum):
    STRICT = "strict"
    EQUAL_RIGHT = "equal_right"
    EQUAL_LEFT = "equal_left"


class Swing(BaseModel):
    """Confirmed swing point with metadata."""
    model_config = ConfigDict(frozen=True)
    
    id: str
    symbol: str
    timeframe: Timeframe
    swing_type: SwingType
    pivot_index: int  # Index in the candle array
    pivot_time: datetime  # Close time of pivot candle
    confirmation_time: datetime  # When swing was confirmed (pivot + right_bars)
    price: float
    # Equal high/low classification
    equal_policy: EqualPolicy = EqualPolicy.STRICT
    is_equal_left: bool = False
    is_equal_right: bool = False
    # Strength/quality metrics
    left_bars: int = 5
    right_bars: int = 5


class SwingCandidate(BaseModel):
    """Unconfirmed swing candidate (pivot found, waiting for right bars)."""
    model_config = ConfigDict(frozen=True)
    
    symbol: str
    timeframe: Timeframe
    swing_type: SwingType
    pivot_index: int
    pivot_time: datetime
    price: float
    left_bars: int
    right_bars: int
    equal_policy: EqualPolicy = EqualPolicy.STRICT
    is_equal_left: bool = False
    is_equal_right: bool = False


def find_swing_candidates(
    candles: list[Candle],
    left_bars: int = 5,
    right_bars: int = 5,
    equal_policy: EqualPolicy = EqualPolicy.STRICT,
) -> list[SwingCandidate]:
    """
    Find all swing candidates (pivots) in the candle series.
    
    A pivot at index i requires:
    - For HIGH: high[i] > high[i-k] for k=1..left_bars AND high[i] >= high[i+k] for k=1..right_bars
    - For LOW: low[i] < low[i-k] for k=1..left_bars AND low[i] <= low[i+k] for k=1..right_bars
    
    Returns candidates that have satisfied the LEFT side condition.
    The RIGHT side condition will be checked at confirmation time.
    """
    if len(candles) < left_bars + right_bars + 1:
        return []
    
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    times = [c.time_close for c in candles]
    
    candidates = []
    
    for i in range(left_bars, len(candles) - right_bars):
        # Check left side for HIGH
        left_highs = highs[i - left_bars:i]
        if equal_policy == EqualPolicy.STRICT:
            is_higher_left = np.all(highs[i] > left_highs)
        elif equal_policy == EqualPolicy.EQUAL_LEFT:
            is_higher_left = np.all(highs[i] >= left_highs)
        else:  # EQUAL_RIGHT - left is strict
            is_higher_left = np.all(highs[i] > left_highs)
        
        is_equal_left = False
        if is_higher_left and equal_policy in (EqualPolicy.EQUAL_LEFT, EqualPolicy.EQUAL_RIGHT):
            is_equal_left = np.any(highs[i] == left_highs)
        
        # Check left side for LOW
        left_lows = lows[i - left_bars:i]
        if equal_policy == EqualPolicy.STRICT:
            is_lower_left = np.all(lows[i] < left_lows)
        elif equal_policy == EqualPolicy.EQUAL_LEFT:
            is_lower_left = np.all(lows[i] <= left_lows)
        else:
            is_lower_left = np.all(lows[i] < left_lows)
        
        is_equal_left_low = False
        if is_lower_left and equal_policy in (EqualPolicy.EQUAL_LEFT, EqualPolicy.EQUAL_RIGHT):
            is_equal_left_low = np.any(lows[i] == left_lows)
        
        if is_higher_left:
            candidates.append(SwingCandidate(
                symbol=candles[i].symbol,
                timeframe=candles[i].timeframe,
                swing_type=SwingType.HIGH,
                pivot_index=i,
                pivot_time=times[i],
                price=highs[i],
                left_bars=left_bars,
                right_bars=right_bars,
                equal_policy=equal_policy,
                is_equal_left=is_equal_left,
            ))
        
        if is_lower_left:
            candidates.append(SwingCandidate(
                symbol=candles[i].symbol,
                timeframe=candles[i].timeframe,
                swing_type=SwingType.LOW,
                pivot_index=i,
                pivot_time=times[i],
                price=lows[i],
                left_bars=left_bars,
                right_bars=right_bars,
                equal_policy=equal_policy,
                is_equal_left=is_equal_left_low,
            ))
    
    return candidates


def confirm_swings(
    candidates: list[SwingCandidate],
    candles: list[Candle],
    as_of_index: int,  # Current candle index (latest available)
) -> list[Swing]:
    """
    Confirm swing candidates whose right side has completed.
    
    A candidate at pivot_index is confirmed when as_of_index >= pivot_index + right_bars.
    At confirmation, we also verify the right side condition.
    """
    if not candles:
        return []
    
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    times = [c.time_close for c in candles]
    
    confirmed = []
    
    for cand in candidates:
        # Check if right side is complete
        confirmation_idx = cand.pivot_index + cand.right_bars
        
        if as_of_index < confirmation_idx:
            continue  # Not yet confirmed
        
        # Verify right side condition
        right_highs = highs[cand.pivot_index + 1:confirmation_idx + 1]
        right_lows = lows[cand.pivot_index + 1:confirmation_idx + 1]
        
        if cand.swing_type == SwingType.HIGH:
            if cand.equal_policy == EqualPolicy.STRICT:
                right_ok = np.all(cand.price >= right_highs)
            elif cand.equal_policy == EqualPolicy.EQUAL_RIGHT:
                right_ok = np.all(cand.price >= right_highs)
            else:  # EQUAL_LEFT - right is strict
                right_ok = np.all(cand.price > right_highs)
            
            is_equal_right = False
            if right_ok and cand.equal_policy in (EqualPolicy.EQUAL_RIGHT, EqualPolicy.EQUAL_LEFT):
                is_equal_right = np.any(cand.price == right_highs)
            
            if not right_ok:
                continue
            
            confirmed.append(Swing(
                id=make_swing_id(
                    cand.symbol, cand.timeframe, "HIGH", cand.pivot_time
                ),
                symbol=cand.symbol,
                timeframe=cand.timeframe,
                swing_type=SwingType.HIGH,
                pivot_index=cand.pivot_index,
                pivot_time=cand.pivot_time,
                confirmation_time=times[confirmation_idx],
                price=cand.price,
                equal_policy=cand.equal_policy,
                is_equal_left=cand.is_equal_left,
                is_equal_right=is_equal_right,
                left_bars=cand.left_bars,
                right_bars=cand.right_bars,
            ))
        
        else:  # LOW
            if cand.equal_policy == EqualPolicy.STRICT:
                right_ok = np.all(cand.price <= right_lows)
            elif cand.equal_policy == EqualPolicy.EQUAL_RIGHT:
                right_ok = np.all(cand.price <= right_lows)
            else:
                right_ok = np.all(cand.price < right_lows)
            
            is_equal_right = False
            if right_ok and cand.equal_policy in (EqualPolicy.EQUAL_RIGHT, EqualPolicy.EQUAL_LEFT):
                is_equal_right = np.any(cand.price == right_lows)
            
            if not right_ok:
                continue
            
            confirmed.append(Swing(
                id=make_swing_id(
                    cand.symbol, cand.timeframe, "LOW", cand.pivot_time
                ),
                symbol=cand.symbol,
                timeframe=cand.timeframe,
                swing_type=SwingType.LOW,
                pivot_index=cand.pivot_index,
                pivot_time=cand.pivot_time,
                confirmation_time=times[confirmation_idx],
                price=cand.price,
                equal_policy=cand.equal_policy,
                is_equal_left=cand.is_equal_left,
                is_equal_right=is_equal_right,
                left_bars=cand.left_bars,
                right_bars=cand.right_bars,
            ))
    
    return confirmed


def get_confirmed_swings_up_to(
    candles: list[Candle],
    as_of_index: int,
    left_bars: int = 5,
    right_bars: int = 5,
    equal_policy: EqualPolicy = EqualPolicy.STRICT,
) -> list[Swing]:
    """
    Get all confirmed swings up to a specific candle index.
    
    This is the main entry point for historical replay - it ensures
    no future data leaks into swing detection.
    """
    # Find all candidates in the full series (but we'll filter by confirmation)
    all_candidates = find_swing_candidates(
        candles, left_bars, right_bars, equal_policy
    )
    
    # Confirm only those that are confirmed by as_of_index
    return confirm_swings(all_candidates, candles, as_of_index)