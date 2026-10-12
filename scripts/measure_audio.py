"""Measure both live transcription modes without approving or paying anything."""

import argparse
import hashlib
import json
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import config
from db.connection import connect
from db.evidence import collect_candidates
from db.seed import seed
from interfaces import _recordings
from live_adapters import live_adapters


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def money_diverged(verbatim, cleaned):
    if verbatim is None or cleaned is None:
        return verbatim != cleaned
    return any(verbatim[field] != cleaned[field] for field in ("amount_cents", "destination_account"))


class MeasuredCall:
    """Record returned model usage, never credentials or request headers."""

    def __init__(self, call, events, provider, purpose):
        self.call, self.events, self.provider, self.purpose = call, events, provider, purpose

    def __call__(self, **request):
        started = time.perf_counter()
        response = self.call(**request)
        usage = getattr(response, "usage", None)
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump(mode="json")
        elif not isinstance(usage, dict):
            usage = None
        self.events.append({
            "provider": self.provider, "purpose": self.purpose, "ts": now(),
            "wall_seconds": round(time.perf_counter() - started, 6),
            "requested_model": request.get("model", request.get("model_id")),
            "served_model": getattr(response, "model", None),
            "no_verbatim": request.get("no_verbatim"), "usage": usage,
            "billed_cost_usd": None,
        })
        return response


def measure(db, audio_dir, output, *, adapters=None, events=None):
    events = events if events is not None else []
    started = time.perf_counter()
    result = {"schema_version": 1, "experiment_id": str(uuid.uuid4()), "started_at": now(),
              "source": "live_api", "approval_performed": False, "clips": [], "api_calls": events,
              "cost": {"billed_usd": None, "reason": "Provider responses do not include invoiced cost; no estimate substituted."}}
    clients = []
    try:
        if adapters is None:
            from anthropic import Anthropic
            from elevenlabs.client import ElevenLabs
            clients = [ElevenLabs(api_key=config.value("ELEVENLABS_API_KEY")),
                       Anthropic(api_key=config.value("ANTHROPIC_API_KEY"))]
            scribe = SimpleNamespace(speech_to_text=SimpleNamespace(convert=MeasuredCall(
                clients[0].speech_to_text.convert, events, "elevenlabs", "transcription")))
            claude = SimpleNamespace(messages=SimpleNamespace(create=MeasuredCall(
                clients[1].messages.create, events, "anthropic", "extraction")))
            vendors = list(db.vendors.find({}, {"_id": 1, "name": 1, "bank_account": 1}).sort("_id", 1))
            result["vendor_catalog"] = vendors
            adapters = live_adapters(vendors, scribe_client=scribe, claude_client=claude)
        for case in _recordings():
            path = Path(audio_dir) / case["audio_file"]
            clip_start, run_id = time.perf_counter(), str(uuid.uuid4())
            with wave.open(str(path)) as recording:
                duration = recording.getnframes() / recording.getframerate()
            texts, terms = collect_candidates(db, run_id, path, adapters)
            logs = list(db.runs.find({"run_id": run_id, "step": {"$in": ["transcribe", "extract_terms"]}}).sort("ts", 1))
            result["clips"].append({
                "case_id": case["case_id"], "audio_file": str(path),
                "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "duration_seconds": duration,
                "run_id": run_id, "verbatim": texts["verbatim"], "cleaned": texts["cleaned"],
                "verbatim_terms": terms["verbatim"], "cleaned_terms": terms["cleaned"],
                "true_terms": case["true_terms"], "truth_source": "existing hand-labeled fixture; independent of API output",
                "money_terms_diverged": money_diverged(terms["verbatim"], terms["cleaned"]),
                "wall_seconds": round(time.perf_counter() - clip_start, 6),
                "cache": [{"step": row["step"], "mode": row["inputs"]["mode"],
                           "hit": row["outputs"]["cache_hit"]} for row in logs],
            })
            result["wall_seconds"] = round(time.perf_counter() - started, 6)
            write_json(output, result)
            print(f"Measured {case['case_id']}: money_terms_diverged={result['clips'][-1]['money_terms_diverged']}", flush=True)
        result["finished_at"] = now()
        result["wall_seconds"] = round(time.perf_counter() - started, 6)
        write_json(output, result)
        return result
    finally:
        for client in clients:
            close = getattr(client, "close", None)
            if close:
                close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, default=Path("audio"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/live_clips.json"))
    args = parser.parse_args(argv)
    db = None
    try:
        if config.value("ADAPTERS", "fixture") != "live":
            raise ValueError("This measurement command requires ADAPTERS=live")
        db = connect()
        seed(db)
        measure(db, args.audio_dir, args.output)
        return 0
    except Exception as error:
        config.print_error(error)
        return 1
    finally:
        if db is not None:
            db.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
