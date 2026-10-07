"""
Fibonacci retracement engine with deterministic anchoring.
"""
from datetime import datetime, timezone
from typing import Optional
from enum import Enum
from app.data.models import Candle, Timeframe
from app.structure.swings import Swing, SwingType
from app.structure.structure import LabeledSwing, StructureLabel
from app.config.settings import config
from app.indicators.engine import calculate_atr
from pydantic import BaseModel, ConfigDict


class FibDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class FibLevel(BaseModel):
    """Single Fibonacci level."""
    model_config = ConfigDict(frozen=True)
    
    ratio: float
    price: float


class FibonacciRetracement(BaseModel):
    """Fibonacci retracement object."""
    model_config = ConfigDict(frozen=True)
    
    direction: FibDirection
    anchor_low_id: str
    anchor_high_id: str
    anchor_low_price: float
    anchor_high_price: float
    anchor_low_time: datetime
    anchor_high_time: datetime
    levels: list[FibLevel]


def find_fib_anchor_leg(
    labeled_swings: list[LabeledSwing],
    atr_value: float,
    min_leg_atr_multiple: float = 1.0,
) -> Optional[tuple[LabeledSwing, LabeledSwing, FibDirection]]:
    """
    Find the latest structurally valid swing pair for Fibonacci anchoring.
    
    For bullish structure: latest confirmed significant swing low -> subsequent confirmed significant swing high
    For bearish structure: latest confirmed significant swing high -> subsequent confirmed significant swing low
    
    A candidate leg should satisfy:
    - Alternating swing types
    - Direction consistency
    - Minimum move size relative to ATR
    """
    if len(labeled_swings) < 2:
        return None
    
    min_move = atr_value * min_leg_atr_multiple
    
    # Look for valid legs from most recent backwards
    for i in range(len(labeled_swings) - 1, 0, -1):
        swing_a = labeled_swings[i - 1]
        swing_b = labeled_swings[i]
        
        # Must be alternating types
        if swing_a.swing.swing_type == swing_b.swing.swing_type:
            continue
        
        move_size = abs(swing_b.swing.price - swing_a.swing.price)
        if move_size < min_move:
            continue
        
        # Determine direction
        if swing_a.swing.swing_type == SwingType.LOW and swing_b.swing.swing_type == SwingType.HIGH:
            # Low to high = bullish leg
            # Check if this aligns with bullish structure context
            direction = FibDirection.BULLISH
        elif swing_a.swing.swing_type == SwingType.HIGH and swing_b.swing.swing_type == SwingType.LOW:
            # High to low = bearish leg
            direction = FibDirection.BEARISH
        else:
            continue
        
        return swing_a, swing_b, direction
    
    return None


def calculate_fibonacci_levels(
    anchor_low: float,
    anchor_high: float,
    direction: FibDirection,
    ratios: list[float],
) -> list[FibLevel]:
    """Calculate Fibonacci retracement levels."""
    levels = []
    
    if direction == FibDirection.BULLISH:
        # Retracements from high back toward low
        for ratio in ratios:
            price = anchor_high - (anchor_high - anchor_low) * ratio
            levels.append(FibLevel(ratio=ratio, price=price))
    else:
        # Retracements from low back toward high
        for ratio in ratios:
            price = anchor_low + (anchor_high - anchor_low) * ratio
            levels.append(FibLevel(ratio=ratio, price=price))
    
    return levels


def build_fibonacci(
    labeled_swings: list[LabeledSwing],
    atr_values: list[Optional[float]],
    ratios: list[float] = None,
    min_leg_atr_multiple: float = 1.0,
) -> Optional[FibonacciRetracement]:
    """Build Fibonacci retracement for a timeframe."""
    if ratios is None:
        ratios = config.fibonacci.ratios
    
    # Get latest ATR
    atr = next((v for v in reversed(atr_values) if v is not None), None)
    if atr is None:
        return None
    
    leg = find_fib_anchor_leg(labeled_swings, atr, min_leg_atr_multiple)
    if leg is None:
        return None
    
    swing_a, swing_b, direction = leg
    
    # Determine anchor low/high based on direction
    if direction == FibDirection.BULLISH:
        anchor_low_swing = swing_a
        anchor_high_swing = swing_b
    else:
        anchor_low_swing = swing_b
        anchor_high_swing = swing_a
    
    levels = calculate_fibonacci_levels(
        anchor_low_swing.swing.price,
        anchor_high_swing.swing.price,
        direction,
        ratios,
    )
    
    return FibonacciRetracement(
        direction=direction,
        anchor_low_id=anchor_low_swing.swing.id,
        anchor_high_id=anchor_high_swing.swing.id,
        anchor_low_price=anchor_low_swing.swing.price,
        anchor_high_price=anchor_high_swing.swing.price,
        anchor_low_time=anchor_low_swing.swing.pivot_time,
        anchor_high_time=anchor_high_swing.swing.pivot_time,
        levels=levels,
    )