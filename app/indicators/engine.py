"""
Indicator calculations: EMA, RSI, ATR.
Deterministic implementations with proper warmup handling.
"""
from typing import Optional
import numpy as np
from app.data.models import Candle


def calculate_ema(values: np.ndarray, period: int) -> np.ndarray:
    """
    Calculate Exponential Moving Average.
    
    Uses the standard EMA formula:
    EMA_today = (Value_today * multiplier) + (EMA_yesterday * (1 - multiplier))
    where multiplier = 2 / (period + 1)
    
    First value is SMA of first `period` values.
    Returns array with NaN for insufficient data.
    """
    if len(values) < period:
        return np.full(len(values), np.nan)
    
    multiplier = 2.0 / (period + 1)
    ema = np.full(len(values), np.nan)
    
    # First EMA is SMA
    ema[period - 1] = np.mean(values[:period])
    
    # Calculate rest
    for i in range(period, len(values)):
        ema[i] = (values[i] * multiplier) + (ema[i - 1] * (1 - multiplier))
    
    return ema


def calculate_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Calculate RSI using Wilder's smoothing method.
    
    Returns array with NaN for insufficient data.
    """
    if len(closes) < period + 1:
        return np.full(len(closes), np.nan)
    
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    
    # First average gain/loss is simple average
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    rsi = np.full(len(closes), np.nan)
    
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - (100.0 / (1.0 + rs))
    
    # Wilder's smoothing
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    
    return rsi


def calculate_atr(candles: list[Candle], period: int = 14) -> np.ndarray:
    """
    Calculate Average True Range using Wilder's smoothing.
    
    True Range = max(high - low, |high - prev_close|, |low - prev_close|)
    
    Returns array with NaN for insufficient data.
    """
    if len(candles) < period + 1:
        return np.full(len(candles), np.nan)
    
    true_ranges = np.zeros(len(candles))
    
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges[i] = tr
    
    atr = np.full(len(candles), np.nan)
    
    # First ATR is simple average
    atr[period] = np.mean(true_ranges[1:period + 1])
    
    # Wilder's smoothing
    for i in range(period + 1, len(candles)):
        atr[i] = (atr[i - 1] * (period - 1) + true_ranges[i]) / period
    
    return atr


class IndicatorResult:
    """Container for indicator values at a specific point."""
    
    def __init__(
        self,
        ema_values: dict[int, float],
        rsi_value: Optional[float],
        atr_value: Optional[float],
    ):
        self.ema = ema_values
        self.rsi = rsi_value
        self.atr = atr_value
    
    def to_dict(self) -> dict:
        return {
            "ema": self.ema,
            "rsi": self.rsi,
            "atr": self.atr,
        }


def compute_indicators_for_candles(
    candles: list[Candle],
    ema_periods: list[int],
    rsi_period: int,
    atr_period: int,
) -> dict[str, np.ndarray]:
    """
    Compute all indicators for a series of candles.
    
    Returns dict with arrays aligned to candle indices.
    """
    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    
    result = {}
    
    # EMA for each period
    for period in ema_periods:
        result[f"ema_{period}"] = calculate_ema(closes, period)
    
    # RSI
    result["rsi"] = calculate_rsi(closes, rsi_period)
    
    # ATR
    result["atr"] = calculate_atr(candles, atr_period)
    
    return result


def get_indicator_state(
    indicators: dict[str, np.ndarray],
    idx: int,
    ema_periods: list[int],
) -> dict:
    """Get indicator values at a specific index."""
    state = {}
    
    for period in ema_periods:
        key = f"ema_{period}"
        val = indicators[key][idx] if idx < len(indicators[key]) else np.nan
        state[key] = float(val) if not np.isnan(val) else None
    
    rsi_val = indicators["rsi"][idx] if idx < len(indicators["rsi"]) else np.nan
    state["rsi"] = float(rsi_val) if not np.isnan(rsi_val) else None
    
    atr_val = indicators["atr"][idx] if idx < len(indicators["atr"]) else np.nan
    state["atr"] = float(atr_val) if not np.isnan(atr_val) else None
    
    return state


def ema_relationships(price: float, ema_values: dict[int, float]) -> dict[str, str]:
    """Determine price vs EMA relationships."""
    rel = {}
    for period, ema in ema_values.items():
        if ema is None:
            rel[f"price_vs_ema{period}"] = "unknown"
        elif price > ema:
            rel[f"price_vs_ema{period}"] = "above"
        elif price < ema:
            rel[f"price_vs_ema{period}"] = "below"
        else:
            rel[f"price_vs_ema{period}"] = "equal"
    return rel


def ema_directions(ema_values: dict[int, float], lookback: int = 3) -> dict[str, str]:
    """
    Determine EMA direction (up/down/flat) based on recent slope.
    Note: This requires historical EMA values, not just current.
    For now, returns 'unknown' - direction should be computed from EMA series.
    """
    return {f"ema{period}": "unknown" for period in ema_values}