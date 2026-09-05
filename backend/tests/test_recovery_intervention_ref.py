from datetime import datetime
from uuid import uuid4

from app.database import SessionLocal
from app.api.webhooks import _handle_captured, _handle_payment_link_paid
from app.models.models import Customer, Order, PaymentAttempt, RecoveryAction, RecoveryOutcome
from app.recovery.executor import execute
from app.recovery.pipeline import run
from app.recovery import predictor as predictor_module
from app.recovery.predictor import MODEL_PATH, loaded_model_key


def test_fresh_failed_payment_stop_has_no_intervention_ref():
    """A STOP decision cannot inherit the retry reference from an earlier action."""
    db = SessionLocal()
    try:
        suffix = uuid4().hex
        customer = Customer(
            id=f"test_stop_customer_{suffix}",
            name="Recovery Test",
            email="recovery-test@example.com",
        )
        order = Order(
            razorpay_order_id=f"order_test_stop_{suffix}",
            customer=customer,
            amount=10000,
            currency="INR",
            status="failed",
        )
        db.add(order)
        db.flush()

        db.add(PaymentAttempt(
            order_id=order.id,
            razorpay_payment_id=f"pay_test_stop_{suffix}",
            attempt_number=1,
            payment_method="card",
            status="failed",
            error_code="BAD_REQUEST_ERROR",
        ))
        # The max-attempt guard produces STOP without invoking the predictor.
        db.add_all([
            RecoveryAction(
                order_id=order.id,
                action_type="RETRY_LATER",
                status="executed",
                scheduled_for=datetime.utcnow(),
                intervention_ref="order_previous_retry",
            ),
            RecoveryAction(
                order_id=order.id,
                action_type="ALTERNATIVE_PAYMENT",
                status="executed",
                scheduled_for=datetime.utcnow(),
                intervention_ref="plink_previous_payment",
            ),
            RecoveryAction(
                order_id=order.id,
                action_type="CUSTOMER_MESSAGE",
                status="executed",
                scheduled_for=datetime.utcnow(),
                intervention_ref="message_previous",
            ),
        ])
        db.flush()

        stop = run(order.id, db)
        assert stop is not None
        assert stop.action_type == "STOP"
        assert stop.intervention_ref is None

        # Defend against any stale in-memory value before the scheduler executes.
        stop.intervention_ref = "order_previous_retry"
        result = execute(stop, db)
        assert result["status"] == "executed"
        assert order.status == "unrecoverable_halt"
        assert stop.intervention_ref is None
    finally:
        db.rollback()
        db.close()


def test_captured_manual_retry_makes_pending_stop_stale():
    """A delayed STOP cannot overwrite a native/manual successful retry."""
    db = SessionLocal()
    try:
        suffix = uuid4().hex
        customer = Customer(
            id=f"test_stop_race_customer_{suffix}",
            name="Recovery Test",
            email="recovery-test@example.com",
        )
        order = Order(
            razorpay_order_id=f"order_test_stop_race_{suffix}",
            customer=customer,
            amount=10000,
            currency="INR",
            status="failed",
        )
        db.add(order)
        db.flush()
        db.add(PaymentAttempt(
            order_id=order.id,
            razorpay_payment_id=f"pay_failed_{suffix}",
            attempt_number=1,
            payment_method="card",
            status="failed",
        ))
        stop = RecoveryAction(
            order_id=order.id,
            action_type="STOP",
            status="scheduled",
            scheduled_for=datetime.utcnow(),
            outcome="pending",
            intervention_ref=None,
        )
        db.add(stop)
        db.flush()

        _handle_captured({
            "id": f"pay_manual_retry_{suffix}",
            "order_id": order.razorpay_order_id,
            "amount": order.amount,
            "currency": order.currency,
            "status": "captured",
            "method": "card",
        }, db)
        db.flush()

        result = execute(stop, db)
        assert result["status"] == "stale"
        assert order.status == "paid"
        assert stop.intervention_ref is None

        outcome = db.query(RecoveryOutcome).filter_by(order_id=order.id).one()
        assert outcome.recovered is True
        assert outcome.recovery_action_id is None
        assert outcome.recovery_source == "native_checkout"
    finally:
        db.rollback()
        db.close()


def test_ai_recovery_outcome_keeps_action_foreign_key():
    """Verified Payment Link recovery retains its AI action reference."""
    db = SessionLocal()
    try:
        suffix = uuid4().hex
        customer = Customer(
            id=f"test_ai_outcome_customer_{suffix}",
            name="Recovery Test",
            email="recovery-test@example.com",
        )
        order = Order(
            razorpay_order_id=f"order_test_ai_outcome_{suffix}",
            customer=customer,
            amount=10000,
            currency="INR",
            status="recovery_in_progress",
        )
        db.add(order)
        db.flush()
        db.add(PaymentAttempt(
            order_id=order.id,
            razorpay_payment_id=f"pay_failed_{suffix}",
            attempt_number=1,
            payment_method="card",
            status="failed",
        ))
        action = RecoveryAction(
            order_id=order.id,
            action_type="ALTERNATIVE_PAYMENT",
            status="executed",
            scheduled_for=datetime.utcnow(),
            intervention_ref=f"plink_test_{suffix}",
        )
        db.add(action)
        db.flush()

        _handle_payment_link_paid({
            "id": f"pay_link_paid_{suffix}",
            "amount": order.amount,
            "currency": order.currency,
            "status": "captured",
            "method": "upi",
        }, {
            "id": action.intervention_ref,
            "status": "paid",
            "amount": order.amount,
            "currency": order.currency,
            "reference_id": f"recovery_action_{action.id}",
            "notes": {
                "recovery_action_id": str(action.id),
                "original_order_id": str(order.id),
            },
        }, db)
        db.flush()

        outcome = db.query(RecoveryOutcome).filter_by(order_id=order.id).one()
        assert outcome.recovered is True
        assert outcome.recovery_action_id == action.id
        assert outcome.recovery_source == "AI_ACTION"
    finally:
        db.rollback()
        db.close()


def test_new_ml_action_records_loaded_model_key():
    """New ML decisions stamp the loaded artifact key; rule decisions stay NULL.

    The key is derived from the loaded artifact path (explicit GB here), never
    from DEMO_MODEL. Legacy rows are untouched by this test because they are
    created directly (model column stays NULL).
    """
    db = SessionLocal()
    prev = predictor_module.get()
    try:
        # Load a specific artifact so the expected key is deterministic.
        predictor_module.load(model_path=MODEL_PATH)
        assert loaded_model_key() == "gb"

        suffix = uuid4().hex
        customer = Customer(
            id=f"test_model_customer_{suffix}",
            name="Recovery Test",
            email="recovery-test@example.com",
        )
        order = Order(
            razorpay_order_id=f"order_test_model_{suffix}",
            customer=customer,
            amount=100000,
            currency="INR",
            status="failed",
        )
        db.add(order)
        db.flush()
        # Prior captured attempt lifts success_rate to 0.5 -> GB RETRY_LATER
        # comfortably clears MIN_CONFIDENCE (measured 0.635).
        db.add(PaymentAttempt(
            order_id=order.id,
            razorpay_payment_id=f"pay_captured_{suffix}",
            attempt_number=1,
            payment_method="netbanking",
            status="captured",
        ))
        db.add(PaymentAttempt(
            order_id=order.id,
            razorpay_payment_id=f"pay_failed_{suffix}",
            attempt_number=2,
            payment_method="netbanking",
            status="failed",
            error_code="SERVER_ERROR",
            error_description="Unable to process payment, please try again.",
            error_source="gateway",
        ))
        db.flush()

        action = run(order.id, db)
        assert action is not None
        assert action.decision_source == "ml"
        assert action.model == "gb"

        # No model in the singleton -> rule-based fallback stores NULL.
        predictor_module._predictor = None
        order2 = Order(
            razorpay_order_id=f"order_test_model_rules_{suffix}",
            customer=customer,
            amount=100000,
            currency="INR",
            status="failed",
        )
        db.add(order2)
        db.flush()
        db.add(PaymentAttempt(
            order_id=order2.id,
            razorpay_payment_id=f"pay_failed_rules_{suffix}",
            attempt_number=1,
            payment_method="netbanking",
            status="failed",
            error_code="SERVER_ERROR",
            error_description="Unable to process payment, please try again.",
            error_source="gateway",
        ))
        db.flush()

        rules_action = run(order2.id, db)
        assert rules_action is not None
        assert rules_action.decision_source == "rules"
        assert rules_action.model is None
    finally:
        predictor_module._predictor = prev
        db.rollback()
        db.close()
