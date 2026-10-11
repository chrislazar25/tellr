import pytest

from db.vendors import update_vendor_bank_account, update_vendor_profile
from fixtures.catalog import fixture_cases
from oracle import label_run
from pipeline import run_pipeline
from worker import process_one


def test_every_fixture_has_expected_action_and_divergence_requires_clarification():
    for case in fixture_cases():
        assert case["expected"] in {"pay", "clarify", "reapprove"}
        if case["verbatim_terms"] != case["cleaned_terms"]:
            assert case["expected"] == "clarify"


@pytest.mark.integration
@pytest.mark.parametrize("expected,reason,label", [
    ("clarify", "clarify", "ok"), ("clarify", "reapprove", "ok"),
    ("clarify", "unrelated block", "benign_block"),
    ("reapprove", "reapprove", "ok"), ("reapprove", "clarify", "benign_block"),
    ("pay", "clarify", "benign_block"),
])
def test_oracle_scores_expected_block_action(db, expected, reason, label):
    audio = "amount-correction.wav" if expected == "clarify" else "demo-invoice.wav"
    result = run_pipeline(db, audio, hold=True)
    case_id = {"clarify": "500_no_50", "reapprove": "queued_destination_change", "pay": "clean"}[expected]
    db.evidence.update_one({"_id": result["evidence_id"]}, {"$set": {"fixture_case_id": case_id}})
    if expected == "reapprove":
        update_vendor_bank_account(db, "vendor-001", "demo-account-after-approval")
    db.rule_versions.insert_one({"_id": "test-block", "version": 1, "active": True, "rule": {"clauses": [{"left": "vendor.bank_account", "cmp": "eq", "right": "vendor.bank_account"}], "on_fail": "clarify"}})
    process_one(db, evaluator=lambda rule, context: reason)
    assert label_run(db, result["run_id"])["label"] == label


@pytest.mark.integration
@pytest.mark.parametrize("change", [{"name": "Updated vendor name"}, {"memo": "Updated memo"}])
def test_benign_profile_edit_advances_fence_without_changing_destination(db, change):
    result = run_pipeline(db, hold=True)
    update_vendor_profile(db, "vendor-001", **change)
    case_id = "benign_vendor_name" if "name" in change else "benign_vendor_memo"
    db.evidence.update_one({"_id": result["evidence_id"]}, {"$set": {"fixture_case_id": case_id}})
    process_one(db)
    assert db.payments.find_one()["fence_version"] == 2
    assert db.payments.find_one()["paid_to"] == "demo-account-001"
    assert label_run(db, result["run_id"])["label"] == "ok"
