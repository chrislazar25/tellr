import json
from copy import deepcopy

import pytest

from fixtures.catalog import fixture_cases
from replay import gate_result, replay_suite, training_failures

CLARIFY = {"clauses": [{"left": f"evidence.verbatim_terms.{field}", "cmp": "eq",
                         "right": f"evidence.cleaned_terms.{field}"}
                        for field in ("vendor_id", "amount_cents", "destination_account", "date")], "on_fail": "clarify"}
BANK = {"clauses": [{"left": "intent.approved_bank_account", "cmp": "eq", "right": "vendor.bank_account"}], "on_fail": "reapprove"}


def document(name, rule):
    return {"_id": name, "version": 1, "rule": rule}


def test_holdout_scenarios_cover_new_vendor_amounts_suffix_and_second_bank_change():
    cases = [case for case in fixture_cases() if case["eval_set"] == "holdout"]
    assert len(cases) == 5
    assert any(case["true_terms"]["vendor_id"] == "vendor-003" for case in cases)
    assert any("ending 13, sorry, 31" in case["verbatim_text"] for case in cases)
    assert any(len(case.get("changes", [])) == 2 for case in cases)


def test_must_catch_contains_failures_and_must_pass_contains_benign_cases():
    cases = fixture_cases()
    catch = [case for case in cases if case["eval_set"] == "must_catch"]
    passed = [case for case in cases if case["eval_set"] == "must_pass"]
    assert len(catch) == 4 and len(passed) == 5
    assert all(case["expected"] in {"clarify", "reapprove"} for case in catch)
    assert all(case["expected"] == "pay" for case in passed)


@pytest.mark.integration
def test_replay_and_gate_fix_train_without_blocking_benign_twins_or_regressing_holdouts(mongo_client, monkeypatch):
    monkeypatch.setenv("STATIC_GUARD", "on")  # Replay owns baseline guard behavior.
    before_databases = set(mongo_client.list_database_names())
    baseline = replay_suite(mongo_client, [])
    assert baseline["counts"] == {"N": 14, "unsafe_commit": 7, "benign_block": 0, "ok": 7, "duplicate": 0}
    clarified = replay_suite(mongo_client, [], candidate=document("a-clarify", CLARIFY))
    assert gate_result(baseline, clarified)["accepted"] is True
    assert clarified["sets"]["must_catch"]["unsafe_commit"] == 1
    healed = replay_suite(mongo_client, [document("a-clarify", CLARIFY)], candidate=document("b-bank", BANK))
    assert gate_result(clarified, healed)["accepted"] is True
    assert healed["counts"] == {"N": 14, "unsafe_commit": 0, "benign_block": 0, "ok": 14, "duplicate": 0}
    assert set(mongo_client.list_database_names()) == before_databases
    failures = training_failures(baseline)
    assert len(failures) == 4
    serialized = json.dumps(failures)
    assert "holdout" not in serialized and "vendor-003" not in serialized
    assert all(failure["reasons"] and failure["field_diffs"] for failure in failures)


@pytest.mark.integration
def test_blanket_block_rule_is_rejected_despite_removing_unsafe_commits(mongo_client):
    baseline = replay_suite(mongo_client, [])
    blanket = {"clauses": [{"left": "vendor.bank_account", "cmp": "neq", "right": "vendor.bank_account"}], "on_fail": "reapprove"}
    after = replay_suite(mongo_client, [], candidate=document("blanket", blanket))
    verdict = gate_result(baseline, after)
    assert verdict["accepted"] is False
    assert "must_pass_benign_blocks_increased" in verdict["reasons"]
    assert "holdout_case_regressed" in verdict["reasons"]


@pytest.mark.integration
def test_replay_never_modifies_source_database(db):
    before = list(db.vendors.find().sort("_id", 1))
    replay_suite(db.client, [], sets=("must_catch",))
    assert list(db.vendors.find().sort("_id", 1)) == before
    assert db.payments.count_documents({}) == 0


@pytest.mark.integration
def test_truth_vendor_cannot_choose_the_pipeline_destination_change_target(db):
    from replay import _run_case
    case = deepcopy(next(case for case in fixture_cases() if case["case_id"] == "queued_destination_change"))
    case["true_terms"]["vendor_id"] = "vendor-003"
    result = _run_case(db, case)
    payment = db.payments.find_one({"run_id": result["run_id"]})
    assert payment["vendor_id"] == "vendor-001"
    assert payment["paid_to"] == "demo-account-after-approval"


@pytest.mark.integration
def test_measured_replay_uses_explicit_truth_instead_of_lossy_fixture_expectations(mongo_client):
    case = deepcopy(next(case for case in fixture_cases() if case["case_id"] == "500_no_50"))
    case.update(cleaned_text=case["verbatim_text"], cleaned_terms=case["verbatim_terms"], expected="pay", eval_set="must_pass")
    result = replay_suite(mongo_client, [], cases=[case])
    row = result["cases"][0]
    assert row["label"] == "ok" and row["expected"] == "pay"
    assert row["amount_paid"] == 5000 and row["paid_to"] == "demo-account-001"
    assert row["approved_terms"] == case["true_terms"]
    assert row["approval_snapshot"]["approved_bank_account"] == row["paid_to"]


@pytest.mark.integration
def test_identical_transcripts_preserve_independent_extraction_results(mongo_client):
    case = deepcopy(next(case for case in fixture_cases() if case["case_id"] == "500_no_50"))
    case["cleaned_text"] = case["verbatim_text"]
    row = replay_suite(mongo_client, [], cases=[case])["cases"][0]
    assert row["approved_terms"] == case["cleaned_terms"]
    assert row["amount_paid"] == 50000 and row["label"] == "unsafe_commit"
