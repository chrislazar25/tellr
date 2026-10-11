"""Local step records, with an optional future Raindrop transport hook."""

import logging
from datetime import datetime, timezone
from typing import Callable

from pymongo.client_session import ClientSession
from pymongo.database import Database

import config

TraceSink = Callable[[dict], None]
_trace_sink: TraceSink | None = None


def configure_trace(sink: TraceSink | None) -> None:
    """Install the real Raindrop adapter later; never configure one by default."""
    global _trace_sink
    _trace_sink = sink


def trace(event: dict) -> None:
    """No-op without a write key and an explicitly installed transport."""
    if _trace_sink is None or not config.value("RAINDROP_WRITE_KEY").strip():
        return
    try:
        _trace_sink(event)
    except Exception:
        # Telemetry must not determine a payment outcome or reveal exception text.
        logging.getLogger(__name__).warning("Trace delivery failed")


def log_step(
    db: Database, run_id: str, step: str, inputs: dict, outputs: dict,
    *, session: ClientSession | None = None,
) -> None:
    event = {
        "run_id": run_id, "step": step, "inputs": inputs, "outputs": outputs,
        "ts": datetime.now(timezone.utc),
    }
    db.runs.insert_one(dict(event), session=session)
    # Transactional logs roll back with their writes. No network calls in a retryable
    # callback; only records written outside a transaction reach the optional sink.
    if session is None:
        trace(event)
