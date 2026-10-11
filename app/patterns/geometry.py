"""
Deterministic pattern geometry.

Everything here is pure geometry over confirmed swings and closed candles:
reversal patterns (double top/bottom, head & shoulders) and trendline patterns
(triangles, wedges, rectangles, channels). No interpretation, no look-ahead -
a pattern is only ever confirmed by candles that closed *after* its pivots were
confirmed.
"""
from datetime import datetime
from typing import Optional
import numpy as np

from app.data.models import Candle, Timeframe, make_pattern_feature_id
from app.structure.swings import SwingType
from app.structure.structure import LabeledSwing
from app.patterns.models import (
    PatternType, PatternStatus, PatternDirection, PatternPoint, TrendLine,
    PatternFeature,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _fit_line(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Least-squares fit of price against pivot index. Returns (slope, intercept)."""
    if not points:
        return 0.0, 0.0
    if len(points) == 1:
        return 0.0, points[0][1]
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    mx, my = xs.mean(), ys.mean()
    denom = ((xs - mx) ** 2).sum()
    if denom == 0:
        return 0.0, float(my)
    slope = float(((xs - mx) * (ys - my)).sum() / denom)
    return slope, float(my - slope * mx)


def _line_from(points: list[tuple[int, float]], slope: float, intercept: float) -> TrendLine:
    idxs = [p[0] for p in points]
    return TrendLine(
        start_index=min(idxs),
        end_index=max(idxs),
        start_price=slope * min(idxs) + intercept,
        end_price=slope * max(idxs) + intercept,
        slope_per_bar=slope,
    )


def _point(role: str, ls: LabeledSwing) -> PatternPoint:
    return PatternPoint(
        role=role, price=ls.swing.price, time=ls.swing.pivot_time, swing_id=ls.swing.id
    )


def _triples(swings: list[LabeledSwing]):
    for i in range(len(swings) - 2):
        yield swings[i], swings[i + 1], swings[i + 2]


def _quintuples(swings: list[LabeledSwing]):
    for i in range(len(swings) - 4):
        yield swings[i:i + 5]


# --------------------------------------------------------------------------
# reversal patterns
# --------------------------------------------------------------------------

def detect_double_patterns(
    swings: list[LabeledSwing], atr: float, symbol: str, timeframe: Timeframe,
    equal_tolerance_atr: float, min_depth_atr: float,
) -> list[PatternFeature]:
    tol = equal_tolerance_atr * atr
    min_depth = min_depth_atr * atr
    out = []

    for a, b, c in _triples(swings):
        # Double top: high - low - high, similar highs, meaningful trough
        if (a.swing.swing_type == SwingType.HIGH
                and b.swing.swing_type == SwingType.LOW
                and c.swing.swing_type == SwingType.HIGH):
            if abs(a.swing.price - c.swing.price) <= tol:
                depth = min(a.swing.price, c.swing.price) - b.swing.price
                if depth >= min_depth:
                    top = max(a.swing.price, c.swing.price)
                    out.append(PatternFeature(
                        id="", symbol=symbol, timeframe=timeframe,
                        pattern_type=PatternType.DOUBLE_TOP,
                        direction=PatternDirection.BEARISH,
                        points=[_point("high_1", a), _point("trough", b), _point("high_2", c)],
                        upper_bound=top,
                        neckline=b.swing.price,
                        confirmation_price=b.swing.price,
                        invalidation_price=top,
                        measured_move_target=b.swing.price - depth,
                        evidence_ids=[a.swing.id, b.swing.id, c.swing.id],
                        formed_at_index=max(a.swing.confirmation_index, c.swing.confirmation_index),
                        notes=f"depth={depth:.5f}",
                    ))

        # Double bottom: low - high - low
        if (a.swing.swing_type == SwingType.LOW
                and b.swing.swing_type == SwingType.HIGH
                and c.swing.swing_type == SwingType.LOW):
            if abs(a.swing.price - c.swing.price) <= tol:
                depth = b.swing.price - max(a.swing.price, c.swing.price)
                if depth >= min_depth:
                    bottom = min(a.swing.price, c.swing.price)
                    out.append(PatternFeature(
                        id="", symbol=symbol, timeframe=timeframe,
                        pattern_type=PatternType.DOUBLE_BOTTOM,
                        direction=PatternDirection.BULLISH,
                        points=[_point("low_1", a), _point("peak", b), _point("low_2", c)],
                        lower_bound=bottom,
                        neckline=b.swing.price,
                        confirmation_price=b.swing.price,
                        invalidation_price=bottom,
                        measured_move_target=b.swing.price + depth,
                        evidence_ids=[a.swing.id, b.swing.id, c.swing.id],
                        formed_at_index=max(a.swing.confirmation_index, c.swing.confirmation_index),
                        notes=f"depth={depth:.5f}",
                    ))
    return out


def detect_head_shoulders(
    swings: list[LabeledSwing], atr: float, symbol: str, timeframe: Timeframe,
    equal_tolerance_atr: float, min_depth_atr: float,
) -> list[PatternFeature]:
    tol = equal_tolerance_atr * atr
    min_depth = min_depth_atr * atr
    out = []

    for p in _quintuples(swings):
        types = [x.swing.swing_type for x in p]
        ls, t1, head, t2, rs = p

        # Head & shoulders: H L H L H, head highest, shoulders similar
        if types == [SwingType.HIGH, SwingType.LOW, SwingType.HIGH, SwingType.LOW, SwingType.HIGH]:
            if (head.swing.price > ls.swing.price and head.swing.price > rs.swing.price
                    and abs(ls.swing.price - rs.swing.price) <= tol
                    and abs(t1.swing.price - t2.swing.price) <= tol):
                neckline = (t1.swing.price + t2.swing.price) / 2
                height = head.swing.price - neckline
                if height >= min_depth:
                    out.append(PatternFeature(
                        id="", symbol=symbol, timeframe=timeframe,
                        pattern_type=PatternType.HEAD_SHOULDERS,
                        direction=PatternDirection.BEARISH,
                        points=[_point("left_shoulder", ls), _point("trough_1", t1),
                                _point("head", head), _point("trough_2", t2),
                                _point("right_shoulder", rs)],
                        neckline=neckline,
                        confirmation_price=neckline,
                        invalidation_price=head.swing.price,
                        measured_move_target=neckline - height,
                        evidence_ids=[x.swing.id for x in p],
                        formed_at_index=max(x.swing.confirmation_index for x in p),
                        notes=f"head_height={height:.5f}",
                    ))

        # Inverse head & shoulders: L H L H L, head lowest
        if types == [SwingType.LOW, SwingType.HIGH, SwingType.LOW, SwingType.HIGH, SwingType.LOW]:
            if (head.swing.price < ls.swing.price and head.swing.price < rs.swing.price
                    and abs(ls.swing.price - rs.swing.price) <= tol
                    and abs(t1.swing.price - t2.swing.price) <= tol):
                neckline = (t1.swing.price + t2.swing.price) / 2
                height = neckline - head.swing.price
                if height >= min_depth:
                    out.append(PatternFeature(
                        id="", symbol=symbol, timeframe=timeframe,
                        pattern_type=PatternType.INVERSE_HEAD_SHOULDERS,
                        direction=PatternDirection.BULLISH,
                        points=[_point("left_shoulder", ls), _point("peak_1", t1),
                                _point("head", head), _point("peak_2", t2),
                                _point("right_shoulder", rs)],
                        neckline=neckline,
                        confirmation_price=neckline,
                        invalidation_price=head.swing.price,
                        measured_move_target=neckline + height,
                        evidence_ids=[x.swing.id for x in p],
                        formed_at_index=max(x.swing.confirmation_index for x in p),
                        notes=f"head_depth={height:.5f}",
                    ))
    return out


# --------------------------------------------------------------------------
# trendline patterns
# --------------------------------------------------------------------------

def detect_trendline_patterns(
    swings: list[LabeledSwing], atr: float, symbol: str, timeframe: Timeframe,
    flat_slope_atr: float, max_swings: int,
) -> list[PatternFeature]:
    highs = [s for s in swings if s.swing.swing_type == SwingType.HIGH][-3:]
    lows = [s for s in swings if s.swing.swing_type == SwingType.LOW][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return []

    h_pts = [(s.swing.pivot_index, s.swing.price) for s in highs]
    l_pts = [(s.swing.pivot_index, s.swing.price) for s in lows]
    r_slope, r_icept = _fit_line(h_pts)
    s_slope, s_icept = _fit_line(l_pts)

    r_line = _line_from(h_pts, r_slope, r_icept)
    s_line = _line_from(l_pts, s_slope, s_icept)

    flat_eps = flat_slope_atr * atr

    def is_flat(s: float) -> bool:
        return abs(s) <= flat_eps

    def up(s: float) -> bool:
        return s > flat_eps

    def down(s: float) -> bool:
        return s < -flat_eps

    last_idx = max([p[0] for p in h_pts + l_pts])
    first_idx = min([p[0] for p in h_pts + l_pts])
    width_first = r_line.price_at(first_idx) - s_line.price_at(first_idx)
    width_last = r_line.price_at(last_idx) - s_line.price_at(last_idx)
    converging = abs(width_last) < abs(width_first) and width_last > 0

    pattern_type: Optional[PatternType] = None
    direction = PatternDirection.NEUTRAL
    if is_flat(r_slope) and up(s_slope):
        pattern_type, direction = PatternType.ASCENDING_TRIANGLE, PatternDirection.BULLISH
    elif is_flat(s_slope) and down(r_slope):
        pattern_type, direction = PatternType.DESCENDING_TRIANGLE, PatternDirection.BEARISH
    elif down(r_slope) and up(s_slope):
        pattern_type, direction = PatternType.SYMMETRICAL_TRIANGLE, PatternDirection.NEUTRAL
    elif up(r_slope) and up(s_slope):
        if converging:
            pattern_type, direction = PatternType.RISING_WEDGE, PatternDirection.BEARISH
        else:
            pattern_type, direction = PatternType.CHANNEL, PatternDirection.BULLISH
    elif down(r_slope) and down(s_slope):
        if converging:
            pattern_type, direction = PatternType.FALLING_WEDGE, PatternDirection.BULLISH
        else:
            pattern_type, direction = PatternType.CHANNEL, PatternDirection.BEARISH
    elif is_flat(r_slope) and is_flat(s_slope):
        pattern_type, direction = PatternType.RECTANGLE, PatternDirection.NEUTRAL

    if pattern_type is None:
        return []

    used = sorted(highs + lows, key=lambda s: s.swing.pivot_index)
    points = [_point(f"high_{i+1}", s) for i, s in enumerate(highs)]
    points += [_point(f"low_{i+1}", s) for i, s in enumerate(lows)]

    return [PatternFeature(
        id="", symbol=symbol, timeframe=timeframe,
        pattern_type=pattern_type, direction=direction,
        points=points,
        resistance_line=r_line,
        support_line=s_line,
        upper_bound=r_line.price_at(last_idx),
        lower_bound=s_line.price_at(last_idx),
        evidence_ids=[s.swing.id for s in used],
        formed_at_index=max(s.swing.confirmation_index for s in used),
        notes=f"resistance_slope={r_slope:.6f} support_slope={s_slope:.6f} converging={converging}",
    )]


# --------------------------------------------------------------------------
# lifecycle state machine
# --------------------------------------------------------------------------

def apply_pattern_state(patterns: list[PatternFeature], candles: list[Candle]) -> list[PatternFeature]:
    """
    Walk forward from each pattern's formation and resolve its state.

    Confirmation and invalidation are only ever triggered by candles that closed
    strictly after the pattern's pivots were confirmed, so replay stays causal.
    """
    resolved = []
    for p in patterns:
        status = PatternStatus.FORMING
        direction = p.direction
        conf_time = None
        conf_candle = None

        for i in range(p.formed_at_index + 1, len(candles)):
            c = candles[i]

            if p.confirmation_price is not None:
                # Directional pattern with explicit thresholds.
                if direction == PatternDirection.BEARISH:
                    if c.close < p.confirmation_price:
                        status, conf_time, conf_candle = PatternStatus.CONFIRMED, c.time_close, c.id
                        break
                    if p.invalidation_price is not None and c.close > p.invalidation_price:
                        status = PatternStatus.FAILED
                        break
                elif direction == PatternDirection.BULLISH:
                    if c.close > p.confirmation_price:
                        status, conf_time, conf_candle = PatternStatus.CONFIRMED, c.time_close, c.id
                        break
                    if p.invalidation_price is not None and c.close < p.invalidation_price:
                        status = PatternStatus.FAILED
                        break
            else:
                # Breakout pattern bounded by two lines.
                upper = p.resistance_line.price_at(i) if p.resistance_line else p.upper_bound
                lower = p.support_line.price_at(i) if p.support_line else p.lower_bound
                if upper is not None and c.close > upper:
                    status, direction = PatternStatus.CONFIRMED, PatternDirection.BULLISH
                    conf_time, conf_candle = c.time_close, c.id
                    break
                if lower is not None and c.close < lower:
                    status, direction = PatternStatus.CONFIRMED, PatternDirection.BEARISH
                    conf_time, conf_candle = c.time_close, c.id
                    break

        resolved.append(p.model_copy(update={
            "status": status,
            "direction": direction,
            "confirmation_time": conf_time,
            "confirmation_candle_id": conf_candle,
        }))
    return resolved


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def detect_patterns(
    labeled_swings: list[LabeledSwing],
    candles: list[Candle],
    atr: Optional[float],
    timeframe: Timeframe,
    symbol: str,
) -> list[PatternFeature]:
    """Detect all patterns for a timeframe and assign stable IDs."""
    from app.config.settings import config

    if atr is None or atr <= 0 or len(labeled_swings) < 3:
        return []

    cfg = config.patterns
    swings = sorted(labeled_swings, key=lambda s: s.swing.pivot_index)

    detected = []
    detected += detect_double_patterns(
        swings, atr, symbol, timeframe, cfg.equal_tolerance_atr, cfg.min_depth_atr)
    detected += detect_head_shoulders(
        swings, atr, symbol, timeframe, cfg.equal_tolerance_atr, cfg.min_depth_atr)
    detected += detect_trendline_patterns(
        swings, atr, symbol, timeframe, cfg.flat_slope_atr, cfg.max_swings)

    # De-duplicate identical geometries, then order deterministically.
    seen = set()
    unique = []
    for p in detected:
        key = (p.pattern_type.value, tuple(sorted(
            (pt.swing_id or pt.role) for pt in p.points)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    unique.sort(key=lambda p: (
        p.pattern_type.value,
        p.points[0].time if p.points else datetime.min.replace(tzinfo=None),
        p.direction.value,
    ))

    resolved = apply_pattern_state(unique, candles)

    out = []
    for seq, p in enumerate(resolved):
        out.append(p.model_copy(update={
            "id": make_pattern_feature_id(symbol, timeframe, p.pattern_type.value, seq)
        }))
    return out
