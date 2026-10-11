"""
Historical replay engine - runs the deterministic engine across historical time.
"""
from datetime import datetime
from typing import Optional
from tqdm import tqdm
from app.data.models import Candle, Timeframe, SymbolMetadata, DataSourceInfo
from app.data.provider import DataProvider, LoadedSeries
from app.data.timeframe_alignment import HistoricalReplay, align_timeframes
from app.market_state.builder import MarketStateBuilder, save_market_state, serialize_market_state


def _organize(
    loaded: dict[Timeframe, LoadedSeries],
) -> tuple[dict[Timeframe, list[Candle]], SymbolMetadata, DataSourceInfo]:
    """Flatten provider output into the shapes the engine consumes."""
    if not loaded:
        raise ValueError("Provider returned no data")
    all_candles = {tf: series.candles for tf, series in loaded.items()}
    first = next(iter(loaded.values()))
    return all_candles, first.symbol_metadata, first.data_source


class ReplayEngine:
    """Runs historical replay across all decision points."""

    def __init__(
        self,
        symbol: str,
        timeframes: list[Timeframe],
        decision_timeframe: Timeframe,
        context_timeframes: list[Timeframe],
        structure_timeframes: list[Timeframe],
        calculation_history_bars: int = 1000,
        symbol_metadata: Optional[SymbolMetadata] = None,
    ):
        self.symbol = symbol
        self.timeframes = timeframes
        self.decision_timeframe = decision_timeframe
        self.context_timeframes = context_timeframes
        self.structure_timeframes = structure_timeframes
        self.calculation_history_bars = calculation_history_bars
        self.symbol_metadata = symbol_metadata
        self.builder = MarketStateBuilder()

    def run(
        self,
        all_candles: dict[Timeframe, list[Candle]],
        data_source,
        output_dir: str,
        start_idx: int = 0,
        max_snapshots: Optional[int] = None,
    ) -> list[str]:
        """Run replay and save market states. Returns list of saved file paths."""
        replay = HistoricalReplay(
            all_candles=all_candles,
            decision_timeframe=self.decision_timeframe,
            context_timeframes=self.context_timeframes,
            structure_timeframes=self.structure_timeframes,
            calculation_history_bars=self.calculation_history_bars,
        )

        decision_candles = all_candles[self.decision_timeframe]
        total = len(decision_candles) - start_idx
        if max_snapshots:
            total = min(total, max_snapshots)

        saved_files = []

        with tqdm(total=total, desc="Replaying") as pbar:
            for i, (decision_candle, aligned) in enumerate(replay.iterate_snapshots(start_idx)):
                if max_snapshots and i >= max_snapshots:
                    break

                state = self.builder.build(
                    symbol=self.symbol,
                    symbol_metadata=self.symbol_metadata,
                    decision_timeframe=self.decision_timeframe,
                    decision_candle=decision_candle,
                    aligned_candles=aligned,
                    data_source=data_source,
                )

                filepath = save_market_state(state, output_dir)
                saved_files.append(filepath)

                pbar.update(1)
                pbar.set_postfix({"time": decision_candle.time_close.isoformat()})

        return saved_files


def _default_timeframes(
    timeframes, context_timeframes, structure_timeframes
):
    if timeframes is None:
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
    if context_timeframes is None:
        context_timeframes = [Timeframe.D1]
    if structure_timeframes is None:
        structure_timeframes = [Timeframe.H4]
    return timeframes, context_timeframes, structure_timeframes


def run_replay(
    provider: DataProvider,
    symbol: str,
    output_dir: str,
    timeframes: list[Timeframe] = None,
    decision_timeframe: Timeframe = Timeframe.H1,
    context_timeframes: list[Timeframe] = None,
    structure_timeframes: list[Timeframe] = None,
    start_idx: int = 0,
    max_snapshots: int = None,
    calculation_history_bars: int = 1000,
) -> list[str]:
    """High-level function to run replay against any data provider."""
    timeframes, context_timeframes, structure_timeframes = _default_timeframes(
        timeframes, context_timeframes, structure_timeframes
    )

    loaded = provider.load(symbol, timeframes)
    all_candles, symbol_meta, data_source = _organize(loaded)

    engine = ReplayEngine(
        symbol=symbol,
        timeframes=timeframes,
        decision_timeframe=decision_timeframe,
        context_timeframes=context_timeframes,
        structure_timeframes=structure_timeframes,
        calculation_history_bars=calculation_history_bars,
        symbol_metadata=symbol_meta,
    )

    return engine.run(
        all_candles=all_candles,
        data_source=data_source,
        output_dir=output_dir,
        start_idx=start_idx,
        max_snapshots=max_snapshots,
    )


def run_single_analysis(
    provider: DataProvider,
    symbol: str,
    decision_time: datetime,
    timeframes: list[Timeframe] = None,
    decision_timeframe: Timeframe = Timeframe.H1,
    context_timeframes: list[Timeframe] = None,
    structure_timeframes: list[Timeframe] = None,
    calculation_history_bars: int = 1000,
) -> str:
    """Run analysis for a single decision point. Returns market state JSON."""
    timeframes, context_timeframes, structure_timeframes = _default_timeframes(
        timeframes, context_timeframes, structure_timeframes
    )

    loaded = provider.load(symbol, timeframes)
    all_candles, symbol_meta, data_source = _organize(loaded)

    decision_candles = all_candles[decision_timeframe]
    decision_candle = next(
        (c for c in decision_candles if c.time_close == decision_time), None
    )
    if decision_candle is None:
        raise ValueError(f"No decision candle found at {decision_time}")

    aligned = align_timeframes(
        all_candles=all_candles,
        decision_timeframe=decision_timeframe,
        decision_candle=decision_candle,
        context_timeframes=context_timeframes,
        structure_timeframes=structure_timeframes,
        calculation_history_bars=calculation_history_bars,
    )

    builder = MarketStateBuilder()
    state = builder.build(
        symbol=symbol,
        symbol_metadata=symbol_meta,
        decision_timeframe=decision_timeframe,
        decision_candle=decision_candle,
        aligned_candles=aligned,
        data_source=data_source,
    )

    return serialize_market_state(state)
