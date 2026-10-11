"""Configuration; blank or missing Atlas URI uses local MongoDB."""

import os
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
LOCAL_MONGODB_URI = (
    "mongodb://127.0.0.1:27017/mongodb_hackathon"
    "?replicaSet=rs0&directConnection=true"
)


def _settings() -> dict:
    return {
        **dotenv_values(ROOT / ".env", interpolate=False),
        **dotenv_values(ROOT / ".env.secrets", interpolate=False),
        **os.environ,
    }


def value(name: str, default: str = "") -> str:
    """Environment > .env.secrets > .env, without mutating the process environment.

    Disable interpolation so secret values containing '$' remain literal. Never
    log or print the settings dictionary or connection string.
    """
    return _settings().get(name) or default


def mongodb_uri() -> str:
    return value("MONGODB_URI").strip() or LOCAL_MONGODB_URI


def static_guard() -> bool:
    """STATIC_GUARD is opt-in; the empty-rules baseline routes to today's account."""
    flag = value("STATIC_GUARD", "off").strip().lower()
    if flag not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError("STATIC_GUARD must be an on/off boolean")
    return flag in {"1", "true", "yes", "on"}


def safe_exception(error: Exception) -> str:
    """Keep useful diagnostics while redacting configured credentials and URIs."""
    secrets = set()
    settings = [dotenv_values(ROOT / ".env", interpolate=False),
                dotenv_values(ROOT / ".env.secrets", interpolate=False), os.environ]
    for name, setting in (item for layer in settings for item in layer.items()):
        if not setting or not re.search(r"KEY|TOKEN|PASSWORD|SECRET|USERNAME|URI|PRIVATE_IP", name, re.I):
            continue
        secrets.add(setting)
        if "URI" in name.upper():
            try:
                parsed = urlsplit(setting)
                secrets.update(item for item in (parsed.hostname, parsed.username, parsed.password) if item)
            except ValueError:
                pass
    # SDKs may echo URL-encoded credentials rather than their original values.
    variants = {variant for secret in secrets for variant in (
        secret, quote(secret, safe=""), quote_plus(secret), json.dumps(secret)[1:-1],
    )}
    message = str(error)
    for secret in sorted(variants, key=len, reverse=True):
        message = message.replace(secret, "[redacted]")
    message = re.sub(r"mongodb(?:\+srv)?://[^\s\"'<>]+", "[redacted MongoDB URI]", message, flags=re.I)
    message = re.sub(r"https?://[^\s/]+@[^\s\"'<>]+", "[redacted URL]", message, flags=re.I)
    message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted key]", message)
    message = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|x-api-key\s*[:=]\s*)[^\s,\"']+",
                     r"\1[redacted]", message)
    message = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted IP]", message)
    message = re.sub(r"\b[\w.-]+\.mongodb\.net\b", "[redacted host]", message, flags=re.I)
    def redact_ipv6(match):
        try:
            ipaddress.IPv6Address(match.group())
        except ValueError:
            return match.group()
        return "[redacted IP]"
    message = re.sub(r"(?<![\w:])(?:[a-fA-F0-9]{0,4}:){2,}[a-fA-F0-9:.]*", redact_ipv6, message)
    # Prevent terminal control sequences or injected multi-line diagnostics.
    message = " ".join(message.split())
    message = "".join(char for char in message if char.isprintable())
    return f"{type(error).__name__}: {message}"


def print_error(error: Exception) -> None:
    print(safe_exception(error), file=sys.stderr)
