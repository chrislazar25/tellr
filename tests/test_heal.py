import json
from copy import deepcopy

import pytest
from pymongo.errors import OperationFailure

from fixtures.catalog import fixture_cases
from heal import ActivationConflict, activate_candidate, active_rules, fingerprint, run_heal
from proposer import FixtureProposer
from replay import EVAL_SETS, summarize

pytestmark = pytest.mark.integration


def fake_suite(client, current, *, candidate=None):
    rows = [{"case_id": case["case_id"], "eval_set": case["eval_set"], "expected": case["expected"],
             "label": "unsafe_commit" if case["expected"] != "pay" else "ok", "reasons": ["test"],
             "block_reason": None, "field_diffs": []} for case in fixture_cases()]
    return {"counts": summarize(rows), "sets": {name: summarize([row for row in rows if row["eval_set"] == name]) for name in EVAL_SETS}, "cases": rows}


def test_healer_uses_real_gate_audits_every_candidate_and_activates_best(db):
    calls = []
    class Captured(FixtureProposer):
        def __call__(self, failures, current, vocabulary):
            calls.append(deepcopy(failures))
            return super().__call__(failures, current, vocabulary)
    before = list(db.vendors.find().sort("_id", 1))
    result = run_heal(db, Captured())
    assert result["status"] == "healed" and result["iterations"] == 2
    assert result["counts"] == {"N": 14, "unsafe_commit": 0, "benign_block": 0, "ok": 14, "duplicate": 0}
    candidates = list(db.rule_versions.find({"heal_id": result["heal_id"]}))
    assert len(candidates) == 6
    assert db.rule_versions.count_documents({"active": True}) == 2
    assert any(document["decision"] == "rejected" and "must_pass" in document["rejected_reason"] for document in candidates)
    assert all(set(document["gate"]["before"]) == set(EVAL_SETS) for document in candidates)
    assert all(document["decision"] != "pending" for document in candidates)
    assert "holdout" not in json.dumps(calls) and "vendor-003" not in json.dumps(calls)
    assert len(calls[0]) == 4 and len(calls[1]) == 1
    traces = list(db.runs.find({"run_id": result["heal_id"], "step": "collect_failures"}).sort("ts", 1))
    assert [len(row["outputs"]["failures"]) for row in traces] == [4, 1]
    assert "holdout" not in json.dumps([row["outputs"] for row in traces])
    assert list(db.vendors.find().sort("_id", 1)) == before
    assert db.payments.count_documents({}) == 0
    assert [row["iteration"] for row in db.runs.find({"step": "heal_iteration"}).sort("iteration", 1)] == [0, 1, 2]


def test_empty_proposals_are_logged_and_stop_cleanly(db):
    result = run_heal(db, lambda *args: [], replayer=fake_suite)
    assert result["status"] == "no_candidates" and result["iterations"] == 1
    assert db.rule_versions.count_documents({}) == 0
    event = db.runs.find_one({"run_id": result["heal_id"], "step": "propose_rules"})
    assert event["outputs"]["candidate_count"] == 0 and event["outputs"]["empty"] is True


def test_rejected_candidates_never_activate_and_iteration_limit_is_three(db):
    rule = FixtureProposer()([], [])[0]
    result = run_heal(db, lambda *args: [rule], replayer=fake_suite)
    assert result["status"] == "iteration_limit" and result["iterations"] == 3
    assert db.rule_versions.count_documents({"active": True}) == 0
    assert db.rule_versions.count_documents({"decision": "rejected"}) == 3


def test_invalid_language_is_audited_inactive_without_replaying_candidate(db):
    calls = []
    def replay(*args, **kwargs):
        assert "candidate" not in kwargs
        calls.append(True)
        return fake_suite(*args)
    result = run_heal(db, lambda *args: [{"clauses": [], "on_fail": "clarify"}], max_iterations=1, replayer=replay)
    document = db.rule_versions.find_one({"heal_id": result["heal_id"]})
    assert document["rule"] is None and document["active"] is False
    assert document["proposal"] == {"clauses": [], "on_fail": "clarify"}
    assert document["gate"]["reasons"] == ["invalid_rule"]
    with pytest.raises(OperationFailure):
        db.rule_versions.update_one({"_id": document["_id"]}, {"$set": {"active": True}})


def test_eight_active_rules_prevent_further_proposals(db):
    rule = {"clauses": [{"left": "vendor.bank_account", "cmp": "eq", "right": "vendor.bank_account"}], "on_fail": "clarify"}
    db.rule_versions.insert_many([{"_id": f"existing-{i}", "version": i, "active": True, "rule": rule} for i in range(8)])
    result = run_heal(db, lambda *args: pytest.fail("Must not propose beyond cap"), replayer=fake_suite)
    assert result["status"] == "active_rule_limit" and result["iterations"] == 0
    assert len(active_rules(db)) == 8


def test_stale_gate_cannot_activate_after_policy_changes(db):
    from proposer import FixtureProposer
    result = run_heal(db, lambda *args: [FixtureProposer()([], [])[0]], max_iterations=1, replayer=fake_suite)
    candidate = db.rule_versions.find_one({"heal_id": result["heal_id"]})
    # Make an accepted gate, then change active policy before trying activation.
    db.rule_versions.update_one({"_id": candidate["_id"]}, {"$set": {"decision": "accepted", "rejected_reason": None, "gate.accepted": True, "gate.reasons": []}})
    db.rule_versions.insert_one({"_id": "concurrent-rule", "version": 20, "active": True, "rule": candidate["rule"]})
    with pytest.raises(ActivationConflict):
        activate_candidate(db, candidate["_id"], fingerprint([]))
    assert db.rule_versions.find_one({"_id": candidate["_id"]})["active"] is False


def test_iteration_limit_cannot_exceed_three(db):
    with pytest.raises(ValueError):
        run_heal(db, FixtureProposer(), max_iterations=4)
