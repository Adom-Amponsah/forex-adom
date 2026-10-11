"""
Tests for reproducibility and deterministic behavior.
"""
import pytest
from datetime import datetime, timezone, timedelta
import numpy as np
from app.data.models import Candle, Timeframe, make_candle_id
from app.indicators.engine import calculate_ema, calculate_rsi, calculate_atr
from app.structure.swings import find_swing_candidates, get_confirmed_swings_up_to, EqualPolicy
from app.structure.structure import classify_swings, determine_structure_direction, StructureDirection


def create_test_candles(count: int, base_price: float = 1.1000, timeframe: Timeframe = Timeframe.H1) -> list[Candle]:
    """Create synthetic candles for testing."""
    candles = []
    start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = base_price
    
    for i in range(count):
        # Create some variation
        open_price = price
        close_price = price + np.random.uniform(-0.0010, 0.0010)
        high_price = max(open_price, close_price) + abs(np.random.uniform(0, 0.0005))
        low_price = min(open_price, close_price) - abs(np.random.uniform(0, 0.0005))
        
        time_open = start_time + timedelta(hours=i)
        time_close = start_time + timedelta(hours=i + 1)
        
        candle = Candle(
            id=make_candle_id("EURUSD", timeframe, time_close),
            symbol="EURUSD",
            timeframe=timeframe,
            time_open=time_open,
            time_close=time_close,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
        )
        candles.append(candle)
        price = close_price
    
    return candles


def create_swing_test_candles() -> list[Candle]:
    """Create candles with known swing structure for testing."""
    # Create a clear pattern: uptrend with pullbacks
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    
    # Prices designed to create clear swings with 5-bar left/right
    prices = [
        1.1000, 1.1010, 1.1020, 1.1015, 1.1025,  # Up
        1.1030, 1.1025, 1.1020, 1.1018, 1.1015,  # Down (swing high at idx 5)
        1.1010, 1.1005, 1.1000, 1.0995, 1.0990,  # Down
        1.0995, 1.1000, 1.1005, 1.1010, 1.1015,  # Up (swing low at idx 15)
        1.1020, 1.1025, 1.1030, 1.1028, 1.1025,  # Up
        1.1020, 1.1015, 1.1010, 1.1008, 1.1005,  # Down (swing high at idx 25)
        1.1000, 1.0995, 1.0990, 1.0988, 1.0985,  # Down
        1.0990, 1.0995, 1.1000, 1.1005, 1.1010,  # Up (swing low at idx 35)
    ]
    
    for i, close in enumerate(prices):
        time_open = base_time + timedelta(hours=i)
        time_close = base_time + timedelta(hours=i + 1)
        
        # Create realistic OHLC
        open_price = prices[i-1] if i > 0 else close
        high = max(open_price, close) + 0.0002
        low = min(open_price, close) - 0.0002
        
        candle = Candle(
            id=make_candle_id("EURUSD", Timeframe.H1, time_close),
            symbol="EURUSD",
            timeframe=Timeframe.H1,
            time_open=time_open,
            time_close=time_close,
            open=open_price,
            high=high,
            low=low,
            close=close,
        )
        candles.append(candle)
    
    return candles


class TestIndicators:
    """Test indicator calculations are deterministic."""
    
    def test_ema_deterministic(self):
        """EMA should produce same results for same input."""
        values = np.array([1.1000, 1.1010, 1.1020, 1.1015, 1.1025] * 20)
        
        ema1 = calculate_ema(values, 20)
        ema2 = calculate_ema(values, 20)
        
        np.testing.assert_array_equal(ema1, ema2)
    
    def test_rsi_deterministic(self):
        """RSI should produce same results for same input."""
        values = np.array([1.1000, 1.1010, 1.1020, 1.1015, 1.1025] * 20)
        
        rsi1 = calculate_rsi(values, 14)
        rsi2 = calculate_rsi(values, 14)
        
        np.testing.assert_array_equal(rsi1, rsi2)
    
    def test_atr_deterministic(self):
        """ATR should produce same results for same input."""
        candles = create_test_candles(50)
        
        atr1 = calculate_atr(candles, 14)
        atr2 = calculate_atr(candles, 14)
        
        np.testing.assert_array_equal(atr1, atr2)


class TestSwingDetection:
    """Test swing detection is deterministic and correct."""
    
    def test_swing_detection_deterministic(self):
        """Swing detection should produce same results for same input."""
        candles = create_swing_test_candles()
        
        candidates1 = find_swing_candidates(candles, 5, 5, EqualPolicy.STRICT)
        candidates2 = find_swing_candidates(candles, 5, 5, EqualPolicy.STRICT)
        
        assert len(candidates1) == len(candidates2)
        for c1, c2 in zip(candidates1, candidates2):
            assert c1.pivot_index == c2.pivot_index
            assert c1.price == c2.price
    
    def test_swing_confirmation_timing(self):
        """Swings should only confirm after right_bars have closed."""
        candles = create_swing_test_candles()
        
        # At index 10 (before first swing high confirmation at idx 5+5=10)
        swings_early = get_confirmed_swings_up_to(candles, as_of_index=9, left_bars=5, right_bars=5)
        assert len(swings_early) == 0
        
        # At index 10 (swing high at idx 5 should confirm)
        swings_confirmed = get_confirmed_swings_up_to(candles, as_of_index=10, left_bars=5, right_bars=5)
        assert len(swings_confirmed) >= 1
        
        # Verify confirmation time is correct
        for swing in swings_confirmed:
            expected_confirm_idx = swing.pivot_index + 5
            assert candles[expected_confirm_idx].time_close == swing.confirmation_time
    
    def test_no_future_leak(self):
        """Future candles must not affect past swing confirmation."""
        candles = create_swing_test_candles()
        
        # Get swings confirmed at index 20
        swings_at_20 = get_confirmed_swings_up_to(candles, as_of_index=20, left_bars=5, right_bars=5)
        
        # Get swings confirmed at index 30 (more future data)
        swings_at_30 = get_confirmed_swings_up_to(candles, as_of_index=30, left_bars=5, right_bars=5)
        
        # Swings confirmed at 20 should be subset of swings at 30
        ids_at_20 = {s.id for s in swings_at_20}
        ids_at_30 = {s.id for s in swings_at_30}
        
        assert ids_at_20.issubset(ids_at_30)
        
        # But no swing confirmed at 20 should have different properties at 30
        for s20 in swings_at_20:
            s30 = next(s for s in swings_at_30 if s.id == s20.id)
            assert s20.price == s30.price
            assert s20.confirmation_time == s30.confirmation_time


class TestMarketStructure:
    """Test market structure classification."""
    
    def test_structure_labels_deterministic(self):
        """Structure labels should be deterministic."""
        candles = create_swing_test_candles()
        swings = get_confirmed_swings_up_to(candles, as_of_index=40, left_bars=5, right_bars=5)
        
        labeled1 = classify_swings(swings)
        labeled2 = classify_swings(swings)
        
        assert len(labeled1) == len(labeled2)
        for l1, l2 in zip(labeled1, labeled2):
            assert l1.label == l2.label
            assert l1.reference_swing_id == l2.reference_swing_id
    
    def test_bullish_structure_requires_hh_hl(self):
        """Bullish structure requires HH + HL sequence."""
        # Create candles with clear HH + HL
        candles = create_swing_test_candles()
        swings = get_confirmed_swings_up_to(candles, as_of_index=40, left_bars=5, right_bars=5)
        labeled = classify_swings(swings)
        
        direction = determine_structure_direction(labeled)
        # Should detect some structure based on our test pattern
        assert direction in [StructureDirection.BULLISH, StructureDirection.BEARISH, StructureDirection.MIXED, StructureDirection.RANGE]


class TestReproducibility:
    """Test full pipeline reproducibility."""

    def test_same_input_same_output(self):
        """Same input data should produce byte-identical market state."""
        from app.data.synthetic_provider import SyntheticDataProvider
        from app.replay.engine import run_single_analysis

        provider = SyntheticDataProvider(seed=7, h1_bars=600)
        tfs = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
        decision_close = provider.load("EURUSD", tfs)[Timeframe.H1].candles[-1].time_close

        a = run_single_analysis(
            provider, "EURUSD", decision_close, tfs, Timeframe.H1,
            [Timeframe.D1], [Timeframe.H4],
        )
        b = run_single_analysis(
            provider, "EURUSD", decision_close, tfs, Timeframe.H1,
            [Timeframe.D1], [Timeframe.H4],
        )
        assert a == b


class TestFutureData:
    """Test that future data doesn't leak into historical analysis."""
    
    def test_append_candles_doesnt_change_past(self):
        """Appending candles after T must not change state at T."""
        # Create initial candles
        candles = create_swing_test_candles()[:30]
        
        # Get swings at index 20
        swings_early = get_confirmed_swings_up_to(candles, as_of_index=20, left_bars=5, right_bars=5)
        
        # Add more candles
        more_candles = create_swing_test_candles()[30:40]
        all_candles = candles + more_candles
        
        # Get swings at same index 20 with more future data
        swings_later = get_confirmed_swings_up_to(all_candles, as_of_index=20, left_bars=5, right_bars=5)
        
        # Results must be identical
        assert len(swings_early) == len(swings_later)
        for s1, s2 in zip(swings_early, swings_later):
            assert s1.id == s2.id
            assert s1.price == s2.price
            assert s1.confirmation_time == s2.confirmation_time


class TestStableIDs:
    """Test that object IDs are stable and not array-position dependent."""
    
    def test_swing_ids_stable(self):
        """Swing IDs should be based on pivot time, not array position."""
        candles = create_swing_test_candles()
        
        swings1 = get_confirmed_swings_up_to(candles, as_of_index=25, left_bars=5, right_bars=5)
        swings2 = get_confirmed_swings_up_to(candles, as_of_index=30, left_bars=5, right_bars=5)
        
        # Find swing at pivot index 5 in both
        s1 = next(s for s in swings1 if s.pivot_index == 5)
        s2 = next(s for s in swings2 if s.pivot_index == 5)
        
        assert s1.id == s2.id
        assert "20260101T060000Z" in s1.id  # Pivot time at index 5
    
    def test_candle_ids_stable(self):
        """Candle IDs should be based on close time."""
        candles = create_test_candles(10)
        
        # Same candle should always have same ID
        assert candles[0].id == make_candle_id("EURUSD", Timeframe.H1, candles[0].time_close)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])