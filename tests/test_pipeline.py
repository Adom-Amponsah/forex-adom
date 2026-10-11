"""
Full-pipeline, determinism, and provider tests.

These exercise the real end-to-end path (provider -> alignment -> market state)
rather than individual functions, which is where the interesting bugs live.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.data.models import (
    Timeframe, make_candle,
)
from app.data.synthetic_provider import SyntheticDataProvider
from app.replay.engine import run_single_analysis


TF_SET = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
CONTEXT = [Timeframe.D1]
STRUCTURE = [Timeframe.H4]


def _state(provider, decision_close):
    return run_single_analysis(
        provider, "EURUSD", decision_close, TF_SET, Timeframe.H1, CONTEXT, STRUCTURE
    )


class TestFullPipelineDeterminism:
    def test_repeated_runs_identical(self):
        provider = SyntheticDataProvider(seed=7, h1_bars=800)
        decision_close = provider.load("EURUSD", TF_SET)[Timeframe.H1].candles[-1].time_close
        assert _state(provider, decision_close) == _state(provider, decision_close)

    def test_no_wallclock_leak(self):
        """No field in the output may depend on the wall clock."""
        provider = SyntheticDataProvider(seed=11, h1_bars=800)
        decision_close = provider.load("EURUSD", TF_SET)[Timeframe.H1].candles[-1].time_close
        # The synthetic series starts in 2024; nothing should be stamped "today".
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today not in _state(provider, decision_close)


class TestFutureDataInvariance:
    """Appending candles after T must not change the state at T."""

    def _state_at(self, provider, decision_close):
        return json.loads(_state(provider, decision_close))

    def test_appending_does_not_change_past(self):
        short = SyntheticDataProvider(seed=7, h1_bars=600)
        tfs_h1 = short.load("EURUSD", TF_SET)[Timeframe.H1].candles
        decision_close = tfs_h1[400].time_close

        long = SyntheticDataProvider(seed=7, h1_bars=900)

        a = self._state_at(short, decision_close)
        b = self._state_at(long, decision_close)
        assert a == b

    def test_structure_ignores_future(self):
        """A swing confirmed after T must not appear in the state at T."""
        provider = SyntheticDataProvider(seed=3, h1_bars=800)
        h1 = provider.load("EURUSD", TF_SET)[Timeframe.H1].candles
        decision_close = h1[300].time_close
        state = json.loads(_state(provider, decision_close))

        h1_state = state["timeframes"]["H1"]
        latest_close = datetime.fromisoformat(
            h1_state["latest_closed_candle"]["time_close"].replace("Z", "+00:00")
        )
        assert latest_close == decision_close
        # No event may be confirmed after the decision time.
        for ev in state["structural_events"]:
            assert datetime.fromisoformat(ev["confirmation_time"].replace("Z", "+00:00")) <= decision_close


class TestCausalEvents:
    def test_events_reference_earlier_swings(self):
        """Every break must occur strictly after its reference swing confirmed."""
        provider = SyntheticDataProvider(seed=7, h1_bars=1500)
        decision_close = provider.load("EURUSD", TF_SET)[Timeframe.H1].candles[-1].time_close
        state = json.loads(_state(provider, decision_close))

        # Map swing id -> confirmation time across timeframes.
        swings = {}
        for tf_state in state["timeframes"].values():
            ext = (tf_state.get("structure") or {}).get("external") or {}
            for key in ("last_high", "last_low", "protected_high", "protected_low"):
                ls = ext.get(key)
                if ls:
                    swings[ls["swing"]["id"]] = ls["swing"]["confirmation_time"]

        for ev in state["structural_events"]:
            ref = ev.get("reference_id")
            if ref in swings:
                ref_time = datetime.fromisoformat(swings[ref].replace("Z", "+00:00"))
                ev_time = datetime.fromisoformat(ev["confirmation_time"].replace("Z", "+00:00"))
                assert ev_time > ref_time

    def test_no_duplicate_break_of_same_level(self):
        provider = SyntheticDataProvider(seed=7, h1_bars=1500)
        decision_close = provider.load("EURUSD", TF_SET)[Timeframe.H1].candles[-1].time_close
        state = json.loads(_state(provider, decision_close))
        seen = set()
        for ev in state["structural_events"]:
            key = (ev["reference_id"], ev["type"])
            assert key not in seen, f"level {key} broke more than once"
            seen.add(key)


class TestZoneLifecycle:
    def _candle(self, i, o, h, low, c):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return make_candle("EURUSD", Timeframe.H1, base + timedelta(hours=i), o, h, low, c)

    def test_support_zone_breaks_and_flips(self):
        from app.zones.engine import Zone, ZoneRole, ZoneStatus, update_zone_status
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        zone = Zone(
            id="Z", timeframe=Timeframe.H1, role=ZoneRole.SUPPORT,
            low=1.1000, high=1.1005, first_seen=base,
        )
        # touch, then close below (broken), then close back above (flipped)
        candles = [
            self._candle(1, 1.1010, 1.1015, 1.1002, 1.1012),   # touch
            self._candle(2, 1.1000, 1.1002, 1.0980, 1.0985),   # close below -> broken
            self._candle(3, 1.0990, 1.1010, 1.0988, 1.1008),   # close above -> flipped
        ]
        updated = update_zone_status([zone], candles)[0]
        assert updated.status == ZoneStatus.FLIPPED
        assert updated.touch_count >= 1

    def test_resistance_zone_breaks(self):
        from app.zones.engine import Zone, ZoneRole, ZoneStatus, update_zone_status
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        zone = Zone(
            id="Z", timeframe=Timeframe.H1, role=ZoneRole.RESISTANCE,
            low=1.1000, high=1.1005, first_seen=base,
        )
        candles = [self._candle(1, 1.1000, 1.1015, 1.0998, 1.1012)]  # close above
        updated = update_zone_status([zone], candles)[0]
        assert updated.status == ZoneStatus.BROKEN


class TestPreviousLevels:
    def test_previous_day_is_last_closed_day(self):
        from app.indicators.levels import calculate_previous_levels
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # D1 bars: Jan 1 (high 1.20), Jan 2 (high 1.30)
        c1 = make_candle("EURUSD", Timeframe.D1, base, 1.10, 1.20, 1.05, 1.15)
        c2 = make_candle("EURUSD", Timeframe.D1, base + timedelta(days=1), 1.15, 1.30, 1.10, 1.25)
        # as_of during Jan 3 (so Jan 2 is the last closed day)
        as_of = base + timedelta(days=2, hours=10)
        prev_day, prev_week = calculate_previous_levels([c1, c2], as_of, 0.0)
        assert prev_day.high == 1.30
        assert prev_day.source_candle_id == c2.id

    def test_current_day_excluded(self):
        from app.indicators.levels import calculate_previous_levels
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        c1 = make_candle("EURUSD", Timeframe.D1, base, 1.10, 1.20, 1.05, 1.15)
        # as_of during Jan 1 - the only bar hasn't closed yet
        as_of = base + timedelta(hours=10)
        prev_day, _ = calculate_previous_levels([c1], as_of, 0.0)
        assert prev_day.high is None


class TestOandaProvider:
    def test_symbol_mapping(self):
        from app.data.oanda_provider import to_oanda_instrument
        assert to_oanda_instrument("EURUSD") == "EUR_USD"
        assert to_oanda_instrument("USDJPY") == "USD_JPY"
        assert to_oanda_instrument("XAUUSD") == "XAU_USD"
        assert to_oanda_instrument("EUR_USD") == "EUR_USD"

    def test_time_parsing(self):
        from app.data.oanda_provider import parse_oanda_time
        dt = parse_oanda_time("2025-12-01T00:00:00.000000000Z")
        assert dt == datetime(2025, 12, 1, tzinfo=timezone.utc)

    def test_load_drops_forming_candle(self, monkeypatch):
        from app.data import oanda_provider as mod

        payload = {
            "candles": [
                {"time": "2025-12-01T00:00:00.000000000Z", "volume": 10, "complete": True,
                 "mid": {"o": "1.1", "h": "1.2", "l": "1.05", "c": "1.15"}},
                {"time": "2025-12-01T01:00:00.000000000Z", "volume": 5, "complete": False,
                 "mid": {"o": "1.15", "h": "1.16", "l": "1.14", "c": "1.155"}},
            ]
        }

        class FakeResp:
            status_code = 200
            def json(self):
                return payload

        provider = mod.OandaDataProvider(token="test-token")
        monkeypatch.setattr(provider.session, "get", lambda *a, **k: FakeResp())

        loaded = provider.load("EURUSD", [Timeframe.H1])
        candles = loaded[Timeframe.H1].candles
        assert len(candles) == 1  # forming candle dropped
        assert candles[0].time_close == datetime(2025, 12, 1, 1, tzinfo=timezone.utc)

    def test_missing_token_errors(self, monkeypatch):
        from app.data import oanda_provider as mod
        from app.data.provider import ProviderError
        monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
        with pytest.raises(ProviderError):
            mod.OandaDataProvider(token=None)


class TestMt5Provider:
    """MT5 provider tests using a fake MetaTrader5 module (no terminal needed)."""

    def _fake_mt5(self, base_epoch, n_bars, tick_time):
        import types

        rates = []
        for i in range(n_bars):
            rates.append({
                "time": base_epoch + i * 3600,
                "open": 1.1000 + i * 0.0010, "high": 1.1005 + i * 0.0010,
                "low": 1.0995 + i * 0.0010, "close": 1.1002 + i * 0.0010,
                "tick_volume": 100 + i, "spread": 8, "real_volume": 0,
            })

        mod = types.SimpleNamespace()
        for name in ("TIMEFRAME_M1", "TIMEFRAME_M5", "TIMEFRAME_M15", "TIMEFRAME_M30",
                     "TIMEFRAME_H1", "TIMEFRAME_H4", "TIMEFRAME_D1", "TIMEFRAME_W1",
                     "TIMEFRAME_MN1"):
            setattr(mod, name, name)
        mod.initialize = lambda *a, **k: True
        mod.shutdown = lambda: None
        mod.last_error = lambda: (0, "ok")
        mod.terminal_info = lambda: types.SimpleNamespace(connected=True)
        mod.symbol_select = lambda s, enable: True
        mod.symbol_info = lambda s: types.SimpleNamespace(digits=5, point=0.00001)
        mod.symbol_info_tick = lambda s: types.SimpleNamespace(time=tick_time)
        mod.copy_rates_from_pos = lambda s, tf, pos, count: rates
        return mod

    def test_load_converts_to_utc_and_drops_forming(self, monkeypatch):
        import sys
        from app.data.mt5_provider import Mt5DataProvider

        monkeypatch.delenv("MT5_LOGIN", raising=False)
        monkeypatch.delenv("MT5_PASSWORD", raising=False)
        monkeypatch.delenv("MT5_SERVER", raising=False)

        base = datetime(2025, 12, 1, 0, 0, tzinfo=timezone.utc)
        base_epoch = int(base.timestamp())
        # server_now sits mid-way through the last bar, so the last bar is forming
        fake = self._fake_mt5(base_epoch, 3, tick_time=base_epoch + 2 * 3600 + 1800)
        monkeypatch.setitem(sys.modules, "MetaTrader5", fake)

        provider = Mt5DataProvider(terminal_path="X", broker_offset_hours=2)
        candles = provider.load("EURUSD", [Timeframe.H1])[Timeframe.H1].candles

        assert len(candles) == 2  # forming bar dropped
        # server 00:00 with offset +2 -> UTC 22:00 previous day
        assert candles[0].time_open == base - timedelta(hours=2)
        assert candles[0].time_close == base - timedelta(hours=1)
        assert candles[1].close == 1.1012

    def test_initialize_failure_is_reported(self, monkeypatch):
        import sys
        from app.data.mt5_provider import Mt5DataProvider
        from app.data.provider import ProviderError

        monkeypatch.delenv("MT5_LOGIN", raising=False)
        mod = self._fake_mt5(0, 0, 0)
        mod.initialize = lambda *a, **k: False
        mod.last_error = lambda: (-6, "Terminal: Authorization failed")
        monkeypatch.setitem(sys.modules, "MetaTrader5", mod)

        provider = Mt5DataProvider(terminal_path="X")
        with pytest.raises(ProviderError, match="not authorized"):
            provider.load("EURUSD", [Timeframe.H1])

    def test_offset_inferred_from_weekend_gap(self):
        """Fri 23:00 server -> Mon 00:00 server implies a UTC+3 broker."""
        import types
        from app.data.mt5_provider import Mt5DataProvider

        # Friday 2026-10-02 23:00 and Monday 2026-10-05 00:00 (server-rendered)
        fri = int(datetime(2026, 10, 2, 23, tzinfo=timezone.utc).timestamp())
        mon = int(datetime(2026, 10, 5, 0, tzinfo=timezone.utc).timestamp())
        times = [fri - 3600, fri, mon, mon + 3600]

        fake = types.SimpleNamespace(
            TIMEFRAME_H1="H1",
            copy_rates_from_pos=lambda s, tf, pos, count: [{"time": t} for t in times],
        )
        assert Mt5DataProvider._infer_offset_from_week(fake, "EURUSD") == 3.0

    def test_offset_uses_configured_value(self):
        from app.data.mt5_provider import Mt5DataProvider
        p = Mt5DataProvider(terminal_path="X", broker_offset_hours=2.0)
        assert p._detect_offset(None, "EURUSD") == 2.0
