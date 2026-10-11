"""
Main entry point for the Analysis Engine.
"""
from app.config.settings import config, load_config
from app.data.provider import DataProvider, LoadedSeries, get_provider
from app.data.csv_loader import load_candles_from_csv
from app.data.csv_provider import CsvDataProvider
from app.data.oanda_provider import OandaDataProvider
from app.data.mt5_provider import Mt5DataProvider
from app.data.timeframe_alignment import align_timeframes, HistoricalReplay
from app.replay.engine import run_replay, run_single_analysis
from app.market_state.builder import MarketStateBuilder, save_market_state, serialize_market_state

__all__ = [
    "config",
    "load_config",
    "DataProvider",
    "LoadedSeries",
    "get_provider",
    "load_candles_from_csv",
    "CsvDataProvider",
    "OandaDataProvider",
    "Mt5DataProvider",
    "align_timeframes",
    "HistoricalReplay",
    "run_replay",
    "run_single_analysis",
    "MarketStateBuilder",
    "save_market_state",
    "serialize_market_state",
]
