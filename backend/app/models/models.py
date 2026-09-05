from sqlalchemy import Column, String, Integer, BigInteger, DateTime, Text, Boolean, Float, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True)  # e.g. cust_001
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String)
    created_at = Column(DateTime, server_default=func.now())

    orders = relationship("Order", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    razorpay_order_id = Column(String, unique=True, nullable=False)
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    amount = Column(BigInteger, nullable=False)   # paise
    currency = Column(String, default="INR")
    status = Column(String, default="created")   # created | attempted | paid | failed | abandoned | recovery_in_progress | recovered | unrecoverable_halt
    version_id = Column(Integer, default=0)       # optimistic locking
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", back_populates="orders")
    payment_attempts = relationship("PaymentAttempt", back_populates="order")
    recovery_actions = relationship("RecoveryAction", back_populates="order")
    recovery_outcomes = relationship("RecoveryOutcome", back_populates="order")


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    razorpay_order_id = Column(String, nullable=True)   # set on RETRY_LATER; NULL for original attempt
    razorpay_payment_id = Column(String, unique=True, nullable=True)
    attempt_number = Column(Integer, nullable=False)
    payment_method = Column(String)
    status = Column(String, nullable=False)       # created | captured | failed
    error_code = Column(String)
    error_description = Column(Text)
    error_source = Column(String)
    created_at = Column(DateTime, server_default=func.now())

    order = relationship("Order", back_populates="payment_attempts")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    razorpay_event_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)
    raw_payload = Column(Text, nullable=False)
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    action_type = Column(String, nullable=False)
    status = Column(String, default="scheduled")
    reasoning = Column(Text)       # technical audit: ML scores, root cause, blocked actions
    explanation = Column(Text)     # merchant-readable: why this action was chosen
    decision_source = Column(String)
    # Which model artifact generated this decision ('gb' | 'lr'), recorded at
    # decision time from the loaded predictor. NULL = legacy row or rule-based
    # decision (no model consulted). Never inferred or derived from current config.
    model = Column(String)
    confidence = Column(Float)
    scheduled_for = Column(DateTime)
    executed_at = Column(DateTime)
    execution_cost = Column(Float, default=0.0)
    incentive_cost = Column(Float, default=0.0)
    outcome = Column(String)
    # Set only after an executor has created the corresponding intervention.
    intervention_ref = Column(String, nullable=True, default=None)
    created_at = Column(DateTime, server_default=func.now())

    order = relationship("Order", back_populates="recovery_actions")


class RecoveryOutcome(Base):
    __tablename__ = "recovery_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    recovery_action_id = Column(Integer, ForeignKey("recovery_actions.id"), nullable=True)
    recovered = Column(Boolean, nullable=False)       # True = order reached paid
    amount_recovered = Column(BigInteger, default=0)  # paise
    recovery_source = Column(String)                  # AI_ACTION | ai_retry | ai_message | native_checkout
    razorpay_payment_id = Column(String)
    explanation = Column(Text)                        # LLM-generated outcome narrative
    created_at = Column(DateTime, server_default=func.now())

    order = relationship("Order", back_populates="recovery_outcomes")
