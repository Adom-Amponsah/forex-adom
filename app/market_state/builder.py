"""
Canonical Market State - the deterministic facts-only output.
This is the core data structure that feeds into the LLM.
"""
from datetime import datetime, timezone
from typing import Optional
from app.data.models import Candle, Timeframe, SymbolMetadata, DataSourceInfo
from app.structure.structure import MarketStructure, ExternalStructure, InternalStructure, LabeledSwing, StructuralEvent
from app.zones.engine import Zone
from app.indicators.fibonacci import FibonacciRetracement
from app.indicators.levels import PreviousLevels, RoundNumberLevels
from app.indicators.sessions import SessionState
from app.patterns.candle_features import CandleFeatures
from app.config.settings import config
from pydantic import BaseModel, ConfigDict, Field


class TimeframeState(BaseModel):
    """Per-timeframe market state."""
    model_config = ConfigDict(frozen=True)
    
    latest_closed_candle: Optional[Candle] = None
    structure: Optional[MarketStructure] = None
    zones: list[Zone] = []
    ema: dict = {}
    rsi: Optional[float] = None
    atr: Optional[float] = None
    fib: Optional[FibonacciRetracement] = None
    candles: list[Candle] = []  # Recent candles for LLM context
    features: list[CandleFeatures] = []
    events: list[StructuralEvent] = []


class MarketState(BaseModel):
    """Canonical market state - facts only."""
    model_config = ConfigDict(frozen=True)
    
    # Version info
    schema_version: str
    engine_version: str
    analysis_engine_version: str = "2.0.0"
    pattern_engine_version: str = "none"
    prompt_version: str = "none"
    model_name: str = "none"
    model_version: str = "none"
    
    # Analysis identity
    symbol: str
    style: str
    decision_timeframe: str
    decision_candle_close_time: datetime
    as_of: datetime  # Same as decision_candle_close_time
    
    # Data source
    data_source: DataSourceInfo
    
    # Market overview
    price: float
    session: SessionState
    previous_day: PreviousLevels
    previous_week: PreviousLevels
    round_numbers: RoundNumberLevels
    
    # Per-timeframe detail
    timeframes: dict[str, TimeframeState] = {}
    
    # Pattern features (deterministic geometry)
    pattern_features: list = []
    
    # Structural events across all timeframes
    structural_events: list[StructuralEvent] = []
    
    # Data quality
    data_quality: DataSourceInfo = Field(default_factory=DataSourceInfo)


class MarketStateBuilder:
    """Builds canonical market state from aligned candle data."""
    
    def __init__(self, config_obj=None):
        self.config = config_obj or config
    
    def build(
        self,
        symbol: str,
        symbol_metadata: SymbolMetadata,
        decision_timeframe: Timeframe,
        decision_candle: Candle,
        aligned_candles: dict[Timeframe, list[Candle]],
        data_source: DataSourceInfo,
    ) -> MarketState:
        """
        Build complete market state for a decision point.
        
        Args:
            symbol: Trading symbol
            symbol_metadata: Symbol metadata (digits, point, etc.)
            decision_timeframe: The decision timeframe (e.g., H1)
            decision_candle: The specific decision candle that just closed
            aligned_candles: Dict of timeframe -> candles aligned to decision time
            data_source: Data source metadata
        
        Returns:
            Complete MarketState object
        """
        as_of = decision_candle.time_close
        
        # Build per-timeframe states
        timeframe_states = {}
        all_events = []
        event_counter = 0
        
        # Timeframes to process
        all_tfs = set(aligned_candles.keys())
        all_tfs.add(decision_timeframe)
        
        for tf in all_tfs:
            tf_candles = aligned_candles.get(tf, [])
            if not tf_candles:
                continue
            
            # Compute indicators
            from app.indicators.engine import compute_indicators_for_candles, get_indicator_state, ema_relationships
            indicators = compute_indicators_for_candles(
                tf_candles,
                self.config.indicators.ema.periods,
                self.config.indicators.rsi.period,
                self.config.indicators.atr.period,
            )
            
            # Get latest indicator values
            latest_idx = len(tf_candles) - 1
            indicator_state = get_indicator_state(indicators, latest_idx, self.config.indicators.ema.periods)
            
            ema_values = {k: v for k, v in indicator_state.items() if k.startswith("ema_")}
            ema_rel = ema_relationships(tf_candles[-1].close, ema_values)
            
            # Detect swings
            from app.structure.swings import get_confirmed_swings_up_to
            swings = get_confirmed_swings_up_to(
                tf_candles,
                as_of_index=latest_idx,
                left_bars=self.config.swing.left_bars,
                right_bars=self.config.swing.right_bars,
                equal_policy=self.config.swing.equal_policy,
            )
            
            # Build market structure
            atr_values = indicators["atr"]
            atr_list = [float(v) if v is not None and not (isinstance(v, float) and v != v) else None for v in atr_values]
            
            from app.structure.structure import build_market_structure
            structure, event_counter = build_market_structure(
                candles=tf_candles,
                swings=swings,
                atr_values=atr_list,
                symbol_point=symbol_metadata.point,
                event_counter=event_counter,
            )
            
            # Collect events
            if structure.all_events:
                all_events.extend(structure.all_events)
            
            # Get labeled swings for zones/fib
            from app.structure.structure import classify_swings
            labeled_swings = classify_swings(swings)
            
            # Previous day/week levels (only for D1 or from D1 data)
            from app.indicators.levels import calculate_previous_levels
            d1_candles = aligned_candles.get(Timeframe.D1, [])
            prev_levels = calculate_previous_levels(d1_candles, data_source.broker_utc_offset_hours)
            
            # Build zones
            from app.zones.engine import build_zones
            from app.indicators.levels import calculate_round_numbers
            
            round_nums = calculate_round_numbers(
                tf_candles[-1].close, symbol_metadata,
                self.config.round_numbers.step_pips,
                self.config.round_numbers.levels_around_price,
            )
            
            zones = build_zones(
                labeled_swings=labeled_swings,
                candles=tf_candles,
                atr_values=atr_list,
                timeframe=tf,
                symbol=symbol,
                prev_day_high=prev_levels.previous_day_high,
                prev_day_low=prev_levels.previous_day_low,
                prev_week_high=prev_levels.previous_week_high,
                prev_week_low=prev_levels.previous_week_low,
                round_numbers=round_nums.levels,
            )
            
            # Fibonacci
            from app.indicators.fibonacci import build_fibonacci
            fib = build_fibonacci(
                labeled_swings=labeled_swings,
                atr_values=atr_list,
                ratios=self.config.fibonacci.ratios,
                min_leg_atr_multiple=self.config.fibonacci.min_leg_atr_multiple,
            )
            
            # Candlestick features
            from app.patterns.candle_features import detect_features_for_series
            features = detect_features_for_series(tf_candles)
            
            # Recent candles for LLM context (last N)
            recent_count = self.config.replay.recent_candles_per_timeframe
            recent_candles = tf_candles[-recent_count:] if len(tf_candles) > recent_count else tf_candles
            
            # Session state at decision time
            from app.indicators.sessions import build_session_state
            session = build_session_state(decision_candle.time_close)
            
            timeframe_states[tf.value] = TimeframeState(
                latest_closed_candle=tf_candles[-1],
                structure=structure,
                zones=zones,
                ema={
                    **ema_values,
                    **ema_rel,
                },
                rsi=indicator_state.get("rsi"),
                atr=indicator_state.get("atr"),
                fib=fib,
                candles=recent_candles,
                features=features,
                events=structure.all_events,
            )
        
        # Overall market price (decision timeframe close)
        price = decision_candle.close
        
        # Session at decision time
        session = build_session_state(as_of)
        
        # Round numbers at decision time
        round_nums = calculate_round_numbers(
            price, symbol_metadata,
            self.config.round_numbers.step_pips,
            self.config.round_numbers.levels_around_price,
        )
        
        # Previous levels
        d1_candles = aligned_candles.get(Timeframe.D1, [])
        prev_levels = calculate_previous_levels(d1_candles, data_source.broker_utc_offset_hours)
        
        # Build market state
        return MarketState(
            schema_version=self.config.engine.schema_version,
            engine_version=self.config.engine.version,
            analysis_engine_version="2.0.0",
            symbol=symbol,
            style=self.config.style,
            decision_timeframe=decision_timeframe.value,
            decision_candle_close_time=decision_candle.time_close,
            as_of=as_of,
            data_source=data_source,
            price=price,
            session=session,
            previous_day=prev_levels,
            previous_week=prev_levels,  # Same object, has both day/week
            round_numbers=round_nums,
            timeframes=timeframe_states,
            structural_events=all_events,
            data_quality=data_source,
        )


def serialize_market_state(state: MarketState) -> str:
    """Serialize market state to JSON."""
    return state.model_dump_json(indent=2, exclude_none=True)


def save_market_state(state: MarketState, output_dir: str) -> str:
    """Save market state to file."""
    from pathlib import Path
    import json
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Filename: SYMBOL_STYLE_DECISIONTF_TIMESTAMP.json
    ts = state.decision_candle_close_time.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{state.symbol}_{state.style}_{state.decision_timeframe}_{ts}_v{state.engine_version}.json"
    filepath = output_path / filename
    
    with open(filepath, "w") as f:
        json.dump(state.model_dump(mode="json", exclude_none=True), f, indent=2)
    
    return str(filepath)