"""
Data quality checks for MT5 candle data.
"""
from app.data.models import Candle, Timeframe, DataQualityReport


def check_candle_sequence(
    candles: list[Candle],
    timeframe: Timeframe,
    max_gap_multiplier: float = 2.0,
) -> DataQualityReport:
    """
    Check candle sequence for gaps, duplicates, and ordering issues.
    """
    report = DataQualityReport()
    report.total_bars = len(candles)
    
    if not candles:
        return report
    
    report.date_range = (candles[0].time_close, candles[-1].time_close)
    
    expected_seconds = timeframe.to_seconds()
    max_gap_seconds = expected_seconds * max_gap_multiplier
    
    seen_times = set()
    prev_time = None
    
    for i, candle in enumerate(candles):
        # Check duplicates
        if candle.time_close in seen_times:
            report.duplicate_bars.append({
                "index": i,
                "time": candle.time_close.isoformat(),
            })
            report.complete = False
        seen_times.add(candle.time_close)
        
        # Check ordering
        if prev_time is not None and candle.time_close < prev_time:
            report.out_of_order_bars.append({
                "index": i,
                "time": candle.time_close.isoformat(),
                "previous_time": prev_time.isoformat(),
            })
            report.complete = False
        
        # Check gaps
        if prev_time is not None:
            gap = (candle.time_close - prev_time).total_seconds()
            if gap > max_gap_seconds:
                missing_count = int(gap / expected_seconds) - 1
                report.missing_bars.append({
                    "after_time": prev_time.isoformat(),
                    "before_time": candle.time_close.isoformat(),
                    "gap_seconds": gap,
                    "estimated_missing_bars": missing_count,
                })
                report.complete = False
        
        prev_time = candle.time_close
    
    return report


def validate_ohlc(candles: list[Candle]) -> list[dict]:
    """Validate OHLC relationships."""
    errors = []
    for i, c in enumerate(candles):
        if c.high < max(c.open, c.close):
            errors.append({"index": i, "time": c.time_close.isoformat(), "error": "high < max(open, close)"})
        if c.low > min(c.open, c.close):
            errors.append({"index": i, "time": c.time_close.isoformat(), "error": "low > min(open, close)"})
        if c.high < c.low:
            errors.append({"index": i, "time": c.time_close.isoformat(), "error": "high < low"})
    return errors


def check_indicator_warmup(
    candles: list[Candle],
    required_bars: int,
) -> bool:
    """Check if enough bars for indicator warmup."""
    return len(candles) >= required_bars