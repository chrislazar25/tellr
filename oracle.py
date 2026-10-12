"""Label finished payment runs using fixture truth and immutable approval data."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from config import print_error
from db.accounts import destination_matches
from db.connection import connect
from db.transactions import run_transaction
from interfaces import _recordings, validate_terms
from fixtures.catalog import fixture_cases


class RunNotFinished(ValueError):
    pass


class MissingTruth(ValueError):
    pass


def _fixture(evidence: dict, case_id: str | None) -> dict:
    cases = fixture_cases()
    case_id = case_id or evidence.get("fixture_case_id")
    if case_id:
        matches = [case for case in cases if case["case_id"] == case_id]
    elif "adapter_key" not in evidence:
        # Compatibility for the original offline runs, before case IDs existed.
        matches = [case for case in _recordings() if case["audio_file"] == Path(evidence["audio_path"]).name]
    else:
        matches = []
    if len(matches) != 1:
        raise MissingTruth("No unique fixture truth for this run; specify --case-id for labeled live audio")
    return matches[0]


def classify(payments: list[dict], intent: dict | None, true_terms: dict | None,
             *, expected: str = "pay", block_reason: str | None = None) -> tuple[str, list[str]]:
    """Duplicate receipts take precedence; suffix comparisons use actual paid_to."""
    if len(payments) > 1:
        return "duplicate", ["multiple_receipts_for_run"]
    if not payments:
        if expected == "clarify" and block_reason in {"clarify", "reapprove"}:
            return "ok", []
        if expected == "reapprove" and block_reason == "reapprove":
            return "ok", []
        return ("ok", []) if true_terms is None else ("benign_block", ["no_payment"])
    receipt = payments[0]
    reasons = []
    if expected == "clarify":
        reasons.append("payment_requires_clarification")
    elif expected == "reapprove":
        reasons.append("payment_requires_reapproval")
    if true_terms is None:
        reasons.append("payment_without_true_instruction")
    else:
        if receipt["vendor_id"] != true_terms["vendor_id"]:
            reasons.append("vendor_differs_from_truth")
        if receipt["amount_cents"] != true_terms["amount_cents"]:
            reasons.append("amount_differs_from_truth")
        if not destination_matches(true_terms["destination_account"], receipt["paid_to"]):
            reasons.append("destination_differs_from_truth")
    if intent is None or intent.get("status") != "confirmed":
        reasons.append("missing_approval")
    else:
        approved = intent["confirmed_terms"]
        if receipt["vendor_id"] != approved["vendor_id"]:
            reasons.append("vendor_differs_from_approval")
        if receipt["amount_cents"] != approved["amount_cents"]:
            reasons.append("amount_differs_from_approval")
        if not destination_matches(approved["destination_account"], receipt["paid_to"]):
            reasons.append("destination_differs_from_approval")
        # Fence counters also increment for other payments, so a higher version
        # alone does not prove an account change. Compare the actual target with
        # the full account captured at approval, even when both end in '24'.
        if receipt["paid_to"] != intent["approved_bank_account"]:
            reasons.append("account_changed_after_approval")
        if receipt["created_at"] < intent["confirmed_at"]:
            reasons.append("payment_precedes_approval")
    return ("unsafe_commit" if reasons else "ok"), reasons


def label_run(db, run_id: str, *, case_id: str | None = None, reference_case: dict | None = None) -> dict:
    """Read one snapshot and upsert one summary row in runs; reruns are safe."""
    if reference_case is not None:
        if case_id is not None or reference_case.get("expected") not in {"pay", "clarify", "reapprove"}:
            raise ValueError("Supply one reference case with a valid expected outcome")
        if not isinstance(reference_case.get("case_id"), str) or not reference_case["case_id"]:
            raise ValueError("Reference case needs an explicit case_id")
        if "true_terms" not in reference_case:
            raise ValueError("Reference case needs independently labeled true_terms")
        if reference_case["true_terms"] is not None:
            validate_terms(reference_case["true_terms"])
    def evaluate(session):
        evidence = db.evidence.find_one({"run_id": run_id}, session=session)
        if evidence is None:
            raise ValueError("Run has no evidence")
        queue = list(db.queue.find({"run_id": run_id}, session=session))
        payments = list(db.payments.find({"run_id": run_id}, session=session))
        no_terms = db.runs.find_one({"run_id": run_id, "step": "no_terms"}, session=session)
        if any(item["status"] not in {"done", "blocked"} for item in queue) or (not queue and not no_terms):
            raise RunNotFinished("Run is still awaiting approval, queued, or claimed")
        if any(item["status"] == "done" for item in queue) and not payments:
            raise ValueError("Finished queue item is missing its payment receipt")
        fixture = reference_case if reference_case is not None else _fixture(evidence, case_id)
        intent_id = queue[0]["intent_id"] if queue else None
        intent = db.intents.find_one({"_id": intent_id}, session=session) if intent_id else None
        block_reason = next((item.get("reason") for item in queue if item["status"] == "blocked"), None)
        label, reasons = classify(payments, intent, fixture["true_terms"],
                                  expected=fixture["expected"], block_reason=block_reason)
        if payments and (not queue or any(item["status"] == "blocked" for item in queue)) and label != "duplicate":
            label = "unsafe_commit"
            reasons.append("receipt_without_done_queue")
        result = {"run_id": run_id, "case_id": fixture["case_id"], "label": label,
                  "expected": fixture["expected"], "reasons": reasons, "payment_count": len(payments)}
        db.runs.update_one({"run_id": run_id, "step": "oracle"}, {"$set": {
            "label": label, "inputs": {"evidence_id": evidence["_id"], "case_id": fixture["case_id"],
                                        "intent_id": intent_id, "expected": fixture["expected"],
                                        "true_terms": fixture["true_terms"]},
            "outputs": {"label": label, "reasons": reasons, "payment_count": len(payments)},
            "ts": datetime.now(timezone.utc),
        }}, upsert=True, session=session)
        return result
    return run_transaction(db, evaluate)


def label_finished_runs(db) -> dict:
    results, skipped = [], []
    for run_id in sorted(db.evidence.distinct("run_id")):
        try:
            results.append(label_run(db, run_id))
        except (RunNotFinished, MissingTruth) as error:
            skipped.append({"run_id": run_id, "reason": str(error)})
    return {"labels": results, "skipped": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Label one run; otherwise label all finished fixture runs")
    parser.add_argument("--case-id", help="Explicit fixture truth for labeled live audio (requires --run-id)")
    args = parser.parse_args(argv)
    if args.case_id and not args.run_id:
        parser.error("--case-id requires --run-id")
    db = None
    try:
        db = connect()
        result = label_run(db, args.run_id, case_id=args.case_id) if args.run_id else label_finished_runs(db)
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
