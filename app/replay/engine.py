"""
Historical replay engine - runs the deterministic engine across historical time.
"""
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
import json
from tqdm import tqdm
from app.data.models import Candle, Timeframe, SymbolMetadata
from app.data.timeframe_alignment import HistoricalReplay, align_timeframes
from app.data.csv_loader import load_multi_timeframe_csv
from app.market_state.builder import MarketStateBuilder, save_market_state, serialize_market_state


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
        """
        Run replay and save market states.
        
        Returns list of saved file paths.
        """
        # Create replay iterator
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
                
                # Build market state
                state = self.builder.build(
                    symbol=self.symbol,
                    symbol_metadata=self.symbol_metadata,
                    decision_timeframe=self.decision_timeframe,
                    decision_candle=decision_candle,
                    aligned_candles=aligned,
                    data_source=data_source,
                )
                
                # Save
                filepath = save_market_state(state, output_dir)
                saved_files.append(filepath)
                
                pbar.update(1)
                pbar.set_postfix({"time": decision_candle.time_close.isoformat()})
        
        return saved_files


def run_replay_from_csv(
    data_dir: str,
    symbol: str,
    output_dir: str,
    timeframes: list[Timeframe] = None,
    decision_timeframe: Timeframe = Timeframe.H1,
    context_timeframes: list[Timeframe] = None,
    structure_timeframes: list[Timeframe] = None,
    broker_offset_hours: float = 2.0,
    drop_last_row: bool = True,
    start_idx: int = 0,
    max_snapshots: int = None,
) -> list[str]:
    """
    High-level function to run replay from CSV files.
    """
    if timeframes is None:
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
    if context_timeframes is None:
        context_timeframes = [Timeframe.D1]
    if structure_timeframes is None:
        structure_timeframes = [Timeframe.H4]
    
    # Load data
    loaded = load_multi_timeframe_csv(
        data_dir=data_dir,
        symbol=symbol,
        timeframes=timeframes,
        broker_offset_hours=broker_offset_hours,
        drop_last_row=drop_last_row,
    )
    
    # Organize candles by timeframe
    all_candles = {}
    symbol_meta = None
    data_source = None
    
    for tf, (candles, meta, source) in loaded.items():
        all_candles[tf] = candles
        if symbol_meta is None:
            symbol_meta = meta
            data_source = source
    
    if symbol_meta is None:
        raise ValueError("No symbol metadata loaded")
    
    # Run replay
    engine = ReplayEngine(
        symbol=symbol,
        timeframes=timeframes,
        decision_timeframe=decision_timeframe,
        context_timeframes=context_timeframes,
        structure_timeframes=structure_timeframes,
        calculation_history_bars=1000,
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
    data_dir: str,
    symbol: str,
    decision_time: datetime,
    timeframes: list[Timeframe] = None,
    decision_timeframe: Timeframe = Timeframe.H1,
    context_timeframes: list[Timeframe] = None,
    structure_timeframes: list[Timeframe] = None,
    broker_offset_hours: float = 2.0,
    drop_last_row: bool = True,
) -> str:
    """
    Run analysis for a single decision point (for testing/manual verification).
    Returns the JSON string of the market state.
    """
    if timeframes is None:
        timeframes = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
    if context_timeframes is None:
        context_timeframes = [Timeframe.D1]
    if structure_timeframes is None:
        structure_timeframes = [Timeframe.H4]
    
    # Load data
    loaded = load_multi_timeframe_csv(
        data_dir=data_dir,
        symbol=symbol,
        timeframes=timeframes,
        broker_offset_hours=broker_offset_hours,
        drop_last_row=drop_last_row,
    )
    
    all_candles = {}
    symbol_meta = None
    data_source = None
    
    for tf, (candles, meta, source) in loaded.items():
        all_candles[tf] = candles
        if symbol_meta is None:
            symbol_meta = meta
            data_source = source
    
    # Find decision candle
    decision_candles = all_candles[decision_timeframe]
    decision_candle = None
    for c in decision_candles:
        if c.time_close == decision_time:
            decision_candle = c
            break
    
    if decision_candle is None:
        raise ValueError(f"No decision candle found at {decision_time}")
    
    # Align timeframes
    aligned = align_timeframes(
        all_candles=all_candles,
        decision_timeframe=decision_timeframe,
        decision_candle=decision_candle,
        context_timeframes=context_timeframes,
        structure_timeframes=structure_timeframes,
        calculation_history_bars=1000,
    )
    
    # Build state
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