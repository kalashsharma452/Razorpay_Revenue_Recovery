"""
Hidden behavior model.

This is the ground truth for the synthetic evaluation.

ISOLATION RULE: This file must NEVER be imported by:
  - app/recovery/pipeline.py
  - app/recovery/predictor.py (Day 5)
  - any ML training code

It is only used by:
  - simulator/dataset_builder.py  (to generate training data)
  - simulator/evaluator.py        (to run held-out evaluation)

The ML model observes only the binary outcome this produces,
never the hidden probabilities themselves.
"""

import random
from dataclasses import dataclass

from simulator.customer_generator import SyntheticCustomer


@dataclass
class FailureContext:
    root_cause: str        # from root_cause.py categories
    payment_method: str    # netbanking / card / upi / wallet
    attempt_number: int
    amount: int            # paise
    hour_of_day: int
    day_of_week: int
    consecutive_failures: int


# Base recovery probabilities per action type
# These are the hidden priors — the model must learn to approximate these
_BASE_PROBS = {
    ("temporary_failure",    "RETRY_LATER",          1):  0.72,
    ("temporary_failure",    "RETRY_LATER",          6):  0.78,
    ("temporary_failure",    "RETRY_LATER",          24): 0.65,
    ("temporary_failure",    "ALTERNATIVE_PAYMENT",  0):  0.60,
    ("temporary_failure",    "CUSTOMER_MESSAGE",     0):  0.35,

    ("bank_unavailable",     "RETRY_LATER",          1):  0.45,
    ("bank_unavailable",     "RETRY_LATER",          6):  0.68,
    ("bank_unavailable",     "RETRY_LATER",          24): 0.55,
    ("bank_unavailable",     "ALTERNATIVE_PAYMENT",  0):  0.72,
    ("bank_unavailable",     "CUSTOMER_MESSAGE",     0):  0.30,

    ("processor_timeout",    "RETRY_LATER",          1):  0.70,
    ("processor_timeout",    "RETRY_LATER",          6):  0.74,
    ("processor_timeout",    "RETRY_LATER",          24): 0.60,
    ("processor_timeout",    "ALTERNATIVE_PAYMENT",  0):  0.55,
    ("processor_timeout",    "CUSTOMER_MESSAGE",     0):  0.28,

    ("insufficient_funds",   "RETRY_LATER",          1):  0.15,
    ("insufficient_funds",   "RETRY_LATER",          6):  0.22,
    ("insufficient_funds",   "RETRY_LATER",          24): 0.38,
    ("insufficient_funds",   "ALTERNATIVE_PAYMENT",  0):  0.50,
    ("insufficient_funds",   "CUSTOMER_MESSAGE",     0):  0.42,

    ("card_expired",         "RETRY_LATER",          1):  0.05,
    ("card_expired",         "RETRY_LATER",          6):  0.05,
    ("card_expired",         "RETRY_LATER",          24): 0.05,
    ("card_expired",         "ALTERNATIVE_PAYMENT",  0):  0.65,
    ("card_expired",         "CUSTOMER_MESSAGE",     0):  0.55,

    ("invalid_payment_method","RETRY_LATER",         1):  0.08,
    ("invalid_payment_method","RETRY_LATER",         6):  0.08,
    ("invalid_payment_method","RETRY_LATER",         24): 0.08,
    ("invalid_payment_method","ALTERNATIVE_PAYMENT", 0):  0.60,
    ("invalid_payment_method","CUSTOMER_MESSAGE",    0):  0.48,

    ("repeated_failure",     "RETRY_LATER",          1):  0.10,
    ("repeated_failure",     "RETRY_LATER",          6):  0.15,
    ("repeated_failure",     "RETRY_LATER",          24): 0.20,
    ("repeated_failure",     "ALTERNATIVE_PAYMENT",  0):  0.35,
    ("repeated_failure",     "CUSTOMER_MESSAGE",     0):  0.25,

    ("unknown",              "RETRY_LATER",          1):  0.40,
    ("unknown",              "RETRY_LATER",          6):  0.50,
    ("unknown",              "RETRY_LATER",          24): 0.45,
    ("unknown",              "ALTERNATIVE_PAYMENT",  0):  0.50,
    ("unknown",              "CUSTOMER_MESSAGE",     0):  0.30,
}


def _get_base_prob(root_cause: str, action_type: str, delay_hours: int) -> float:
    return _BASE_PROBS.get((root_cause, action_type, delay_hours), 0.30)


def recovery_probability(
    customer: SyntheticCustomer,
    failure: FailureContext,
    action_type: str,
    delay_hours: int,
) -> float:
    """
    Compute the true hidden recovery probability.
    This is the ground truth the ML model must approximate from observations.
    """
    p = _get_base_prob(failure.root_cause, action_type, delay_hours)

    # Customer segment modifiers
    p *= (0.5 + customer.historical_success_rate)   # high success rate → more likely to recover
    p *= max(0.4, 1.0 - 0.03 * failure.consecutive_failures)  # repeated failures reduce probability
    if customer.previous_recovery_success:
        p *= 1.15   # responded to recovery before

    # Amount modifier — high-value orders have slightly lower recovery rate
    if failure.amount >= 50000:    # ≥ ₹500
        p *= 0.90
    elif failure.amount <= 5000:   # ≤ ₹50
        p *= 1.05

    # Time-of-day modifier — bank availability peaks during business hours
    if 9 <= failure.hour_of_day <= 18:
        p *= 1.08
    elif failure.hour_of_day < 6 or failure.hour_of_day > 22:
        p *= 0.85

    # Attempt number penalty
    p *= max(0.3, 1.0 - 0.15 * (failure.attempt_number - 1))

    return min(max(p, 0.01), 0.99)


def sample_outcome(
    customer: SyntheticCustomer,
    failure: FailureContext,
    action_type: str,
    delay_hours: int,
    rng: random.Random,
) -> int:
    """Sample a binary outcome (1=recovered, 0=not) from the hidden probability."""
    p = recovery_probability(customer, failure, action_type, delay_hours)
    return int(rng.random() < p)
