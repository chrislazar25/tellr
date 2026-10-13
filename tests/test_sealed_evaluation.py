import json
from copy import deepcopy
from pathlib import Path

import pytest

from experiments.failure_hunt import keys
from experiments.failure_hunt.sealed_eval import digest, evaluate_sealed, frozen_policy, read_sealed

POLICY = Path(__file__).resolve().parents[1] / "artifacts/failure_hunt_experiment.json"


def test_sealed_cache_matches_independent_truth_without_keys(monkeypatch):
    monkeypatch.setattr(keys, "get", lambda *args: pytest.fail("No key access for sealed cache"))
    data = read_sealed()
    assert len(data["payment_attempts"]) == 60
    assert len(data["cases"]) == 56 and len(data["excluded"]) == 4
    assert data["scribe_errors"] == []
    assert sum(case["expected"] == "pay" for case in data["cases"]) == 55
    assert sum(case["recognizer_a_terms"] is None for case in data["cases"]) == 1
    assert all(case["expected"] == "clarify" for case in data["excluded"])
    policy = frozen_policy(POLICY)
    assert digest(policy["rules"]) == policy["rules_sha256"]


def test_modified_frozen_policy_cannot_run(tmp_path):
    policy = frozen_policy(POLICY)
    policy["rules"][0]["on_fail"] = "reapprove"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        evaluate_sealed(None, policy, tmp_path / "no-result.json")
    assert not (tmp_path / "no-result.json").exists()


def test_sealed_loader_rejects_wrong_cached_audio_link(tmp_path):
    from experiments.failure_hunt.sealed_score import ROOT
    root = tmp_path / "experiments/failure_hunt"
    root.mkdir(parents=True)
    for name in ("sealed_attempts.jsonl", "sealed_scored.json", "sealed_manifest.json"):
        (root / name).write_bytes((ROOT / name).read_bytes())
    manifest = json.loads((root / "sealed_manifest.json").read_text())
    manifest["files"][0]["file"] = "outside.wav"
    (root / "sealed_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="audio directory"):
        read_sealed(root)


@pytest.mark.integration
def test_frozen_evaluation_runs_real_worker_without_gate_or_activation(db, tmp_path, monkeypatch):
    import replay
    import heal
    from experiments.failure_hunt import compare
    def forbidden(*args, **kwargs):
        pytest.fail("Sealed results cannot enter proposal, gate, or activation")
    monkeypatch.setattr(replay, "gate_result", forbidden)
    monkeypatch.setattr(compare, "gate_result", forbidden)
    monkeypatch.setattr(heal, "run_heal", forbidden)
    data = read_sealed()
    disagreement = next(case for case in data["cases"] if case["recognizer_a_terms"] is not None and (case["recognizer_b_terms"] is None or any(case["recognizer_a_terms"][f] != case["recognizer_b_terms"][f] for f in ("amount_cents", "destination_account"))))
    agreement = next(case for case in data["cases"] if case["recognizer_a_terms"] == case["recognizer_b_terms"])
    data["cases"] = [disagreement, agreement]
    ids = {case["case_id"] for case in data["cases"]} | {data["excluded"][0]["case_id"]}
    data["payment_attempts"] = [row for row in data["payment_attempts"] if row["case_id"] in ids]
    data["excluded"] = data["excluded"][:1]
    policy = frozen_policy(POLICY)
    original = deepcopy(policy)
    sentinel = {"_id": "existing-policy", "version": 1, "active": True, "rule": policy["rules"][0]}
    db.rule_versions.insert_one(sentinel)
    dropped = []
    original_drop = db.client.drop_database
    def record_owned_cleanup(name, *args, **kwargs):
        dropped.append(name)
        return original_drop(name, *args, **kwargs)
    monkeypatch.setattr(db.client, "drop_database", record_owned_cleanup)
    output = tmp_path / "sealed.json"
    result = evaluate_sealed(db.client, policy, output, data=data)
    assert policy == original
    assert db.rule_versions.find_one({"_id": sentinel["_id"]}) == sentinel
    assert len(set(dropped)) == 3
    assert all(name.startswith("mongodb_hackathon_replay_") for name in dropped)
    assert not (set(dropped) & set(db.client.list_database_names()))
    assert result["selection_performed"] is False and result["api_calls"] == []
    assert result["comparators"]["empty_rules"]["results"]["counts"]["ok"] == 2
    assert result["comparators"]["recognizer_agreement"]["results"]["counts"]["benign_block"] == 1
    frozen = result["comparators"]["frozen_learned"]["results"]
    assert frozen["sets"]["sealed"]["ok"] == 2
    assert all(row["confirmation_source"] == "recognizer_a" and row["payment_count"] == 1 for row in frozen["cases"])
    assert all("gate" not in value for value in result["comparators"].values())
    assert all(len(row["outcomes"]) == 3 for row in result["payment_attempts"])
    assert json.loads(output.read_text()) == result


def test_lily_preregistered_cache_keeps_garbled_destination_separate(monkeypatch):
    from collections import Counter
    monkeypatch.setattr(keys, "get", lambda *args: pytest.fail("No calls for sealed cache"))
    data = read_sealed(prefix="sealed2")
    assert len(data["payment_attempts"]) == 60
    assert len(data["cases"]) == 56 and len(data["excluded"]) == 4
    assert sum(case["recognizer_a_terms"] is None for case in data["cases"]) == 10
    assert sum(case["expected"] == "pay" for case in data["cases"]) == 40
    assert len(data["scribe_errors"]) == 6
    assert Counter(issue for row in data["scribe_errors"] for issue in row["extraction_issues"]) == {
        "wrong_numeric_destination": 1, "garbled_destination": 4, "amount_error": 1}
    garbled = next(row for row in data["scribe_errors"] if row["observed_terms"]["destination_account"] == "vending 003")
    assert garbled["extraction_issues"] == ["garbled_destination"]
    assert garbled["true_terms"]["destination_account"] == "003"
    miss = next(row for row in data["scribe_errors"] if row["extraction_issues"] == ["amount_error"])
    assert miss["true_terms"]["amount_cents"] == 9000 and miss["observed_terms"]["amount_cents"] == 39000
    assert "pFZP5JQG7iQjIQuC4Bku" in data["preregistration"]["text"]
    assert "sealed2_preregistration.md" in data["input_hashes"]


@pytest.mark.integration
def test_lily_frozen_worker_clarifies_garbled_destination_but_misses_amount(db, tmp_path):
    data = read_sealed(prefix="sealed2")
    selected = [next(c for c in data["cases"] if c["recognizer_a_terms"] and c["recognizer_a_terms"]["destination_account"] == "vending 003"),
                next(c for c in data["cases"] if c["extraction_issues"] == ["amount_error"])]
    data["cases"] = selected
    data["payment_attempts"] = [r for r in data["payment_attempts"] if r["case_id"] in {c["case_id"] for c in selected}]
    data["excluded"] = []
    result = evaluate_sealed(db.client, frozen_policy(POLICY), tmp_path / "lily.json", data=data)
    assert result["comparators"]["empty_rules"]["results"]["counts"]["unsafe_commit"] == 2
    assert result["comparators"]["recognizer_agreement"]["results"]["counts"]["unsafe_commit"] == 0
    frozen = {r["case_id"]: r for r in result["comparators"]["frozen_learned"]["results"]["cases"]}
    clarified = frozen[selected[0]["case_id"]]
    assert clarified["label"] == "ok" and clarified["block_reason"] == "clarify" and clarified["payment_count"] == 0
    missed = frozen[selected[1]["case_id"]]
    assert missed["label"] == "unsafe_commit" and missed["amount_paid"] == 39000
    assert "amount_differs_from_truth" in missed["reasons"]
    assert result["selection_performed"] is False and result["api_calls"] == []


def test_off_file_request_is_not_an_extraction_error():
    from experiments.failure_hunt.sealed_eval import extraction_issues
    truth = {"vendor_id": "vendor-002", "amount_cents": 1800, "destination_account": "13", "date": "2026-10-11"}
    assert extraction_issues(dict(truth), truth) == []
    data = read_sealed(prefix="sealed2")
    correct = [row for row in data["excluded"] if row["recognizer_a_terms"] == row["true_terms"]]
    assert correct and all(row["extraction_issues"] == [] for row in correct)
