"""
Dataset builder.

Generates synthetic transaction rows and writes three CSV files:
  data/train.csv
  data/validation.csv
  data/held_out.csv

Each row is one (customer, failure, candidate_action) observation.
The target column is `recovered` (0 or 1).

The held-out set is written but not used until final evaluation.
"""

import csv
import os
import random
from dataclasses import asdict

from simulator.customer_generator import SyntheticCustomer, generate
from simulator.behavior_model import FailureContext, sample_outcome

# Candidate actions the model scores (STOP is excluded — it's a policy outcome)
CANDIDATE_ACTIONS = [
    ("RETRY_LATER",         1),
    ("RETRY_LATER",         6),
    ("RETRY_LATER",         24),
    ("ALTERNATIVE_PAYMENT", 0),
    ("CUSTOMER_MESSAGE",    0),
]

ROOT_CAUSES = [
    "temporary_failure", "bank_unavailable", "processor_timeout",
    "insufficient_funds", "card_expired", "invalid_payment_method",
    "repeated_failure", "unknown",
]

PAYMENT_METHODS = ["netbanking", "card", "upi", "wallet"]

AMOUNT_BUCKETS = {
    "low":    (100,   4999),
    "medium": (5000,  49999),
    "high":   (50000, 500000),
}


def _amount_bucket(amount: int) -> str:
    if amount < 5000:
        return "low"
    if amount < 50000:
        return "medium"
    return "high"


def _generate_failure(rng: random.Random) -> FailureContext:
    root_cause = rng.choice(ROOT_CAUSES)
    method = rng.choice(PAYMENT_METHODS)
    attempt = rng.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]
    bucket = rng.choice(list(AMOUNT_BUCKETS.keys()))
    lo, hi = AMOUNT_BUCKETS[bucket]
    amount = rng.randint(lo, hi)
    hour = rng.randint(0, 23)
    dow = rng.randint(0, 6)
    consec = rng.choices([1, 2, 3, 4], weights=[0.5, 0.3, 0.15, 0.05])[0]
    return FailureContext(
        root_cause=root_cause,
        payment_method=method,
        attempt_number=attempt,
        amount=amount,
        hour_of_day=hour,
        day_of_week=dow,
        consecutive_failures=consec,
    )


def _build_rows(
    customers: list[SyntheticCustomer],
    failures_per_customer: int,
    rng: random.Random,
) -> list[dict]:
    rows = []
    for customer in customers:
        for _ in range(failures_per_customer):
            failure = _generate_failure(rng)
            for action_type, delay_hours in CANDIDATE_ACTIONS:
                outcome = sample_outcome(customer, failure, action_type, delay_hours, rng)
                rows.append({
                    # Customer features
                    "historical_success_rate":   customer.historical_success_rate,
                    "historical_failure_count":  customer.historical_failure_count,
                    "consecutive_failures":      failure.consecutive_failures,
                    "total_orders":              customer.total_orders,
                    "customer_age_days":         customer.customer_age_days,
                    "previous_recovery_success": customer.previous_recovery_success,
                    # Failure features
                    "root_cause":                failure.root_cause,
                    "payment_method":            failure.payment_method,
                    "attempt_number":            failure.attempt_number,
                    "amount_bucket":             _amount_bucket(failure.amount),
                    "hour_of_day":               failure.hour_of_day,
                    "day_of_week":               failure.day_of_week,
                    # Action features
                    "action_type":               action_type,
                    "delay_hours":               delay_hours,
                    # Target
                    "recovered":                 outcome,
                })
    return rows


def _write_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"[dataset] wrote {len(rows):,} rows -> {path}")


def build(
    n_train: int = 2000,
    n_validation: int = 500,
    n_held_out: int = 1000,
    failures_per_customer: int = 5,
    output_dir: str = "data",
    seed: int = 42,
) -> None:
    """
    Generate and write all three dataset splits.

    n_train / n_validation / n_held_out are customer counts.
    Each customer contributes failures_per_customer * len(CANDIDATE_ACTIONS) rows.
    """
    rng = random.Random(seed)
    total = n_train + n_validation + n_held_out

    all_customers = generate(total, seed=seed)
    train_custs     = all_customers[:n_train]
    val_custs       = all_customers[n_train:n_train + n_validation]
    held_out_custs  = all_customers[n_train + n_validation:]

    _write_csv(_build_rows(train_custs,    failures_per_customer, rng), f"{output_dir}/train.csv")
    _write_csv(_build_rows(val_custs,      failures_per_customer, rng), f"{output_dir}/validation.csv")
    _write_csv(_build_rows(held_out_custs, failures_per_customer, rng), f"{output_dir}/held_out.csv")

    print(f"[dataset] done - {total} customers, {failures_per_customer} failures each")
    print("[dataset] held_out.csv is sealed - do not use until final evaluation")


if __name__ == "__main__":
    build()
