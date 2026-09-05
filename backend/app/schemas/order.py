from pydantic import BaseModel
from typing import Optional


class CreateOrderRequest(BaseModel):
    customer_id: str
    amount: int       # paise
    currency: str = "INR"


class CreateOrderResponse(BaseModel):
    order_id: int
    razorpay_order_id: str
    amount: int
    currency: str
    key_id: str


class OrderStatusResponse(BaseModel):
    order_id: int
    razorpay_order_id: str
    amount: int
    currency: str
    status: str
    attempt_count: int
