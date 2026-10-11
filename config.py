"""Configuration; blank or missing Atlas URI uses local MongoDB."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
LOCAL_MONGODB_URI = (
    "mongodb://127.0.0.1:27017/mongodb_hackathon"
    "?replicaSet=rs0&directConnection=true"
)


def mongodb_uri() -> str:
    load_dotenv(ROOT / ".env", override=False)
    return os.getenv("MONGODB_URI", "").strip() or LOCAL_MONGODB_URI


def static_guard() -> bool:
    """STATIC_GUARD is opt-in; the empty-rules baseline routes to today's account."""
    return os.getenv("STATIC_GUARD", "off").strip().lower() in {"1", "true", "yes", "on"}
