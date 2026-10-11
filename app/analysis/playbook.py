"""
Build the top-down playbook from a canonical market state.

Deterministic and causal: everything is derived from the state's already-causal
facts. No wall clock, no look-ahead.
"""
from __future__ import annotations


from app.market_state.builder import MarketState, TimeframeState
from app.patterns.models import PatternClass, PatternStatus
from app.analysis.models import (
    TimeframeBias, ConfluenceLevel, Confirmation, Alignment, Setup, Playbook,
)


def _norm(direction: str) -> str:
    return direction if direction in ("bullish", "bearish") else "neutral"


def _role_of(tf_name: str, context: set[str], structure: set[str], decision: str) -> str:
    if tf_name in context:
        return "context"
    if tf_name in structure:
        return "structure"
    if tf_name == decision:
        return "decision"
    return "other"


def _key_levels(tf: TimeframeState, price: float):
    """Nearest resistance above and support below the current price."""
    resistance = support = None
    for z in tf.zones:
        if z.role.value == "resistance" and z.low >= price:
            if resistance is None or z.low < resistance.low:
                resistance = z
        if z.role.value == "support" and z.high <= price:
            if support is None or z.high > support.high:
                support = z
    return resistance, support


def _timeframe_bias(tf_name: str, tf: TimeframeState, role: str, price: float) -> TimeframeBias:
    structure_direction = tf.structure.direction.value if tf.structure else "unknown"
    resistance, support = _key_levels(tf, price)
    confirmed = sum(1 for p in tf.pattern_features if p.status == PatternStatus.CONFIRMED)
    forming = sum(1 for p in tf.pattern_features if p.status == PatternStatus.FORMING)
    return TimeframeBias(
        timeframe=tf_name,
        role=role,
        direction=_norm(structure_direction),
        structure_direction=structure_direction,
        key_resistance=resistance.high if resistance else None,
        resistance_id=resistance.id if resistance else None,
        key_support=support.low if support else None,
        support_id=support.id if support else None,
        confirmed_patterns=confirmed,
        forming_patterns=forming,
    )


def _confluence(state: MarketState, tolerance: float) -> list[ConfluenceLevel]:
    """Cluster structural zone levels across timeframes; multi-TF levels are stronger."""
    entries = []  # (price, tf, role, zone_id, low, high)
    for tf_name, tf in state.timeframes.items():
        for z in tf.zones:
            if z.status.value in ("broken", "invalidated"):
                continue
            # Round numbers exist identically on every timeframe, so they would
            # "confluence" trivially. Only structural levels count here.
            if z.origin_ids and all(o.startswith("ROUND_") for o in z.origin_ids):
                continue
            entries.append(((z.low + z.high) / 2, tf_name, z.role.value, z.id, z.low, z.high))

    entries.sort(key=lambda e: e[0])
    clusters: list[list] = []
    for e in entries:
        if clusters and abs(e[0] - clusters[-1][-1][0]) <= tolerance:
            clusters[-1].append(e)
        else:
            clusters.append([e])

    out = []
    for cluster in clusters:
        tfs = sorted({c[1] for c in cluster})
        if len(tfs) < 2:
            continue  # only cross-timeframe levels are "confluence"
        roles = {c[2] for c in cluster}
        role = "resistance" if roles == {"resistance"} else ("support" if roles == {"support"} else "mixed")
        out.append(ConfluenceLevel(
            price=round(sum(c[0] for c in cluster) / len(cluster), 5),
            low=min(c[4] for c in cluster),
            high=max(c[5] for c in cluster),
            role=role,
            timeframes=tfs,
            strength=len(tfs),
            evidence_ids=[c[3] for c in cluster],
        ))

    out.sort(key=lambda c: (-c.strength, abs(c.price - state.price)))
    return out


def _aggregate(biases: list[TimeframeBias]) -> str:
    dirs = [b.direction for b in biases if b.direction != "neutral"]
    if not dirs:
        return "neutral"
    if len(set(dirs)) == 1:
        return dirs[0]
    return "mixed"


def _alignment(biases: list[TimeframeBias], state: MarketState) -> Alignment:
    from collections import Counter

    context_biases = [b for b in biases if b.role == "context"]
    structure_biases = [b for b in biases if b.role == "structure"]
    decision_biases = [b for b in biases if b.role == "decision"]

    context_bias = _aggregate(context_biases)
    structure_bias = _aggregate(structure_biases)
    decision_bias = _aggregate(decision_biases)
    per_tf = {b.timeframe: b.direction for b in biases}

    non_neutral = [b for b in biases if b.direction != "neutral"]
    counts = Counter(b.direction for b in non_neutral)
    directional_count = len(non_neutral)
    dominant = counts.most_common(1)[0][0] if counts else None
    agreement_count = counts.get(dominant, 0) if dominant else 0

    # Graded confirmation: unanimous is strong, a majority is partial.
    if directional_count == 0:
        quality, aligned, direction = "none", False, None
    elif agreement_count == directional_count:
        quality, aligned, direction = "strong", True, dominant
    elif agreement_count > directional_count / 2:
        quality, aligned, direction = "partial", False, dominant
    else:
        quality, aligned, direction = "conflicted", False, None

    confirmations: list[Confirmation] = []
    conflicts: list[str] = []

    if direction:
        agreeing = [b.timeframe for b in biases if b.direction == direction]
        confirmations.append(Confirmation(
            type="structure_alignment" if quality == "strong" else "partial_alignment",
            direction=direction,
            timeframes=agreeing,
            description=(
                f"{direction} structure agrees across {', '.join(agreeing)}"
                if quality == "strong" else
                f"{direction} on {', '.join(agreeing)} but not all timeframes agree"
            ),
        ))
        for b in biases:
            if b.direction not in ("neutral", direction):
                conflicts.append(f"{b.timeframe} is {b.direction} against {direction}")

    # Pattern confirmation: a confirmed pattern in the aligned direction.
    if direction:
        for p in state.pattern_features:
            if p.status == PatternStatus.CONFIRMED and p.direction.value == direction:
                cls = p.pattern_class
                kind = ("continuation with trend" if cls == PatternClass.CONTINUATION.value
                        else "reversal setup" if cls == PatternClass.REVERSAL.value
                        else "pattern")
                confirmations.append(Confirmation(
                    type="pattern_confirmation",
                    direction=direction,
                    timeframes=[p.timeframe.value],
                    description=f"confirmed {p.pattern_type.value} ({kind}) on {p.timeframe.value}",
                    evidence_ids=[p.id] + p.evidence_ids,
                ))
                break

    # Break of structure in the aligned direction.
    if direction:
        for ev in state.structural_events:
            if ev.is_with_trend and ev.type.value.endswith(direction.upper()):
                confirmations.append(Confirmation(
                    type="break_of_structure",
                    direction=direction,
                    timeframes=[ev.timeframe.value],
                    description=f"{ev.type.value} on {ev.timeframe.value}",
                    evidence_ids=[ev.id, ev.reference_id],
                ))
                break

    return Alignment(
        context_bias=context_bias, structure_bias=structure_bias, decision_bias=decision_bias,
        per_timeframe=per_tf,
        quality=quality, aligned=aligned, direction=direction,
        agreement_count=agreement_count, directional_count=directional_count,
        confirmations=confirmations, conflicts=conflicts,
    )


def _setup(p, tf_name: str) -> Setup:
    return Setup(
        pattern_id=p.id,
        timeframe=tf_name,
        pattern_type=p.pattern_type.value,
        pattern_class=p.pattern_class,
        direction=p.direction.value,
        status=p.status.value,
        key_level=p.confirmation_price if p.confirmation_price is not None else p.neckline,
        invalidation=p.invalidation_price,
        target=p.measured_move_target,
        evidence_ids=[p.id] + p.evidence_ids,
    )


_STATUS_ORDER = {"confirmed": 0, "forming": 1, "failed": 2}


def build_playbook(state: MarketState) -> Playbook:
    from app.config.settings import config

    context = set(config.timeframes.context)
    structure = set(config.timeframes.structure)
    decision = config.timeframes.decision

    # Deterministic order: largest timeframe first
    ordered = sorted(
        state.timeframes.items(),
        key=lambda kv: -_tf_seconds(kv[0]),
    )
    biases = [
        _timeframe_bias(name, tf, _role_of(name, context, structure, decision), state.price)
        for name, tf in ordered
    ]

    dec_tf = state.timeframes.get(decision)
    tol = (dec_tf.atr if dec_tf and dec_tf.atr else state.price * 0.001)

    confluence = _confluence(state, tol)

    # Setups across all timeframes
    all_setups = []
    for name, tf in ordered:
        for p in tf.pattern_features:
            all_setups.append(_setup(p, name))
    all_setups.sort(key=lambda s: (_STATUS_ORDER.get(s.status, 3), s.timeframe))

    continuation = [s for s in all_setups if s.pattern_class == PatternClass.CONTINUATION.value]
    reversal = [s for s in all_setups if s.pattern_class == PatternClass.REVERSAL.value]

    alignment = _alignment(biases, state)

    steps = _steps(state, biases, confluence, alignment, continuation, reversal)

    return Playbook(
        symbol=state.symbol, as_of=state.as_of, price=state.price,
        higher_timeframes=biases,
        confluence_levels=confluence[:10],
        alignment=alignment,
        continuation_setups=continuation[:10],
        reversal_setups=reversal[:10],
        steps=steps,
    )


def _tf_seconds(tf_name: str) -> int:
    from app.data.models import Timeframe
    try:
        return Timeframe(tf_name).to_seconds()
    except ValueError:
        return 0


def _steps(state, biases, confluence, alignment, continuation, reversal) -> list[str]:
    steps: list[str] = []
    for b in biases:
        if b.role in ("context", "structure", "decision"):
            steps.append(
                f"{b.role.upper()} {b.timeframe}: {b.direction} structure; "
                f"resistance {b.key_resistance}, support {b.key_support}"
            )
    if confluence:
        top = confluence[0]
        steps.append(
            f"Confluence S/R: {len(confluence)} multi-timeframe level(s); "
            f"strongest {top.price} on {'/'.join(top.timeframes)}"
        )
    steps.append(
        f"Agreement: {alignment.agreement_count}/{alignment.directional_count} directional "
        f"timeframes agree ({alignment.quality}); context={alignment.context_bias}, "
        f"structure={alignment.structure_bias}, decision={alignment.decision_bias}"
    )
    if alignment.direction:
        agreeing = [tf for tf, d in alignment.per_timeframe.items() if d == alignment.direction]
        steps.append(
            f"Bias: {alignment.direction.upper()} ({alignment.quality}) on {', '.join(agreeing)}"
        )
    else:
        steps.append("Bias: none (timeframes do not agree)")
    if alignment.conflicts:
        steps.append(f"Conflicts: {'; '.join(alignment.conflicts)}")
    steps.append(f"Confirmations: {len(alignment.confirmations)}")
    steps.append(
        f"Continuation setups: {len(continuation)} "
        f"({sum(1 for s in continuation if s.status == 'confirmed')} confirmed)"
    )
    steps.append(
        f"Reversal setups: {len(reversal)} "
        f"({sum(1 for s in reversal if s.status == 'confirmed')} confirmed)"
    )
    return steps
