"""
Evidence index.

Collects every ID the LLM is allowed to cite from a canonical market state, so
the validator can reject any reference the model invents.
"""
from app.market_state.builder import MarketState


def collect_evidence(state: MarketState) -> dict[str, str]:
    """Return a mapping of valid evidence ID -> kind (swing/zone/event/pattern/candle/fib)."""
    ids: dict[str, str] = {}

    def add(value, kind: str):
        if value and value not in ids:
            ids[value] = kind

    for tf_state in state.timeframes.values():
        structure = tf_state.structure
        if structure:
            ext = structure.external
            for ls in (ext.last_high, ext.last_low, ext.protected_high, ext.protected_low):
                if ls:
                    add(ls.swing.id, "swing")
            for ev in structure.all_events:
                add(ev.id, "event")
                add(ev.confirmation_candle_id, "candle")
                add(ev.reference_id, "swing")

        for zone in tf_state.zones:
            add(zone.id, "zone")
            for origin in zone.origin_ids:
                add(origin, "swing")

        for ev in tf_state.events:
            add(ev.id, "event")

        if tf_state.fib:
            add(tf_state.fib.anchor_low_id, "fib")
            add(tf_state.fib.anchor_high_id, "fib")

        for pattern in tf_state.pattern_features:
            add(pattern.id, "pattern")
            for pt in pattern.points:
                add(pt.swing_id, "swing")
            add(pattern.confirmation_candle_id, "candle")

        for candle in tf_state.candles:
            add(candle.id, "candle")

    for ev in state.structural_events:
        add(ev.id, "event")

    for pattern in state.pattern_features:
        add(pattern.id, "pattern")

    return ids
