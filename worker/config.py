import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_UI_URL = os.getenv("TEMPORAL_UI_URL", "http://localhost:8233")

# API base URL — used by the worker for intra-container HTTP calls (canonical
# inventory store: /api/inventory/{reserve,release,adjust}, /api/catalog).
# In docker-compose this is `http://api:8000`; the customer's browser cannot
# resolve that hostname.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

# Public-facing API URL — used to build customer-clickable links emailed for
# HITL approve/deny. Must be host-accessible (the recipient clicks from their
# own browser), so this resolves to localhost in the demo regardless of where
# the worker is running. Falls back to API_BASE_URL only when not set so a
# bare-metal `make codespace` run still works without setting two env vars.
API_PUBLIC_URL = os.getenv("API_PUBLIC_URL", os.getenv("API_BASE_URL", "http://localhost:8000"))

# Customer HITL email (MailHog for the demo)
SMTP_HOST = os.getenv("SMTP_HOST", "mailhog")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
MAILHOG_UI_URL = os.getenv("MAILHOG_UI_URL", "http://localhost:8025")
HITL_FROM_EMAIL = os.getenv("HITL_FROM_EMAIL", "orders@flourish-and-blotts.test")
HITL_TOKEN_SECRET = os.getenv("HITL_TOKEN_SECRET", "change-me-in-demo")

TASK_QUEUE = "flourish-blotts-oms"
