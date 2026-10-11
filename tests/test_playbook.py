"""Top-down playbook tests: alignment, confluence, continuation vs reversal."""
from app.data.models import Timeframe
from app.data.synthetic_provider import SyntheticDataProvider
from app.replay.engine import run_single_analysis
from app.market_state.builder import MarketState
from app.patterns.models import PatternType, PatternClass, pattern_class_of


def _state() -> MarketState:
    provider = SyntheticDataProvider(seed=7, h1_bars=1500)
    tfs = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
    dc = provider.load("EURUSD", tfs)[Timeframe.H1].candles[-1].time_close
    text = run_single_analysis(provider, "EURUSD", dc, tfs, Timeframe.H1,
                               [Timeframe.D1], [Timeframe.H4])
    return MarketState.model_validate_json(text)


def test_pattern_class_mapping():
    assert pattern_class_of(PatternType.DOUBLE_TOP) == PatternClass.REVERSAL
    assert pattern_class_of(PatternType.HEAD_SHOULDERS) == PatternClass.REVERSAL
    assert pattern_class_of(PatternType.RISING_WEDGE) == PatternClass.REVERSAL
    assert pattern_class_of(PatternType.ASCENDING_TRIANGLE) == PatternClass.CONTINUATION
    assert pattern_class_of(PatternType.CHANNEL) == PatternClass.CONTINUATION
    assert pattern_class_of(PatternType.RECTANGLE) == PatternClass.CONTINUATION


def test_playbook_present_with_roles_and_steps():
    pb = _state().playbook
    assert pb is not None
    assert len(pb.steps) >= 5
    roles = {b.role for b in pb.higher_timeframes}
    assert {"context", "structure", "decision"} <= roles
    # ordered largest timeframe first
    assert pb.higher_timeframes[0].timeframe == "D1"


def test_confluence_is_structural_not_round_numbers():
    state = _state()
    zones = {z.id: z for tf in state.timeframes.values() for z in tf.zones}
    for c in state.playbook.confluence_levels:
        assert c.strength >= 2  # by definition cross-timeframe
        for zid in c.evidence_ids:
            z = zones[zid]
            assert not (z.origin_ids and all(o.startswith("ROUND_") for o in z.origin_ids)), \
                f"round-number zone {zid} leaked into confluence"


def test_setups_split_by_class():
    pb = _state().playbook
    assert all(s.pattern_class == "continuation" for s in pb.continuation_setups)
    assert all(s.pattern_class == "reversal" for s in pb.reversal_setups)


def test_playbook_is_deterministic():
    a = _state().playbook.model_dump(mode="json")
    b = _state().playbook.model_dump(mode="json")
    assert a == b


def test_alignment_reports_direction_only_when_aligned():
    pb = _state().playbook
    if pb.alignment.aligned:
        assert pb.alignment.direction in ("bullish", "bearish")
        assert any(c.type == "structure_alignment" for c in pb.alignment.confirmations)
    else:
        assert pb.alignment.direction is None
