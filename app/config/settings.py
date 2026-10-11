"""
Configuration settings for the MT5 Analysis Engine.
Loads from config.yaml with environment variable overrides.
"""
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field


class EngineConfig(BaseModel):
    version: str = "2.0.0"
    schema_version: str = "2.0"


class TimeframeConfig(BaseModel):
    context: list[str] = ["D1"]
    structure: list[str] = ["H4"]
    decision: str = "H1"
    confirmation: list[str] = []


class SwingConfig(BaseModel):
    left_bars: int = 5
    right_bars: int = 5
    equal_policy: str = "strict"


class EmaConfig(BaseModel):
    periods: list[int] = [20, 50, 200]


class RsiConfig(BaseModel):
    period: int = 14
    overbought: int = 70
    oversold: int = 30


class AtrConfig(BaseModel):
    period: int = 14


class IndicatorConfig(BaseModel):
    ema: EmaConfig = Field(default_factory=EmaConfig)
    rsi: RsiConfig = Field(default_factory=RsiConfig)
    atr: AtrConfig = Field(default_factory=AtrConfig)


class FibonacciConfig(BaseModel):
    ratios: list[float] = [0.382, 0.500, 0.618]
    min_leg_atr_multiple: float = 1.0


class BreakoutConfig(BaseModel):
    n_points: int = 0
    atr_fraction: float = 0.05


class ZoneConfig(BaseModel):
    atr_tolerance_min: float = 0.25
    atr_tolerance_max: float = 0.50


class RoundNumberConfig(BaseModel):
    step_pips: int = 100
    levels_around_price: int = 5


class PatternsConfig(BaseModel):
    # Max difference between "equal" pivots, as an ATR multiple
    equal_tolerance_atr: float = 0.5
    # Minimum pattern depth/height, as an ATR multiple
    min_depth_atr: float = 1.0
    # Slope magnitude (price per bar) below which a trendline counts as flat
    flat_slope_atr: float = 0.05
    # How many recent swings per side to fit trendlines through
    max_swings: int = 8


class SessionDefinition(BaseModel):
    start: str
    end: str


class SessionsConfig(BaseModel):
    timezone: str = "UTC"
    definitions: dict[str, SessionDefinition] = Field(default_factory=dict)


class CSVImportConfig(BaseModel):
    broker_offset_hours: float = 2.0
    drop_last_row: bool = True
    columns: dict[str, str] = Field(default_factory=lambda: {
        "time": "time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "tick_volume": "tick_volume",
        "spread": "spread",
        "real_volume": "real_volume",
    })


class ReplayConfig(BaseModel):
    recent_candles_per_timeframe: int = 30
    calculation_history_bars: int = 1000


class OandaConfig(BaseModel):
    environment: str = "practice"
    price: str = "M"
    default_bars: int = 5000
    max_bars_per_request: int = 5000
    request_timeout: float = 30.0


class Mt5Config(BaseModel):
    # Path to terminal64.exe; None = auto-detect common install locations
    terminal_path: Optional[str] = None
    default_bars: int = 5000
    # Broker server offset from UTC. None = auto-detect from a live tick.
    broker_offset_hours: Optional[float] = None
    # Broker symbol suffix, e.g. ".a" for "EURUSD.a"
    symbol_suffix: str = ""


class SymbolConfig(BaseModel):
    digits: int = 5
    point: float = 0.00001


class Config(BaseModel):
    engine: EngineConfig = Field(default_factory=EngineConfig)
    style: str = "swing"
    # Data source: "csv" (MT5 exports) or "oanda" (live broker API)
    data_provider: str = "csv"
    timeframes: TimeframeConfig = Field(default_factory=TimeframeConfig)
    swing: SwingConfig = Field(default_factory=SwingConfig)
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    fibonacci: FibonacciConfig = Field(default_factory=FibonacciConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
    zones: ZoneConfig = Field(default_factory=ZoneConfig)
    round_numbers: RoundNumberConfig = Field(default_factory=RoundNumberConfig)
    patterns: PatternsConfig = Field(default_factory=PatternsConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    csv_import: CSVImportConfig = Field(default_factory=CSVImportConfig)
    oanda: OandaConfig = Field(default_factory=OandaConfig)
    mt5: Mt5Config = Field(default_factory=Mt5Config)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    symbol: SymbolConfig = Field(default_factory=SymbolConfig)


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from YAML file."""
    if path is None:
        # Look for config.yaml in project root (parent of app/)
        path = Path(__file__).parent.parent.parent / "config.yaml"
    
    with open(path) as f:
        data = yaml.safe_load(f)
    
    return Config(**data)


# Global config instance
config = load_config()