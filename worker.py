"""Atomically claim work; fence, evaluate, and record the outcome together."""

import argparse
import json
import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.database import Database

import config
import rules
from db.connection import connect
from db.accounts import destination_matches
from db.transactions import RetryObserver, run_transaction
from interfaces import validate_terms
from observability import log_step


def claim_one(db: Database, *, queue_id: str | None = None) -> dict | None:
    query = {"status": "pending"}
    if queue_id is not None:
        query["_id"] = queue_id
    item = db.queue.find_one_and_update(
        query,
        {"$set": {"status": "claimed", "claim_token": str(uuid.uuid4()),
                  "claimed_at": datetime.now(timezone.utc)}},
        sort=[("created_at", 1), ("_id", 1)], return_document=ReturnDocument.AFTER,
    )
    if item is not None:
        log_step(db, item["run_id"], "claim", {"queue_id": item["_id"]}, {"status": "claimed"})
    return item


def process_claimed(
    db: Database, item: dict, *, evaluator: rules.Evaluator | None = None,
    on_retry: RetryObserver | None = None,
) -> dict:
    """Also safe to call again with the same claim after an uncertain result."""
    evaluator = evaluator or rules.evaluate
    guard_enabled = config.static_guard()
    run_id, queue_id = item["run_id"], item["_id"]

    def process(session):
        current = db.queue.find_one({"_id": queue_id}, session=session)
        if current is None or current.get("claim_token") != item.get("claim_token"):
            raise ValueError("Queue claim is missing or owned by another worker")
        if current["status"] == "done":
            payment = db.payments.find_one({"idempotency_key": queue_id}, session=session)
            if payment is None:
                raise ValueError("Completed queue item has no receipt")
            return {"queue_id": queue_id, "status": "done", "payment_id": payment["_id"], "paid_to": payment["paid_to"]}
        if current["status"] == "blocked":
            return {"queue_id": queue_id, "status": "blocked", "reason": current["reason"]}
        if current["status"] != "claimed":
            raise ValueError("Queue item is not claimed")

        def block(reason: str) -> dict:
            db.queue.update_one({"_id": queue_id}, {"$set": {
                "status": "blocked", "reason": reason, "completed_at": datetime.now(timezone.utc),
            }}, session=session)
            outcome = {"queue_id": queue_id, "status": "blocked", "reason": reason}
            log_step(db, run_id, "blocked", {"queue_id": queue_id}, outcome, session=session)
            return outcome

        # First write in the transaction: shared serialization point for payments
        # and account changes. Every subsequent read belongs to this snapshot.
        fence = db.fences.find_one_and_update(
            {"_id": f"fence:{current['vendor_id']}"}, {"$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER, session=session,
        )
        if fence is None:
            return block("Vendor fence is missing")
        log_step(db, run_id, "fence", {"vendor_id": current["vendor_id"]},
                 {"version": fence["version"]}, session=session)

        vendor = db.vendors.find_one({"_id": current["vendor_id"]}, session=session)
        intent = db.intents.find_one({"_id": current["intent_id"]}, session=session)
        if vendor is None or intent is None:
            return block("Vendor or intent is missing")
        if intent["vendor_id"] != vendor["_id"]:
            return block("Intent vendor does not match the queue vendor")
        if intent.get("status") != "confirmed" or "confirmed_terms" not in intent:
            return block("Intent has not been confirmed")
        evidence = db.evidence.find_one({"_id": intent["evidence_id"]}, session=session)
        if evidence is None:
            return block("Intent evidence is missing")
        log_step(db, run_id, "load_evidence", {"evidence_id": evidence["_id"]},
                 {"verbatim_terms": evidence["verbatim_terms"], "cleaned_terms": evidence["cleaned_terms"]},
                 session=session)
        terms = validate_terms(intent["confirmed_terms"])
        if terms["vendor_id"] != vendor["_id"]:
            return block("Confirmed vendor does not match the queue vendor")
        log_step(db, run_id, "load_vendor", {"vendor_id": vendor["_id"]},
                 {"bank_account": vendor["bank_account"]}, session=session)
        log_step(db, run_id, "load_intent", {"intent_id": intent["_id"]}, terms, session=session)

        active_rules = list(db.rule_versions.find({"active": True}, session=session).sort("_id", 1))
        rule_ids = [rule["_id"] for rule in active_rules]
        log_step(db, run_id, "load_rules", {}, {"rule_version_ids": rule_ids}, session=session)

        # Optional static comparison. With no rules and the guard off, route the
        # receipt to the vendor's current account, retaining the requested account.
        if guard_enabled and not destination_matches(terms["destination_account"], vendor["bank_account"]):
            return block("Destination account does not match the current vendor account")

        context = {"vendor": vendor, "intent": intent, "evidence": evidence, "queue": current,
                   "terms": terms, "fence_version": fence["version"], "paid_to": vendor["bank_account"]}
        for rule in active_rules:
            reason = evaluator(rule, context)
            if reason is not None and (not isinstance(reason, str) or not reason.strip()):
                raise ValueError("Rule hook must return None or a non-empty blocking reason")
            log_step(db, run_id, "evaluate_rule", {"rule_version_id": rule["_id"]},
                     {"allowed": reason is None, "reason": reason}, session=session)
            if reason is not None:
                return block(reason)

        payment_id = f"payment:{queue_id}"
        receipt = {
            "_id": payment_id, "idempotency_key": queue_id, "queue_id": queue_id,
            "run_id": run_id, **terms, "vendor_account": vendor["bank_account"],
            "paid_to": vendor["bank_account"],
            "fence_version": fence["version"], "rule_version_ids": rule_ids,
            "created_at": datetime.now(timezone.utc),
        }
        db.payments.insert_one(receipt, session=session)
        log_step(db, run_id, "payment_receipt", {"queue_id": queue_id},
                 {"payment_id": payment_id, "amount_cents": terms["amount_cents"],
                  "paid_to": vendor["bank_account"]}, session=session)
        db.queue.update_one({"_id": queue_id}, {"$set": {
            "status": "done", "payment_id": payment_id, "completed_at": datetime.now(timezone.utc),
        }}, session=session)
        outcome = {"queue_id": queue_id, "status": "done", "payment_id": payment_id, "paid_to": vendor["bank_account"]}
        log_step(db, run_id, "done", {"queue_id": queue_id}, outcome, session=session)
        return outcome

    try:
        outcome = run_transaction(db, process, run_id=run_id, on_retry=on_retry)
    except Exception as error:
        log_step(db, run_id, "worker_error", {"queue_id": queue_id}, {"error_type": type(error).__name__})
        # Do not requeue on an ambiguous commit. The existing claim can be resumed.
        raise
    log_step(db, run_id, "transaction_complete", {"queue_id": queue_id}, outcome)
    return outcome


def process_one(
    db: Database, *, queue_id: str | None = None,
    evaluator: rules.Evaluator | None = None, on_retry: RetryObserver | None = None,
) -> dict | None:
    item = claim_one(db, queue_id=queue_id)
    return None if item is None else process_claimed(db, item, evaluator=evaluator, on_retry=on_retry)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-id")
    args = parser.parse_args()
    db = None
    try:
        db = connect()
        print(json.dumps(process_one(db, queue_id=args.queue_id)))
        return 0
    except Exception as error:
        config.print_error(error)
        return 1
    finally:
        if db is not None:
            db.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
