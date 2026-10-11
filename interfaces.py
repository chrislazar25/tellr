"""Offline implementations of the eventual speech/LLM/readback interfaces."""

import json
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Protocol, TypedDict


class Terms(TypedDict):
    vendor_id: str
    amount_cents: int
    destination_account: str
    date: str


class TranscriptionAdapter(Protocol):
    def __call__(self, audio_path: str | Path, *, no_verbatim: bool) -> str: ...


class ExtractionAdapter(Protocol):
    def __call__(self, text: str) -> Terms | None: ...


class ReadbackAdapter(Protocol):
    def __call__(self, terms: Terms) -> str: ...


@dataclass(frozen=True)
class Adapters:
    transcribe: TranscriptionAdapter
    extract_terms: ExtractionAdapter
    readback: ReadbackAdapter


# JSON Schema for the future Claude tool-use adapter. Null means no payment
# instruction; never turn filler or an incomplete instruction into guessed terms.
EXTRACT_TERMS_TOOL = {
    "name": "extract_payment_terms",
    "description": "Extract the final intended payment after corrections and reaffirmations. Return null for filler-only or incomplete instructions. Amounts must be integer cents.",
    "strict": True,
    "input_schema": {
        "type": "object", "required": ["terms"], "additionalProperties": False,
        "properties": {"terms": {"anyOf": [
            {"type": "object", "required": ["vendor_id", "amount_cents", "destination_account", "date"],
             "additionalProperties": False, "properties": {
                 "vendor_id": {"type": "string", "minLength": 1},
                 "amount_cents": {"type": "integer", "minimum": 1, "maximum": 2**63 - 1},
                 "destination_account": {"type": "string", "minLength": 1},
                 "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
             }}, {"type": "null"},
        ]}},
    },
}


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


def _fixture_transcribe(audio_path: str | Path, *, no_verbatim: bool) -> str:
    """Match the fixture basename; no audio file or API key is needed."""
    for recording in _recordings():
        if recording["audio_file"] == Path(audio_path).name:
            return recording["cleaned_text" if no_verbatim else "verbatim_text"]
    raise ValueError("No transcription fixture for that audio filename")


def _fixture_extract_terms(text: str) -> Terms | None:
    for recording in _recordings():
        for mode in ("verbatim", "cleaned"):
            if text == recording[f"{mode}_text"]:
                terms = recording[f"{mode}_terms"]
                return None if terms is None else validate_terms(terms)
    raise ValueError("No extraction fixture for that transcript")


def _fixture_readback(terms: Terms) -> str:
    """Text readback now; a real speech adapter may return audio later."""
    terms = validate_terms(terms)
    template = json.loads(files("fixtures").joinpath("pipeline.json").read_text())["readback_template"]
    dollars, cents = divmod(terms["amount_cents"], 100)
    return template.format(**terms, dollars=dollars, cents=cents)


DEFAULT_ADAPTERS = Adapters(_fixture_transcribe, _fixture_extract_terms, _fixture_readback)


def transcribe(audio_path: str | Path, verbatim: bool) -> str:
    return DEFAULT_ADAPTERS.transcribe(audio_path, no_verbatim=not verbatim)


def extract_terms(text: str) -> Terms | None:
    return DEFAULT_ADAPTERS.extract_terms(text)


def readback(terms: Terms) -> str:
    return DEFAULT_ADAPTERS.readback(validate_terms(terms))
