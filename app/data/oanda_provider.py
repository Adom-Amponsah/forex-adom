"""
OANDA v20 REST data provider.

Pulls historical candles from the OANDA v20 API (fxTrade / fxTrade Practice).

Why OANDA is the primary source:
  - Real broker feed with deep history (back to ~2005).
  - Timestamps are UTC RFC3339 and denote the bar's OPEN time.
  - Each candle carries a `complete` flag, so forming bars are identified by the
    API rather than guessed at - no more `drop_last_row` heuristics.
  - Native granularities cover everything we need (H1/H4/D/W/M and finer).

Auth: a personal access token, read from the `OANDA_API_TOKEN` environment
variable (or passed explicitly). The instrument-level candles endpoint does not
require an account id.
"""
from __future__ import annotations

import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests

from app.data.models import (
    Timeframe, SymbolMetadata, DataSourceInfo, DataQualityReport, make_candle,
)
from app.data.provider import LoadedSeries, ProviderError

_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

# OANDA granularity codes. Daily/weekly/monthly are single letters.
_GRANULARITY = {
    Timeframe.M1: "M1",
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.M30: "M30",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
    Timeframe.MN1: "M",
}


def to_oanda_instrument(symbol: str) -> str:
    """EURUSD -> EUR_USD, XAUUSD -> XAU_USD, EUR_USD -> EUR_USD."""
    s = symbol.upper().replace("/", "_")
    if "_" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}_{s[3:]}"
    if len(s) == 7 and s[:3] in {"XAU", "XAG", "XPT", "XPD"}:  # XAUUSD
        return f"{s[:3]}_{s[3:]}"
    raise ProviderError(f"Cannot map symbol '{symbol}' to an OANDA instrument")


def parse_oanda_time(value: str) -> datetime:
    """Parse an OANDA RFC3339 timestamp (nanosecond precision) into UTC."""
    base = value[:19]
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


class OandaDataProvider:
    """Loads candles from the OANDA v20 REST API."""

    def __init__(
        self,
        environment: Optional[str] = None,
        token: Optional[str] = None,
        price: Optional[str] = None,
        bars: Optional[int] = None,
        request_timeout: Optional[float] = None,
        max_bars_per_request: Optional[int] = None,
    ):
        from app.config.settings import config

        cfg = config.oanda
        self.environment = (environment or cfg.environment).lower()
        self.price = (price or cfg.price).upper()
        self.bars = bars or cfg.default_bars
        self.request_timeout = request_timeout or cfg.request_timeout
        self.max_bars_per_request = max_bars_per_request or cfg.max_bars_per_request

        self.token = token or os.environ.get("OANDA_API_TOKEN")
        if not self.token:
            raise ProviderError(
                "OANDA_API_TOKEN is not set. Create a free fxTrade Practice "
                "account, generate a personal access token, and export it as "
                "OANDA_API_TOKEN."
            )

        if self.environment not in _BASE_URLS:
            raise ProviderError(
                f"Unknown OANDA environment '{self.environment}' (use 'practice' or 'live')"
            )
        self.base_url = _BASE_URLS[self.environment]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept-Datetime-Format": "RFC3339",
            "Content-Type": "application/json",
        })

    # -- low level ---------------------------------------------------------

    def _request(self, instrument: str, granularity: str, count: int,
                 to: Optional[str] = None) -> list[dict]:
        params = {
            "granularity": granularity,
            "count": min(count, self.max_bars_per_request),
            "price": self.price,
        }
        if to:
            params["to"] = to

        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=self.request_timeout)
            except requests.RequestException as e:
                last_err = e
                _time.sleep(0.5 * (attempt + 1))
                continue

            if resp.status_code == 200:
                return resp.json().get("candles", [])
            if resp.status_code in (401, 403):
                raise ProviderError(
                    f"OANDA auth failed ({resp.status_code}): check OANDA_API_TOKEN "
                    f"and that it matches the '{self.environment}' environment."
                )
            if resp.status_code == 429:
                last_err = ProviderError("OANDA rate limit (429)")
                _time.sleep(1.0 * (attempt + 1))
                continue
            raise ProviderError(
                f"OANDA request failed ({resp.status_code}): {resp.text[:200]}"
            )
        raise ProviderError(f"OANDA request failed after retries: {last_err}")

    def _fetch(self, instrument: str, granularity: str, want: int) -> list[dict]:
        """Fetch up to `want` candles, paginating backwards past the 5000 cap."""
        collected: list[dict] = []
        cursor: Optional[str] = None

        while len(collected) < want:
            remaining = want - len(collected)
            batch = self._request(instrument, granularity, remaining, to=cursor)
            if not batch:
                break
            collected = batch + collected
            if len(batch) < min(remaining, self.max_bars_per_request):
                break  # exhausted history
            # Step the cursor back to just before the earliest candle we have.
            earliest = parse_oanda_time(batch[0]["time"])
            cursor = (earliest - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        return collected

    # -- provider API ------------------------------------------------------

    def load(
        self, symbol: str, timeframes: list[Timeframe]
    ) -> dict[Timeframe, LoadedSeries]:
        instrument = to_oanda_instrument(symbol)
        metadata = SymbolMetadata(
            symbol=symbol,
            digits=3 if "JPY" in symbol.upper() else 5,
            point=0.001 if "JPY" in symbol.upper() else 0.00001,
            broker=f"OANDA_{self.environment}",
            session_timezone="UTC",
        )

        results: dict[Timeframe, LoadedSeries] = {}
        for tf in timeframes:
            gran = _GRANULARITY.get(tf)
            if gran is None:
                raise ProviderError(f"OANDA does not support timeframe {tf.value}")

            raw = self._fetch(instrument, gran, self.bars)

            candles = []
            dropped_forming = 0
            for c in raw:
                if not c.get("complete", True):
                    dropped_forming += 1
                    continue
                mid = c.get(self.price.lower()) or c.get("mid") or c.get("bid")
                if not mid:
                    continue
                candles.append(make_candle(
                    symbol=symbol,
                    timeframe=tf,
                    bar_open=parse_oanda_time(c["time"]),
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    tick_volume=int(c.get("volume", 0) or 0),
                    source="OANDA",
                ))

            candles.sort(key=lambda k: k.time_close)

            quality = DataQualityReport(
                source=f"OANDA_{self.environment}",
                total_bars=len(candles),
            )
            if candles:
                quality.date_range = (candles[0].time_close, candles[-1].time_close)

            data_source = DataSourceInfo(
                provider="OANDA",
                volume_type="tick_volume",
                feed=f"OANDA_{self.environment}",
                broker=f"OANDA_{self.environment}",
                broker_utc_offset_hours=0.0,
                weekly_open_verified=False,
            )

            results[tf] = LoadedSeries(
                timeframe=tf,
                candles=candles,
                symbol_metadata=metadata,
                data_source=data_source,
                quality=quality,
            )

        return results
