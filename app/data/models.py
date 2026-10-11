"""
Core data models for the MT5 Analysis Engine.
Implements the MT5 Data Contract from the V2 specification.
"""
from datetime import datetime, timedelta
from enum import Enum
from pydantic import BaseModel, ConfigDict


class Timeframe(str, Enum):
    """Supported timeframes."""
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    def to_seconds(self) -> int:
        """Convert timeframe to seconds."""
        mapping = {
            "M1": 60,
            "M5": 300,
            "M15": 900,
            "M30": 1800,
            "H1": 3600,
            "H4": 14400,
            "D1": 86400,
            "W1": 604800,
            "MN1": 2592000,
        }
        return mapping[self.value]

    def to_pandas_freq(self) -> str:
        """Convert to pandas frequency string."""
        mapping = {
            "M1": "1min",
            "M5": "5min",
            "M15": "15min",
            "M30": "30min",
            "H1": "1h",
            "H4": "4h",
            "D1": "1D",
            "W1": "1W",
            "MN1": "1M",
        }
        return mapping[self.value]


class VolumeType(str, Enum):
    TICK_VOLUME = "tick_volume"
    REAL_VOLUME = "real_volume"


class SymbolMetadata(BaseModel):
    """Symbol metadata from MT5."""
    model_config = ConfigDict(frozen=True)
    
    symbol: str
    digits: int
    point: float
    volume_type: VolumeType = VolumeType.TICK_VOLUME
    data_provider: str = "MT5"
    broker: str = "unknown"
    # Session timezone for this symbol (e.g., "EET", "EST", "UTC")
    session_timezone: str = "UTC"


class Candle(BaseModel):
    """Normalized candle model with explicit UTC timestamps."""
    model_config = ConfigDict(frozen=True)
    
    # Unique deterministic ID
    id: str
    # Symbol
    symbol: str
    # Timeframe
    timeframe: Timeframe
    # Open time (UTC)
    time_open: datetime
    # Close time (UTC) - this is the authoritative timestamp
    time_close: datetime
    # OHLC prices
    open: float
    high: float
    low: float
    close: float
    # Volume
    tick_volume: int = 0
    spread_points: int = 0
    real_volume: int = 0
    # Source metadata
    source: str = "MT5"
    
    @property
    def is_bullish(self) -> bool:
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        return self.close < self.open
    
    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)
    
    @property
    def range_size(self) -> float:
        return self.high - self.low
    
    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)
    
    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low
    
    @property
    def body_ratio(self) -> float:
        """Body size as fraction of total range."""
        if self.range_size == 0:
            return 0.0
        return self.body_size / self.range_size
    
    @property
    def close_location(self) -> float:
        """Where the close is within the range (0 = low, 1 = high)."""
        if self.range_size == 0:
            return 0.5
        return (self.close - self.low) / self.range_size

    @property
    def upper_wick_ratio(self) -> float:
        """Upper wick as fraction of total range."""
        if self.range_size == 0:
            return 0.0
        return self.upper_wick / self.range_size

    @property
    def lower_wick_ratio(self) -> float:
        """Lower wick as fraction of total range."""
        if self.range_size == 0:
            return 0.0
        return self.lower_wick / self.range_size


def make_candle_id(symbol: str, timeframe: Timeframe, time_close: datetime) -> str:
    """Create deterministic candle ID."""
    ts = time_close.strftime("%Y%m%dT%H%M%SZ")
    return f"{timeframe.value}_CANDLE_{symbol}_{ts}"


def make_candle(
    symbol: str,
    timeframe: Timeframe,
    bar_open: datetime,
    open: float,
    high: float,
    low: float,
    close: float,
    tick_volume: int = 0,
    spread_points: int = 0,
    real_volume: int = 0,
    source: str = "MT5",
) -> Candle:
    """
    Build a Candle from a bar's OPEN time.

    Providers (MT5 exports, OANDA) timestamp bars by their open time. The close
    time - the authoritative "this bar is known" timestamp used by all causal
    logic - is derived as open + timeframe. Never pass a close time here.
    """
    time_close = bar_open + timedelta(seconds=timeframe.to_seconds())
    return Candle(
        id=make_candle_id(symbol, timeframe, time_close),
        symbol=symbol,
        timeframe=timeframe,
        time_open=bar_open,
        time_close=time_close,
        open=open,
        high=high,
        low=low,
        close=close,
        tick_volume=tick_volume,
        spread_points=spread_points,
        real_volume=real_volume,
        source=source,
    )


def make_swing_id(symbol: str, timeframe: Timeframe, swing_type: str, pivot_time: datetime, version: str = "v1") -> str:
    """Create deterministic swing ID."""
    ts = pivot_time.strftime("%Y%m%dT%H%M%SZ")
    return f"{timeframe.value}_SWING_{swing_type.upper()}_{symbol}_{ts}_{version}"


def make_zone_id(symbol: str, timeframe: Timeframe, role: str, seq: int, version: str = "v1") -> str:
    """Create deterministic zone ID."""
    return f"{timeframe.value}_ZONE_{role.upper()}_{symbol}_{seq:04d}_{version}"


def make_event_id(symbol: str, timeframe: Timeframe, event_type: str, seq: int, version: str = "v1") -> str:
    """Create deterministic structural event ID."""
    return f"{timeframe.value}_EVENT_{event_type}_{symbol}_{seq:04d}_{version}"


def make_pattern_feature_id(symbol: str, timeframe: Timeframe, pattern_type: str, seq: int, version: str = "v1") -> str:
    """Create deterministic pattern feature ID."""
    return f"{timeframe.value}_PATTERN_{pattern_type.upper()}_{symbol}_{seq:04d}_{version}"


# Market state version info
class VersionInfo(BaseModel):
    """Version information for reproducibility."""
    market_state_schema_version: str
    analysis_engine_version: str
    pattern_engine_version: str
    prompt_version: str = "none"
    model_name: str = "none"
    model_version: str = "none"


class DataSourceInfo(BaseModel):
    """Data source metadata."""
    provider: str
    volume_type: str
    feed: str = "MT5"
    broker: str = "unknown"
    # Broker server time offset from UTC (hours)
    broker_utc_offset_hours: float = 0.0
    # Whether timestamps were verified against broker weekly open
    weekly_open_verified: bool = False


class DataQualityReport(BaseModel):
    """Data quality metadata for loaded candles."""
    complete: bool = True
    missing_bars: list[dict] = []
    duplicate_bars: list[dict] = []
    out_of_order_bars: list[dict] = []
    source: str = "MT5_CSV"
    total_bars: int = 0
    date_range: tuple[datetime, datetime] | None = None