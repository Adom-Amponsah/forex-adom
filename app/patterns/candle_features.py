"""
Deterministic candlestick pattern features.
"""
from typing import Optional
from app.data.models import Candle
from pydantic import BaseModel, ConfigDict


class CandleFeatures(BaseModel):
    """Detected candlestick features for a single candle."""
    model_config = ConfigDict(frozen=True)
    
    candle_id: str
    # Pattern flags
    is_doji: bool = False
    is_bullish_engulfing: bool = False
    is_bearish_engulfing: bool = False
    is_hammer: bool = False
    is_shooting_star: bool = False
    is_inside_bar: bool = False
    is_strong_bullish_close: bool = False
    is_strong_bearish_close: bool = False
    is_pin_bar_bullish: bool = False
    is_pin_bar_bearish: bool = False
    
    # Ratios for context
    body_ratio: float = 0.0
    close_location: float = 0.5
    upper_wick_ratio: float = 0.0
    lower_wick_ratio: float = 0.0


# Configuration for pattern thresholds
DOJI_RATIO = 0.1  # body/range <= 0.1
ENGULFING_MIN_BODY_RATIO = 0.5
HAMMER_WICK_RATIO = 2.0  # lower_wick / body >= 2
SHOOTING_STAR_WICK_RATIO = 2.0  # upper_wick / body >= 2
STRONG_CLOSE_LOCATION = 0.75  # close in top/bottom 25% of range
INSIDE_BAR_TOLERANCE = 0.0  # exact inside


def detect_candle_features(
    current: Candle,
    previous: Optional[Candle] = None,
    doji_ratio: float = DOJI_RATIO,
    engulfing_min_body: float = ENGULFING_MIN_BODY_RATIO,
    hammer_wick_ratio: float = HAMMER_WICK_RATIO,
    shooting_star_wick_ratio: float = SHOOTING_STAR_WICK_RATIO,
    strong_close_loc: float = STRONG_CLOSE_LOCATION,
) -> CandleFeatures:
    """Detect all candlestick features for a single candle."""
    
    # Compute all flags first
    is_doji = False
    is_bullish_engulfing = False
    is_bearish_engulfing = False
    is_hammer = False
    is_shooting_star = False
    is_inside_bar = False
    is_strong_bullish_close = False
    is_strong_bearish_close = False
    is_pin_bar_bullish = False
    is_pin_bar_bearish = False
    
    # Doji
    if current.range_size > 0 and current.body_ratio <= doji_ratio:
        is_doji = True
    
    # Strong closes
    if current.is_bullish and current.close_location >= strong_close_loc:
        is_strong_bullish_close = True
    elif current.is_bearish and current.close_location <= (1 - strong_close_loc):
        is_strong_bearish_close = True
    
    # Pin bars (hammer/shooting star)
    if current.range_size > 0 and current.body_ratio > 0:
        if current.lower_wick_ratio >= hammer_wick_ratio * current.body_ratio:
            if current.is_bullish or current.close_location > 0.5:
                is_hammer = True
                is_pin_bar_bullish = True
        if current.upper_wick_ratio >= shooting_star_wick_ratio * current.body_ratio:
            if current.is_bearish or current.close_location < 0.5:
                is_shooting_star = True
                is_pin_bar_bearish = True
    
    # Engulfing (needs previous candle)
    if previous and current.range_size > 0 and previous.range_size > 0:
        # Bullish engulfing: prev bearish, current bullish, current body engulfs prev body
        if previous.is_bearish and current.is_bullish:
            if (current.open <= previous.close and current.close >= previous.open and
                current.body_size >= previous.body_size * engulfing_min_body):
                is_bullish_engulfing = True
        
        # Bearish engulfing: prev bullish, current bearish, current body engulfs prev body
        if previous.is_bullish and current.is_bearish:
            if (current.open >= previous.close and current.close <= previous.open and
                current.body_size >= previous.body_size * engulfing_min_body):
                is_bearish_engulfing = True
    
    # Inside bar
    if previous:
        if (current.high <= previous.high + INSIDE_BAR_TOLERANCE and
            current.low >= previous.low - INSIDE_BAR_TOLERANCE):
            is_inside_bar = True
    
    return CandleFeatures(
        candle_id=current.id,
        body_ratio=current.body_ratio,
        close_location=current.close_location,
        upper_wick_ratio=current.upper_wick_ratio,
        lower_wick_ratio=current.lower_wick_ratio,
        is_doji=is_doji,
        is_bullish_engulfing=is_bullish_engulfing,
        is_bearish_engulfing=is_bearish_engulfing,
        is_hammer=is_hammer,
        is_shooting_star=is_shooting_star,
        is_inside_bar=is_inside_bar,
        is_strong_bullish_close=is_strong_bullish_close,
        is_strong_bearish_close=is_strong_bearish_close,
        is_pin_bar_bullish=is_pin_bar_bullish,
        is_pin_bar_bearish=is_pin_bar_bearish,
    )


def detect_features_for_series(candles: list[Candle]) -> list[CandleFeatures]:
    """Detect features for a series of candles."""
    features = []
    for i, candle in enumerate(candles):
        prev = candles[i - 1] if i > 0 else None
        features.append(detect_candle_features(candle, prev))
    return features