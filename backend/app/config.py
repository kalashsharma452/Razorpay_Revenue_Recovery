from dotenv import load_dotenv
import os

load_dotenv()

RAZORPAY_KEY_ID = os.environ["RAZORPAY_KEY_ID"]
RAZORPAY_KEY_SECRET = os.environ["RAZORPAY_KEY_SECRET"]
DATABASE_URL = os.environ["DATABASE_URL"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
RECOVERY_DEMO_LOAD_ONLY = os.environ.get("RECOVERY_DEMO_LOAD_ONLY", "").lower() in {
    "1", "true", "yes", "on",
}

def _resolve_demo_model() -> str:
    value = os.environ.get("DEMO_MODEL", "gb").strip().lower()
    return value if value in ("gb", "lr") else "gb"


DEMO_MODEL = _resolve_demo_model()

DEMO_CUSTOMER_HISTORY = os.environ.get("DEMO_CUSTOMER_HISTORY", "false").lower() in {
    "1", "true", "yes", "on",
}
