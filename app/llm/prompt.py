"""
Prompt construction for LLM interpretation.

The model is given *facts only* (the canonical market state) plus the exact set
of evidence IDs it may cite, and is required to answer in the structured schema.
"""
import json

from app.market_state.builder import MarketState
from app.llm.evidence import collect_evidence

SYSTEM_PROMPT = """You are a forex market analyst. You interpret a machine-generated \
market state; you do NOT invent data.

Rules:
1. Use ONLY the facts and evidence IDs provided. Never invent prices, levels,
   events, indicators, or IDs.
2. Every claim in `timeframe_read`, `key_levels`, `scenarios`, and `patterns`
   must cite at least one `references` ID that exists in the provided evidence.
3. Read top-down: higher timeframes set context, lower timeframes refine timing.
4. Scenarios must be conditional and machine-checkable: express triggers as typed
   conditions (e.g. {"type":"closes_above","price":1.1234}) - never as vague prose.
5. Distinguish facts (given) from interpretation (yours). If the state is
   ambiguous, say so in `unclear` rather than guessing.
6. Output ONLY a single JSON object matching the schema. No markdown, no prose
   outside the JSON.
"""


def _prev_level(level) -> dict:
    return {"high": level.high, "low": level.low}


def build_facts(state: MarketState) -> dict:
    """A compact, facts-only view of the market state for the model."""
    facts: dict = {
        "symbol": state.symbol,
        "style": state.style,
        "decision_timeframe": state.decision_timeframe,
        "as_of": state.as_of.isoformat(),
        "price": state.price,
        "session": {"active": state.session.active, "primary": state.session.primary},
        "previous_day": _prev_level(state.previous_day),
        "previous_week": _prev_level(state.previous_week),
        "round_numbers": state.round_numbers.levels,
        "timeframes": {},
        "structural_events": [],
        "pattern_features": [],
    }

    for tf_name, tf in state.timeframes.items():
        tf_facts: dict = {"indicators": {"rsi": tf.rsi, "atr": tf.atr, "ema": tf.ema}}

        if tf.structure:
            ext = tf.structure.external
            tf_facts["structure"] = {
                "direction": tf.structure.direction.value,
                "last_high": _swing(ext.last_high),
                "last_low": _swing(ext.last_low),
                "protected_high": _swing(ext.protected_high),
                "protected_low": _swing(ext.protected_low),
            }

        tf_facts["zones"] = [
            {"id": z.id, "role": z.role.value, "low": z.low, "high": z.high,
             "status": z.status.value, "touch_count": z.touch_count}
            for z in tf.zones
        ]

        tf_facts["events"] = [
            {"id": e.id, "type": e.type.value, "time": e.confirmation_time.isoformat(),
             "reference_id": e.reference_id, "reference_price": e.reference_price,
             "break_price": e.break_price}
            for e in tf.events
        ]

        tf_facts["patterns"] = [
            {"id": p.id, "type": p.pattern_type.value, "status": p.status.value,
             "direction": p.direction.value, "neckline": p.neckline,
             "confirmation_price": p.confirmation_price,
             "invalidation_price": p.invalidation_price,
             "measured_move_target": p.measured_move_target,
             "evidence_ids": p.evidence_ids}
            for p in tf.pattern_features
        ]

        if tf.fib:
            tf_facts["fib"] = {
                "direction": tf.fib.direction.value,
                "anchor_low_id": tf.fib.anchor_low_id,
                "anchor_high_id": tf.fib.anchor_high_id,
                "levels": [{"ratio": lvl.ratio, "price": lvl.price} for lvl in tf.fib.levels],
            }

        tf_facts["recent_candles"] = [
            {"time": c.time_close.isoformat(), "o": c.open, "h": c.high,
             "l": c.low, "c": c.close, "id": c.id}
            for c in tf.candles
        ]

        facts["timeframes"][tf_name] = tf_facts

    facts["structural_events"] = [
        {"id": e.id, "type": e.type.value, "timeframe": e.timeframe.value,
         "time": e.confirmation_time.isoformat(), "reference_id": e.reference_id,
         "reference_price": e.reference_price, "break_price": e.break_price}
        for e in state.structural_events
    ]
    facts["pattern_features"] = [
        {"id": p.id, "timeframe": p.timeframe.value, "type": p.pattern_type.value,
         "status": p.status.value, "direction": p.direction.value}
        for p in state.pattern_features
    ]

    return facts


def _swing(labeled) -> dict | None:
    if labeled is None:
        return None
    return {
        "id": labeled.swing.id,
        "label": labeled.label.value,
        "price": labeled.swing.price,
        "time": labeled.swing.pivot_time.isoformat(),
    }


def build_user_prompt(state: MarketState) -> str:
    facts = build_facts(state)
    evidence = collect_evidence(state)

    by_kind: dict[str, list[str]] = {}
    for eid, kind in sorted(evidence.items()):
        by_kind.setdefault(kind, []).append(eid)

    schema_hint = {
        "bias": "bullish|bearish|neutral|mixed|conditional",
        "market_phase": ("trend|pullback|range|consolidation|breakout|retest|"
                         "possible_reversal|transition|unknown"),
        "summary": "string",
        "timeframe_read": {"<TF>": {"interpretation": "string", "references": ["<evidence id>"]}},
        "patterns": ["<pattern id>"],
        "key_levels": [{"price": 0.0, "role": "support|resistance|target|invalidation",
                        "references": ["<evidence id>"]}],
        "scenarios": [{
            "name": "string", "direction": "bullish|bearish|neutral",
            "conditions": [{"type": "closes_above|closes_below|price_above|price_below|event_occurs",
                            "price": 0.0, "reference_id": "<evidence id>", "description": "string"}],
            "invalidation": [{"type": "closes_below", "price": 0.0, "description": "string"}],
            "target": 0.0, "references": ["<evidence id>"],
        }],
        "what_changes_the_read": ["string"],
        "unclear": ["string"],
    }

    return (
        "MARKET STATE FACTS (authoritative, do not contradict):\n"
        f"{json.dumps(facts, indent=2)}\n\n"
        "VALID EVIDENCE IDS (cite only these):\n"
        f"{json.dumps(by_kind, indent=2)}\n\n"
        "Respond with a single JSON object in exactly this shape:\n"
        f"{json.dumps(schema_hint, indent=2)}"
    )
