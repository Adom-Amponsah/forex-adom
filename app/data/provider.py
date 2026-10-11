"""
Data provider abstraction.

The engine consumes `list[Candle]` and knows nothing about where the candles
came from. Every source (MT5 CSV exports, the OANDA v20 REST API, ...) is
wrapped in a `DataProvider` that returns `LoadedSeries` objects keyed by
timeframe. This keeps the deterministic core independent of the data source
and lets us swap feeds without touching the analysis code.
"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from app.data.models import (
    Candle, Timeframe, SymbolMetadata, DataSourceInfo, DataQualityReport
)


@dataclass
class LoadedSeries:
    """A single timeframe's worth of candles plus its provenance."""
    timeframe: Timeframe
    candles: list[Candle]
    symbol_metadata: SymbolMetadata
    data_source: DataSourceInfo
    quality: DataQualityReport = field(default_factory=DataQualityReport)


@runtime_checkable
class DataProvider(Protocol):
    """Loads candles for a symbol across one or more timeframes."""

    def load(
        self, symbol: str, timeframes: list[Timeframe]
    ) -> dict[Timeframe, LoadedSeries]:
        ...


class ProviderError(Exception):
    """Raised when a provider cannot fulfil a load request."""
    pass


def get_provider(name: str | None = None, **kwargs) -> DataProvider:
    """
    Build a provider by name. Defaults to the configured `data_provider`.

    Extra keyword arguments are forwarded to the provider constructor.
    """
    from app.config.settings import config

    name = (name or config.data_provider).lower()

    if name == "csv":
        from app.data.csv_provider import CsvDataProvider
        return CsvDataProvider(**kwargs)
    if name == "oanda":
        from app.data.oanda_provider import OandaDataProvider
        return OandaDataProvider(**kwargs)
    if name == "mt5":
        from app.data.mt5_provider import Mt5DataProvider
        return Mt5DataProvider(**kwargs)
    if name == "synthetic":
        from app.data.synthetic_provider import SyntheticDataProvider
        return SyntheticDataProvider(**kwargs)

    raise ProviderError(
        f"Unknown data provider '{name}'. Expected 'csv', 'oanda', 'mt5' or 'synthetic'."
    )
