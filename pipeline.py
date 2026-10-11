"""Dual transcription -> cleaned baseline approval -> queue -> receipt."""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pymongo.database import Database

import interfaces
from config import print_error
from db.connection import connect
from db.evidence import collect_candidates
from db.transactions import run_transaction
from observability import log_step
from worker import process_one


def prepare_intent(
    db: Database, audio_path: str = "demo-invoice.wav", *, adapters: interfaces.Adapters | None = None,
) -> dict:
    adapters = adapters or interfaces.configured_adapters(db)
    run_id, evidence_id, intent_id = (str(uuid.uuid4()) for _ in range(3))
    texts, candidates, metadata = collect_candidates(db, run_id, audio_path, adapters)
    if adapters is interfaces.DEFAULT_ADAPTERS:
        recording = next(record for record in interfaces._recordings()
                         if record["audio_file"] == Path(audio_path).name)
        metadata["fixture_case_id"] = recording["case_id"]

    terms = candidates["verbatim"] or candidates["cleaned"]
    now = datetime.now(timezone.utc)

    def store(session):
        db.evidence.insert_one({
            "_id": evidence_id, "run_id": run_id, "audio_path": str(audio_path), **metadata,
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
        if source != "manual":
            evidence = db.evidence.find_one({"_id": intent["evidence_id"]}, session=session)
            if evidence is None or evidence[f"{source}_terms"] != terms:
                raise ValueError("Confirmed terms do not match the chosen evidence candidate")
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


def approve_and_enqueue(
    db: Database, prepared: dict, *, confirmation_source: str | None = None,
    confirmed_terms: dict | None = None, adapters: interfaces.Adapters | None = None,
) -> dict:
    adapters = adapters or interfaces.configured_adapters(db)
    if prepared["status"] == "no_terms":
        return prepared
    if confirmed_terms is None and confirmation_source is None:
        return prepared
    if confirmed_terms is not None and confirmation_source is not None:
        raise ValueError("Choose a transcript or supply manual confirmed terms, not both")
    source = "manual" if confirmed_terms is not None else confirmation_source
    if source not in {"verbatim", "cleaned", "manual"}:
        raise ValueError("Unknown confirmation source")
    terms = confirmed_terms if source == "manual" else prepared["candidates"][source]
    if terms is None:
        raise ValueError("Selected transcript has no payment terms")
    terms = interfaces.validate_terms(terms)
    confirmation = adapters.readback(terms)
    log_step(db, prepared["run_id"], "readback", terms, {"text": confirmation})
    confirm_intent(db, prepared["intent_id"], terms, source=source)
    item = enqueue_intent(db, prepared["intent_id"])
    return {**prepared, "status": item["status"], "readback": confirmation,
            "outcome": {"queue_id": item["_id"], "status": item["status"]}}


def run_pipeline(
    db: Database, audio_path: str = "demo-invoice.wav", *, confirmation_source: str | None = None,
    confirmed_terms: dict | None = None, hold: bool = False, adapters: interfaces.Adapters | None = None,
) -> dict:
    if confirmation_source is None and confirmed_terms is None:
        confirmation_source = "cleaned"
    result = approve_and_enqueue(db, prepare_intent(db, audio_path, adapters=adapters),
                                 confirmation_source=confirmation_source, confirmed_terms=confirmed_terms,
                                 adapters=adapters)
    if result["status"] != "pending" or hold:
        return result
    outcome = process_one(db, queue_id=result["outcome"]["queue_id"])
    return {**result, "status": outcome["status"], "outcome": outcome}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("enqueue", "demo"):
        command = commands.add_parser(name)
        command.add_argument("audio_path", nargs="?", default="demo-invoice.wav")
        choice = command.add_mutually_exclusive_group()
        choice.add_argument("--confirm-from", choices=["verbatim", "cleaned"])
        choice.add_argument("--confirmed-terms", type=Path, help="JSON file with manually approved terms")
        if name == "demo":
            command.add_argument("--hold", action="store_true", help="Stop after enqueue, before payment")
    process = commands.add_parser("process")
    process.add_argument("--queue-id", help="Only claim this queue item; otherwise claim the oldest pending item")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "--hold":
        argv.insert(0, "demo")
    args = parser.parse_args(argv)
    db = None
    try:
        db = connect()
        if args.command == "process":
            result = process_one(db, queue_id=args.queue_id)
        else:
            prepared = prepare_intent(db, args.audio_path)
            source = None if args.confirmed_terms else (args.confirm_from or "cleaned")
            terms = json.loads(args.confirmed_terms.read_text()) if args.confirmed_terms else None
            result = approve_and_enqueue(db, prepared, confirmation_source=source, confirmed_terms=terms)
            if args.command == "demo" and not args.hold and result["status"] == "pending":
                outcome = process_one(db, queue_id=result["outcome"]["queue_id"])
                result = {**result, "status": outcome["status"], "outcome": outcome}
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print_error(error)
        return 1
    finally:
        if db is not None:
            db.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
