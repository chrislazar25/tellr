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
    databases = set(db.client.list_database_names())
    output = tmp_path / "sealed.json"
    result = evaluate_sealed(db.client, policy, output, data=data)
    assert policy == original
    assert db.rule_versions.find_one({"_id": sentinel["_id"]}) == sentinel
    assert set(db.client.list_database_names()) == databases
    assert result["selection_performed"] is False and result["api_calls"] == []
    assert result["comparators"]["empty_rules"]["results"]["counts"]["ok"] == 2
    assert result["comparators"]["recognizer_agreement"]["results"]["counts"]["benign_block"] == 1
    frozen = result["comparators"]["frozen_learned"]["results"]
    assert frozen["sets"]["sealed"]["ok"] == 2
    assert all(row["confirmation_source"] == "recognizer_a" and row["payment_count"] == 1 for row in frozen["cases"])
    assert all("gate" not in value for value in result["comparators"].values())
    assert all(len(row["outcomes"]) == 3 for row in result["payment_attempts"])
    assert json.loads(output.read_text()) == result
