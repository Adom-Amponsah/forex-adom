"""
Canonical Market State - the deterministic facts-only output.
This is the core data structure that feeds into the LLM.
"""
from datetime import datetime
from typing import Optional
import numpy as np
from app.data.models import Candle, Timeframe, SymbolMetadata, DataSourceInfo, DataQualityReport
from app.structure.structure import MarketStructure, StructuralEvent
from app.zones.engine import Zone
from app.indicators.fibonacci import FibonacciRetracement
from app.indicators.levels import PreviousLevel, RoundNumberLevels
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
    previous_day: PreviousLevel
    previous_week: PreviousLevel
    round_numbers: RoundNumberLevels

    # Per-timeframe detail
    timeframes: dict[str, TimeframeState] = {}

    # Pattern features (deterministic geometry)
    pattern_features: list = []

    # Structural events across all timeframes
    structural_events: list[StructuralEvent] = []

    # Data quality
    data_quality: DataQualityReport = Field(default_factory=DataQualityReport)


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

        All timeframes are processed in a fixed (descending timeframe size)
        order so event IDs and the output dict are identical run-to-run.
        """
        as_of = decision_candle.time_close

        # Deterministic timeframe ordering: largest timeframe first.
        ordered_tfs = sorted(aligned_candles.keys(), key=lambda t: -t.to_seconds())

        # Session state at the decision time (shared across timeframes).
        from app.indicators.sessions import build_session_state
        session = build_session_state(as_of)

        # Previous day/week levels from closed D1 candles (shared).
        d1_candles = aligned_candles.get(Timeframe.D1, [])
        from app.indicators.levels import calculate_previous_levels, calculate_round_numbers
        previous_day, previous_week = calculate_previous_levels(
            d1_candles, as_of, data_source.broker_utc_offset_hours
        )

        # Data quality is scoped to the candles actually used up to T, so the
        # state never changes when later candles are appended to the source.
        from app.data.data_quality import check_candle_sequence
        data_quality = check_candle_sequence(
            aligned_candles.get(decision_timeframe, []), decision_timeframe
        )
        data_quality.source = data_source.feed

        # Overall market price (decision timeframe close).
        price = decision_candle.close

        round_nums = calculate_round_numbers(
            price, symbol_metadata,
            self.config.round_numbers.step_pips,
            self.config.round_numbers.levels_around_price,
        )

        timeframe_states: dict[str, TimeframeState] = {}
        all_events: list[StructuralEvent] = []
        event_counter = 0

        for tf in ordered_tfs:
            tf_candles = aligned_candles.get(tf, [])
            if not tf_candles:
                continue

            from app.indicators.engine import (
                compute_indicators_for_candles, get_indicator_state,
                ema_relationships, ema_directions,
            )
            indicators = compute_indicators_for_candles(
                tf_candles,
                self.config.indicators.ema.periods,
                self.config.indicators.rsi.period,
                self.config.indicators.atr.period,
            )

            latest_idx = len(tf_candles) - 1
            indicator_state = get_indicator_state(
                indicators, latest_idx, self.config.indicators.ema.periods
            )

            ema_values = {k: v for k, v in indicator_state.items() if k.startswith("ema_")}
            ema_rel = ema_relationships(tf_candles[-1].close, ema_values)
            ema_dir = ema_directions(
                indicators, latest_idx, self.config.indicators.ema.periods
            )

            from app.structure.swings import get_confirmed_swings_up_to
            swings = get_confirmed_swings_up_to(
                tf_candles,
                as_of_index=latest_idx,
                left_bars=self.config.swing.left_bars,
                right_bars=self.config.swing.right_bars,
                equal_policy=self.config.swing.equal_policy,
            )

            atr_values = indicators["atr"]
            atr_list = [None if np.isnan(v) else float(v) for v in atr_values]

            from app.structure.structure import build_market_structure, classify_swings
            structure, event_counter = build_market_structure(
                candles=tf_candles,
                swings=swings,
                atr_values=atr_list,
                symbol_point=symbol_metadata.point,
                event_counter=event_counter,
            )

            if structure.all_events:
                all_events.extend(structure.all_events)

            labeled_swings = classify_swings(swings)

            from app.zones.engine import build_zones
            tf_round_nums = calculate_round_numbers(
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
                as_of=as_of,
                prev_day_high=previous_day.high,
                prev_day_low=previous_day.low,
                prev_week_high=previous_week.high,
                prev_week_low=previous_week.low,
                round_numbers=tf_round_nums.levels,
            )

            from app.indicators.fibonacci import build_fibonacci
            fib = build_fibonacci(
                labeled_swings=labeled_swings,
                atr_values=atr_list,
                ratios=self.config.fibonacci.ratios,
                min_leg_atr_multiple=self.config.fibonacci.min_leg_atr_multiple,
            )

            from app.patterns.candle_features import detect_features_for_series
            features = detect_features_for_series(tf_candles)

            recent_count = self.config.replay.recent_candles_per_timeframe
            recent_candles = tf_candles[-recent_count:] if len(tf_candles) > recent_count else tf_candles

            timeframe_states[tf.value] = TimeframeState(
                latest_closed_candle=tf_candles[-1],
                structure=structure,
                zones=zones,
                ema={
                    **ema_values,
                    **ema_rel,
                    **ema_dir,
                },
                rsi=indicator_state.get("rsi"),
                atr=indicator_state.get("atr"),
                fib=fib,
                candles=recent_candles,
                features=features,
                events=structure.all_events,
            )

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
            previous_day=previous_day,
            previous_week=previous_week,
            round_numbers=round_nums,
            timeframes=timeframe_states,
            structural_events=all_events,
            data_quality=data_quality,
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

    ts = state.decision_candle_close_time.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{state.symbol}_{state.style}_{state.decision_timeframe}_{ts}_v{state.engine_version}.json"
    filepath = output_path / filename

    with open(filepath, "w") as f:
        json.dump(state.model_dump(mode="json", exclude_none=True), f, indent=2)

    return str(filepath)
