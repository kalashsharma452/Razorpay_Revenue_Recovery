"""
Root cause classifier.

Maps structured Razorpay error signals (error_code, error_source, attempt_number)
to one of the canonical root cause categories defined in DESIGN.md.
"""

# Razorpay error codes that indicate a transient infrastructure problem
_TEMPORARY_CODES = {
    "BAD_REQUEST_ERROR",
    "GATEWAY_ERROR",
    "SERVER_ERROR",
}

# Razorpay error sources that indicate the bank/processor is the problem
_BANK_SOURCES = {"bank", "issuer"}
_PROCESSOR_SOURCES = {"gateway", "acquirer", "network"}


def classify(
    error_code: str | None,
    error_description: str | None,
    error_source: str | None,
    attempt_number: int,
    payment_method: str | None,
) -> str:
    """Return a root cause category string."""

    code = (error_code or "").upper()
    source = (error_source or "").lower()
    desc = (error_description or "").lower()

    # Repeated failure — check first regardless of error type
    if attempt_number >= 3:
        return "repeated_failure"

    # Insufficient funds signals
    if "insufficient" in desc or "low balance" in desc or code == "INSUFFICIENT_FUNDS":
        return "insufficient_funds"

    # Expired card
    if "expired" in desc or code == "CARD_EXPIRED":
        return "card_expired"

    # Invalid payment method
    if "invalid" in desc and ("card" in desc or "account" in desc):
        return "invalid_payment_method"

    # Bank unavailable
    if source in _BANK_SOURCES:
        return "bank_unavailable"

    # Processor/gateway timeout or error
    if source in _PROCESSOR_SOURCES or code in _TEMPORARY_CODES:
        return "temporary_failure"

    # Processor timeout by description
    if "timeout" in desc or "timed out" in desc:
        return "processor_timeout"

    return "unknown"
