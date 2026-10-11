"""
CSV parsing for MT5 exported candles.

MT5's CSV export timestamps each bar by its OPEN time and may include the
currently forming bar as the final row. We convert to UTC, optionally drop the
forming bar, and build `Candle`s whose `time_close` is the authoritative
"this bar is known" timestamp (open + timeframe).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import pandas as pd
from app.data.models import (
    Timeframe, SymbolMetadata, VolumeType, DataSourceInfo,
    DataQualityReport, make_candle,
)
from app.data.provider import LoadedSeries
from app.config.settings import config


class CSVLoadError(Exception):
    """Error during CSV loading."""
    pass


def parse_mt5_time(time_str: str, broker_offset_hours: float) -> datetime:
    """
    Parse an MT5 time string (bar OPEN time, broker server time) and return UTC.

    MT5 exports carry no timezone info; the broker's server offset is subtracted
    to obtain UTC.
    """
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

    utc_dt = dt - timedelta(hours=broker_offset_hours)
    return utc_dt.replace(tzinfo=timezone.utc)


def infer_symbol_metadata(symbol: str, volume_type: VolumeType = VolumeType.TICK_VOLUME) -> SymbolMetadata:
    """Infer digits/point from the symbol name when no metadata is supplied."""
    upper = symbol.upper()
    if "JPY" in upper:
        digits, point = 3, 0.001
    elif "XAU" in upper or "GOLD" in upper:
        digits, point = 2, 0.01
    elif "XAG" in upper or "SILVER" in upper:
        digits, point = 3, 0.001
    elif upper.endswith("BTC") or "BTC" in upper:
        digits, point = 2, 0.01
    else:
        digits, point = 5, 0.00001
    return SymbolMetadata(symbol=symbol, digits=digits, point=point, volume_type=volume_type)


def load_candles_from_csv(
    filepath: str | Path,
    symbol: str,
    timeframe: Timeframe,
    symbol_metadata: Optional[SymbolMetadata] = None,
    broker_offset_hours: Optional[float] = None,
    drop_last_row: Optional[bool] = None,
) -> LoadedSeries:
    """Load a single MT5 CSV export into a `LoadedSeries`."""
    filepath = Path(filepath)
    if not filepath.exists():
        raise CSVLoadError(f"File not found: {filepath}")

    if broker_offset_hours is None:
        broker_offset_hours = config.csv_import.broker_offset_hours
    if drop_last_row is None:
        drop_last_row = config.csv_import.drop_last_row

    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        raise CSVLoadError(f"Failed to read CSV: {e}")

    if df.empty:
        raise CSVLoadError("CSV file is empty")

    cols = config.csv_import.columns
    for col in ["time", "open", "high", "low", "close"]:
        if cols[col] not in df.columns:
            raise CSVLoadError(f"Required column '{cols[col]}' not found in CSV")

    # The final row of an MT5 export is often the still-forming bar.
    if drop_last_row and len(df) > 0:
        df = df.iloc[:-1].copy()

    try:
        df["bar_open_utc"] = df[cols["time"]].apply(
            lambda x: parse_mt5_time(str(x), broker_offset_hours)
        )
    except Exception as e:
        raise CSVLoadError(f"Failed to parse timestamps: {e}")

    df = df.sort_values("bar_open_utc").reset_index(drop=True)

    quality = DataQualityReport(source="MT5_CSV", total_bars=len(df))

    # Drop duplicate bar-open timestamps, keeping the first occurrence.
    duplicated = df["bar_open_utc"].duplicated(keep=False)
    if duplicated.any():
        quality.complete = False
        for ts, count in df.loc[duplicated, "bar_open_utc"].value_counts().items():
            quality.duplicate_bars.append({"time": ts.isoformat(), "count": int(count)})
        df = df.drop_duplicates(subset=["bar_open_utc"], keep="first").reset_index(drop=True)

    if symbol_metadata is None:
        symbol_metadata = infer_symbol_metadata(symbol)

    candles = []
    for _, row in df.iterrows():
        candles.append(make_candle(
            symbol=symbol,
            timeframe=timeframe,
            bar_open=row["bar_open_utc"],
            open=float(row[cols["open"]]),
            high=float(row[cols["high"]]),
            low=float(row[cols["low"]]),
            close=float(row[cols["close"]]),
            tick_volume=int(row.get(cols["tick_volume"], 0) or 0),
            spread_points=int(row.get(cols["spread"], 0) or 0),
            real_volume=int(row.get(cols["real_volume"], 0) or 0),
        ))

    if candles:
        quality.date_range = (candles[0].time_close, candles[-1].time_close)

    # Gap detection over closed bars.
    from app.data.data_quality import check_candle_sequence
    seq_quality = check_candle_sequence(candles, timeframe)
    quality.missing_bars = seq_quality.missing_bars
    quality.out_of_order_bars = seq_quality.out_of_order_bars
    if not seq_quality.complete:
        quality.complete = False

    data_source = DataSourceInfo(
        provider="MT5",
        volume_type=symbol_metadata.volume_type.value,
        feed="MT5_CSV",
        broker=symbol_metadata.broker,
        broker_utc_offset_hours=broker_offset_hours,
    )

    return LoadedSeries(
        timeframe=timeframe,
        candles=candles,
        symbol_metadata=symbol_metadata,
        data_source=data_source,
        quality=quality,
    )
