"""
Prompt construction for LLM interpretation.

The model is given *facts only* (a curated view of the canonical market state)
plus the exact set of evidence IDs it may cite, and is required to answer in
the structured schema.

The raw state is far too large to send verbatim (hundreds of events/zones), so
we curate: the levels nearest price, the most recent events, the live patterns,
and a window of recent candles.
"""
import json

from app.market_state.builder import MarketState

SYSTEM_PROMPT = """You are a forex market analyst. You interpret a machine-generated \
market state; you do NOT invent data.

Rules:
1. Use ONLY the facts and evidence IDs provided. Never invent prices, levels,
   events, indicators, or IDs.
2. Read the PLAYBOOK first - it is the chart read top-down the way a trader
   reads it: higher-timeframe context, key S/R, cross-timeframe alignment and
   confirmations, then continuation vs reversal setups. Follow that order.
3. Every claim in `timeframe_read`, `key_levels`, `scenarios`, and `patterns`
   must cite at least one `references` ID that exists in the provided evidence.
4. Read top-down: higher timeframes set context, lower timeframes refine timing.
5. Scenarios must be conditional and machine-checkable: express triggers as typed
   conditions (e.g. {"type":"closes_above","price":1.1234}) - never as vague prose.
6. Distinguish facts (given) from interpretation (yours). If the state is
   ambiguous, say so in `unclear` rather than guessing.
7. Output ONLY a single JSON object matching the schema. No markdown, no prose
   outside the JSON.
"""

_ID_KEYS = {"id", "reference_id", "confirmation_candle_id", "pattern_id",
            "anchor_low_id", "anchor_high_id"}
_ID_LIST_KEYS = {"evidence_ids", "references"}


def _collect_ids(obj, acc: set):
    """Recursively collect every evidence ID referenced inside the facts."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _ID_KEYS and isinstance(v, str) and v:
                acc.add(v)
            elif k in _ID_LIST_KEYS and isinstance(v, list):
                acc.update(x for x in v if isinstance(x, str) and x)
            else:
                _collect_ids(v, acc)
    elif isinstance(obj, list):
        for x in obj:
            _collect_ids(x, acc)


def _swing(labeled) -> dict | None:
    if labeled is None:
        return None
    return {
        "id": labeled.swing.id,
        "label": labeled.label.value,
        "price": labeled.swing.price,
        "time": labeled.swing.pivot_time.isoformat(),
    }


def _curate_zones(zones, price, limit):
    def key(z):
        live = z.status.value in ("active", "tested")
        mid = (z.low + z.high) / 2
        return (0 if live else 1, abs(mid - price))
    return sorted(zones, key=key)[:limit]


def _curate_patterns(patterns, limit):
    priority = {"forming": 0, "confirmed": 1, "failed": 2}

    def key(p):
        last_t = p.points[-1].time.timestamp() if p.points else 0.0
        return (priority.get(p.status.value, 3), -last_t)
    return sorted(patterns, key=key)[:limit]


def build_facts(state: MarketState) -> dict:
    """A curated, facts-only view of the market state for the model."""
    from app.config.settings import config
    cfg = config.llm

    facts: dict = {
        "symbol": state.symbol,
        "style": state.style,
        "decision_timeframe": state.decision_timeframe,
        "as_of": state.as_of.isoformat(),
        "price": state.price,
        "session": {"active": state.session.active, "primary": state.session.primary},
        "previous_day": {"high": state.previous_day.high, "low": state.previous_day.low},
        "previous_week": {"high": state.previous_week.high, "low": state.previous_week.low},
        "round_numbers": state.round_numbers.levels,
        "timeframes": {},
        "structural_events": [],
        "pattern_features": [],
    }

    if state.playbook is not None:
        pb = state.playbook
        facts["playbook"] = {
            "steps": pb.steps,
            "higher_timeframes": [b.model_dump(mode="json") for b in pb.higher_timeframes],
            "confluence_levels": [c.model_dump(mode="json") for c in pb.confluence_levels],
            "alignment": pb.alignment.model_dump(mode="json"),
            "continuation_setups": [s.model_dump(mode="json") for s in pb.continuation_setups],
            "reversal_setups": [s.model_dump(mode="json") for s in pb.reversal_setups],
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

        zones = _curate_zones(tf.zones, state.price, cfg.max_zones_per_tf)
        tf_facts["zones"] = [
            {"id": z.id, "role": z.role.value, "low": z.low, "high": z.high,
             "status": z.status.value, "touch_count": z.touch_count}
            for z in zones
        ]
        tf_facts["zones_total"] = len(tf.zones)

        events = sorted(tf.events, key=lambda e: e.confirmation_time, reverse=True)
        tf_facts["events"] = [
            {"id": e.id, "type": e.type.value, "time": e.confirmation_time.isoformat(),
             "reference_id": e.reference_id, "reference_price": e.reference_price,
             "break_price": e.break_price}
            for e in events[:cfg.max_events_per_tf]
        ]
        tf_facts["events_total"] = len(tf.events)

        patterns = _curate_patterns(tf.pattern_features, cfg.max_patterns_per_tf)
        tf_facts["patterns"] = [
            {"id": p.id, "type": p.pattern_type.value, "status": p.status.value,
             "direction": p.direction.value, "neckline": p.neckline,
             "confirmation_price": p.confirmation_price,
             "invalidation_price": p.invalidation_price,
             "measured_move_target": p.measured_move_target,
             "evidence_ids": p.evidence_ids}
            for p in patterns
        ]
        tf_facts["patterns_total"] = len(tf.pattern_features)

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
            for c in tf.candles[-cfg.max_candles_per_tf:]
        ]

        facts["timeframes"][tf_name] = tf_facts

    globals_sorted = sorted(state.structural_events,
                            key=lambda e: e.confirmation_time, reverse=True)
    facts["structural_events"] = [
        {"id": e.id, "type": e.type.value, "timeframe": e.timeframe.value,
         "time": e.confirmation_time.isoformat(), "reference_id": e.reference_id,
         "reference_price": e.reference_price, "break_price": e.break_price}
        for e in globals_sorted[:cfg.max_global_events]
    ]
    facts["structural_events_total"] = len(state.structural_events)

    patterns_sorted = _curate_patterns(state.pattern_features, cfg.max_patterns_per_tf)
    facts["pattern_features"] = [
        {"id": p.id, "timeframe": p.timeframe.value, "type": p.pattern_type.value,
         "status": p.status.value, "direction": p.direction.value}
        for p in patterns_sorted
    ]
    facts["pattern_features_total"] = len(state.pattern_features)

    return facts


def build_user_prompt(state: MarketState) -> str:
    facts = build_facts(state)

    ids: set = set()
    _collect_ids(facts, ids)
    evidence = {"all_ids": sorted(ids)}

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
        f"{json.dumps(evidence, indent=2)}\n\n"
        "Respond with a single JSON object in exactly this shape:\n"
        f"{json.dumps(schema_hint, indent=2)}"
    )
