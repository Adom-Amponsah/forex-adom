"""
MetaTrader 5 data provider (official `MetaTrader5` Python package).

This talks to a locally running MT5 terminal over IPC - no CSV exports. It is
Windows-only and requires the terminal to be installed and logged into the
target account.

Two ways to authenticate:
  1. Preferred: the terminal is already logged in. Just point us at it.
  2. Pass credentials (via env `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER`).

MT5 timestamps are broker server time; we convert to UTC using the broker
offset (auto-detected from a live tick, or configured explicitly).
"""
from __future__ import annotations

import os
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.data.models import (
    Timeframe, SymbolMetadata, DataSourceInfo, DataQualityReport, make_candle,
)
from app.data.provider import LoadedSeries, ProviderError

# Common install locations, checked in order when no path is configured.
_CANDIDATE_PATHS = [
    r"C:\Program Files\OANDA MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
]


def _find_terminal() -> Optional[str]:
    for p in _CANDIDATE_PATHS:
        if Path(p).exists():
            return p
    return None


def _copy_rates_with_retry(mt5, symbol: str, tf_const, count: int,
                           attempts: int = 8, delay: float = 0.5):
    """
    Fetch bars, tolerating two common MT5 quirks:

    - Right after a symbol is selected, `copy_rates_from_pos` can fail with
      "Terminal: Call failed" until history finishes downloading, so we retry.
    - Asking for more bars than the terminal actually holds fails outright for
      some timeframes, so we progressively shrink the request.
    """
    current = count
    rates = None
    for _ in range(attempts):
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, current)
        if rates is not None and len(rates) > 0:
            return rates
        if current > 500:
            current = max(500, current // 2)
        _time.sleep(delay)
    return rates


class Mt5DataProvider:
    """Loads candles from a running MetaTrader 5 terminal."""

    def __init__(
        self,
        terminal_path: Optional[str] = None,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        bars: Optional[int] = None,
        broker_offset_hours: Optional[float] = None,
        symbol_suffix: str = "",
        request_timeout_ms: int = 60000,
    ):
        from app.config.settings import config

        cfg = config.mt5
        self.terminal_path = terminal_path or cfg.terminal_path or _find_terminal()
        self.bars = bars or cfg.default_bars
        self.broker_offset_hours = (
            broker_offset_hours if broker_offset_hours is not None else cfg.broker_offset_hours
        )
        self.symbol_suffix = symbol_suffix or cfg.symbol_suffix
        self.request_timeout_ms = request_timeout_ms

        self.login = login if login is not None else _env_int("MT5_LOGIN")
        self.password = password or os.environ.get("MT5_PASSWORD")
        self.server = server or os.environ.get("MT5_SERVER")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _tf_map(mt5):
        return {
            Timeframe.M1: mt5.TIMEFRAME_M1,
            Timeframe.M5: mt5.TIMEFRAME_M5,
            Timeframe.M15: mt5.TIMEFRAME_M15,
            Timeframe.M30: mt5.TIMEFRAME_M30,
            Timeframe.H1: mt5.TIMEFRAME_H1,
            Timeframe.H4: mt5.TIMEFRAME_H4,
            Timeframe.D1: mt5.TIMEFRAME_D1,
            Timeframe.W1: mt5.TIMEFRAME_W1,
            Timeframe.MN1: mt5.TIMEFRAME_MN1,
        }

    def _connect(self, mt5):
        kwargs = {"timeout": self.request_timeout_ms}
        if self.login and self.password and self.server:
            kwargs.update(login=self.login, password=self.password, server=self.server)

        args = (self.terminal_path,) if self.terminal_path else ()
        if not mt5.initialize(*args, **kwargs):
            code, msg = mt5.last_error()
            hint = ""
            if code == -10005:
                hint = (" (IPC timeout - is the terminal busy, showing a modal dialog, "
                        "or running as administrator?)")
            elif code == -6:
                hint = (" (not authorized - log the terminal into an account, or set "
                        "MT5_LOGIN/MT5_PASSWORD/MT5_SERVER)")
            raise ProviderError(f"MT5 initialize() failed: {code} {msg}{hint}")

        if not mt5.terminal_info().connected:
            raise ProviderError("MT5 terminal is not connected to a trade server")

    def _detect_offset(self, mt5, symbol: str) -> float:
        """
        Determine the broker's server-time offset from UTC (hours).

        A live tick gives the exact offset, but it goes stale when the market is
        closed (weekends). We therefore fall back to inferring the offset from
        the weekly gap: the FX week reopens at 21:00 UTC on Sunday, so the first
        H1 bar after the weekend gap pins the offset - and stays correct across
        DST changes.
        """
        if self.broker_offset_hours is not None:
            return float(self.broker_offset_hours)

        now = _time.time()
        tick = mt5.symbol_info_tick(symbol)
        if tick and tick.time and abs(tick.time - now) <= 300:
            return float(round((tick.time - now) / 3600))

        inferred = self._infer_offset_from_week(mt5, symbol)
        if inferred is not None:
            return inferred
        return 0.0

    @staticmethod
    def _infer_offset_from_week(mt5, symbol: str) -> Optional[float]:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 500)
        if rates is None or len(rates) < 2:
            return None

        times = [int(r["time"]) for r in rates]
        first_after_gap = None
        for i in range(len(times) - 1, 0, -1):
            if times[i] - times[i - 1] > 24 * 3600:  # weekend gap
                first_after_gap = times[i]
                break
        if first_after_gap is None:
            return None

        dt = datetime.fromtimestamp(first_after_gap, tz=timezone.utc)
        days_since_sunday = (dt.weekday() + 1) % 7  # Mon=0 -> 1, Sun=6 -> 0
        week_open = dt.replace(hour=21, minute=0, second=0, microsecond=0) - timedelta(days=days_since_sunday)
        offset = round((first_after_gap - week_open.timestamp()) / 3600)
        return float(offset) if -12 <= offset <= 14 else None

    def _resolve_symbol(self, mt5, symbol: str) -> Optional[str]:
        """
        Resolve a logical symbol (e.g. "EURUSD") to the broker's actual name.

        Brokers suffix symbols inconsistently (e.g. OANDA Global Markets exposes
        "EURUSD.sml"). We try: exact -> configured suffix -> wildcard search,
        picking the lexicographically smallest match for determinism.
        """
        if mt5.symbol_info(symbol) is not None:
            return symbol
        if self.symbol_suffix:
            candidate = symbol + self.symbol_suffix
            if mt5.symbol_info(candidate) is not None:
                return candidate
        matches = mt5.symbols_get(f"*{symbol}*")
        if matches:
            names = sorted(m.name for m in matches)
            # Prefer an exact prefix match (e.g. EURUSD.sml over EURUSDX)
            prefixed = [n for n in names if n.upper().startswith(symbol.upper())]
            return (prefixed or names)[0]
        return None

    # -- provider API ------------------------------------------------------

    def load(self, symbol: str, timeframes: list[Timeframe]) -> dict[Timeframe, LoadedSeries]:
        try:
            import MetaTrader5 as mt5
        except ImportError as e:  # pragma: no cover - platform dependent
            raise ProviderError(
                "The MetaTrader5 package is not installed (pip install MetaTrader5). "
                "It is Windows-only and requires the MT5 terminal."
            ) from e

        tf_map = self._tf_map(mt5)

        self._connect(mt5)
        try:
            resolved = self._resolve_symbol(mt5, symbol)
            if resolved is None:
                raise ProviderError(
                    f"MT5 symbol '{symbol}' not found in the terminal. "
                    f"Set mt5.symbol_suffix if your broker suffixes symbols."
                )
            mt5_symbol = resolved
            if not mt5.symbol_select(mt5_symbol, True):
                raise ProviderError(f"MT5 symbol '{mt5_symbol}' cannot be selected")

            # Wait for the terminal to finish syncing the symbol's quotes/history.
            for _ in range(20):
                if mt5.symbol_info_tick(mt5_symbol) is not None:
                    break
                _time.sleep(0.25)

            info = mt5.symbol_info(mt5_symbol)
            metadata = SymbolMetadata(
                symbol=symbol,
                digits=info.digits,
                point=info.point,
                broker="MT5",
                session_timezone="UTC",
            )

            offset = self._detect_offset(mt5, mt5_symbol)

            server_now = None
            tick = mt5.symbol_info_tick(mt5_symbol)
            if tick is not None:
                server_now = tick.time

            results: dict[Timeframe, LoadedSeries] = {}
            for tf in timeframes:
                tf_const = tf_map.get(tf)
                if tf_const is None:
                    raise ProviderError(f"MT5 does not support timeframe {tf.value}")

                rates = _copy_rates_with_retry(mt5, mt5_symbol, tf_const, self.bars)
                if rates is None:
                    code, msg = mt5.last_error()
                    raise ProviderError(f"MT5 copy_rates_from_pos failed for {tf.value}: {code} {msg}")

                tf_seconds = tf.to_seconds()
                candles = []
                for r in rates:
                    server_open = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
                    # Drop the still-forming bar (its close is in the future).
                    if server_now is not None and (int(r["time"]) + tf_seconds) > server_now:
                        continue
                    utc_open = server_open - timedelta(hours=offset)
                    candles.append(make_candle(
                        symbol=symbol,
                        timeframe=tf,
                        bar_open=utc_open,
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                        tick_volume=int(r["tick_volume"]),
                        spread_points=int(r["spread"]),
                        real_volume=int(r["real_volume"]),
                        source="MT5",
                    ))

                candles.sort(key=lambda c: c.time_close)

                quality = DataQualityReport(source="MT5_IPC", total_bars=len(candles))
                if candles:
                    quality.date_range = (candles[0].time_close, candles[-1].time_close)

                results[tf] = LoadedSeries(
                    timeframe=tf,
                    candles=candles,
                    symbol_metadata=metadata,
                    data_source=DataSourceInfo(
                        provider="MT5",
                        volume_type="tick_volume",
                        feed="MT5_IPC",
                        broker=metadata.broker,
                        broker_utc_offset_hours=float(offset),
                    ),
                    quality=quality,
                )
            return results
        finally:
            mt5.shutdown()


def _env_int(name: str) -> Optional[int]:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
