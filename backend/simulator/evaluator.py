"""
Held-out evaluator.

Compares a fixed static recovery baseline against the AI strategy on
data/held_out.csv. Both strategies receive MAX_INTERVENTIONS=2 attempts
per transaction, making the comparison structurally fair.

Static baseline:  RETRY_LATER_1h → RETRY_LATER_24h → stop
AI strategy:      ML picks best permitted action → if it fails, ML reassesses
                  and picks the next best (excluding the already-tried action)

ISOLATION RULE: This file never imports behavior_model.py.
It reads only sealed held-out rows and their observed binary outcomes.

Run from backend/:
    python simulator/evaluator.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.recovery import predictor as predictor_module

HELD_OUT_PATH = BACKEND_ROOT / "data" / "held_out.csv"
MODEL_PATH    = BACKEND_ROOT / "data" / "model.pkl"
RESULTS_PATH  = BACKEND_ROOT / "data" / "evaluation_results.json"

# Tuned on validation set — do not change after held-out evaluation
MIN_CONFIDENCE    = 0.15
MAX_INTERVENTIONS = 2   # both strategies get the same number of attempts

CANDIDATE_KEYS = [
    "RETRY_LATER_1h",
    "RETRY_LATER_6h",
    "RETRY_LATER_24h",
    "ALTERNATIVE_PAYMENT_0h",
    "CUSTOMER_MESSAGE_0h",
]

CONTEXT_COLUMNS = [
    "historical_success_rate",
    "historical_failure_count",
    "consecutive_failures",
    "total_orders",
    "customer_age_days",
    "previous_recovery_success",
    "root_cause",
    "payment_method",
    "attempt_number",
    "amount_bucket",
    "hour_of_day",
    "day_of_week",
]

NUMERIC_CONTEXT_COLUMNS = [
    "historical_success_rate",
    "historical_failure_count",
    "consecutive_failures",
    "total_orders",
    "customer_age_days",
    "previous_recovery_success",
    "attempt_number",
    "hour_of_day",
    "day_of_week",
]

PERMITTED_ACTIONS: dict[str, set[str]] = {
    "temporary_failure":      {"RETRY_LATER", "ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "bank_unavailable":       {"RETRY_LATER", "ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "processor_timeout":      {"RETRY_LATER", "ALTERNATIVE_PAYMENT"},
    "insufficient_funds":     {"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "card_expired":           {"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "invalid_payment_method": {"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "repeated_failure":       {"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "unknown":                {"RETRY_LATER", "ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
}

AMOUNT_BY_BUCKET = {
    "low":    2500,
    "medium": 15000,
    "high":   100000,
}

ACTION_COST = {
    "RETRY_LATER":        1.0,
    "ALTERNATIVE_PAYMENT": 3.0,
    "CUSTOMER_MESSAGE":   0.5,
    "STOP":               0.0,
}

# Static baseline sequence — fixed, context-independent
BASELINE_SEQUENCE = ["RETRY_LATER_1h", "RETRY_LATER_24h"]


@dataclass
class StrategyTotals:
    transactions: int = 0
    recovered: int = 0
    gross_recovered_paise: int = 0
    action_cost_paise: int = 0
    net_recovered_paise: int = 0
    retry_count: int = 0
    unnecessary_retries: int = 0
    recovery_actions: int = 0
    failed_recovery_actions: int = 0
    stopped: int = 0
    action_counts: dict[str, int] = field(default_factory=dict)

    @property
    def recovery_rate(self) -> float:
        return self.recovered / self.transactions if self.transactions else 0.0


def _amount_for(row: pd.Series) -> int:
    return AMOUNT_BY_BUCKET.get(str(row["amount_bucket"]), AMOUNT_BY_BUCKET["medium"])


def _split_key(key: str) -> tuple[str, int]:
    action_type, delay = key.rsplit("_", 1)
    return action_type, int(delay.replace("h", ""))


def _row_context(row: pd.Series) -> dict:
    context = {col: row[col] for col in CONTEXT_COLUMNS}
    for col in NUMERIC_CONTEXT_COLUMNS:
        context[col] = float(context[col]) if col == "historical_success_rate" else int(context[col])
    return context


def _group_held_out(df: pd.DataFrame):
    for _, group in df.groupby(CONTEXT_COLUMNS, sort=False, dropna=False):
        outcomes = {}
        first = group.iloc[0]
        for _, row in group.iterrows():
            key = row["action_type"] + "_" + str(int(row["delay_hours"])) + "h"
            outcomes[key] = int(row["recovered"])
        if all(k in outcomes for k in CANDIDATE_KEYS):
            yield first, outcomes


def _charge(totals: StrategyTotals, action_type: str, succeeded: bool) -> None:
    totals.recovery_actions += 1
    totals.action_counts[action_type] = totals.action_counts.get(action_type, 0) + 1
    totals.action_cost_paise += int(ACTION_COST.get(action_type, 0.0) * 100)
    if action_type == "RETRY_LATER":
        totals.retry_count += 1
    if not succeeded:
        totals.failed_recovery_actions += 1
        if action_type == "RETRY_LATER":
            totals.unnecessary_retries += 1


def _evaluate_static(row: pd.Series, outcomes: dict[str, int], totals: StrategyTotals) -> None:
    """Fixed sequence: RETRY_LATER_1h → RETRY_LATER_24h → stop (max MAX_INTERVENTIONS)."""
    amount = _amount_for(row)
    totals.transactions += 1

    for key in BASELINE_SEQUENCE[:MAX_INTERVENTIONS]:
        action_type, _ = _split_key(key)
        succeeded = bool(outcomes[key])
        _charge(totals, action_type, succeeded)
        if succeeded:
            totals.recovered += 1
            totals.gross_recovered_paise += amount
            return

    totals.stopped += 1


def _select_ai_action(
    scores: dict[str, float],
    root_cause: str,
    exclude_key: str | None,
) -> tuple[str, float] | None:
    """Pick the highest-scoring permitted action, skipping exclude_key."""
    permitted = PERMITTED_ACTIONS.get(root_cause, {"RETRY_LATER", "ALTERNATIVE_PAYMENT"})
    best_key, best_score = None, 0.0

    for key, score in scores.items():
        if key == exclude_key:
            continue
        action_type, _ = _split_key(key)
        if action_type not in permitted:
            continue
        if score > best_score:
            best_key, best_score = key, float(score)

    if best_key is None or best_score < MIN_CONFIDENCE:
        return None
    return best_key, best_score


def _evaluate_ai(
    row: pd.Series,
    outcomes: dict[str, int],
    model: predictor_module.RecoveryPredictor,
    totals: StrategyTotals,
) -> None:
    """
    AI gets MAX_INTERVENTIONS attempts.
    After each failure the model reassesses, excluding the already-tried action.
    """
    amount = _amount_for(row)
    totals.transactions += 1
    context = _row_context(row)
    root_cause = str(row["root_cause"])

    last_key: str | None = None
    for _ in range(MAX_INTERVENTIONS):
        scores = model.score(context)
        selected = _select_ai_action(scores, root_cause, exclude_key=last_key)
        if selected is None:
            break

        key, _ = selected
        action_type, _ = _split_key(key)
        succeeded = bool(outcomes[key])
        _charge(totals, action_type, succeeded)

        if succeeded:
            totals.recovered += 1
            totals.gross_recovered_paise += amount
            return

        last_key = key  # don't repeat the same action on the next attempt

    totals.stopped += 1


def _rupees(paise: int) -> float:
    return round(paise / 100, 2)


def _format_strategy(totals: StrategyTotals) -> dict:
    return {
        **asdict(totals),
        "recovery_rate":       round(totals.recovery_rate, 4),
        "gross_recovered_inr": _rupees(totals.gross_recovered_paise),
        "action_cost_inr":     _rupees(totals.action_cost_paise),
        "net_recovered_inr":   _rupees(totals.net_recovered_paise),
    }


def evaluate(
    held_out_path: Path = HELD_OUT_PATH,
    model_path: Path = MODEL_PATH,
    results_path: Path = RESULTS_PATH,
) -> dict:
    df    = pd.read_csv(held_out_path)
    model = predictor_module.load(str(model_path))

    static = StrategyTotals()
    ai     = StrategyTotals()

    for row, outcomes in _group_held_out(df):
        _evaluate_static(row, outcomes, static)
        _evaluate_ai(row, outcomes, model, ai)

    static.net_recovered_paise = static.gross_recovered_paise - static.action_cost_paise
    ai.net_recovered_paise     = ai.gross_recovered_paise     - ai.action_cost_paise

    incremental_net   = ai.net_recovered_paise   - static.net_recovered_paise
    incremental_gross = ai.gross_recovered_paise - static.gross_recovered_paise
    rate_lift         = ai.recovery_rate         - static.recovery_rate
    net_uplift_pct    = (
        incremental_net / static.net_recovered_paise
        if static.net_recovered_paise else 0.0
    )

    results = {
        "dataset": {
            "held_out_path":   str(held_out_path),
            "candidate_rows":  int(len(df)),
            "transactions":    static.transactions,
            "source":          "sealed held_out.csv",
        },
        "assumptions": {
            "amount_by_bucket_paise": AMOUNT_BY_BUCKET,
            "action_cost_inr":        ACTION_COST,
            "static_baseline":        BASELINE_SEQUENCE,
            "ai_threshold":           MIN_CONFIDENCE,
            "max_interventions":      MAX_INTERVENTIONS,
        },
        "static_baseline": _format_strategy(static),
        "ai_strategy":     _format_strategy(ai),
        "uplift": {
            "incremental_gross_recovered_paise": incremental_gross,
            "incremental_net_recovered_paise":   incremental_net,
            "incremental_gross_recovered_inr":   _rupees(incremental_gross),
            "incremental_net_recovered_inr":     _rupees(incremental_net),
            "recovery_rate_lift_points":         round(rate_lift * 100, 2),
            "net_uplift_pct":                    round(net_uplift_pct * 100, 2),
        },
    }

    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def _print_summary(results: dict) -> None:
    s = results["static_baseline"]
    a = results["ai_strategy"]
    u = results["uplift"]
    d = results["assumptions"]

    print("=" * 52)
    print("HELD-OUT EVALUATION")
    print(f"Transactions:      {results['dataset']['transactions']}")
    print(f"Max interventions: {d['max_interventions']}  (both strategies)")
    print(f"AI threshold:      {d['ai_threshold']}")
    print("=" * 52)
    print()
    print("STATIC BASELINE  (RETRY_LATER_1h -> RETRY_LATER_24h)")
    print(f"  Recovery rate:     {s['recovery_rate']*100:.2f}%")
    print(f"  Gross recovered:   INR {s['gross_recovered_inr']:>12,.2f}")
    print(f"  Net recovered:     INR {s['net_recovered_inr']:>12,.2f}")
    print(f"  Retries:           {s['retry_count']}")
    print(f"  Unnecessary:       {s['unnecessary_retries']}")
    print()
    print("AI STRATEGY  (ML + policy, up to 2 adaptive actions)")
    print(f"  Recovery rate:     {a['recovery_rate']*100:.2f}%")
    print(f"  Gross recovered:   INR {a['gross_recovered_inr']:>12,.2f}")
    print(f"  Net recovered:     INR {a['net_recovered_inr']:>12,.2f}")
    print(f"  Action counts:     {a['action_counts']}")
    print(f"  Unnecessary:       {a['unnecessary_retries']}")
    print()
    print("UPLIFT")
    print(f"  Recovery lift:     {u['recovery_rate_lift_points']:+.2f} percentage points")
    print(f"  Net uplift:        {u['net_uplift_pct']:+.2f}%")
    print(f"  Incremental net:   INR {u['incremental_net_recovered_inr']:>10,.2f}")
    print()
    print(f"Results written: {RESULTS_PATH}")


if __name__ == "__main__":
    _print_summary(evaluate())
