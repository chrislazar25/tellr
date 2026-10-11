"""Offline implementations of the eventual speech/LLM/readback interfaces."""

import json
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import TypedDict


class Terms(TypedDict):
    vendor_id: str
    amount_cents: int
    destination_account: str
    date: str


def _recordings() -> list[dict]:
    return json.loads(files("fixtures").joinpath("pipeline.json").read_text())["recordings"]


def validate_terms(terms: dict) -> Terms:
    required = {"vendor_id", "amount_cents", "destination_account", "date"}
    if set(terms) != required:
        raise ValueError("Terms must contain exactly vendor_id, amount_cents, destination_account, and date")
    amount = terms["amount_cents"]
    if type(amount) is not int or not 1 <= amount <= 2**63 - 1:
        raise ValueError("amount_cents must be a positive signed 64-bit integer; no floats or booleans")
    for key in ("vendor_id", "destination_account", "date"):
        if not isinstance(terms[key], str) or not terms[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if date.fromisoformat(terms["date"]).isoformat() != terms["date"]:
        raise ValueError("date must use YYYY-MM-DD")
    return dict(terms)


def transcribe(audio_path: str | Path, verbatim: bool) -> str:
    """Match the fixture basename; no audio file or API key is needed."""
    for recording in _recordings():
        if recording["audio_file"] == Path(audio_path).name:
            return recording["verbatim_text" if verbatim else "cleaned_text"]
    raise ValueError("No transcription fixture for that audio filename")


def extract_terms(text: str) -> Terms | None:
    for recording in _recordings():
        for mode in ("verbatim", "cleaned"):
            if text == recording[f"{mode}_text"]:
                terms = recording[f"{mode}_terms"]
                return None if terms is None else validate_terms(terms)
    raise ValueError("No extraction fixture for that transcript")


def readback(terms: Terms) -> str:
    """Text readback now; a real speech adapter may return audio later."""
    terms = validate_terms(terms)
    template = json.loads(files("fixtures").joinpath("pipeline.json").read_text())["readback_template"]
    dollars, cents = divmod(terms["amount_cents"], 100)
    return template.format(**terms, dollars=dollars, cents=cents)
