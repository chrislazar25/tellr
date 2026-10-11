import json

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
    cases = [case for case in fixture_cases() if case["split"] == "holdout"]
    assert len(cases) == 5
    assert any(case["true_terms"]["vendor_id"] == "vendor-003" for case in cases)
    assert any("ending 13, sorry, 31" in case["verbatim_text"] for case in cases)
    assert any(len(case.get("changes", [])) == 2 for case in cases)


@pytest.mark.integration
def test_replay_and_gate_fix_train_without_blocking_benign_twins_or_regressing_holdouts(mongo_client, monkeypatch):
    monkeypatch.setenv("STATIC_GUARD", "on")  # Replay owns baseline guard behavior.
    before_databases = set(mongo_client.list_database_names())
    baseline = replay_suite(mongo_client, [])
    assert baseline["counts"] == {"N": 14, "unsafe_commit": 7, "benign_block": 0, "ok": 7, "duplicate": 0}
    clarified = replay_suite(mongo_client, [], candidate=document("a-clarify", CLARIFY))
    assert gate_result(baseline, clarified)["accepted"] is True
    assert clarified["splits"]["train"]["unsafe_commit"] == 1
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
    assert "benign_benign_blocks_increased" in verdict["reasons"]
    assert "holdout_case_regressed" in verdict["reasons"]


@pytest.mark.integration
def test_replay_never_modifies_source_database(db):
    before = list(db.vendors.find().sort("_id", 1))
    replay_suite(db.client, [], splits=("train",))
    assert list(db.vendors.find().sort("_id", 1)) == before
    assert db.payments.count_documents({}) == 0
