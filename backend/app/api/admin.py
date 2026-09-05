from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.clock import advance_clock, get_offset_hours, get_simulated_now, reset_clock
from app.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/clock/advance")
def clock_advance(hours: float = 0, minutes: float = 0):
    new_now = advance_clock(hours=hours, minutes=minutes)
    return {
        "simulated_now": new_now.isoformat(),
        "offset_hours": get_offset_hours(),
    }


@router.post("/clock/reset")
def clock_reset():
    reset_clock()
    return {
        "simulated_now": get_simulated_now().isoformat(),
        "offset_hours": 0,
    }


@router.get("/clock")
def clock_status():
    return {
        "simulated_now": get_simulated_now().isoformat(),
        "offset_hours": get_offset_hours(),
    }


@router.post("/scheduler/run")
def scheduler_run(db: Session = Depends(get_db)):
    """Manually trigger one scheduler tick."""
    from app.scheduler import tick
    executed = tick(db)
    return {"executed_action_ids": executed}


@router.get("/model/status")
def model_status():
    """Show whether the ML model is loaded and its validation metrics."""
    from app.recovery.predictor import get, get_metrics
    p = get()
    m = get_metrics()
    if p is None:
        return {"loaded": False}
    return {
        "loaded": True,
        "validation": {
            "roc_auc":  m.roc_auc  if m else None,
            "log_loss": m.log_loss if m else None,
            "n_train":  m.n_train  if m else None,
            "n_val":    m.n_val    if m else None,
        },
    }
