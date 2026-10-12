import json
from copy import deepcopy

import pytest

from db.accounts import destination_matches
from db.seed import SAMPLE_VENDORS
from experiments.failure_hunt.compare import ON_FILE, identical_contexts, run_experiment
from experiments.failure_hunt.dataset import read_dataset
from replay import replay_suite


@pytest.mark.integration
def test_comparison_exports_every_attempt_outcome_with_real_candidate_gates(db, tmp_path):
    data = read_dataset()
    vendors = {v["_id"]: v for v in SAMPLE_VENDORS}
    bad = next(c for c in data["cases"] if c["eval_set"] == "must_catch" and c["recognizer_a_terms"]["amount_cents"] == c["true_terms"]["amount_cents"]
               and not destination_matches(c["recognizer_a_terms"]["destination_account"], vendors[c["recognizer_a_terms"]["vendor_id"]]["bank_account"]))
    good = next(c for c in data["cases"] if c["eval_set"] == "must_pass" and c["recognizer_a_terms"] is not None)
    no_terms = next(c for c in data["cases"] if c["eval_set"] == "holdout" and c["recognizer_a_terms"] is None)
    data["cases"] = [bad, good, no_terms]
    ids = {c["case_id"] for c in data["cases"]} | {data["excluded"][0]["case_id"]}
    data["payment_attempts"] = [r for r in data["payment_attempts"] if r["case_id"] in ids]
    data["excluded"] = data["excluded"][:1]
    path = tmp_path / "comparison.json"
    result = run_experiment(db, path, proposer=lambda *args: [ON_FILE], data=data)
    assert result["status"] == "complete"
    assert set(result["comparators"]) == {"empty_rules", "recognizer_agreement", "spoken_account_on_file", "live_heal"}
    assert result["comparators"]["empty_rules"]["metrics"]["unsafe_baseline"] == 1
    assert result["comparators"]["live_heal"]["metrics"]["caught_unsafe"] == 1
    assert result["heal_iterations"][1]["candidates"][0]["decision"] == "activated"
    assert result["comparators"]["live_heal"]["metrics"]["intake_clarifications"] == 1
    assert all(len(r["outcomes"]) == 4 for r in result["payment_attempts"])
    excluded = next(r for r in result["payment_attempts"] if r["eval_set"] == "excluded")
    assert all(v["status"] == "excluded" for v in excluded["outcomes"].values())
    assert json.loads(path.read_text()) == result


@pytest.mark.integration
def test_shared_amount_error_has_a_real_safe_context_twin(mongo_client):
    cases = read_dataset()["cases"]
    bad = next(c for c in cases if c["case_id"].endswith("fourteen") and c["selection_predicted_unsafe"]
               and c["recognizer_a_terms"] == c["recognizer_b_terms"])
    good = next(c for c in cases if c["case_id"].endswith("forty") and not c["selection_predicted_unsafe"]
                and c["recognizer_a_terms"] == bad["recognizer_a_terms"] and c["recognizer_b_terms"] == bad["recognizer_b_terms"])
    result = replay_suite(mongo_client, [], cases=[bad, good])
    collisions = identical_contexts(result)
    assert len(collisions) == 1
    assert collisions[0]["unsafe_cases"] == [bad["case_id"]]
    assert collisions[0]["good_cases"] == [good["case_id"]]
