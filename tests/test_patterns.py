"""
Deterministic pattern-geometry tests.

Patterns are constructed from hand-shaped price series so the expected geometry
is unambiguous, then checked for correct detection, determinism, and causality.
"""
from datetime import datetime, timedelta, timezone

from app.data.models import Timeframe, make_candle
from app.structure.swings import get_confirmed_swings_up_to
from app.structure.structure import classify_swings
from app.patterns.geometry import detect_patterns
from app.patterns.models import PatternType, PatternStatus

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candles(closes: list[float]):
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi = max(prev, c) + 0.00005
        lo = min(prev, c) - 0.00005
        out.append(make_candle("EURUSD", Timeframe.H1, BASE + timedelta(hours=i),
                               prev, hi, lo, c))
        prev = c
    return out


def _interp(waypoints: list[tuple[int, float]], n: int) -> list[float]:
    """Piecewise-linear price series through (index, price) waypoints."""
    closes = []
    for i in range(n):
        for j in range(len(waypoints) - 1):
            x0, y0 = waypoints[j]
            x1, y1 = waypoints[j + 1]
            if x0 <= i <= x1:
                t = 0 if x1 == x0 else (i - x0) / (x1 - x0)
                closes.append(round(y0 + (y1 - y0) * t, 5))
                break
    return closes


def _detect(closes: list[float], atr: float = 0.002):
    candles = _candles(closes)
    swings = get_confirmed_swings_up_to(candles, len(candles) - 1, 5, 5)
    labeled = classify_swings(swings)
    return candles, detect_patterns(labeled, candles, atr, Timeframe.H1, "EURUSD")


def test_double_top_detected():
    # up -> down -> up (equal high) -> down
    closes = _interp([(0, 1.100), (10, 1.120), (18, 1.110), (26, 1.120), (36, 1.100)], 37)
    _, patterns = _detect(closes)
    dts = [p for p in patterns if p.pattern_type == PatternType.DOUBLE_TOP]
    assert dts, "expected a double top"
    p = dts[0]
    assert abs(p.points[0].price - p.points[2].price) < 1e-6  # equal highs
    assert p.neckline == p.points[1].price
    assert p.direction.value == "bearish"


def test_double_bottom_detected():
    # down -> up -> down (equal low) -> up
    closes = _interp([(0, 1.120), (10, 1.100), (18, 1.110), (26, 1.100), (36, 1.120)], 37)
    _, patterns = _detect(closes)
    dbs = [p for p in patterns if p.pattern_type == PatternType.DOUBLE_BOTTOM]
    assert dbs, "expected a double bottom"
    assert dbs[0].direction.value == "bullish"


def test_head_and_shoulders_detected():
    # LS(1.115) T1(1.110) Head(1.125) T2(1.110) RS(1.115)
    closes = _interp([
        (0, 1.108), (8, 1.115), (14, 1.110), (22, 1.125),
        (30, 1.110), (38, 1.115), (46, 1.108),
    ], 47)
    _, patterns = _detect(closes)
    hs = [p for p in patterns if p.pattern_type == PatternType.HEAD_SHOULDERS]
    assert hs, "expected head & shoulders"
    head = next(pt for pt in hs[0].points if pt.role == "head")
    assert head.price == max(pt.price for pt in hs[0].points)


def test_ascending_triangle_detected():
    # flat highs (1.120, 1.120), rising lows (1.100, 1.110)
    closes = _interp([
        (0, 1.110), (8, 1.120), (16, 1.100), (24, 1.120), (32, 1.110), (40, 1.120),
    ], 41)
    _, patterns = _detect(closes)
    tris = [p for p in patterns if p.pattern_type == PatternType.ASCENDING_TRIANGLE]
    assert tris, "expected an ascending triangle"
    assert tris[0].direction.value == "bullish"


def test_patterns_are_deterministic():
    closes = _interp([(0, 1.100), (10, 1.120), (18, 1.110), (26, 1.120), (36, 1.100)], 37)
    _, a = _detect(closes)
    _, b = _detect(closes)
    dump_a = [p.model_dump(mode="json") for p in a]
    dump_b = [p.model_dump(mode="json") for p in b]
    assert dump_a == dump_b


def test_pattern_ids_are_stable():
    closes = _interp([(0, 1.100), (10, 1.120), (18, 1.110), (26, 1.120), (36, 1.100)], 37)
    _, a = _detect(closes)
    _, b = _detect(closes)
    assert [p.id for p in a] == [p.id for p in b]


def test_confirmation_is_after_pivots():
    """A confirmed pattern must be confirmed by a candle after its last pivot."""
    closes = _interp([(0, 1.100), (10, 1.120), (18, 1.110), (26, 1.120), (36, 1.100)], 37)
    _, patterns = _detect(closes)
    for p in patterns:
        if p.status == PatternStatus.CONFIRMED:
            last_pivot = max(pt.time for pt in p.points)
            assert p.confirmation_time >= last_pivot


def test_evidence_ids_present():
    closes = _interp([(0, 1.100), (10, 1.120), (18, 1.110), (26, 1.120), (36, 1.100)], 37)
    _, patterns = _detect(closes)
    assert patterns
    for p in patterns:
        assert p.evidence_ids, f"{p.pattern_type} has no evidence"
        assert all(isinstance(i, str) and i for i in p.evidence_ids)
