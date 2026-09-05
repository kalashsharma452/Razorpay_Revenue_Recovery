"""
Demo-only customer seed.

Seeds 3 clearly distinct demo customers with historical payment behavior so
the live dashboard can demonstrate differentiated recovery decisions when
DEMO_CUSTOMER_HISTORY=true is set.

The three profiles sit across the live 0.40 confidence floor for high-ticket
bank_unavailable failures:
  - cust_demo_reliable   (~520 days, 25 orders)  -> RETRY_LATER_6h clears 0.40
  - cust_demo_occasional (~210 days,  8 orders)  -> near the floor
  - cust_demo_new        (~  5 days,  2 orders)  -> below floor -> STOP

ISOLATION: touches only customers / orders / payment_attempts. It never
creates recovery_actions, recovery_outcomes, or webhook_events, so the
attribution logic, example outcome counts, and sealed evaluation are untouched.
Idempotent and reversible: seed() skips existing customers; --unseed removes
exactly the rows this script created (identified by the seed_* row IDs).

Usage (from backend/):
    python seed_demo_customers.py          # seed
    python seed_demo_customers.py --unseed # remove seeded rows
"""

import sys
from datetime import datetime, timedelta, timezone
from itertools import cycle
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import SessionLocal
from app.models.models import Customer, Order, PaymentAttempt

SEED_CUSTOMERS = [
    {
        "customer_id": "cust_demo_reliable",
        "name": "Maya Deshmukh",
        "email": "reliable@example.com",
        "age_days": 520,
        "order_count": 25,
    },
    {
        "customer_id": "cust_demo_occasional",
        "name": "Rohan Iyer",
        "email": "occasional@example.com",
        "age_days": 210,
        "order_count": 8,
    },
    {
        "customer_id": "cust_demo_new",
        "name": "Ananya Rao",
        "email": "new@example.com",
        "age_days": 5,
        "order_count": 2,
    },
]

# Deterministic variety for amounts (paise) and payment methods across orders
_SEED_AMOUNTS = [3000, 8000, 15000, 25000, 50000, 100000]
_SEED_METHODS = ["netbanking", "card", "upi", "wallet"]


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed(db=None) -> dict:
    """Create the 3 demo customers with historical orders/attempts if absent."""
    own_session = db is None
    db = db or SessionLocal()
    created = []
    try:
        for cfg in SEED_CUSTOMERS:
            if db.get(Customer, cfg["customer_id"]):
                continue
            created_at = _now_naive() - timedelta(days=cfg["age_days"])
            customer = Customer(
                id=cfg["customer_id"],
                name=cfg["name"],
                email=cfg["email"],
                created_at=created_at,
            )
            db.add(customer)
            db.flush()

            amounts = cycle(_SEED_AMOUNTS)
            methods = cycle(_SEED_METHODS)
            for i in range(1, cfg["order_count"] + 1):
                amount = next(amounts)
                order = Order(
                    razorpay_order_id=f"seed_{cfg['customer_id']}_order_{i}",
                    customer_id=cfg["customer_id"],
                    amount=amount,
                    currency="INR",
                    status="paid",
                    created_at=created_at + timedelta(days=i),
                )
                db.add(order)
                db.flush()
                db.add(PaymentAttempt(
                    order_id=order.id,
                    razorpay_order_id=None,
                    razorpay_payment_id=f"seed_pay_{cfg['customer_id']}_{i}",
                    attempt_number=1,
                    payment_method=next(methods),
                    status="captured",
                ))
            created.append(cfg["customer_id"])
        db.commit()
    finally:
        if own_session:
            db.close()
    return {"created": created}


def unseed(db=None) -> dict:
    """Remove exactly the customers/orders/attempts this script created."""
    own_session = db is None
    db = db or SessionLocal()
    removed = []
    try:
        ids = [cfg["customer_id"] for cfg in SEED_CUSTOMERS]
        order_ids = [
            o.id for o in
            db.query(Order)
            .filter(Order.customer_id.in_(ids), Order.razorpay_order_id.like("seed_%"))
            .all()
        ]
        db.query(PaymentAttempt).filter(
            PaymentAttempt.order_id.in_(order_ids)
        ).delete(synchronize_session=False)
        db.query(Order).filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
        for customer_id in ids:
            if db.get(Customer, customer_id):
                db.delete(db.get(Customer, customer_id))
                removed.append(customer_id)
        db.commit()
    finally:
        if own_session:
            db.close()
    return {"removed": removed}


if __name__ == "__main__":
    if "--unseed" in sys.argv:
        result = unseed()
        print(f"[seed] unseed complete: {result['removed']}")
    else:
        result = seed()
        print(f"[seed] seeded new customers: {result['created']} "
              f"(skipped already-present ones)")