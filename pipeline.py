"""Dual transcription -> evidence -> explicit confirmation -> queue -> receipt."""

import argparse
import json
import uuid
from datetime import datetime, timezone

from pymongo.database import Database

import interfaces
from db.connection import connect
from db.transactions import run_transaction
from observability import log_step
from worker import process_one


def prepare_intent(db: Database, audio_path: str = "demo-invoice.wav") -> dict:
    run_id, evidence_id, intent_id = (str(uuid.uuid4()) for _ in range(3))
    texts, candidates = {}, {}
    for mode in ("verbatim", "cleaned"):
        texts[mode] = interfaces.transcribe(audio_path, mode == "verbatim")
        log_step(db, run_id, "transcribe", {"audio_path": audio_path, "mode": mode}, {"text": texts[mode]})
        terms = interfaces.extract_terms(texts[mode])
        candidates[mode] = None if terms is None else interfaces.validate_terms(terms)
        log_step(db, run_id, "extract_terms", {"text": texts[mode], "mode": mode}, {"terms": candidates[mode]})

    terms = candidates["verbatim"] or candidates["cleaned"]
    now = datetime.now(timezone.utc)

    def store(session):
        db.evidence.insert_one({
            "_id": evidence_id, "run_id": run_id, "audio_path": audio_path,
            "verbatim_text": texts["verbatim"], "cleaned_text": texts["cleaned"],
            "verbatim_terms": candidates["verbatim"], "cleaned_terms": candidates["cleaned"], "created_at": now,
        }, session=session)
        log_step(db, run_id, "store_evidence", {}, {"evidence_id": evidence_id}, session=session)
        if terms is None:
            log_step(db, run_id, "no_terms", {"evidence_id": evidence_id}, {"status": "no_terms"}, session=session)
            return
        db.intents.insert_one({
            "_id": intent_id, "run_id": run_id, "evidence_id": evidence_id,
            **terms, "status": "draft", "created_at": now,
        }, session=session)
        log_step(db, run_id, "store_intent", {}, {"intent_id": intent_id, "status": "draft"}, session=session)

    run_transaction(db, store, run_id=run_id)
    return {
        "run_id": run_id, "evidence_id": evidence_id, "intent_id": intent_id if terms else None,
        "status": "awaiting_confirmation" if terms else "no_terms", "candidates": candidates,
    }


def confirm_intent(db: Database, intent_id: str, terms: dict, *, source: str = "manual") -> dict:
    """Persist chosen terms and a coherent vendor/fence snapshot at approval."""
    terms = interfaces.validate_terms(terms)
    if source not in {"verbatim", "cleaned", "manual"}:
        raise ValueError("Unknown confirmation source")

    def confirm(session):
        intent = db.intents.find_one({"_id": intent_id}, session=session)
        if intent is None:
            raise ValueError("Intent is missing")
        if intent["status"] == "confirmed":
            if intent["confirmed_terms"] != terms:
                raise ValueError("Confirmed terms are immutable; create a new intent")
            return intent
        vendor = db.vendors.find_one({"_id": terms["vendor_id"]}, session=session)
        fence = db.fences.find_one({"_id": f"fence:{terms['vendor_id']}"}, session=session)
        if vendor is None or fence is None:
            raise ValueError("Vendor or fence is missing at approval")
        confirmed = {
            **terms, "status": "confirmed", "confirmed_terms": terms,
            "confirmed_at": datetime.now(timezone.utc), "confirmation_source": source,
            "approved_bank_account": vendor["bank_account"], "approved_fence_version": fence["version"],
        }
        db.intents.update_one({"_id": intent_id}, {"$set": confirmed}, session=session)
        log_step(db, intent["run_id"], "confirm", {"intent_id": intent_id, "source": source}, {
            "confirmed_terms": terms, "approved_bank_account": vendor["bank_account"],
            "approved_fence_version": fence["version"], "confirmed_at": confirmed["confirmed_at"],
        }, session=session)
        return db.intents.find_one({"_id": intent_id}, session=session)

    return run_transaction(db, confirm)


def enqueue_intent(db: Database, intent_id: str) -> dict:
    """Enqueue a confirmed intent once; repeats never create a second job."""
    def enqueue(session):
        intent = db.intents.find_one({"_id": intent_id}, session=session)
        if intent is None or intent["status"] != "confirmed":
            raise ValueError("Only confirmed intents can be enqueued")
        queue_id = f"queue:{intent_id}"
        item = {
            "_id": queue_id, "run_id": intent["run_id"], "intent_id": intent_id,
            "vendor_id": intent["confirmed_terms"]["vendor_id"],
            "status": "pending", "created_at": datetime.now(timezone.utc),
        }
        inserted = db.queue.update_one({"_id": queue_id}, {"$setOnInsert": item}, upsert=True, session=session)
        if inserted.upserted_id is not None:
            log_step(db, intent["run_id"], "enqueue", {"intent_id": intent_id}, {"queue_id": queue_id}, session=session)
        return db.queue.find_one({"_id": queue_id}, session=session)

    return run_transaction(db, enqueue)


def run_pipeline(
    db: Database, audio_path: str = "demo-invoice.wav", *, verbatim: bool = True, hold: bool = False,
) -> dict:
    prepared = prepare_intent(db, audio_path)
    if prepared["status"] == "no_terms":
        return prepared
    source = "verbatim" if verbatim else "cleaned"
    terms = prepared["candidates"][source]
    if terms is None:
        raise ValueError("Selected transcript has no payment terms")
    confirmation = interfaces.readback(terms)
    log_step(db, prepared["run_id"], "readback", terms, {"text": confirmation})
    confirm_intent(db, prepared["intent_id"], terms, source=source)
    item = enqueue_intent(db, prepared["intent_id"])
    outcome = {"queue_id": item["_id"], "status": item["status"]} if hold else process_one(db, queue_id=item["_id"])
    return {**prepared, "status": outcome["status"], "readback": confirmation, "outcome": outcome}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_path", nargs="?", default="demo-invoice.wav")
    parser.add_argument("--normalized", action="store_true")
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    db = connect()
    try:
        print(json.dumps(run_pipeline(db, args.audio_path, verbatim=not args.normalized, hold=args.hold), indent=2))
    finally:
        db.client.close()


if __name__ == "__main__":
    main()
