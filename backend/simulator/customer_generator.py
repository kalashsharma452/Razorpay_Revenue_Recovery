"""
Synthetic customer generator.

Produces customers with observable features only.
Hidden behavior state lives exclusively in behavior_model.py.

Customer segments:
  reliable    — high success rate, frequent buyer
  occasional  — moderate success rate, infrequent
  struggling  — low success rate, often fails
  new         — little history
"""

import random
from dataclasses import dataclass


@dataclass
class SyntheticCustomer:
    customer_id: str
    segment: str

    # Observable features (these go into the ML model)
    historical_success_rate: float
    historical_failure_count: int
    total_orders: int
    customer_age_days: int
    previous_recovery_success: int   # 0 or 1


_SEGMENTS = {
    "reliable":   dict(success_rate=(0.80, 0.99), total_orders=(10, 50),  age_days=(180, 730), failure_count=(0, 5),  prev_recovery=0.8),
    "occasional": dict(success_rate=(0.55, 0.80), total_orders=(3, 15),   age_days=(60, 365),  failure_count=(2, 15), prev_recovery=0.5),
    "struggling": dict(success_rate=(0.20, 0.55), total_orders=(1, 10),   age_days=(7, 180),   failure_count=(5, 30), prev_recovery=0.3),
    "new":        dict(success_rate=(0.50, 0.90), total_orders=(1, 3),    age_days=(1, 60),    failure_count=(0, 3),  prev_recovery=0.0),
}

_SEGMENT_WEIGHTS = [0.40, 0.30, 0.20, 0.10]  # reliable, occasional, struggling, new


def generate(n: int, seed: int = 42) -> list[SyntheticCustomer]:
    rng = random.Random(seed)
    customers = []
    segments = list(_SEGMENTS.keys())

    for i in range(n):
        segment = rng.choices(segments, weights=_SEGMENT_WEIGHTS, k=1)[0]
        cfg = _SEGMENTS[segment]

        success_rate = round(rng.uniform(*cfg["success_rate"]), 3)
        total_orders = rng.randint(*cfg["total_orders"])
        failure_count = rng.randint(*cfg["failure_count"])
        age_days = rng.randint(*cfg["age_days"])
        prev_recovery = int(rng.random() < cfg["prev_recovery"])

        customers.append(SyntheticCustomer(
            customer_id=f"sim_cust_{i:05d}",
            segment=segment,
            historical_success_rate=success_rate,
            historical_failure_count=failure_count,
            total_orders=total_orders,
            customer_age_days=age_days,
            previous_recovery_success=prev_recovery,
        ))

    return customers
