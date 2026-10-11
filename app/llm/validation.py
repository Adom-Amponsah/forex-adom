"""
Grounding / validation layer.

Rejects LLM output that isn't traceable to the canonical market state:
  - schema errors (handled by Pydantic on parse)
  - hallucinated evidence IDs
  - levels that don't correspond to any real level
  - timeframe keys that don't exist
  - scenario conditions that aren't checkable
"""
from pydantic import BaseModel, ConfigDict

from app.market_state.builder import MarketState
from app.llm.schema import AIAnalysis
from app.llm.evidence import collect_evidence


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: str  # "error" | "warning"
    code: str
    message: str


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []


def _opposite(direction: str) -> str:
    return "bullish" if direction == "bearish" else "bearish"


def _known_levels(state: MarketState) -> list[float]:
    levels: list[float] = []
    for level in (state.previous_day, state.previous_week):
        if level.high is not None:
            levels.append(level.high)
        if level.low is not None:
            levels.append(level.low)
    levels.extend(state.round_numbers.levels)

    for tf in state.timeframes.values():
        for z in tf.zones:
            levels.extend([z.low, z.high])
        if tf.fib:
            levels.extend(lvl.price for lvl in tf.fib.levels)
        if tf.structure:
            ext = tf.structure.external
            for ls in (ext.last_high, ext.last_low, ext.protected_high, ext.protected_low):
                if ls:
                    levels.append(ls.swing.price)
        for p in tf.pattern_features:
            for v in (p.neckline, p.confirmation_price, p.invalidation_price,
                      p.measured_move_target, p.upper_bound, p.lower_bound):
                if v is not None:
                    levels.append(v)
    return levels


def _tolerance(state: MarketState) -> float:
    tf = state.timeframes.get(state.decision_timeframe)
    atr = tf.atr if tf and tf.atr else None
    if atr:
        return max(atr, state.price * 0.0005)
    return state.price * 0.005


def validate_analysis(analysis: AIAnalysis, state: MarketState) -> ValidationResult:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    evidence = collect_evidence(state)
    valid_ids = set(evidence)
    valid_tfs = set(state.timeframes.keys())
    known_levels = _known_levels(state)
    tol = _tolerance(state)

    def check_refs(refs: list[str], where: str):
        for r in refs:
            if r not in valid_ids:
                errors.append(ValidationIssue(
                    severity="error", code="unknown_reference",
                    message=f"{where} cites unknown evidence id '{r}'"))

    def check_level(price: float, where: str):
        if not known_levels:
            return
        if not any(abs(price - lv) <= tol for lv in known_levels):
            errors.append(ValidationIssue(
                severity="error", code="unsupported_level",
                message=f"{where} cites price {price} not near any known level "
                        f"(tolerance {tol:.5f})"))

    # timeframe keys
    for tf_name, read in analysis.timeframe_read.items():
        if tf_name not in valid_tfs:
            errors.append(ValidationIssue(
                severity="error", code="unknown_timeframe",
                message=f"timeframe_read has unknown timeframe '{tf_name}'"))
        check_refs(read.references, f"timeframe_read[{tf_name}]")

    # patterns
    pattern_ids = {p.id for p in state.pattern_features}
    for pid in analysis.patterns:
        if pid not in pattern_ids:
            errors.append(ValidationIssue(
                severity="error", code="unknown_pattern",
                message=f"patterns cites unknown pattern id '{pid}'"))

    # key levels
    for kl in analysis.key_levels:
        check_level(kl.price, "key_levels")
        check_refs(kl.references, f"key_levels[{kl.price}]")

    # scenarios
    BULLISH_TRIGGERS = {"closes_above", "price_above"}
    BEARISH_TRIGGERS = {"closes_below", "price_below"}

    for sc in analysis.scenarios:
        check_refs(sc.references, f"scenario '{sc.name}'")
        if sc.target is not None:
            check_level(sc.target, f"scenario '{sc.name}' target")

        if sc.direction == "bullish":
            want_cond, want_inv = BULLISH_TRIGGERS, BEARISH_TRIGGERS
        elif sc.direction == "bearish":
            want_cond, want_inv = BEARISH_TRIGGERS, BULLISH_TRIGGERS
        else:
            want_cond = want_inv = None

        for cond in list(sc.conditions) + list(sc.invalidation):
            if cond.reference_id and cond.reference_id not in valid_ids:
                errors.append(ValidationIssue(
                    severity="error", code="unknown_reference",
                    message=f"scenario '{sc.name}' condition references unknown id "
                            f"'{cond.reference_id}'"))
            if cond.type in ("closes_above", "closes_below", "price_above", "price_below"):
                if cond.price is None:
                    errors.append(ValidationIssue(
                        severity="error", code="uncheckable_condition",
                        message=f"scenario '{sc.name}' condition '{cond.type}' has no price"))
                else:
                    check_level(cond.price, f"scenario '{sc.name}' condition")

        # Semantic consistency: the triggers must agree with the direction.
        if want_cond is not None:
            for cond in sc.conditions:
                if cond.type in (BULLISH_TRIGGERS | BEARISH_TRIGGERS) and cond.type not in want_cond:
                    errors.append(ValidationIssue(
                        severity="error", code="inconsistent_condition",
                        message=f"scenario '{sc.name}' is {sc.direction} but its trigger "
                                f"'{cond.type}' is a {_opposite(sc.direction)} trigger"))
            for cond in sc.invalidation:
                if cond.type in (BULLISH_TRIGGERS | BEARISH_TRIGGERS) and cond.type not in want_inv:
                    errors.append(ValidationIssue(
                        severity="error", code="inconsistent_invalidation",
                        message=f"scenario '{sc.name}' is {sc.direction} but its invalidation "
                                f"'{cond.type}' is a {sc.direction} trigger (it would fire "
                                f"immediately)"))

    if not analysis.scenarios:
        warnings.append(ValidationIssue(
            severity="warning", code="no_scenarios",
            message="analysis contains no scenarios"))
    if not analysis.summary.strip():
        errors.append(ValidationIssue(
            severity="error", code="empty_summary", message="summary is empty"))

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
