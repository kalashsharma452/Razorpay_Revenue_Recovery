"""
Scheduler — polls for eligible recovery actions and executes them.

Uses the simulation clock so scheduled_for times can be demonstrated
instantly by advancing the clock via /admin/clock/advance.

Optimistic locking: before executing, re-checks that status is still
'scheduled' inside the same transaction. Prevents duplicate execution
if two scheduler ticks overlap.
"""

from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.clock import get_simulated_now
from app.database import SessionLocal
from app.models.models import RecoveryAction


def tick(db: Session) -> list[int]:
    """
    One scheduler tick.
    Finds all actions where scheduled_for <= simulated_now and status = scheduled.
    Executes each exactly once.
    Returns list of executed action IDs.
    """
    from app.recovery.executor import execute

    now = get_simulated_now()
    # Strip timezone for comparison — DB stores naive UTC
    now_naive = now.replace(tzinfo=None)

    due = (
        db.query(RecoveryAction)
        .filter(
            RecoveryAction.status == "scheduled",
            RecoveryAction.scheduled_for <= now_naive,
        )
        .all()
    )

    executed_ids = []
    for action in due:
        # Optimistic lock: re-fetch and check status hasn't changed
        db.refresh(action)
        if action.status != "scheduled":
            print(f"[scheduler] action {action.id} already {action.status}, skipping")
            continue

        # Mark in-progress atomically before doing external work
        action.status = "executing"
        db.flush()

        try:
            result = execute(action, db)
            result_status = result.get("status", "unknown")
            action.executed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if result_status == "failed":
                # Executor reported a failure (e.g. Razorpay API error)
                action.status = "failed"
                action.outcome = "failed"
            elif result_status == "stale":
                action.status = "executed"
                action.outcome = "stale"
            elif action.action_type == "STOP":
                action.status = "executed"
                action.outcome = "halted"
            else:
                action.status = "executed"
                action.outcome = result_status
            db.commit()
            executed_ids.append(action.id)
            print(f"[scheduler] action {action.id} ({action.action_type}) result={result_status}: {result}")
        except Exception as exc:
            db.rollback()
            # Re-fetch after rollback and mark failed
            action = db.get(RecoveryAction, action.id)
            if action:
                action.status = "failed"
                action.outcome = "failed"
                db.commit()
            print(f"[scheduler] action {action.id if action else '?'} failed: {exc}")

    return executed_ids


def _scheduler_tick():
    """Called by APScheduler every 30 seconds."""
    db = SessionLocal()
    try:
        tick(db)
    except Exception as exc:
        print(f"[scheduler] tick error: {exc}")
        db.rollback()
    finally:
        db.close()


def start() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(_scheduler_tick, "interval", seconds=30, id="recovery_tick")
    scheduler.start()
    print("[scheduler] started — polling every 30s")
    return scheduler
