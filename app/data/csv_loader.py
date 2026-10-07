"""
CSV data loader for MT5 exported candles.
Handles broker timezone offset, closed-candle filtering, and data validation.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd
import numpy as np
from pydantic import BaseModel
from app.data.models import (
    Candle, Timeframe, SymbolMetadata, VolumeType, DataSourceInfo,
    make_candle_id
)
from app.config.settings import config


class CSVLoadError(Exception):
    """Error during CSV loading."""
    pass


class DataQualityReport(BaseModel):
    """Data quality metadata for loaded candles."""
    complete: bool = True
    missing_bars: list[dict] = []
    duplicate_bars: list[dict] = []
    out_of_order_bars: list[dict] = []
    source: str = "MT5_CSV"
    total_bars: int = 0
    date_range: tuple[datetime, datetime] | None = None


def parse_mt5_time(time_str: str, broker_offset_hours: float) -> datetime:
    """
    Parse MT5 time string and convert to UTC.
    
    MT5 typically exports in broker server time (no timezone info).
    We apply the broker offset to convert to UTC.
    """
    # Try common MT5 formats
    formats = [
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ]
    
    dt = None
    for fmt in formats:
        try:
            dt = datetime.strptime(time_str, fmt)
            break
        except ValueError:
            continue
    
    if dt is None:
        raise CSVLoadError(f"Unable to parse time string: {time_str}")
    
    # MT5 exports are typically in broker server time (no DST)
    # Apply offset to get UTC
    # If broker is UTC+2, offset = 2, so UTC = server_time - 2 hours
    utc_dt = dt - timedelta(hours=broker_offset_hours)
    return utc_dt.replace(tzinfo=timezone.utc)


def load_candles_from_csv(
    filepath: str | Path,
    symbol: str,
    timeframe: Timeframe,
    symbol_metadata: Optional[SymbolMetadata] = None,
    broker_offset_hours: Optional[float] = None,
    drop_last_row: Optional[bool] = None,
) -> tuple[list[Candle], SymbolMetadata, DataSourceInfo]:
    """
    Load candles from MT5 CSV export.
    
    Args:
        filepath: Path to CSV file
        symbol: Trading symbol (e.g., "EURUSD")
        timeframe: Timeframe of the data
        symbol_metadata: Optional symbol metadata (digits, point, etc.)
        broker_offset_hours: Broker server time offset from UTC (e.g., 2 for EET)
        drop_last_row: Whether to drop the last row (forming candle)
    
    Returns:
        Tuple of (candles, symbol_metadata, data_source_info)
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise CSVLoadError(f"File not found: {filepath}")
    
    # Use config defaults if not provided
    if broker_offset_hours is None:
        broker_offset_hours = config.csv_import.broker_offset_hours
    if drop_last_row is None:
        drop_last_row = config.csv_import.drop_last_row
    
    # Read CSV
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        raise CSVLoadError(f"Failed to read CSV: {e}")
    
    if df.empty:
        raise CSVLoadError("CSV file is empty")
    
    # Map columns
    cols = config.csv_import.columns
    required_cols = ["time", "open", "high", "low", "close"]
    for col in required_cols:
        if cols[col] not in df.columns:
            raise CSVLoadError(f"Required column '{cols[col]}' not found in CSV")
    
    # Drop last row if it's a forming candle
    if drop_last_row and len(df) > 0:
        df = df.iloc[:-1].copy()
    
    # Parse timestamps
    time_col = cols["time"]
    try:
        df["time_utc"] = df[time_col].apply(
            lambda x: parse_mt5_time(str(x), broker_offset_hours)
        )
    except Exception as e:
        raise CSVLoadError(f"Failed to parse timestamps: {e}")
    
    # Sort by time
    df = df.sort_values("time_utc").reset_index(drop=True)
    
    # Data quality checks
    quality = DataQualityReport()
    quality.total_bars = len(df)
    
    if len(df) > 0:
        quality.date_range = (df["time_utc"].iloc[0], df["time_utc"].iloc[-1])
    
    # Check for duplicates
    duplicates = df[df.duplicated(subset=["time_utc"], keep=False)]
    if not duplicates.empty:
        quality.complete = False
        for _, row in duplicates.iterrows():
            quality.duplicate_bars.append({
                "time": row["time_utc"].isoformat(),
                "count": int(df["time_utc"].value_counts()[row["time_utc"]])
            })
        # Keep first occurrence
        df = df.drop_duplicates(subset=["time_utc"], keep="first").reset_index(drop=True)
    
    # Check for out of order (should be sorted now, but check original)
    # Check for gaps (missing bars)
    expected_freq = timeframe.to_pandas_freq()
    if len(df) > 1:
        expected_times = pd.date_range(
            start=df["time_utc"].iloc[0],
            end=df["time_utc"].iloc[-1],
            freq=expected_freq,
            tz=timezone.utc
        )
        missing = expected_times.difference(df["time_utc"])
        if len(missing) > 0:
            quality.complete = False
            for mt in missing[:100]:  # Limit reported missing bars
                quality.missing_bars.append({"time": mt.isoformat()})
    
    # Create symbol metadata if not provided
    if symbol_metadata is None:
        # Try to infer from data
        digits = 5
        point = 0.00001
        if "JPY" in symbol.upper():
            digits = 3
            point = 0.001
        elif "XAU" in symbol.upper() or "GOLD" in symbol.upper():
            digits = 2
            point = 0.01
        
        symbol_metadata = SymbolMetadata(
            symbol=symbol,
            digits=digits,
            point=point,
        )
    
    # Build candles
    candles = []
    for _, row in df.iterrows():
        candle = Candle(
            id=make_candle_id(symbol, timeframe, row["time_utc"]),
            symbol=symbol,
            timeframe=timeframe,
            time_open=row["time_utc"] - timedelta(seconds=timeframe.to_seconds()),
            time_close=row["time_utc"],
            open=float(row[cols["open"]]),
            high=float(row[cols["high"]]),
            low=float(row[cols["low"]]),
            close=float(row[cols["close"]]),
            tick_volume=int(row.get(cols["tick_volume"], 0)),
            spread_points=int(row.get(cols["spread"], 0)),
            real_volume=int(row.get(cols["real_volume"], 0)),
        )
        candles.append(candle)
    
    # Data source info
    data_source = DataSourceInfo(
        provider="MT5",
        volume_type=symbol_metadata.volume_type.value,
        feed="MT5_CSV",
        broker=symbol_metadata.broker,
        broker_utc_offset_hours=broker_offset_hours,
    )
    
    return candles, symbol_metadata, data_source


def load_multi_timeframe_csv(
    data_dir: str | Path,
    symbol: str,
    timeframes: list[Timeframe],
    symbol_metadata: Optional[SymbolMetadata] = None,
    broker_offset_hours: Optional[float] = None,
    drop_last_row: Optional[bool] = None,
) -> dict[Timeframe, tuple[list[Candle], SymbolMetadata, DataSourceInfo]]:
    """
    Load candles for multiple timeframes from a directory.
    
    Expected file naming: {SYMBOL}_{TIMEFRAME}.csv (e.g., EURUSD_H1.csv)
    """
    data_dir = Path(data_dir)
    results = {}
    
    for tf in timeframes:
        # Try common naming patterns
        patterns = [
            f"{symbol}_{tf.value}.csv",
            f"{symbol.lower()}_{tf.value.lower()}.csv",
            f"{symbol}{tf.value}.csv",
        ]
        
        filepath = None
        for pattern in patterns:
            candidate = data_dir / pattern
            if candidate.exists():
                filepath = candidate
                break
        
        if filepath is None:
            raise CSVLoadError(f"No CSV file found for {symbol} {tf.value} in {data_dir}")
        
        results[tf] = load_candles_from_csv(
            filepath=filepath,
            symbol=symbol,
            timeframe=tf,
            symbol_metadata=symbol_metadata,
            broker_offset_hours=broker_offset_hours,
            drop_last_row=drop_last_row,
        )
    
    return results