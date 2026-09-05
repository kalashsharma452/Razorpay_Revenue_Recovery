"""
Dashboard API.

Serves all data needed by the merchant dashboard.
Reads directly from existing models — no duplicate data sources.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Order, PaymentAttempt, RecoveryAction, RecoveryOutcome

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

RESULTS_PATH = Path(__file__).resolve().parents[2] / "data" / "evaluation_results.json"


# ---------------------------------------------------------------------------
# 1. Overview
# ---------------------------------------------------------------------------

@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    """Live DB stats + sealed evaluation results."""
    total_orders = db.query(func.count(Order.id)).scalar() or 0
    paid = db.query(func.count(Order.id)).filter(Order.status == "paid").scalar() or 0
    failed = db.query(func.count(Order.id)).filter(Order.status == "failed").scalar() or 0
    in_recovery = db.query(func.count(Order.id)).filter(
        Order.status == "recovery_in_progress"
    ).scalar() or 0
    halted = db.query(func.count(Order.id)).filter(
        Order.status == "unrecoverable_halt"
    ).scalar() or 0

    total_actions = db.query(func.count(RecoveryAction.id)).scalar() or 0
    executed_actions = db.query(func.count(RecoveryAction.id)).filter(
        RecoveryAction.status == "executed"
    ).scalar() or 0

    recovered_outcomes = db.query(func.count(RecoveryOutcome.id)).filter(
        RecoveryOutcome.recovered.is_(True)
    ).scalar() or 0
    recovered_revenue = db.query(func.sum(RecoveryOutcome.amount_recovered)).filter(
        RecoveryOutcome.recovered.is_(True)
    ).scalar() or 0

    # Load sealed evaluation results
    eval_results = {}
    if RESULTS_PATH.exists():
        eval_results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))

    return {
        "live": {
            "total_orders": total_orders,
            "paid": paid,
            "failed": failed,
            "in_recovery": in_recovery,
            "halted": halted,
            "total_recovery_actions": total_actions,
            "executed_recovery_actions": executed_actions,
            "recovered_outcomes": recovered_outcomes,
            "recovered_revenue_paise": int(recovered_revenue),
            "recovered_revenue_inr": round(recovered_revenue / 100, 2),
        },
        "evaluation": eval_results,
    }


# ---------------------------------------------------------------------------
# 2. Orders list
# ---------------------------------------------------------------------------

@router.get("/orders")
def orders_list(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    rows = (
        db.query(Order)
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = db.query(func.count(Order.id)).scalar() or 0

    items = []
    for o in rows:
        attempts = o.payment_attempts
        outcomes = o.recovery_outcomes
        actions = o.recovery_actions

        attempt_count = len(attempts)
        failed_count = sum(1 for a in attempts if a.status == "failed")
        recovery_source = None
        recovered = False
        if outcomes:
            latest = max(outcomes, key=lambda x: x.created_at)
            recovered = latest.recovered
            recovery_source = latest.recovery_source

        items.append({
            "id": o.id,
            "razorpay_order_id": o.razorpay_order_id,
            "customer_id": o.customer_id,
            "amount_paise": o.amount,
            "amount_inr": round(o.amount / 100, 2),
            "currency": o.currency,
            "status": o.status,
            "attempt_count": attempt_count,
            "failed_attempts": failed_count,
            "recovery_action_count": len(actions),
            "recovered": recovered,
            "recovery_source": recovery_source,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "updated_at": o.updated_at.isoformat() if o.updated_at else None,
        })

    return {"total": total, "items": items}


# ---------------------------------------------------------------------------
# 3. Order detail / timeline
# ---------------------------------------------------------------------------

@router.get("/orders/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Payment attempts
    attempts = sorted(order.payment_attempts, key=lambda a: a.attempt_number)
    attempts_out = []
    for a in attempts:
        attempts_out.append({
            "id": a.id,
            "attempt_number": a.attempt_number,
            "razorpay_payment_id": a.razorpay_payment_id,
            "razorpay_order_id": a.razorpay_order_id,
            "payment_method": a.payment_method,
            "status": a.status,
            "error_code": a.error_code,
            "error_description": a.error_description,
            "error_source": a.error_source,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    # Recovery actions — parse ML scores and root cause from reasoning field
    actions = sorted(order.recovery_actions, key=lambda a: a.id)
    actions_out = []
    for a in actions:
        root_cause = None
        ml_scores = {}   # {"ALTERNATIVE_PAYMENT_0h": 0.570, ...}
        blocked = []     # action keys blocked by policy
        if a.reasoning:
            import re
            # Split on ". " (period + space) to avoid splitting decimal numbers like 0.570
            for part in re.split(r'\.\s+', a.reasoning):
                part = part.strip()
                if "Root cause" in part:
                    rc = part.split("Root cause")[-1].strip().lstrip(":'\" ").rstrip(".'\" ")
                    if rc:
                        root_cause = rc
                if "All scores:" in part:
                    scores_str = part.split("All scores:")[-1].strip().rstrip(".")
                    for token in scores_str.split(","):
                        token = token.strip()
                        if not token:
                            continue
                        is_blocked = "[blocked]" in token
                        token_clean = token.replace("[blocked]", "").strip()
                        if "=" in token_clean:
                            key, val = token_clean.split("=", 1)
                            try:
                                ml_scores[key.strip()] = round(float(val.strip()), 4)
                                if is_blocked:
                                    blocked.append(key.strip())
                            except ValueError:
                                pass

        # Sort scores descending for display
        ml_scores_sorted = dict(sorted(ml_scores.items(), key=lambda x: -x[1]))

        actions_out.append({
            "id": a.id,
            "action_type": a.action_type,
            "status": a.status,
            "root_cause": root_cause,
            "reasoning": a.reasoning,
            "explanation": a.explanation,
"decision_source": a.decision_source,
                "model": a.model,
                "confidence": a.confidence,
            "ml_scores": ml_scores_sorted,
            "ml_scores_blocked": blocked,
            "scheduled_for": a.scheduled_for.isoformat() if a.scheduled_for else None,
            "executed_at": a.executed_at.isoformat() if a.executed_at else None,
            "intervention_ref": a.intervention_ref,
            "outcome": a.outcome,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    # Recovery outcomes
    outcomes = sorted(order.recovery_outcomes, key=lambda o: o.created_at)
    outcomes_out = []
    for o in outcomes:
        outcomes_out.append({
            "id": o.id,
            "recovered": o.recovered,
            "amount_recovered_paise": o.amount_recovered,
            "amount_recovered_inr": round((o.amount_recovered or 0) / 100, 2),
            "recovery_source": o.recovery_source,
            "razorpay_payment_id": o.razorpay_payment_id,
            "explanation": o.explanation,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    # Build unified chronological timeline
    # Each event has: event_type, ts (for sorting), payload (the typed object)
    timeline_events = []
    for a in attempts_out:
        timeline_events.append({"event_type": "payment_attempt", "ts": a["created_at"] or "", "data": a})
    for a in actions_out:
        timeline_events.append({"event_type": "recovery_action", "ts": a["created_at"] or "", "data": a})
    for o in outcomes_out:
        timeline_events.append({"event_type": "recovery_outcome", "ts": o["created_at"] or "", "data": o})
    timeline_events.sort(key=lambda e: e["ts"])

    return {
        "id": order.id,
        "razorpay_order_id": order.razorpay_order_id,
        "customer_id": order.customer_id,
        "amount_paise": order.amount,
        "amount_inr": round(order.amount / 100, 2),
        "currency": order.currency,
        "status": order.status,
        "version_id": order.version_id,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        "payment_attempts": attempts_out,
        "recovery_actions": actions_out,
        "recovery_outcomes": outcomes_out,
        "timeline": timeline_events,
    }


# ---------------------------------------------------------------------------
# 4. AI analytics
# ---------------------------------------------------------------------------

@router.get("/analytics")
def analytics(db: Session = Depends(get_db)):
    """Action distribution, recovery rates, and evaluation comparison from live DB."""

    # Action distribution from live DB
    action_dist_rows = (
        db.query(RecoveryAction.action_type, func.count(RecoveryAction.id))
        .filter(RecoveryAction.status == "executed")
        .group_by(RecoveryAction.action_type)
        .all()
    )
    action_distribution = {at: cnt for at, cnt in action_dist_rows}

    # Recovery rate by action type (live)
    recovery_by_action = {}
    for action_type, _ in action_dist_rows:
        total = (
            db.query(func.count(RecoveryAction.id))
            .filter(RecoveryAction.action_type == action_type, RecoveryAction.status == "executed")
            .scalar() or 0
        )
        recovered = (
            db.query(func.count(RecoveryAction.id))
            .filter(
                RecoveryAction.action_type == action_type,
                RecoveryAction.status == "executed",
                RecoveryAction.outcome == "recovered",
            )
            .scalar() or 0
        )
        recovery_by_action[action_type] = {
            "total": total,
            "recovered": recovered,
            "rate": round(recovered / total, 4) if total else 0,
        }

    # Recovery source breakdown from outcomes
    source_rows = (
        db.query(RecoveryOutcome.recovery_source, func.count(RecoveryOutcome.id))
        .filter(RecoveryOutcome.recovered.is_(True))
        .group_by(RecoveryOutcome.recovery_source)
        .all()
    )
    recovery_by_source = {src: cnt for src, cnt in source_rows}

    # Evaluation results (sealed)
    eval_results = {}
    if RESULTS_PATH.exists():
        eval_results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))

    return {
        "live": {
            "action_distribution": action_distribution,
            "recovery_by_action": recovery_by_action,
            "recovery_by_source": recovery_by_source,
        },
        "evaluation": eval_results,
    }
