import razorpay
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
from app.database import get_db
from app.models.models import Customer, Order, RecoveryAction
from app.schemas.order import CreateOrderRequest, CreateOrderResponse, OrderStatusResponse

router = APIRouter(prefix="/orders", tags=["orders"])
rzp = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


@router.post("", response_model=CreateOrderResponse)
def create_order(req: CreateOrderRequest, db: Session = Depends(get_db)):
    customer = db.get(Customer, req.customer_id)
    if not customer:
        # auto-create a guest customer for demo convenience
        customer = Customer(id=req.customer_id, name="Guest", email="guest@demo.com")
        db.add(customer)
        db.flush()

    rzp_order = rzp.order.create({
        "amount": req.amount,
        "currency": req.currency,
        "payment_capture": 1,
    })

    order = Order(
        razorpay_order_id=rzp_order["id"],
        customer_id=req.customer_id,
        amount=req.amount,
        currency=req.currency,
        status="created",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    return CreateOrderResponse(
        order_id=order.id,
        razorpay_order_id=order.razorpay_order_id,
        amount=order.amount,
        currency=order.currency,
        key_id=RAZORPAY_KEY_ID,
    )


@router.get("/{order_id}", response_model=OrderStatusResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderStatusResponse(
        order_id=order.id,
        razorpay_order_id=order.razorpay_order_id,
        amount=order.amount,
        currency=order.currency,
        status=order.status,
        attempt_count=len(order.payment_attempts),
    )


@router.get("/{order_id}/retry-session")
def get_retry_session(order_id: int, db: Session = Depends(get_db)):
    """
    Returns the current razorpay_order_id for an order in recovery.
    The frontend uses this to re-open Razorpay Checkout for a retry attempt.
    """
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ("recovery_in_progress", "failed"):
        raise HTTPException(status_code=400, detail=f"Order not in recoverable state: {order.status}")
    retry_action = (
        db.query(RecoveryAction)
        .filter(
            RecoveryAction.order_id == order.id,
            RecoveryAction.action_type == "RETRY_LATER",
            RecoveryAction.status == "executed",
            RecoveryAction.intervention_ref.isnot(None),
        )
        .order_by(RecoveryAction.id.desc())
        .first()
    )
    razorpay_order_id = retry_action.intervention_ref if retry_action else order.razorpay_order_id

    return {
        "order_id": order.id,
        "razorpay_order_id": razorpay_order_id,
        "amount": order.amount,
        "currency": order.currency,
        "key_id": RAZORPAY_KEY_ID,
        "attempt_count": len(order.payment_attempts),
    }
