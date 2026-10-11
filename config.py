"""Configuration; blank or missing Atlas URI uses local MongoDB."""

import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
LOCAL_MONGODB_URI = (
    "mongodb://127.0.0.1:27017/mongodb_hackathon"
    "?replicaSet=rs0&directConnection=true"
)


def value(name: str, default: str = "") -> str:
    """Environment > .env.secrets > .env, without mutating the process environment.

    Disable interpolation so secret values containing '$' remain literal. Never
    log or print the settings dictionary or connection string.
    """
    settings = {
        **dotenv_values(ROOT / ".env", interpolate=False),
        **dotenv_values(ROOT / ".env.secrets", interpolate=False),
        **os.environ,
    }
    return settings.get(name) or default


def mongodb_uri() -> str:
    return value("MONGODB_URI").strip() or LOCAL_MONGODB_URI


def static_guard() -> bool:
    """STATIC_GUARD is opt-in; the empty-rules baseline routes to today's account."""
    flag = value("STATIC_GUARD", "off").strip().lower()
    if flag not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError("STATIC_GUARD must be an on/off boolean")
    return flag in {"1", "true", "yes", "on"}
