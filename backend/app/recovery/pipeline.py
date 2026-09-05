"""
Recovery pipeline.

Flow:
  1. Guard checks (order state, max retries)
  2. Root cause classification
  3. Build observable feature context
  4. Score candidate actions via ML predictor
  5. Policy layer filters permitted actions and enforces guardrails
  6. Best permitted action above threshold wins
  7. If nothing clears threshold -> STOP
  8. Create RecoveryAction record, transition order state
"""

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.clock import get_simulated_now
from app.config import DEMO_CUSTOMER_HISTORY
from app.models.models import Order, PaymentAttempt, RecoveryAction
from app.recovery.root_cause import classify
from app.recovery.explainer import explain
import app.recovery.predictor as predictor_module

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------
MAX_RECOVERY_ATTEMPTS = 3
MIN_CONFIDENCE = 0.40          # actions below this threshold are not taken

# Which action types are permitted per root cause
# Keys not listed fall back to the default set
_PERMITTED_ACTIONS: dict[str, set[str]] = {
    "temporary_failure":     {"RETRY_LATER", "ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "bank_unavailable":      {"RETRY_LATER", "ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "processor_timeout":     {"RETRY_LATER", "ALTERNATIVE_PAYMENT"},
    "insufficient_funds":    {"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "card_expired":          {"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "invalid_payment_method":{"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "repeated_failure":      {"ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
    "unknown":               {"RETRY_LATER", "ALTERNATIVE_PAYMENT", "CUSTOMER_MESSAGE"},
}

# Best delay per root cause used when RETRY_LATER wins
_RETRY_DELAY_HOURS: dict[str, int] = {
    "temporary_failure":  1,
    "bank_unavailable":   6,
    "processor_timeout":  1,
    "insufficient_funds": 24,
    "unknown":            6,
}


def run(order_id: int, db: Session) -> RecoveryAction | None:
    order = db.get(Order, order_id)
    if not order:
        return None

    if order.status not in ("failed",):
        return None

    # Enforce max recovery attempts
    existing_count = (
        db.query(RecoveryAction)
        .filter(RecoveryAction.order_id == order_id)
        .count()
    )
    if existing_count >= MAX_RECOVERY_ATTEMPTS:
        return _create_action(
            order, db,
            action_type="STOP",
            reasoning=f"Maximum recovery attempts ({MAX_RECOVERY_ATTEMPTS}) reached. Halting.",
            decision_source="policy",
            confidence=1.0,
            scheduled_for=get_simulated_now(),
        )

    # Get latest failed attempt for error signals
    latest: PaymentAttempt = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.order_id == order_id, PaymentAttempt.status == "failed")
        .order_by(PaymentAttempt.id.desc())
        .first()
    )
    if not latest:
        return None

    root_cause = classify(
        error_code=latest.error_code,
        error_description=latest.error_description,
        error_source=latest.error_source,
        attempt_number=latest.attempt_number,
        payment_method=latest.payment_method,
    )

    # Build observable feature context for the ML model
    context = _build_context(order, latest, root_cause, db)

    # Score candidate actions
    predictor = predictor_module.get()
    if predictor is not None:
        scores = predictor.score(context)
        decision_source = "ml"
        # Stamp the identity of the artifact that just scored this decision.
        # Derived from the loaded artifact, never from DEMO_MODEL/config.
        model_key = predictor_module.loaded_model_key()
    else:
        # Fallback to rule-based defaults if model not loaded
        scores = _rule_based_scores(root_cause)
        decision_source = "rules"
        model_key = None

    # Policy: filter to permitted actions for this root cause
    permitted = _PERMITTED_ACTIONS.get(root_cause, {"RETRY_LATER", "ALTERNATIVE_PAYMENT"})

    best_action, best_score, best_delay = _select_best(scores, permitted)

    if best_action is None or best_score < MIN_CONFIDENCE:
        return _create_action(
            order, db,
            action_type="STOP",
            reasoning=(
                f"Root cause '{root_cause}'. "
                f"No permitted action cleared confidence threshold ({MIN_CONFIDENCE}). "
                f"Best score was {best_score:.3f}."
            ),
            decision_source="policy",
            confidence=best_score or 0.0,
            scheduled_for=get_simulated_now(),
            model=model_key,
        )

    # Determine delay for RETRY_LATER
    if best_action == "RETRY_LATER":
        delay_hours = best_delay if best_delay else _RETRY_DELAY_HOURS.get(root_cause, 6)
    else:
        delay_hours = 0

    scheduled_for = get_simulated_now() + timedelta(hours=delay_hours)

    reasoning = (
        f"Root cause: '{root_cause}'. "
        f"ML score for {best_action}: {best_score:.3f}. "
        f"All scores: {_format_scores(scores, permitted)}."
    )

    explanation = explain(
        root_cause=root_cause,
        action_type=best_action,
        scores=scores,
        permitted=list(permitted),
    )

    action = _create_action(
        order, db,
        action_type=best_action,
        reasoning=reasoning,
        explanation=explanation,
        decision_source=decision_source,
        confidence=best_score,
        scheduled_for=scheduled_for,
        model=model_key,
    )

    order.status = "recovery_in_progress"
    order.version_id += 1
    return action


def _build_context(order: Order, attempt: PaymentAttempt, root_cause: str, db: Session) -> dict:
    from app.clock import get_simulated_now
    now = get_simulated_now()

    # Customer historical features — use defaults if no history available
    customer = order.customer
    all_attempts = order.payment_attempts
    total_attempts = len(all_attempts)
    captured = sum(1 for a in all_attempts if a.status == "captured")
    failed_attempts = sum(1 for a in all_attempts if a.status == "failed")

    success_rate = (captured / total_attempts) if total_attempts > 0 else 0.5

    # Demo-only customer history: derive from real Customer/Order rows.
    # Disabled by default -> literals 1/30 (production behavior unchanged).
    if DEMO_CUSTOMER_HISTORY and customer is not None:
        total_orders = (
            db.query(func.count(Order.id))
            .filter(Order.customer_id == customer.id)
            .scalar()
        ) or 0
        if customer.created_at:
            now_naive = now.replace(tzinfo=None)  # DB stores naive UTC
            customer_age_days = max((now_naive - customer.created_at).days, 1)
        else:
            customer_age_days = 30  # legacy/NULL fallback
    else:
        total_orders = 1
        customer_age_days = 30

    return {
        "historical_success_rate":    success_rate,
        "historical_failure_count":   failed_attempts,
        "consecutive_failures":       attempt.attempt_number,
        "total_orders":               total_orders,
        "customer_age_days":          customer_age_days,
        "previous_recovery_success":  0,
        "attempt_number":            attempt.attempt_number,
        "hour_of_day":               now.hour,
        "day_of_week":               now.weekday(),
        "root_cause":                root_cause,
        "payment_method":            attempt.payment_method or "unknown",
        "amount_bucket":             _amount_bucket(order.amount),
    }


def _amount_bucket(amount: int) -> str:
    if amount < 5000:
        return "low"
    if amount < 50000:
        return "medium"
    return "high"


def _select_best(
    scores: dict[str, float],
    permitted: set[str],
) -> tuple[str | None, float, int | None]:
    """Return (action_type, score, delay_hours) for the best permitted action."""
    best_action = None
    best_score = 0.0
    best_delay = None

    for key, score in scores.items():
        # key format: "RETRY_LATER_6h" or "ALTERNATIVE_PAYMENT_0h"
        parts = key.rsplit("_", 1)
        action_type = parts[0]
        delay_hours = int(parts[1].replace("h", ""))

        if action_type not in permitted:
            continue
        if score > best_score:
            best_score = score
            best_action = action_type
            best_delay = delay_hours

    return best_action, best_score, best_delay


def _rule_based_scores(root_cause: str) -> dict[str, float]:
    """Fallback scores when model is not loaded."""
    defaults = {
        "RETRY_LATER_1h":          0.45,
        "RETRY_LATER_6h":          0.55,
        "RETRY_LATER_24h":         0.50,
        "ALTERNATIVE_PAYMENT_0h":  0.65,
        "CUSTOMER_MESSAGE_0h":     0.35,
    }
    return defaults


def _format_scores(scores: dict[str, float], permitted: set[str]) -> str:
    parts = []
    for key, score in sorted(scores.items(), key=lambda x: -x[1]):
        action_type = key.rsplit("_", 1)[0]
        marker = "" if action_type in permitted else " [blocked]"
        parts.append(f"{key}={score:.3f}{marker}")
    return ", ".join(parts)


def _create_action(
    order: Order,
    db: Session,
    action_type: str,
    reasoning: str,
    decision_source: str,
    confidence: float,
    scheduled_for,
    explanation: str = "",
    model: str | None = None,
) -> RecoveryAction:
    action = RecoveryAction(
        order_id=order.id,
        action_type=action_type,
        status="scheduled",
        reasoning=reasoning,
        explanation=explanation,
        decision_source=decision_source,
        model=model,
        confidence=confidence,
        scheduled_for=scheduled_for,
        outcome="pending",
        # A recovery decision is not evidence of an intervention. The executor
        # assigns this only after it has successfully created one.
        intervention_ref=None,
    )
    db.add(action)
    db.flush()
    return action
