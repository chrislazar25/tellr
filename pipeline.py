"""Offline audio -> evidence -> intent -> readback -> queue -> receipt."""

import argparse
import json
import uuid
from datetime import datetime, timezone

from pymongo.database import Database

from db.connection import connect
from db.transactions import run_transaction
from interfaces import extract_terms, readback, transcribe
from observability import log_step
from worker import process_one


def run_pipeline(db: Database, audio_path: str = "demo-invoice.wav", *, verbatim: bool = True) -> dict:
    run_id = str(uuid.uuid4())
    text = transcribe(audio_path, verbatim)
    log_step(db, run_id, "transcribe", {"audio_path": audio_path, "verbatim": verbatim}, {"text": text})
    terms = extract_terms(text)
    log_step(db, run_id, "extract_terms", {"text": text}, terms)
    confirmation = readback(terms)
    log_step(db, run_id, "readback", terms, {"text": confirmation})

    evidence_id, intent_id, queue_id = (str(uuid.uuid4()) for _ in range(3))
    now = datetime.now(timezone.utc)

    def enqueue(session):
        db.evidence.insert_one({
            "_id": evidence_id, "run_id": run_id, "audio_path": audio_path,
            "verbatim": verbatim, "text": text, "created_at": now,
        }, session=session)
        log_step(db, run_id, "store_evidence", {}, {"evidence_id": evidence_id}, session=session)
        db.intents.insert_one({
            "_id": intent_id, "run_id": run_id, "evidence_id": evidence_id, **terms, "created_at": now,
        }, session=session)
        log_step(db, run_id, "store_intent", {}, {"intent_id": intent_id}, session=session)
        db.queue.insert_one({
            "_id": queue_id, "run_id": run_id, "intent_id": intent_id,
            "vendor_id": terms["vendor_id"], "status": "pending", "created_at": now,
        }, session=session)
        log_step(db, run_id, "enqueue", {"intent_id": intent_id}, {"queue_id": queue_id}, session=session)

    run_transaction(db, enqueue, run_id=run_id)
    outcome = process_one(db, queue_id=queue_id)
    return {"run_id": run_id, "readback": confirmation, "outcome": outcome}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_path", nargs="?", default="demo-invoice.wav")
    parser.add_argument("--normalized", action="store_true", help="Use the non-verbatim fixture transcript")
    args = parser.parse_args()
    db = connect()
    try:
        print(json.dumps(run_pipeline(db, args.audio_path, verbatim=not args.normalized), indent=2))
    finally:
        db.client.close()


if __name__ == "__main__":
    main()
