import json
from importlib.resources import files

import pytest

from interfaces import extract_terms, transcribe
from pipeline import run_pipeline

CASES = json.loads(files("fixtures").joinpath("pipeline.json").read_text())["recordings"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_independent_extraction_against_hand_labeled_truth(case):
    actual = extract_terms(transcribe(case["audio_file"], True))
    assert actual == case["true_terms"]
    cleaned = extract_terms(transcribe(case["audio_file"], False))
    lossy_cases = {"500_no_50", "fifteen_vs_fifty", "account_42_sorry_24"}
    assert (cleaned != case["true_terms"]) == (case["case_id"] in lossy_cases)


@pytest.mark.integration
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_all_six_cases_through_confirmed_pipeline(db, case):
    result = run_pipeline(db, case["audio_file"], confirmation_source="verbatim")
    evidence = db.evidence.find_one({"_id": result["evidence_id"]})
    assert evidence["verbatim_terms"] == case["verbatim_terms"]
    assert evidence["cleaned_terms"] == case["cleaned_terms"]
    if case["true_terms"] is None:
        assert result["status"] == "no_terms"
        assert db.intents.count_documents({}) == db.queue.count_documents({}) == db.payments.count_documents({}) == 0
    else:
        assert result["status"] == "done"
        intent = db.intents.find_one({"_id": result["intent_id"]})
        assert intent["confirmed_terms"] == case["true_terms"]
        receipt = db.payments.find_one({"run_id": result["run_id"]})
        assert receipt["amount_cents"] == case["true_terms"]["amount_cents"]
        assert receipt["destination_account"] == case["true_terms"]["destination_account"]
        assert receipt["paid_to"] == receipt["vendor_account"]


def test_truth_labels_are_never_used_for_extraction(monkeypatch):
    import interfaces
    case = {**CASES[0], "true_terms": None}
    monkeypatch.setattr(interfaces, "_recordings", lambda: [case])
    assert extract_terms(case["verbatim_text"]) == case["verbatim_terms"]
