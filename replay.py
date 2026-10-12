"""Replay isolated fixture suites through the real worker and oracle."""

import uuid
from collections import Counter
from copy import deepcopy

from pymongo.write_concern import WriteConcern
from pymongo import ReplaceOne

from db.seed import SAMPLE_VENDORS, seed
from db.vendors import update_vendor_bank_account, update_vendor_profile
from fixtures.catalog import fixture_cases
from interfaces import Adapters, DEFAULT_ADAPTERS, validate_terms
from oracle import label_run
from pipeline import approve_and_enqueue, prepare_intent, run_pipeline
from rules import TERM_FIELDS, field_value, validate_rule
from worker import process_one

LABELS = ("unsafe_commit", "benign_block", "ok", "duplicate")
EVAL_SETS = ("must_catch", "must_pass", "holdout")


def summarize(rows):
    counts = Counter(row["label"] for row in rows)
    return {"N": len(rows), **{label: counts[label] for label in LABELS}}


def field_diffs(context):
    pairs = [(f"evidence.verbatim_terms.{field}", f"evidence.cleaned_terms.{field}") for field in TERM_FIELDS]
    pairs += [(f"intent.confirmed_terms.{field}", f"evidence.verbatim_terms.{field}") for field in TERM_FIELDS]
    pairs += [("intent.approved_bank_account", "vendor.bank_account")]
    if "recognizer_b_terms" in context.get("evidence", {}):
        pairs += [(f"evidence.recognizer_a_terms.{field}", f"evidence.recognizer_b_terms.{field}") for field in TERM_FIELDS]
        pairs += [("evidence.recognizer_a_terms.destination_account", "vendor.bank_account")]
    diffs = []
    for left, right in pairs:
        values = [field_value(context, name) for name in (left, right)]
        values = [value if isinstance(value, (str, int)) else None for value in values]
        if values[0] != values[1]:
            diffs.append({"left": left, "right": right, "left_value": values[0], "right_value": values[1]})
    return diffs


def _adapters(case):
    def transcribe(path, *, no_verbatim):
        return case["cleaned_text" if no_verbatim else "verbatim_text"]
    modes = iter(("verbatim", "cleaned"))
    def extract(text):
        mode = next(modes)
        if text != case[f"{mode}_text"]:
            raise ValueError("Transcript is outside the replay scenario")
        terms = case[f"{mode}_terms"]
        return None if terms is None else validate_terms(terms)
    return Adapters(transcribe, extract, DEFAULT_ADAPTERS.readback)


def _run_case(db, case):
    # Only this private scratch database is reset. Stored receipts and approval
    # snapshots remain sufficient for historical oracle checks across cases.
    db.vendors.bulk_write([ReplaceOne({"_id": vendor["_id"]}, dict(vendor), upsert=True) for vendor in SAMPLE_VENDORS])
    db.fences.bulk_write([ReplaceOne({"_id": f"fence:{vendor['_id']}"},
                                    {"_id": f"fence:{vendor['_id']}", "version": 0}, upsert=True)
                          for vendor in SAMPLE_VENDORS])
    # Scenario setup follows extracted candidates; truth is oracle-only data.
    terms = case["cleaned_terms"] or case["verbatim_terms"]
    vendor_id = terms["vendor_id"] if terms else None
    if case.get("initial_vendor"):
        db.vendors.update_one({"_id": vendor_id}, {"$set": case["initial_vendor"]})
    if "recognizer_b_terms" in case:
        adapters = _adapters(case)
        prepared = prepare_intent(db, case["audio_file"], adapters=adapters)
        db.evidence.update_one({"_id": prepared["evidence_id"]}, {"$set": {
            field: case[field] for field in ("recognizer_a_text", "recognizer_a_terms", "recognizer_b_text", "recognizer_b_terms")
        }})
        prepared["candidates"]["recognizer_a"] = case["recognizer_a_terms"]
        result = approve_and_enqueue(db, prepared, confirmation_source="recognizer_a", adapters=adapters)
    else:
        result = run_pipeline(db, case["audio_file"], hold=True, adapters=_adapters(case))
    db.evidence.update_one({"_id": result["evidence_id"]}, {"$set": {"fixture_case_id": case["case_id"]}})
    for change in case.get("changes", []):
        if "bank_account" in change:
            update_vendor_bank_account(db, vendor_id, change["bank_account"], run_id=result["run_id"])
        else:
            update_vendor_profile(db, vendor_id, **change, run_id=result["run_id"])
    context = {
        "evidence": db.evidence.find_one({"_id": result["evidence_id"]}),
        "intent": db.intents.find_one({"_id": result["intent_id"]}) if result["intent_id"] else {},
        "vendor": db.vendors.find_one({"_id": vendor_id}) if vendor_id else {},
    }
    outcome = process_one(db, queue_id=result["outcome"]["queue_id"], static_guard=False) if result["status"] == "pending" else None
    verdict = label_run(db, result["run_id"], reference_case=case)
    receipt = db.payments.find_one({"run_id": result["run_id"]})
    intent = context["intent"]
    return {**verdict, "eval_set": case["eval_set"], "block_reason": outcome.get("reason") if outcome else "clarify" if result["status"] == "no_terms" else None,
            "field_diffs": field_diffs(context), "approved_terms": intent.get("confirmed_terms"),
            "approval_snapshot": {key: intent.get(key) for key in ("approved_bank_account", "approved_fence_version")},
            "paid_to": receipt["paid_to"] if receipt else None,
            "amount_paid": receipt["amount_cents"] if receipt else None,
            "amount_unit": "integer_cents", "evidence_id": result["evidence_id"]}


def replay_suite(client, current_rules: list[dict], *, candidate: dict | None = None,
                 sets=EVAL_SETS, cases=None) -> dict:
    """Own and drop a fresh scratch database; never copy application data into it."""
    documents = deepcopy(current_rules) + ([deepcopy(candidate)] if candidate is not None else [])
    for document in documents:
        document["rule"] = validate_rule(document["rule"])
    if any(eval_set not in EVAL_SETS for eval_set in sets):
        raise ValueError("Unknown replay eval_set")
    name = "mongodb_hackathon_replay_" + uuid.uuid4().hex
    db = client[name].with_options(write_concern=WriteConcern("majority"))
    try:
        seed(db)
        if documents:
            db.rule_versions.insert_many([
                {"_id": document["_id"], "version": document["version"], "active": True, "rule": document["rule"]}
                for document in documents
            ])
        rows = [_run_case(db, case) for case in (fixture_cases() if cases is None else cases) if case["eval_set"] in sets]
        return {"counts": summarize(rows),
                "sets": {eval_set: summarize([row for row in rows if row["eval_set"] == eval_set]) for eval_set in sets},
                "cases": rows}
    finally:
        client.drop_database(name)


def training_failures(result: dict) -> list[dict]:
    """The sole scenario-data boundary into the proposer excludes benign/holdout."""
    return [{key: deepcopy(row[key]) for key in ("case_id", "eval_set", "label", "expected", "reasons", "block_reason", "field_diffs")}
            for row in result["cases"] if row["eval_set"] == "must_catch" and row["label"] != "ok"]


def gate_result(before: dict, after: dict) -> dict:
    reasons = []
    if sum(after["sets"]["must_catch"][label] for label in LABELS if label != "ok") >= sum(before["sets"]["must_catch"][label] for label in LABELS if label != "ok"):
        reasons.append("must_catch_failures_not_decreased")
    if after["sets"]["must_pass"]["benign_block"] != 0:
        reasons.append("must_pass_benign_blocks_nonzero")
    for eval_set in EVAL_SETS:
        if after["sets"][eval_set]["benign_block"] > before["sets"][eval_set]["benign_block"]:
            reasons.append(f"{eval_set}_benign_blocks_increased")
        if after["sets"][eval_set]["duplicate"] > before["sets"][eval_set]["duplicate"]:
            reasons.append(f"{eval_set}_duplicates_increased")
    old_rows = {row["case_id"]: row for row in before["cases"]}
    for row in after["cases"]:
        old = old_rows[row["case_id"]]
        if row["eval_set"] == "holdout" and old["label"] == "ok" and row["label"] != "ok":
            reasons.append("holdout_case_regressed")
    for label in ("unsafe_commit", "benign_block", "duplicate"):
        if after["sets"]["holdout"][label] > before["sets"]["holdout"][label]:
            reasons.append("holdout_worse")
    return {"accepted": not reasons, "reasons": sorted(set(reasons)),
            "before": deepcopy(before["sets"]), "after": deepcopy(after["sets"])}
