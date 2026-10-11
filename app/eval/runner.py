"""
Batch evaluation runner.

Replays decision points, asks the LLM for a grounded read at each, then scores
the scenarios it produced against the candles that followed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from app.data.models import Timeframe
from app.data.provider import DataProvider
from app.data.timeframe_alignment import align_timeframes
from app.market_state.builder import MarketStateBuilder
from app.llm.client import LLMClient
from app.llm.analysis import interpret_market_state
from app.eval.outcomes import evaluate_scenario


def run_evaluation(
    provider: DataProvider,
    symbol: str,
    client: LLMClient,
    timeframes: list[Timeframe],
    decision_timeframe: Timeframe,
    context_timeframes: list[Timeframe],
    structure_timeframes: list[Timeframe],
    start_idx: int = 0,
    count: int = 20,
    step: int = 1,
    horizon_bars: int = 50,
    calculation_history_bars: int = 1000,
    max_retries: Optional[int] = None,
    reject_dir: Optional[str] = None,
    out_path: Optional[str] = None,
    progress: bool = True,
) -> list[dict]:
    """Run the LLM over a slice of history and return per-point records."""
    loaded = provider.load(symbol, timeframes)
    all_candles = {tf: s.candles for tf, s in loaded.items()}
    first = next(iter(loaded.values()))
    meta, source = first.symbol_metadata, first.data_source

    decision_candles = all_candles[decision_timeframe]
    builder = MarketStateBuilder()

    indices = list(range(start_idx, min(start_idx + count * step, len(decision_candles)), step))
    records: list[dict] = []

    iterator = tqdm(indices, desc="Evaluating", disable=not progress)
    for idx in iterator:
        decision_candle = decision_candles[idx]
        aligned = align_timeframes(
            all_candles=all_candles,
            decision_timeframe=decision_timeframe,
            decision_candle=decision_candle,
            context_timeframes=context_timeframes,
            structure_timeframes=structure_timeframes,
            calculation_history_bars=calculation_history_bars,
        )
        state = builder.build(
            symbol=symbol, symbol_metadata=meta,
            decision_timeframe=decision_timeframe,
            decision_candle=decision_candle, aligned_candles=aligned,
            data_source=source,
        )

        record: dict = {
            "as_of": state.as_of.isoformat(),
            "price": state.price,
            "n_patterns": len(state.pattern_features),
        }

        try:
            result = interpret_market_state(
                state, client, max_retries=max_retries, reject_dir=reject_dir
            )
            record.update({
                "ok": result.ok,
                "attempts": result.attempts,
                "error": result.error,
                "validation_errors": [e.code for e in (result.validation.errors if result.validation else [])],
            })
            if result.analysis is not None:
                record["bias"] = result.analysis.bias.value
                record["market_phase"] = result.analysis.market_phase.value
                record["n_scenarios"] = len(result.analysis.scenarios)
                record["analysis"] = result.analysis.model_dump(mode="json")
                record["outcomes"] = [
                    evaluate_scenario(sc, decision_candles, state.as_of, horizon_bars).model_dump(mode="json")
                    for sc in result.analysis.scenarios
                ]
        except Exception as e:  # keep the run going
            record.update({"ok": False, "attempts": 0, "error": f"{type(e).__name__}: {e}"})

        records.append(record)
        if out_path:
            _append_jsonl(out_path, record)

    return records


def _append_jsonl(path: str, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(record) + "\n")
