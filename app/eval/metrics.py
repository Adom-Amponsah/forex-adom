"""
Aggregate metrics over evaluation records.
"""
from __future__ import annotations

from statistics import mean


def summarize(records: list[dict]) -> dict:
    total = len(records)
    grounded = sum(1 for r in records if r.get("ok"))
    errored = sum(1 for r in records if r.get("error") and not r.get("validation_errors"))

    attempts = [r.get("attempts", 0) for r in records if r.get("attempts")]
    # A run that needed >1 attempt means the first response failed grounding.
    first_try_ok = sum(1 for r in records if r.get("ok") and r.get("attempts") == 1)

    outcomes = [o for r in records for o in r.get("outcomes", [])]
    n_scen = len(outcomes)
    activated = [o for o in outcomes if o["activated"]]
    invalidated = [o for o in outcomes if o["invalidated"] and not o["activated"]]
    unresolved = [o for o in outcomes if not o["activated"] and not o["invalidated"]]

    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return round(mean(vals), 5) if vals else None

    return {
        "points": total,
        "grounded": grounded,
        "grounding_rate": round(grounded / total, 3) if total else None,
        "first_try_grounding_rate": round(first_try_ok / total, 3) if total else None,
        "mean_attempts": round(mean(attempts), 2) if attempts else None,
        "errors": errored,
        "scenarios": n_scen,
        "scenarios_per_point": round(n_scen / total, 2) if total else None,
        "activated": len(activated),
        "activation_rate": round(len(activated) / n_scen, 3) if n_scen else None,
        "invalidated": len(invalidated),
        "unresolved": len(unresolved),
        "avg_mfe": _avg([o["mfe"] for o in outcomes]),
        "avg_mae": _avg([o["mae"] for o in outcomes]),
        "avg_bars_to_activation": _avg([o["bars_to_activation"] for o in activated]),
    }
