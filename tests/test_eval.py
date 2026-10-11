"""Scenario-outcome evaluation tests (deterministic, no LLM)."""
from datetime import datetime, timedelta, timezone

from app.data.models import Timeframe, make_candle
from app.llm.schema import Scenario, ScenarioCondition
from app.eval.outcomes import evaluate_scenario
from app.eval.metrics import summarize

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candles(closes):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(make_candle("EURUSD", Timeframe.H1, BASE + timedelta(hours=i),
                               prev, max(prev, c) + 0.0001, min(prev, c) - 0.0001, c))
        prev = c
    return out


def _scenario(direction, cond_price, cond_type="closes_above", inv_price=None):
    inv = []
    if inv_price is not None:
        inv = [ScenarioCondition(type="closes_below", price=inv_price)]
    return Scenario(
        name="test", direction=direction,
        conditions=[ScenarioCondition(type=cond_type, price=cond_price)],
        invalidation=inv,
    )


def test_scenario_activates():
    closes = [1.1000] * 6 + [1.1010, 1.1030, 1.1060, 1.1100, 1.1150, 1.1200]
    candles = _candles(closes)
    as_of = candles[5].time_close
    outcome = evaluate_scenario(_scenario("bullish", 1.1100), candles, as_of)
    assert outcome.activated
    assert outcome.activated_at is not None
    assert outcome.bars_to_activation >= 1
    assert outcome.mfe is not None and outcome.mfe > 0


def test_scenario_invalidates_before_activating():
    closes = [1.1000] * 6 + [1.0950, 1.0900, 1.1150]
    candles = _candles(closes)
    as_of = candles[5].time_close
    outcome = evaluate_scenario(_scenario("bullish", 1.1100, inv_price=1.0970), candles, as_of)
    assert not outcome.activated
    assert outcome.invalidated


def test_mfe_mae_direction():
    closes = [1.1000] * 6 + [1.1500, 1.0900]
    candles = _candles(closes)
    as_of = candles[5].time_close
    bull = evaluate_scenario(_scenario("bullish", 1.9999), candles, as_of)
    bear = evaluate_scenario(_scenario("bearish", 0.0001), candles, as_of)
    # bullish favourable move is up, bearish favourable move is down -> inverses
    assert bull.mfe == bear.mae
    assert bull.mae == bear.mfe
    assert bull.mfe > bull.mae  # the up move was larger in this series


def test_summarize():
    records = [
        {"ok": True, "attempts": 1, "outcomes": [
            {"activated": True, "invalidated": False, "mfe": 0.01, "mae": 0.002, "bars_to_activation": 3}]},
        {"ok": True, "attempts": 2, "validation_errors": ["unknown_reference"], "outcomes": [
            {"activated": False, "invalidated": True, "mfe": 0.001, "mae": 0.01, "bars_to_activation": None}]},
    ]
    report = summarize(records)
    assert report["points"] == 2
    assert report["grounding_rate"] == 1.0
    assert report["first_try_grounding_rate"] == 0.5
    assert report["scenarios"] == 2
    assert report["activated"] == 1
    assert report["invalidated"] == 1
