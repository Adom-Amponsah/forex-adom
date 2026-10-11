"""
LLM interpretation orchestration.

state -> prompt -> LLM -> parse -> ground -> (retry with feedback) -> result.
Rejected attempts are persisted for auditing and prompt improvement.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from app.market_state.builder import MarketState
from app.llm.schema import AIAnalysis
from app.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from app.llm.validation import validate_analysis, ValidationResult
from app.llm.client import LLMClient


class InterpretationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    analysis: Optional[AIAnalysis] = None
    validation: Optional[ValidationResult] = None
    attempts: int = 0
    raw: str = ""
    error: Optional[str] = None
    rejected_files: list[str] = []


def extract_json(text: str) -> str:
    """Pull a JSON object out of a model response (tolerating code fences)."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in response")
    return t[start:end + 1]


def parse_analysis(text: str) -> AIAnalysis:
    return AIAnalysis.model_validate_json(extract_json(text))


def _save_rejected(reject_dir: str, state: MarketState, raw: str,
                   errors: list, attempt: int) -> str:
    d = Path(reject_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = state.as_of.strftime("%Y%m%dT%H%M%SZ")
    path = d / f"{state.symbol}_{ts}_attempt{attempt}.json"
    path.write_text(json.dumps({
        "symbol": state.symbol,
        "as_of": state.as_of.isoformat(),
        "attempt": attempt,
        "errors": [e.model_dump() if hasattr(e, "model_dump") else e for e in errors],
        "raw": raw,
    }, indent=2))
    return str(path)


def interpret_market_state(
    state: MarketState,
    client: LLMClient,
    max_retries: Optional[int] = None,
    reject_dir: Optional[str] = None,
    prior: Optional[dict] = None,
) -> InterpretationResult:
    """Interpret a market state, retrying with validation feedback.

    `prior` optionally carries the previous read forward so the model can
    maintain continuity across a walk-forward run.
    """
    from app.config.settings import config

    if max_retries is None:
        max_retries = config.llm.max_retries
    if reject_dir is None:
        reject_dir = config.llm.reject_dir

    base_user = build_user_prompt(state, prior=prior)
    user = base_user

    last_analysis: Optional[AIAnalysis] = None
    last_validation: Optional[ValidationResult] = None
    last_raw = ""
    last_error: Optional[str] = None
    rejected: list[str] = []

    for attempt in range(max_retries + 1):
        raw = client.complete(SYSTEM_PROMPT, user)
        last_raw = raw

        try:
            analysis = parse_analysis(raw)
        except (ValidationError, ValueError) as e:
            last_error = f"schema error: {e}"
            if reject_dir:
                rejected.append(_save_rejected(reject_dir, state, raw, [], attempt))
            user = base_user + (
                f"\n\nYour previous response was invalid: {e}\n"
                "Return corrected JSON only, matching the schema exactly."
            )
            continue

        last_analysis = analysis
        result = validate_analysis(analysis, state)
        last_validation = result

        if result.ok:
            return InterpretationResult(
                ok=True, analysis=analysis, validation=result,
                attempts=attempt + 1, raw=raw, rejected_files=rejected,
            )

        if reject_dir:
            rejected.append(_save_rejected(reject_dir, state, raw, result.errors, attempt))
        feedback = "; ".join(f"{e.code}: {e.message}" for e in result.errors)
        last_error = f"grounding validation failed: {feedback}"
        user = base_user + (
            f"\n\nYour previous response failed grounding validation: {feedback}\n"
            "Fix these and return corrected JSON only."
        )

    return InterpretationResult(
        ok=False, analysis=last_analysis, validation=last_validation,
        attempts=max_retries + 1, raw=last_raw, error=last_error,
        rejected_files=rejected,
    )
