from copy import deepcopy

import pytest
from pymongo.errors import OperationFailure

from db.vendors import update_vendor_bank_account, update_vendor_profile
from oracle import label_run
from pipeline import run_pipeline
from rules import VOCABULARY, evaluate, validate_rule
from worker import process_one


def predicate(left="evidence.verbatim_terms.amount_cents", right="evidence.cleaned_terms.amount_cents",
              cmp="eq", on_fail="clarify"):
    return {"clauses": [{"left": left, "cmp": cmp, "right": right}], "on_fail": on_fail}


@pytest.mark.parametrize("cmp,left,right,outcome", [
    ("eq", 50, 50, None), ("eq", 50, 500, "clarify"),
    ("neq", 50, 50, "clarify"), ("neq", 50, 500, None),
    ("eq", 1, True, "clarify"), ("eq", 50, "50", "clarify"),
])
def test_fixed_comparisons_are_deterministic_without_mutating_input(cmp, left, right, outcome):
    context = {"evidence": {"verbatim_terms": {"amount_cents": left}, "cleaned_terms": {"amount_cents": right}}}
    rule, before = predicate(cmp=cmp), deepcopy(context)
    assert evaluate(rule, context) == evaluate({"rule": rule}, context) == outcome
    assert context == before


@pytest.mark.parametrize("cmp", ["eq", "neq"])
def test_missing_or_null_fields_fail_closed(cmp):
    assert evaluate(predicate(cmp=cmp), {}) == "clarify"
    assert evaluate(predicate(cmp=cmp), {"evidence": {"verbatim_terms": None, "cleaned_terms": None}}) == "clarify"


@pytest.mark.parametrize("invalid", [
    {}, {"clauses": [], "on_fail": "clarify"},
    {"clauses": [{"left": "vendor.memo", "cmp": "eq", "right": "vendor.bank_account"}], "on_fail": "reapprove"},
    {"clauses": [{"left": "vendor.bank_account", "cmp": "exec", "right": "vendor.bank_account"}], "on_fail": "clarify"},
    {"clauses": [{"left": "vendor.bank_account", "cmp": "eq", "right": "vendor.bank_account", "literal": "x"}], "on_fail": "clarify"},
    {"clauses": [{"left": "vendor.bank_account", "cmp": "eq", "right": "vendor.bank_account"}], "on_fail": "pay"},
])
def test_invalid_language_is_rejected(invalid):
    with pytest.raises(ValueError):
        validate_rule(invalid)


def test_vocabulary_has_no_truth_labels_literals_or_current_fence():
    assert len(VOCABULARY) == 15
    assert not any("true_terms" in field or "expected" in field or "vendor.name" in field for field in VOCABULARY)
    assert "vendor.fence_version" not in VOCABULARY


@pytest.mark.integration
def test_worker_reads_evidence_and_clarifies_divergent_terms(db):
    db.rule_versions.insert_one({"_id": "clarify", "version": 1, "active": True, "rule": predicate()})
    result = run_pipeline(db, "amount-correction.wav")
    assert result["status"] == "blocked"
    assert result["outcome"]["reason"] == "clarify"
    assert label_run(db, result["run_id"])["label"] == "ok"


@pytest.mark.integration
@pytest.mark.parametrize("change", [{"bank_account": "new-account"}, {"name": "New name"}, {"memo": "New memo"}])
def test_bank_snapshot_rule_blocks_account_change_but_allows_benign_fence_bump(db, change):
    rule = predicate("intent.approved_bank_account", "vendor.bank_account", on_fail="reapprove")
    db.rule_versions.insert_one({"_id": "bank", "version": 1, "active": True, "rule": rule})
    run_pipeline(db, hold=True)
    if "bank_account" in change:
        update_vendor_bank_account(db, "vendor-001", change["bank_account"])
    else:
        update_vendor_profile(db, "vendor-001", **change)
    outcome = process_one(db)
    assert outcome["status"] == ("blocked" if "bank_account" in change else "done")


@pytest.mark.integration
def test_rule_schema_rejects_unknown_fields(db):
    invalid = predicate("vendor.name", "vendor.bank_account")
    with pytest.raises(OperationFailure):
        db.rule_versions.insert_one({"_id": "invalid", "version": 1, "active": True, "rule": invalid})
