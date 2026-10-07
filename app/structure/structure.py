"""
Market structure analysis: HH/HL/LH/LL classification, protected levels, BOS/CHoCH.
"""
from datetime import datetime, timezone
from typing import Optional
from enum import Enum
from app.data.models import Candle, Timeframe, make_event_id
from app.structure.swings import Swing, SwingType, get_confirmed_swings_up_to
from app.config.settings import config
from pydantic import BaseModel, ConfigDict


class StructureLabel(str, Enum):
    """Swing classification relative to previous swing of same type."""
    HH = "HH"  # Higher High
    HL = "HL"  # Higher Low
    LH = "LH"  # Lower High
    LL = "LL"  # Lower Low
    EH = "EH"  # Equal High
    EL = "EL"  # Equal Low
    UNKNOWN = "UNKNOWN"


class StructureDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGE = "range"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    BOS_BULLISH = "BOS_BULLISH"
    BOS_BEARISH = "BOS_BEARISH"
    CHOCH_BULLISH = "CHOCH_BULLISH"
    CHOCH_BEARISH = "CHOCH_BEARISH"
    LIQUIDITY_SWEEP_HIGH = "LIQUIDITY_SWEEP_HIGH"
    LIQUIDITY_SWEEP_LOW = "LIQUIDITY_SWEEP_LOW"
    BREAKOUT_BULLISH = "BREAKOUT_BULLISH"
    BREAKOUT_BEARISH = "BREAKOUT_BEARISH"
    RETEST = "RETEST"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"


class StructuralEvent(BaseModel):
    """Structural event with full traceability."""
    model_config = ConfigDict(frozen=True)
    
    id: str
    type: EventType
    timeframe: Timeframe
    confirmation_candle_id: str
    confirmation_time: datetime
    reference_id: str  # ID of the level that was broken (swing or zone)
    reference_price: float
    break_price: float  # Close price that confirmed the break
    break_buffer: float
    # Direction of the break relative to structure
    is_with_trend: bool  # True = BOS, False = CHoCH


class LabeledSwing(BaseModel):
    """Swing with structural label."""
    model_config = ConfigDict(frozen=True)
    
    swing: Swing
    label: StructureLabel
    # Reference to the previous swing of same type used for comparison
    reference_swing_id: Optional[str] = None


class MarketStructure(BaseModel):
    """Complete market structure for a timeframe."""
    model_config = ConfigDict(frozen=True)
    
    timeframe: Timeframe
    direction: StructureDirection
    external: "ExternalStructure"
    internal: "InternalStructure"
    last_event: Optional[StructuralEvent] = None
    all_events: list[StructuralEvent] = []


class ExternalStructure(BaseModel):
    """External (major) structure - the larger market leg."""
    model_config = ConfigDict(frozen=True)
    
    last_high: Optional[LabeledSwing] = None
    last_low: Optional[LabeledSwing] = None
    protected_high: Optional[LabeledSwing] = None
    protected_low: Optional[LabeledSwing] = None
    direction: StructureDirection = StructureDirection.UNKNOWN


class InternalStructure(BaseModel):
    """Internal (minor) structure - smaller swings within the leg."""
    model_config = ConfigDict(frozen=True)
    
    direction: StructureDirection = StructureDirection.UNKNOWN
    swings: list[LabeledSwing] = []


def classify_swings(swings: list[Swing]) -> list[LabeledSwing]:
    """
    Classify each swing relative to previous swing of same type.
    
    For highs: HH, LH, EH
    For lows: HL, LL, EL
    """
    labeled = []
    last_high: Optional[Swing] = None
    last_low: Optional[Swing] = None
    
    for swing in swings:
        label = StructureLabel.UNKNOWN
        ref_id = None
        
        if swing.swing_type == SwingType.HIGH:
            if last_high is not None:
                ref_id = last_high.id
                if swing.price > last_high.price:
                    label = StructureLabel.HH
                elif swing.price < last_high.price:
                    label = StructureLabel.LH
                else:
                    label = StructureLabel.EH
            last_high = swing
        else:
            if last_low is not None:
                ref_id = last_low.id
                if swing.price > last_low.price:
                    label = StructureLabel.HL
                elif swing.price < last_low.price:
                    label = StructureLabel.LL
                else:
                    label = StructureLabel.EL
            last_low = swing
        
        labeled.append(LabeledSwing(
            swing=swing,
            label=label,
            reference_swing_id=ref_id,
        ))
    
    return labeled


def determine_structure_direction(labeled_swings: list[LabeledSwing]) -> StructureDirection:
    """
    Determine overall structure direction from labeled swings.
    
    Bullish: requires HH + HL sequence
    Bearish: requires LH + LL sequence
    Range: neither clearly bullish nor bearish
    """
    if len(labeled_swings) < 2:
        return StructureDirection.UNKNOWN
    
    # Get recent labels (last 4 swings of each type)
    high_labels = [ls.label for ls in labeled_swings if ls.swing.swing_type == SwingType.HIGH]
    low_labels = [ls.label for ls in labeled_swings if ls.swing.swing_type == SwingType.LOW]
    
    # Check for bullish: need at least one HH and one HL in recent structure
    has_hh = StructureLabel.HH in high_labels[-3:]
    has_hl = StructureLabel.HL in low_labels[-3:]
    
    # Check for bearish: need at least one LH and one LL
    has_lh = StructureLabel.LH in high_labels[-3:]
    has_ll = StructureLabel.LL in low_labels[-3:]
    
    if has_hh and has_hl and not (has_lh or has_ll):
        return StructureDirection.BULLISH
    elif has_lh and has_ll and not (has_hh or has_hl):
        return StructureDirection.BEARISH
    elif (has_hh or has_hl) and (has_lh or has_ll):
        return StructureDirection.MIXED
    else:
        return StructureDirection.RANGE


def identify_protected_levels(
    labeled_swings: list[LabeledSwing],
    direction: StructureDirection,
) -> tuple[Optional[LabeledSwing], Optional[LabeledSwing]]:
    """
    Identify protected high and low based on structure direction.
    
    Bullish: latest HL becomes protected_low
    Bearish: latest LH becomes protected_high
    """
    protected_high = None
    protected_low = None
    
    if direction == StructureDirection.BULLISH:
        # Find latest HL (higher low)
        for ls in reversed(labeled_swings):
            if ls.swing.swing_type == SwingType.LOW and ls.label == StructureLabel.HL:
                protected_low = ls
                break
    elif direction == StructureDirection.BEARISH:
        # Find latest LH (lower high)
        for ls in reversed(labeled_swings):
            if ls.swing.swing_type == SwingType.HIGH and ls.label == StructureLabel.LH:
                protected_high = ls
                break
    elif direction == StructureDirection.MIXED:
        # In mixed, both could be relevant
        for ls in reversed(labeled_swings):
            if protected_high is None and ls.swing.swing_type == SwingType.HIGH and ls.label == StructureLabel.LH:
                protected_high = ls
            if protected_low is None and ls.swing.swing_type == SwingType.LOW and ls.label == StructureLabel.HL:
                protected_low = ls
            if protected_high and protected_low:
                break
    
    return protected_high, protected_low


def calculate_break_buffer(
    atr_value: Optional[float],
    symbol_point: float,
    n_points: int = 0,
    atr_fraction: float = 0.05,
) -> float:
    """Calculate breakout buffer per spec."""
    buffer_atr = atr_value * atr_fraction if atr_value else 0
    buffer_points = n_points * symbol_point
    return max(buffer_points, buffer_atr)


def detect_structural_events(
    candles: list[Candle],
    labeled_swings: list[LabeledSwing],
    protected_high: Optional[LabeledSwing],
    protected_low: Optional[LabeledSwing],
    direction: StructureDirection,
    atr_values: list[Optional[float]],
    symbol_point: float,
    event_counter: int,
) -> tuple[list[StructuralEvent], int]:
    """
    Detect BOS/CHoCH events.
    
    A structural break requires a CLOSED candle beyond the protected level + buffer.
    """
    events = []
    counter = event_counter
    
    # Map swing ID to labeled swing for reference
    swing_by_id = {ls.swing.id: ls for ls in labeled_swings}
    
    for i, candle in enumerate(candles):
        atr = atr_values[i] if i < len(atr_values) else None
        buffer = calculate_break_buffer(
            atr, symbol_point,
            config.breakout.n_points,
            config.breakout.atr_fraction
        )
        
        # Check bullish break (close above protected high)
        if protected_high:
            level_price = protected_high.swing.price
            if candle.close > level_price + buffer:
                is_with_trend = (direction == StructureDirection.BULLISH)
                event_type = EventType.BOS_BULLISH if is_with_trend else EventType.CHOCH_BULLISH
                
                events.append(StructuralEvent(
                    id=make_event_id(candle.symbol, candle.timeframe, event_type.value, counter),
                    type=event_type,
                    timeframe=candle.timeframe,
                    confirmation_candle_id=candle.id,
                    confirmation_time=candle.time_close,
                    reference_id=protected_high.swing.id,
                    reference_price=level_price,
                    break_price=candle.close,
                    break_buffer=buffer,
                    is_with_trend=is_with_trend,
                ))
                counter += 1
        
        # Check bearish break (close below protected low)
        if protected_low:
            level_price = protected_low.swing.price
            if candle.close < level_price - buffer:
                is_with_trend = (direction == StructureDirection.BEARISH)
                event_type = EventType.BOS_BEARISH if is_with_trend else EventType.CHOCH_BEARISH
                
                events.append(StructuralEvent(
                    id=make_event_id(candle.symbol, candle.timeframe, event_type.value, counter),
                    type=event_type,
                    timeframe=candle.timeframe,
                    confirmation_candle_id=candle.id,
                    confirmation_time=candle.time_close,
                    reference_id=protected_low.swing.id,
                    reference_price=level_price,
                    break_price=candle.close,
                    break_buffer=buffer,
                    is_with_trend=is_with_trend,
                ))
                counter += 1
    
    return events, counter


def build_market_structure(
    candles: list[Candle],
    swings: list[Swing],
    atr_values: list[Optional[float]],
    symbol_point: float,
    event_counter: int = 0,
) -> tuple[MarketStructure, int]:
    """
    Build complete market structure for a timeframe.
    
    Returns structure and updated event counter.
    """
    # Classify swings
    labeled = classify_swings(swings)
    
    # Determine direction
    direction = determine_structure_direction(labeled)
    
    # Identify protected levels
    protected_high, protected_low = identify_protected_levels(labeled, direction)
    
    # Build external structure
    high_swings = [ls for ls in labeled if ls.swing.swing_type == SwingType.HIGH]
    low_swings = [ls for ls in labeled if ls.swing.swing_type == SwingType.LOW]
    
    external = ExternalStructure(
        last_high=high_swings[-1] if high_swings else None,
        last_low=low_swings[-1] if low_swings else None,
        protected_high=protected_high,
        protected_low=protected_low,
        direction=direction,
    )
    
    # Internal structure (simplified - uses all swings)
    internal = InternalStructure(
        direction=direction,
        swings=labeled,
    )
    
    # Detect events
    events, new_counter = detect_structural_events(
        candles=candles,
        labeled_swings=labeled,
        protected_high=protected_high,
        protected_low=protected_low,
        direction=direction,
        atr_values=atr_values,
        symbol_point=symbol_point,
        event_counter=event_counter,
    )
    
    last_event = events[-1] if events else None
    
    structure = MarketStructure(
        timeframe=candles[0].timeframe if candles else Timeframe.H1,
        direction=direction,
        external=external,
        internal=internal,
        last_event=last_event,
        all_events=events,
    )
    
    return structure, new_counter