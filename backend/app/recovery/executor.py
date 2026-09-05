"""
Recovery executor.

Handles the actual execution of a scheduled RecoveryAction.

RETRY_LATER execution:
  - Creates a new Razorpay order (new payment session, same amount)
  - Records a new PaymentAttempt against the SAME internal Order (Rule 21)
  - Does NOT autonomously charge the customer
  - The new razorpay_order_id is stored on the action so the frontend can re-open checkout

This is honest and explainable: we are preparing a retry opportunity,
not pretending to autonomously debit anyone.
"""

import razorpay
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
from app.models.models import Order, PaymentAttempt, RecoveryAction

rzp = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


def execute(action: RecoveryAction, db: Session) -> dict:
    """
    Execute a scheduled recovery action.
    Returns a result dict describing what happened.
    """
    # Execution must never inherit evidence from a prior action or attempt.
    # Individual executors set a reference only after creating their artifact.
    action.intervention_ref = None

    if action.action_type == "RETRY_LATER":
        return _execute_retry(action, db)
    elif action.action_type == "ALTERNATIVE_PAYMENT":
        return _execute_alternative(action, db)
    elif action.action_type == "CUSTOMER_MESSAGE":
        return _execute_customer_message(action, db)
    elif action.action_type == "STOP":
        return _execute_stop(action, db)
    else:
        return {"status": "skipped", "reason": f"unhandled action_type: {action.action_type}"}


def _execute_retry(action: RecoveryAction, db: Session) -> dict:
    order = db.get(Order, action.order_id)
    if not order:
        return {"status": "failed", "reason": "order not found"}

    # Create a new Razorpay payment session for the same amount
    # This is a new checkout session, NOT a new business order
    try:
        rzp_order = rzp.order.create({
            "amount": order.amount,
            "currency": order.currency,
            "payment_capture": 1,
            "notes": {
                "recovery_action_id": str(action.id),
                "original_order_id": str(order.id),
                "retry_attempt": "true",
            },
        })
    except Exception as exc:
        return {"status": "failed", "reason": f"razorpay error: {exc}"}

    # Count existing attempts for this order
    attempt_count = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.order_id == order.id)
        .count()
    )

    # Record the retry attempt against the SAME internal order (Rule 21)
    attempt = PaymentAttempt(
        order_id=order.id,
        razorpay_order_id=rzp_order["id"],  # trace the retry session
        razorpay_payment_id=None,           # not yet paid — awaiting customer action
        attempt_number=attempt_count + 1,
        payment_method=None,                # unknown until customer pays
        status="created",
    )
    db.add(attempt)

    # Store concrete evidence that this action created the retry payment session.
    action.intervention_ref = rzp_order["id"]

    # Keep the original Razorpay order id on Order so late native checkout
    # success can still be recognized as native checkout, not AI recovery.
    order.status = "recovery_in_progress"
    order.version_id += 1

    return {
        "status": "executed",
        "new_razorpay_order_id": rzp_order["id"],
        "attempt_number": attempt_count + 1,
    }


def _execute_alternative(action: RecoveryAction, db: Session) -> dict:
    """
    For ALTERNATIVE_PAYMENT: create a Razorpay Payment Link.
    The link can be sent to the customer to complete payment via any method.
    """
    order = db.get(Order, action.order_id)
    if not order:
        return {"status": "failed", "reason": "order not found"}

    try:
        payment_link = rzp.payment_link.create({
            "amount": order.amount,
            "currency": order.currency,
            "reference_id": f"recovery_action_{action.id}",
            "description": "Payment recovery — please complete your payment",
            "customer": {
                "email": order.customer.email if order.customer else "customer@demo.com",
            },
            "notify": {"email": False, "sms": False},
            "reminder_enable": False,
            "notes": {
                "recovery_action_id": str(action.id),
                "original_order_id": str(order.id),
            },
        })
    except Exception as exc:
        return {"status": "failed", "reason": f"razorpay error: {exc}"}

    action.intervention_ref = payment_link.get("id")
    order.status = "recovery_in_progress"
    order.version_id += 1

    return {
        "status": "executed",
        "payment_link_id": payment_link.get("id"),
        "payment_link_url": payment_link.get("short_url"),
    }


def _execute_stop(action: RecoveryAction, db: Session) -> dict:
    # STOP creates no intervention and must never retain an earlier reference.
    action.intervention_ref = None
    order = db.get(Order, action.order_id)
    if not order:
        return {"status": "failed", "reason": "order not found"}

    # The customer may complete a native retry after STOP was scheduled. Refresh
    # immediately before halting so this delayed action cannot overwrite payment.
    db.refresh(order)
    has_captured_payment = (
        db.query(PaymentAttempt.id)
        .filter(
            PaymentAttempt.order_id == order.id,
            PaymentAttempt.status == "captured",
        )
        .first()
        is not None
    )
    if order.status == "paid" or has_captured_payment:
        return {"status": "stale", "reason": "order already paid"}

    order.status = "unrecoverable_halt"
    order.version_id += 1
    return {"status": "executed", "reason": "recovery halted by policy"}


def _execute_customer_message(action: RecoveryAction, db: Session) -> dict:
    """
    Records a customer recovery message.
    In a real system this would send an email/SMS.
    In demo/test mode we record the message text from the action's explanation.
    """
    order = db.get(Order, action.order_id)
    if not order:
        return {"status": "failed", "reason": "order not found"}

    message = action.explanation or (
        f"We noticed your payment of {order.currency} {order.amount / 100:.2f} "
        "was unsuccessful. Please retry or use an alternative payment method."
    )

    # In production: send via email/SMS provider
    # For demo: message is stored in the action's explanation field already
    print(f"[executor] customer message for order {order.id}: {message}")

    message_ref = f"message_{action.id}"
    action.intervention_ref = message_ref
    order.status = "recovery_in_progress"
    order.version_id += 1

    return {
        "status": "executed",
        "message_ref": message_ref,
        "message_preview": message[:120],
    }
