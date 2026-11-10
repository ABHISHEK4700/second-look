"""Configuration from environment variables (.env supported)."""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


# --- PayPal (sandbox) -------------------------------------------------------
PAYPAL_CLIENT_ID = _env("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = _env("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE = _env("PAYPAL_MODE", "sandbox")          # "sandbox" or "mock" (tests only)
PAYPAL_API_BASE = _env("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")
CURRENCY = _env("CURRENCY", "USD")

# --- Gemini -----------------------------------------------------------------
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.5-flash")
# "low" makes replies much faster; set to "off" to send no thinking setting at all
GEMINI_THINKING = _env("GEMINI_THINKING", "low")
GEMINI_API_BASE = _env("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")

# --- Household (demo) -------------------------------------------------------
SENDER_NAME = _env("SENDER_NAME", "Margaret")
GUARDIAN_NAME = _env("GUARDIAN_NAME", "Daniel")
GUARDIAN_RELATION = _env("GUARDIAN_RELATION", "son")
GUARDIAN_PIN = _env("GUARDIAN_PIN", "2468")
TIMEZONE = _env("TIMEZONE", "UTC")

# Payments at or above this risk score are frozen until the guardian decides.
HOLD_THRESHOLD = int(_env("HOLD_THRESHOLD", "40"))
MAX_AMOUNT = float(_env("MAX_AMOUNT", "5000"))

DATABASE_PATH = _env("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "secondlook.db"))
