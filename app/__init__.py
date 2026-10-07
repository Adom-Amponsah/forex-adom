"""
Main entry point for the MT5 Analysis Engine.
"""
from app.config.settings import config, load_config
from app.data.csv_loader import load_candles_from_csv, load_multi_timeframe_csv
from app.data.timeframe_alignment import align_timeframes, HistoricalReplay
from app.replay.engine import run_replay_from_csv, run_single_analysis
from app.market_state.builder import MarketStateBuilder, save_market_state, serialize_market_state

__all__ = [
    "config",
    "load_config",
    "load_candles_from_csv",
    "load_multi_timeframe_csv",
    "align_timeframes",
    "HistoricalReplay",
    "run_replay_from_csv",
    "run_single_analysis",
    "MarketStateBuilder",
    "save_market_state",
    "serialize_market_state",
]