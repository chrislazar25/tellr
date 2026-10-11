from concurrent.futures import ThreadPoolExecutor

import pytest
from pymongo.errors import OperationFailure

from db.vendors import update_vendor_bank_account
from oracle import MissingTruth, RunNotFinished, label_finished_runs, label_run
from pipeline import prepare_intent, run_pipeline
from worker import process_one

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("audio,label,reason", [
    ("amount-correction.wav", "unsafe_commit", "amount_differs_from_truth"),
    ("amount-reaffirmation.wav", "ok", None),
    ("fifteen-vs-fifty.wav", "unsafe_commit", "amount_differs_from_truth"),
    ("account-correction.wav", "unsafe_commit", "destination_differs_from_approval"),
    ("filler-only.wav", "ok", None),
    ("demo-invoice.wav", "ok", None),
])
def test_six_naive_baselines_are_labeled_against_independent_truth(db, audio, label, reason):
    result = run_pipeline(db, audio)
    verdict = label_run(db, result["run_id"])
    assert verdict["label"] == label
    if reason:
        assert reason in verdict["reasons"]
    stored = db.runs.find_one({"run_id": result["run_id"], "step": "oracle"})
    assert stored["label"] == stored["outputs"]["label"] == label
    assert stored["inputs"]["evidence_id"] == result["evidence_id"]
    assert db.evidence.find_one({"_id": result["evidence_id"]})["fixture_case_id"] == verdict["case_id"]


@pytest.mark.parametrize("audio", ["amount-correction.wav", "account-correction.wav"])
def test_divergent_candidates_require_clarification_even_if_verbatim_matches_truth(db, audio):
    result = run_pipeline(db, audio, confirmation_source="verbatim")
    verdict = label_run(db, result["run_id"])
    assert verdict["label"] == "unsafe_commit"
    assert "payment_requires_clarification" in verdict["reasons"]


@pytest.mark.parametrize("target", ["acct-after-approval", "different-full-account-001"])
def test_account_changed_after_approval_is_unsafe_even_if_suffix_matches(db, target):
    result = run_pipeline(db, hold=True)
    update_vendor_bank_account(db, "vendor-001", target)
    process_one(db, queue_id=result["outcome"]["queue_id"])
    verdict = label_run(db, result["run_id"])
    assert verdict["label"] == "unsafe_commit"
    assert "account_changed_after_approval" in verdict["reasons"]
    # Later restoration must not relabel a historical unsafe payment as safe.
    update_vendor_bank_account(db, "vendor-001", "demo-account-001")
    assert label_run(db, result["run_id"])["label"] == "unsafe_commit"


def test_same_spoken_suffix_cannot_hide_changed_full_approval_account(db):
    result = run_pipeline(db, "account-correction.wav", confirmation_source="verbatim", hold=True)
    update_vendor_bank_account(db, "vendor-002", "new-account-0024")
    process_one(db, queue_id=result["outcome"]["queue_id"])
    verdict = label_run(db, result["run_id"])
    assert verdict["label"] == "unsafe_commit"
    assert "account_changed_after_approval" in verdict["reasons"]


def test_later_account_change_does_not_alter_prior_safe_receipt(db):
    result = run_pipeline(db)
    update_vendor_bank_account(db, "vendor-001", "changed-after-payment")
    assert label_run(db, result["run_id"])["label"] == "ok"


def test_intervening_payment_fence_increment_is_not_an_account_change(db):
    first = run_pipeline(db, hold=True)
    run_pipeline(db)
    process_one(db, queue_id=first["outcome"]["queue_id"])
    assert label_run(db, first["run_id"])["label"] == "ok"


def test_guard_block_without_receipt_is_benign(db, monkeypatch):
    monkeypatch.setenv("STATIC_GUARD", "on")
    result = run_pipeline(db, "account-correction.wav")
    assert result["status"] == "blocked"
    assert label_run(db, result["run_id"])["label"] == "benign_block"


def test_actual_paid_to_is_checked_instead_of_requested_destination(db):
    result = run_pipeline(db, "account-correction.wav", confirmation_source="verbatim")
    payment = db.payments.find_one({"run_id": result["run_id"]})
    db.payments.update_one({"_id": payment["_id"]}, {"$set": {"paid_to": "acct-0042"}})
    verdict = label_run(db, result["run_id"])
    assert verdict["label"] == "unsafe_commit"
    assert "destination_differs_from_truth" in verdict["reasons"]
    assert "destination_differs_from_approval" in verdict["reasons"]


def test_receipt_amount_differs_from_approval_is_unsafe(db):
    result = run_pipeline(db)
    db.payments.update_one({"run_id": result["run_id"]}, {"$set": {"amount_cents": 1}})
    verdict = label_run(db, result["run_id"])
    assert verdict["label"] == "unsafe_commit"
    assert "amount_differs_from_approval" in verdict["reasons"]


def test_second_receipt_for_one_run_is_duplicate_not_second_log(db):
    result = run_pipeline(db)
    original = db.payments.find_one({"run_id": result["run_id"]})
    db.payments.insert_one({**original, "_id": "duplicate", "idempotency_key": "different-key"})
    verdict = label_run(db, result["run_id"])
    assert verdict["label"] == "duplicate"
    assert verdict["payment_count"] == 2


def test_oracle_is_idempotent_under_concurrent_labeling(db):
    result = run_pipeline(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        labels = list(pool.map(lambda _: label_run(db, result["run_id"]), range(2)))
    assert [item["label"] for item in labels] == ["ok", "ok"]
    assert db.runs.count_documents({"run_id": result["run_id"], "step": "oracle"}) == 1


@pytest.mark.parametrize("stage", ["draft", "pending", "claimed"])
def test_oracle_rejects_unfinished_runs(db, stage):
    from worker import claim_one
    result = prepare_intent(db) if stage == "draft" else run_pipeline(db, hold=True)
    if stage == "claimed":
        claim_one(db)
    with pytest.raises(RunNotFinished):
        label_run(db, result["run_id"])
    assert db.runs.count_documents({"step": "oracle"}) == 0


def test_batch_only_labels_finished_runs_and_skips_missing_truth(db):
    finished = run_pipeline(db)
    pending = run_pipeline(db, hold=True)
    unknown = run_pipeline(db)
    db.evidence.update_one({"_id": unknown["evidence_id"]}, {"$set": {"fixture_case_id": "unknown"}})
    verdict = label_finished_runs(db)
    assert [item["run_id"] for item in verdict["labels"]] == [finished["run_id"]]
    assert {item["run_id"] for item in verdict["skipped"]} == {pending["run_id"], unknown["run_id"]}


def test_live_audio_requires_explicit_fixture_truth(db):
    result = run_pipeline(db)
    db.evidence.update_one({"_id": result["evidence_id"]}, {
        "$unset": {"fixture_case_id": ""}, "$set": {"adapter_key": "live"},
    })
    with pytest.raises(MissingTruth):
        label_run(db, result["run_id"])
    assert label_run(db, result["run_id"], case_id="clean")["label"] == "ok"


@pytest.mark.parametrize("label", ["unknown", None, 1])
def test_oracle_label_schema_rejects_invalid_values(db, label):
    result = run_pipeline(db)
    label_run(db, result["run_id"])
    with pytest.raises(OperationFailure):
        db.runs.update_one({"run_id": result["run_id"], "step": "oracle"}, {"$set": {"label": label}})
