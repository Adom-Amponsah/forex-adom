"""
LLM-layer tests using a fake client (no API key, no network).

These exercise the parts that must be trustworthy: schema parsing, grounding
validation, retry-with-feedback, and rejected-attempt storage.
"""
import json


from app.data.models import Timeframe
from app.data.synthetic_provider import SyntheticDataProvider
from app.replay.engine import run_single_analysis
from app.market_state.builder import MarketState
from app.llm.schema import AIAnalysis
from app.llm.validation import validate_analysis
from app.llm.analysis import interpret_market_state, extract_json
from app.llm.prompt import build_user_prompt


def _state() -> MarketState:
    provider = SyntheticDataProvider(seed=7, h1_bars=1500)
    tfs = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
    dc = provider.load("EURUSD", tfs)[Timeframe.H1].candles[-1].time_close
    text = run_single_analysis(provider, "EURUSD", dc, tfs, Timeframe.H1,
                               [Timeframe.D1], [Timeframe.H4])
    return MarketState.model_validate_json(text)


def _grounded_analysis(state: MarketState) -> dict:
    tf = state.decision_timeframe
    zone = state.timeframes[tf].zones[0]
    return {
        "bias": "bullish",
        "market_phase": "pullback",
        "summary": "Price is pulling back into support.",
        "timeframe_read": {
            tf: {"interpretation": "Testing support", "references": [zone.id]}
        },
        "patterns": [],
        "key_levels": [{"price": zone.high, "role": "support", "references": [zone.id]}],
        "scenarios": [{
            "name": "bullish continuation",
            "direction": "bullish",
            "conditions": [{"type": "closes_above", "price": zone.high,
                            "reference_id": zone.id, "description": "break and hold"}],
            "invalidation": [{"type": "closes_below", "price": zone.low,
                              "description": "lose support"}],
            "target": zone.high, "references": [zone.id],
        }],
        "what_changes_the_read": ["a close below support"],
        "unclear": [],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(user)
        return self.responses.pop(0)


def test_prompt_contains_evidence_ids():
    state = _state()
    prompt = build_user_prompt(state)
    zone_id = state.timeframes[state.decision_timeframe].zones[0].id
    assert zone_id in prompt
    assert "VALID EVIDENCE IDS" in prompt


def test_extract_json_handles_code_fences():
    raw = "```json\n{\"a\": 1}\n```"
    assert json.loads(extract_json(raw)) == {"a": 1}


def test_grounded_analysis_passes():
    state = _state()
    analysis = AIAnalysis.model_validate(_grounded_analysis(state))
    result = validate_analysis(analysis, state)
    assert result.ok, [e.message for e in result.errors]


def test_hallucinated_reference_fails():
    state = _state()
    bad = _grounded_analysis(state)
    bad["timeframe_read"][state.decision_timeframe]["references"] = ["H1_ZONE_MADE_UP_9999"]
    result = validate_analysis(AIAnalysis.model_validate(bad), state)
    assert not result.ok
    assert any(e.code == "unknown_reference" for e in result.errors)


def test_unknown_timeframe_fails():
    state = _state()
    bad = _grounded_analysis(state)
    bad["timeframe_read"]["M5"] = {"interpretation": "x", "references": []}
    result = validate_analysis(AIAnalysis.model_validate(bad), state)
    assert any(e.code == "unknown_timeframe" for e in result.errors)


def test_unsupported_level_fails():
    state = _state()
    bad = _grounded_analysis(state)
    bad["key_levels"] = [{"price": state.price * 2, "role": "support", "references": []}]
    result = validate_analysis(AIAnalysis.model_validate(bad), state)
    assert any(e.code == "unsupported_level" for e in result.errors)


def test_retry_with_feedback_recovers(tmp_path):
    state = _state()
    bad = _grounded_analysis(state)
    bad["timeframe_read"][state.decision_timeframe]["references"] = ["NOPE_123"]
    good = _grounded_analysis(state)

    client = FakeClient([json.dumps(bad), json.dumps(good)])
    result = interpret_market_state(state, client, max_retries=2, reject_dir=str(tmp_path))

    assert result.ok
    assert result.attempts == 2
    assert len(client.calls) == 2
    assert "failed grounding validation" in client.calls[1]
    assert result.rejected_files  # the bad attempt was stored


def test_exhausted_retries_fails_and_stores(tmp_path):
    state = _state()
    bad = _grounded_analysis(state)
    bad["key_levels"] = [{"price": state.price * 2, "role": "support", "references": []}]
    client = FakeClient([json.dumps(bad), json.dumps(bad)])

    result = interpret_market_state(state, client, max_retries=1, reject_dir=str(tmp_path))
    assert not result.ok
    assert result.attempts == 2
    assert len(result.rejected_files) == 2


def test_schema_error_triggers_retry(tmp_path):
    state = _state()
    good = _grounded_analysis(state)
    client = FakeClient(["not json at all", json.dumps(good)])
    result = interpret_market_state(state, client, max_retries=1, reject_dir=str(tmp_path))
    assert result.ok
    assert result.attempts == 2
