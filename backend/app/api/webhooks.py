import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import RAZORPAY_KEY_SECRET
from app.database import SessionLocal, get_db
from app.models.models import Order, PaymentAttempt, RecoveryAction, RecoveryOutcome, WebhookEvent

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

CUSTOMER_MESSAGE_ATTRIBUTION_WINDOW = timedelta(hours=72)


def _verify_signature(body: bytes, signature: str) -> bool:
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _next_attempt_number(db: Session, order_id: int) -> int:
    from sqlalchemy import func

    result = db.query(func.count(PaymentAttempt.id)).filter(
        PaymentAttempt.order_id == order_id
    ).scalar()
    return (result or 0) + 1


def _payment_notes(payment: dict) -> dict:
    notes = payment.get("notes") or {}
    return notes if isinstance(notes, dict) else {}


def _payment_created_at(payment: dict) -> datetime:
    created_at = payment.get("created_at")
    if created_at:
        return datetime.fromtimestamp(int(created_at), tz=timezone.utc).replace(tzinfo=None)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _payment_link_refs(payment: dict) -> set[str]:
    refs = {
        payment.get("payment_link_id"),
        payment.get("invoice_id"),
        payment.get("link_id"),
    }
    return {str(ref) for ref in refs if ref}


def _entity_notes(entity: dict) -> dict:
    notes = entity.get("notes") or {}
    return notes if isinstance(notes, dict) else {}


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _action_from_notes(notes: dict, db: Session) -> RecoveryAction | None:
    action_id = _to_int(notes.get("recovery_action_id"))
    return db.get(RecoveryAction, action_id) if action_id else None


def _find_order_for_payment(payment: dict, db: Session) -> Order | None:
    rzp_order_id = payment.get("order_id")
    if rzp_order_id:
        order = db.query(Order).filter(Order.razorpay_order_id == rzp_order_id).first()
        if order:
            return order
        action = (
            db.query(RecoveryAction)
            .filter(
                RecoveryAction.action_type == "RETRY_LATER",
                RecoveryAction.intervention_ref == rzp_order_id,
            )
            .order_by(RecoveryAction.id.desc())
            .first()
        )
        if action:
            return db.get(Order, action.order_id)

    notes = _payment_notes(payment)
    action = _action_from_notes(notes, db)
    if action and action.action_type in ("RETRY_LATER", "ALTERNATIVE_PAYMENT"):
        return db.get(Order, action.order_id)

    return None


def _latest_executed_action(order_id: int, db: Session) -> RecoveryAction | None:
    return (
        db.query(RecoveryAction)
        .filter(RecoveryAction.order_id == order_id, RecoveryAction.status == "executed")
        .order_by(RecoveryAction.id.desc())
        .first()
    )


def _get_or_create_captured_attempt(
    order: Order,
    payment: dict,
    db: Session,
) -> PaymentAttempt:
    payment_id = payment.get("id")
    if payment_id:
        existing = (
            db.query(PaymentAttempt)
            .filter(PaymentAttempt.razorpay_payment_id == payment_id)
            .first()
        )
        if existing:
            return existing

    attempt = PaymentAttempt(
        order_id=order.id,
        razorpay_payment_id=payment_id,
        attempt_number=_next_attempt_number(db, order.id),
        payment_method=payment.get("method"),
        status="captured",
    )
    db.add(attempt)
    db.flush()
    return attempt


def _matches_amount_currency(order: Order, payment: dict, payment_link: dict) -> bool:
    payment_amount = payment.get("amount")
    link_amount = payment_link.get("amount")
    payment_currency = payment.get("currency")
    link_currency = payment_link.get("currency")

    if payment_amount is not None and int(payment_amount) != int(order.amount):
        return False
    if link_amount is not None and int(link_amount) != int(order.amount):
        return False
    if payment_currency and payment_currency != order.currency:
        return False
    if link_currency and link_currency != order.currency:
        return False
    return True


def _is_verified_payment_link_action(
    action: RecoveryAction,
    order: Order,
    payment: dict,
    payment_link: dict,
) -> bool:
    link_id = payment_link.get("id")
    link_order_id = payment_link.get("order_id")
    payment_order_id = payment.get("order_id")
    reference_id = payment_link.get("reference_id")
    link_notes = _entity_notes(payment_link)
    payment_notes = _payment_notes(payment)

    if action.action_type != "ALTERNATIVE_PAYMENT":
        return False
    if action.status != "executed":
        return False
    if not action.intervention_ref or link_id != action.intervention_ref:
        return False
    if payment.get("status") != "captured":
        return False
    if payment_link.get("status") and payment_link.get("status") != "paid":
        return False
    if link_order_id and payment_order_id and link_order_id != payment_order_id:
        return False
    if not _matches_amount_currency(order, payment, payment_link):
        return False

    expected_reference = f"recovery_action_{action.id}"
    if reference_id and reference_id != expected_reference:
        return False

    expected_action_id = str(action.id)
    note_action_id = str(
        link_notes.get("recovery_action_id")
        or payment_notes.get("recovery_action_id")
        or ""
    )
    if note_action_id and note_action_id != expected_action_id:
        return False

    expected_order_id = str(order.id)
    note_order_id = str(
        link_notes.get("original_order_id")
        or payment_notes.get("original_order_id")
        or ""
    )
    if note_order_id and note_order_id != expected_order_id:
        return False

    return True


def _is_verified_alternative_payment_capture(
    action: RecoveryAction,
    order: Order,
    payment: dict,
) -> bool:
    notes = _payment_notes(payment)
    note_action_id = str(notes.get("recovery_action_id") or "")
    note_order_id = str(notes.get("original_order_id") or "")

    if action.action_type != "ALTERNATIVE_PAYMENT":
        return False
    if action.status != "executed":
        return False
    if action.order_id != order.id:
        return False
    if not action.intervention_ref:
        return False
    if payment.get("status") != "captured":
        return False
    if note_action_id != str(action.id):
        return False
    if note_order_id and note_order_id != str(order.id):
        return False

    payment_amount = payment.get("amount")
    payment_currency = payment.get("currency")
    if payment_amount is not None and int(payment_amount) != int(order.amount):
        return False
    if payment_currency and payment_currency != order.currency:
        return False

    return True


def _attribute_recovery(
    order: Order,
    payment: dict,
    captured_at: datetime,
    db: Session,
) -> tuple[RecoveryAction | None, str]:
    """
    Attribute a successful payment only when the webhook contains evidence that
    it belongs to a delivered intervention. Otherwise call it native checkout.
    """
    noted_action = _action_from_notes(_payment_notes(payment), db)
    if noted_action and _is_verified_alternative_payment_capture(noted_action, order, payment):
        return noted_action, "AI_ACTION"

    action = _latest_executed_action(order.id, db)
    if not action or not action.intervention_ref:
        return None, "native_checkout"

    payment_order_id = payment.get("order_id")

    if action.action_type == "RETRY_LATER":
        if payment_order_id == action.intervention_ref:
            return action, "ai_retry"
        return None, "native_checkout"

    if action.action_type == "ALTERNATIVE_PAYMENT":
        return None, "native_checkout"

    if action.action_type == "CUSTOMER_MESSAGE":
        if not action.intervention_ref.startswith("message_") or not action.executed_at:
            return None, "native_checkout"
        window_end = action.executed_at + CUSTOMER_MESSAGE_ATTRIBUTION_WINDOW
        if action.executed_at <= captured_at <= window_end:
            return action, "ai_message"
        return None, "native_checkout"

    return None, "native_checkout"


def _record_recovery_outcome(
    order: Order,
    action: RecoveryAction | None,
    recovery_source: str,
    payment: dict,
    attempt: PaymentAttempt,
    db: Session,
) -> None:
    existing = (
        db.query(RecoveryOutcome)
        .filter(
            RecoveryOutcome.order_id == order.id,
            RecoveryOutcome.recovered.is_(True),
        )
        .first()
    )

    first_failure = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.order_id == order.id, PaymentAttempt.status == "failed")
        .order_by(PaymentAttempt.attempt_number)
        .first()
    )

    if action:
        action.outcome = "recovered"

    from app.recovery.explainer import explain_outcome

    outcome_explanation = explain_outcome(
        failed_method=first_failure.payment_method or "unknown" if first_failure else "unknown",
        failed_reason=first_failure.error_description or "unknown" if first_failure else "unknown",
        success_method=payment.get("method") or "unknown",
        attempt_count=attempt.attempt_number,
    )

    outcome = existing or RecoveryOutcome(order_id=order.id)
    outcome.recovery_action_id = action.id if action else None
    outcome.recovered = True
    outcome.amount_recovered = order.amount
    outcome.recovery_source = recovery_source
    outcome.razorpay_payment_id = payment.get("id")
    outcome.explanation = outcome_explanation
    if existing is None:
        db.add(outcome)


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_razorpay_signature: str = Header(...),
    db: Session = Depends(get_db),
):
    body = await request.body()

    sig_ok = _verify_signature(body, x_razorpay_signature)
    if not sig_ok:
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)
    event_id = payload.get("id") or hashlib.sha256(body).hexdigest()
    event_type = payload.get("event", "")

    existing = db.get(WebhookEvent, event_id)
    if existing:
        return {"status": "duplicate", "event_id": event_id}

    webhook = WebhookEvent(
        razorpay_event_id=event_id,
        event_type=event_type,
        raw_payload=body.decode(),
        processed=False,
    )
    db.add(webhook)
    db.flush()

    try:
        order_id = _process_event(event_type, payload, db)
        webhook.processed = True
        webhook.processed_at = datetime.now(timezone.utc)
        db.commit()
        if event_type == "payment.failed" and order_id:
            background_tasks.add_task(_run_recovery, order_id)
    except Exception as exc:
        db.rollback()
        webhook2 = WebhookEvent(
            razorpay_event_id=event_id,
            event_type=event_type,
            raw_payload=body.decode(),
            processed=False,
        )
        db.add(webhook2)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc))

    return {"status": "ok", "event": event_type}


def _process_event(event_type: str, payload: dict, db: Session) -> int | None:
    """Process the event and return the internal order_id if relevant."""
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    payment_link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})

    if event_type == "payment.captured":
        _handle_captured(payment_entity, db)
    elif event_type == "payment_link.paid":
        _handle_payment_link_paid(payment_entity, payment_link_entity, db)
    elif event_type == "payment.failed":
        return _handle_failed(payment_entity, db)
    return None


def _handle_payment_link_paid(payment: dict, payment_link: dict, db: Session):
    link_id = payment_link.get("id")
    if not link_id:
        return

    action = (
        db.query(RecoveryAction)
        .filter(
            RecoveryAction.action_type == "ALTERNATIVE_PAYMENT",
            RecoveryAction.intervention_ref == link_id,
        )
        .order_by(RecoveryAction.id.desc())
        .first()
    )
    if not action:
        return

    order = db.get(Order, action.order_id)
    if not order:
        return

    if not _is_verified_payment_link_action(action, order, payment, payment_link):
        return

    attempt = _get_or_create_captured_attempt(order, payment, db)
    order.status = "paid"
    order.version_id += 1
    _record_recovery_outcome(order, action, "AI_ACTION", payment, attempt, db)


def _handle_captured(payment: dict, db: Session):
    order = _find_order_for_payment(payment, db)
    if not order:
        return

    attempt = _get_or_create_captured_attempt(order, payment, db)

    state_before_capture = order.status
    order.status = "paid"
    order.version_id += 1

    had_failed_attempt = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.order_id == order.id, PaymentAttempt.status == "failed")
        .first()
        is not None
    )
    if state_before_capture in ("recovery_in_progress", "failed") or had_failed_attempt:
        action, recovery_source = _attribute_recovery(
            order,
            payment,
            _payment_created_at(payment),
            db,
        )
        _record_recovery_outcome(order, action, recovery_source, payment, attempt, db)


def _handle_failed(payment: dict, db: Session) -> int | None:
    order = _find_order_for_payment(payment, db)
    if not order:
        return None

    attempt = PaymentAttempt(
        order_id=order.id,
        razorpay_payment_id=payment.get("id"),
        attempt_number=_next_attempt_number(db, order.id),
        payment_method=payment.get("method"),
        status="failed",
        error_code=payment.get("error_code"),
        error_description=payment.get("error_description"),
        error_source=payment.get("error_source"),
    )
    db.add(attempt)

    order.status = "failed"
    order.version_id += 1
    return order.id


def _run_recovery(order_id: int) -> None:
    """Background task - runs after webhook has already returned 200."""
    from app.recovery import pipeline

    db = SessionLocal()
    try:
        pipeline.run(order_id, db)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[recovery] pipeline error for order {order_id}: {exc}")
    finally:
        db.close()
