import json
from copy import deepcopy

import pytest

from interfaces import _recordings
from proposer import FixtureProposer
from scripts.portfolio_experiment import experiment, measured_cases, truth_mismatches


def measurement():
    return {"reference_date": "2026-10-11", "clips": [
        {"case_id": case["case_id"], "audio_file": case["audio_file"],
         "verbatim": case["verbatim_text"], "cleaned": case["verbatim_text"],
         "verbatim_terms": deepcopy(case["verbatim_terms"]), "cleaned_terms": deepcopy(case["verbatim_terms"]),
         "true_terms": deepcopy(case["true_terms"])} for case in _recordings()]}


def test_observed_agreement_moves_recordings_to_must_pass_without_altering_truth():
    data = measurement()
    cases = measured_cases(data)
    assert [case["case_id"] for case in cases if case["eval_set"] == "must_catch"] == ["queued_destination_change"]
    assert sum(case["eval_set"] == "must_pass" for case in cases) == 8
    assert sum(case["eval_set"] == "holdout" for case in cases) == 5
    assert cases[0]["true_terms"] == data["clips"][0]["true_terms"]
    assert all(case["source"] == "synthetic_fixture" for case in cases if case["eval_set"] == "holdout")


def test_agreed_wrong_terms_are_flagged_without_silently_relabeling():
    data = measurement()
    data["clips"][0]["cleaned_terms"]["amount_cents"] = 50000
    data["clips"][0]["verbatim_terms"]["amount_cents"] = 50000
    case = measured_cases(data)[0]
    assert case["true_terms"]["amount_cents"] == 5000
    assert case["eval_set"] == "must_catch" and case["expected"] == "pay"
    assert truth_mismatches(case["cleaned_terms"], case["true_terms"]) == ["amount_cents"]


@pytest.mark.integration
def test_export_contains_real_receipts_candidate_audits_and_final_holdout_scores(db, tmp_path):
    output = tmp_path / "experiment.json"
    result = experiment(db, measurement(), output, proposer=FixtureProposer())
    assert result["status"] == "complete"
    assert result["baseline_replay"]["sets"]["must_catch"]["unsafe_commit"] == 1
    assert result["final_replay"]["sets"]["must_pass"]["ok"] == 8
    assert result["final_replay"]["sets"]["holdout"]["unsafe_commit"] == 2
    assert result["heal"]["status"] == "healed"  # Training completion is not a zero-error holdout claim.
    candidates = result["heal_iterations"][1]["candidates"]
    assert len(candidates) == 3 and sum(c["active"] for c in candidates) == 1
    assert any(c["decision"] == "rejected" and c["rejected_reason"] for c in candidates)
    assert set(candidates[0]["gate"]["after"]) == {"must_catch", "must_pass", "holdout"}
    changed = next(row for row in result["baseline_runs"] if row["case_id"] == "queued_destination_change")
    assert changed["amount_paid"] == 12550
    assert changed["paid_to"] != changed["approval_snapshot"]["approved_bank_account"]
    assert changed["label"] == "unsafe_commit"
    assert json.loads(output.read_text()) == result
    assert result["cost"]["billed_usd"] is None
