"""
Timeframe alignment for historical replay.
Ensures causal consistency: at decision time T, only candles closed by T are used.
"""
from datetime import datetime, timezone
from typing import Optional
from app.data.models import Candle, Timeframe


def get_latest_closed_candle(
    candles: list[Candle],
    as_of: datetime,
) -> Optional[Candle]:
    """
    Get the latest candle that closed at or before `as_of`.
    
    This is the core function for causal historical replay.
    """
    # Candles must be sorted by time_close
    for candle in reversed(candles):
        if candle.time_close <= as_of:
            return candle
    return None


def get_candles_up_to(
    candles: list[Candle],
    as_of: datetime,
    max_count: Optional[int] = None,
) -> list[Candle]:
    """
    Get all candles that closed at or before `as_of`, optionally limited to max_count most recent.
    """
    result = [c for c in candles if c.time_close <= as_of]
    if max_count and len(result) > max_count:
        result = result[-max_count:]
    return result


def get_candles_for_timeframe_at(
    all_candles: dict[Timeframe, list[Candle]],
    timeframe: Timeframe,
    as_of: datetime,
    max_count: Optional[int] = None,
) -> list[Candle]:
    """Get candles for a specific timeframe up to `as_of`."""
    candles = all_candles.get(timeframe, [])
    return get_candles_up_to(candles, as_of, max_count)


def align_timeframes(
    all_candles: dict[Timeframe, list[Candle]],
    decision_timeframe: Timeframe,
    decision_candle: Candle,
    context_timeframes: list[Timeframe],
    structure_timeframes: list[Timeframe],
    calculation_history_bars: int = 1000,
) -> dict[Timeframe, list[Candle]]:
    """
    Align all timeframes to the decision candle's close time.
    
    This ensures causal consistency - higher timeframe candles used must
    have closed at or before the decision time.
    
    Args:
        all_candles: Dict of timeframe -> all available candles (sorted)
        decision_timeframe: The decision timeframe (e.g., H1)
        decision_candle: The specific decision candle that just closed
        context_timeframes: Higher timeframes for context (e.g., [D1])
        structure_timeframes: Intermediate timeframes (e.g., [H4])
        calculation_history_bars: How many bars to include for indicator calculation
    
    Returns:
        Dict of timeframe -> aligned candles (for calculation)
    """
    as_of = decision_candle.time_close
    aligned = {}
    
    # Decision timeframe: include the decision candle and history
    aligned[decision_timeframe] = get_candles_up_to(
        all_candles[decision_timeframe],
        as_of,
        max_count=calculation_history_bars,
    )
    
    # Structure timeframes: include latest closed candles
    for tf in structure_timeframes:
        aligned[tf] = get_candles_up_to(
            all_candles.get(tf, []),
            as_of,
            max_count=calculation_history_bars,
        )
    
    # Context timeframes: include latest closed candles
    for tf in context_timeframes:
        aligned[tf] = get_candles_up_to(
            all_candles.get(tf, []),
            as_of,
            max_count=calculation_history_bars,
        )
    
    return aligned


def get_decision_candles(
    candles: list[Candle],
    start_idx: int = 0,
) -> list[Candle]:
    """
    Get all candles that could serve as decision candles (closed candles).
    
    For historical replay, each closed candle in the decision timeframe
    can be an analysis point.
    """
    return candles[start_idx:]


class HistoricalReplay:
    """Manages historical replay across timeframes."""
    
    def __init__(
        self,
        all_candles: dict[Timeframe, list[Candle]],
        decision_timeframe: Timeframe,
        context_timeframes: list[Timeframe],
        structure_timeframes: list[Timeframe],
        calculation_history_bars: int = 1000,
    ):
        self.all_candles = all_candles
        self.decision_timeframe = decision_timeframe
        self.context_timeframes = context_timeframes
        self.structure_timeframes = structure_timeframes
        self.calculation_history_bars = calculation_history_bars
        
        # Decision candles are all closed candles in decision timeframe
        self.decision_candles = all_candles[decision_timeframe]
    
    def get_snapshot_at(self, decision_candle: Candle) -> dict[Timeframe, list[Candle]]:
        """Get aligned candles for a specific decision candle."""
        return align_timeframes(
            all_candles=self.all_candles,
            decision_timeframe=self.decision_timeframe,
            decision_candle=decision_candle,
            context_timeframes=self.context_timeframes,
            structure_timeframes=self.structure_timeframes,
            calculation_history_bars=self.calculation_history_bars,
        )
    
    def iterate_snapshots(self, start_idx: int = 0):
        """Iterate over all valid decision points."""
        for i in range(start_idx, len(self.decision_candles)):
            decision_candle = self.decision_candles[i]
            aligned = self.get_snapshot_at(decision_candle)
            yield decision_candle, aligned