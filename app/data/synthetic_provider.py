"""
Deterministic synthetic data provider.

Produces realistic multi-timeframe candles (trends, pullbacks, ranges, weekend
gaps) from a single H1 price path that is aggregated up to H4/D1. Because it is
seeded and aggregation is exact, the output is fully reproducible - which makes
it useful for offline development and for exercising structure/zone/event logic
in tests without a network call or a broker account.

It is NOT a substitute for real data; use the OANDA provider for that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import numpy as np

from app.data.models import (
    Timeframe, SymbolMetadata, DataSourceInfo, DataQualityReport, make_candle,
)
from app.data.provider import LoadedSeries

# H1 bars per aggregated bar (approx: 24h/day, 5 trading days/week)
_GROUP_SIZE = {
    Timeframe.H1: 1,
    Timeframe.H4: 4,
    Timeframe.D1: 24,
    Timeframe.W1: 120,
    Timeframe.MN1: 504,
}


class SyntheticDataProvider:
    """Deterministic, offline multi-timeframe provider."""

    def __init__(
        self,
        seed: int = 7,
        h1_bars: int = 2400,
        start: datetime = datetime(2024, 1, 1, tzinfo=timezone.utc),
        start_price: float = 1.1000,
        symbol_metadata: SymbolMetadata | None = None,
    ):
        self.seed = seed
        self.h1_bars = h1_bars
        self.start = start
        self.start_price = start_price
        self.symbol_metadata = symbol_metadata

    def _h1_times(self) -> list[datetime]:
        times: list[datetime] = []
        day = self.start
        while len(times) < self.h1_bars:
            if day.weekday() < 5:  # Mon-Fri only
                for hour in range(24):
                    times.append(day + timedelta(hours=hour))
            day += timedelta(days=1)
        return times[: self.h1_bars]

    def _h1_candles(self, symbol: str) -> list:
        # Two independent RNG streams: the price path and the intrabar noise.
        # Keeping them separate guarantees that any prefix of the series is
        # identical regardless of the total requested length.
        rng_path = np.random.default_rng(self.seed)
        rng_noise = np.random.default_rng(self.seed + 1)
        times = self._h1_times()
        digits = self.symbol_metadata.digits if self.symbol_metadata else 5
        point = self.symbol_metadata.point if self.symbol_metadata else 0.00001

        closes: list[float] = []
        price = self.start_price
        while len(closes) < len(times):
            seg_len = int(rng_path.integers(20, 60))
            drift = float(rng_path.choice([-1, 0, 1])) * rng_path.uniform(0.0002, 0.0006)
            vol = rng_path.uniform(0.0003, 0.0008)
            for _ in range(seg_len):
                if len(closes) >= len(times):
                    break
                price = price + drift + rng_path.normal(0, vol)
                closes.append(round(price, digits))

        candles = []
        prev_close = closes[0]
        for i, ts in enumerate(times):
            close = closes[i]
            open_ = prev_close
            wick = abs(rng_noise.normal(0, point * 5))
            high = round(max(open_, close) + wick, digits)
            low = round(min(open_, close) - wick, digits)
            candles.append(make_candle(
                symbol=symbol,
                timeframe=Timeframe.H1,
                bar_open=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                tick_volume=int(rng_noise.integers(500, 2500)),
            ))
            prev_close = close
        return candles

    @staticmethod
    def _aggregate(h1: list, group: int, tf: Timeframe, symbol: str) -> list:
        out = []
        for i in range(0, len(h1) - group + 1, group):
            chunk = h1[i:i + group]
            out.append(make_candle(
                symbol=symbol,
                timeframe=tf,
                bar_open=chunk[0].time_open,
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
                tick_volume=sum(c.tick_volume for c in chunk),
            ))
        return out

    def load(self, symbol: str, timeframes: list[Timeframe]) -> dict[Timeframe, LoadedSeries]:
        h1 = self._h1_candles(symbol)
        metadata = self.symbol_metadata or SymbolMetadata(
            symbol=symbol, digits=5, point=0.00001, broker="SYNTHETIC", session_timezone="UTC"
        )
        source = DataSourceInfo(
            provider="SYNTHETIC", volume_type="tick_volume", feed="SYNTHETIC",
            broker="SYNTHETIC", broker_utc_offset_hours=0.0,
        )

        results: dict[Timeframe, LoadedSeries] = {}
        for tf in timeframes:
            if tf == Timeframe.H1:
                candles = list(h1)
            else:
                group = _GROUP_SIZE.get(tf)
                if group is None:
                    raise ValueError(f"Synthetic provider cannot build {tf.value}")
                candles = self._aggregate(h1, group, tf, symbol)

            quality = DataQualityReport(source="SYNTHETIC", total_bars=len(candles))
            if candles:
                quality.date_range = (candles[0].time_close, candles[-1].time_close)
            results[tf] = LoadedSeries(
                timeframe=tf, candles=candles,
                symbol_metadata=metadata, data_source=source, quality=quality,
            )
        return results
