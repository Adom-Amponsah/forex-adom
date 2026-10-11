"""
CSV-backed data provider (MT5 exports).

Resolves `{SYMBOL}_{TIMEFRAME}.csv` files in a directory and loads each into a
`LoadedSeries`. This is the offline/back-compat provider; the OANDA provider is
the primary live source.
"""
from pathlib import Path
from typing import Optional
from app.data.models import Timeframe, SymbolMetadata
from app.data.provider import LoadedSeries, ProviderError
from app.data.csv_loader import load_candles_from_csv


class CsvDataProvider:
    """Loads candles from a directory of MT5 CSV exports."""

    def __init__(
        self,
        data_dir: str | Path,
        broker_offset_hours: Optional[float] = None,
        drop_last_row: Optional[bool] = None,
        symbol_metadata: Optional[SymbolMetadata] = None,
    ):
        self.data_dir = Path(data_dir)
        self.broker_offset_hours = broker_offset_hours
        self.drop_last_row = drop_last_row
        self.symbol_metadata = symbol_metadata

    def _resolve(self, symbol: str, timeframe: Timeframe) -> Path:
        patterns = [
            f"{symbol}_{timeframe.value}.csv",
            f"{symbol.lower()}_{timeframe.value.lower()}.csv",
            f"{symbol}{timeframe.value}.csv",
        ]
        for pattern in patterns:
            candidate = self.data_dir / pattern
            if candidate.exists():
                return candidate
        raise ProviderError(
            f"No CSV file found for {symbol} {timeframe.value} in {self.data_dir}"
        )

    def load(
        self, symbol: str, timeframes: list[Timeframe]
    ) -> dict[Timeframe, LoadedSeries]:
        if not self.data_dir.exists():
            raise ProviderError(f"Data directory not found: {self.data_dir}")

        results: dict[Timeframe, LoadedSeries] = {}
        for tf in timeframes:
            filepath = self._resolve(symbol, tf)
            results[tf] = load_candles_from_csv(
                filepath=filepath,
                symbol=symbol,
                timeframe=tf,
                symbol_metadata=self.symbol_metadata,
                broker_offset_hours=self.broker_offset_hours,
                drop_last_row=self.drop_last_row,
            )
        return results
