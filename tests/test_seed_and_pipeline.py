from datetime import datetime, timezone

import pytest
from bson.decimal128 import Decimal128
from pymongo.errors import DuplicateKeyError, OperationFailure

from db.schemas import VALIDATORS
from db.seed import seed
from db.vendors import update_vendor_bank_account
from pipeline import confirm_intent, enqueue_intent, prepare_intent, run_pipeline
from worker import process_one

pytestmark = pytest.mark.integration


def test_seed_is_idempotent_and_preserves_vendor_changes(db):
    update_vendor_bank_account(db, "vendor-001", "demo-account-changed")
    seed(db)
    seed(db)
    assert db.vendors.count_documents({}) == 3
    assert db.fences.count_documents({}) == 3
    assert db.vendors.find_one({"_id": "vendor-001"})["bank_account"] == "demo-account-changed"
    assert db.fences.find_one({"_id": "fence:vendor-001"})["version"] == 1
    assert db.fences.find_one({"_id": "fence:vendor-002"}) == {"_id": "fence:vendor-002", "version": 0}
    collections = {item["name"]: item for item in db.list_collections()}
    for name in VALIDATORS:
        assert "$jsonSchema" in collections[name]["options"]["validator"]
        assert collections[name]["options"]["validationAction"] == "error"
    index = db.payments.index_information()["idempotency_key_1"]
    assert index["unique"] is True


@pytest.mark.parametrize("verbatim", [True, False])
def test_offline_pipeline_end_to_end_logs_every_step(db, monkeypatch, verbatim):
    for key in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "RAINDROP_WRITE_KEY"):
        monkeypatch.delenv(key, raising=False)
    result = run_pipeline(db, confirmation_source="verbatim" if verbatim else "cleaned")
    assert result["outcome"]["status"] == "done"
    evidence = db.evidence.find_one({"run_id": result["run_id"]})
    assert evidence["verbatim_text"].startswith("Um,")
    assert evidence["cleaned_text"] != evidence["verbatim_text"]
    assert evidence["verbatim_terms"] == evidence["cleaned_terms"]
    intent = db.intents.find_one({"run_id": result["run_id"]})
    assert intent["confirmed_terms"] == evidence["verbatim_terms"]
    assert intent["confirmed_at"] is not None
    assert intent["approved_bank_account"] == "demo-account-001"
    assert intent["approved_fence_version"] == 0
    assert db.intents.count_documents({"run_id": result["run_id"]}) == 1
    assert db.payments.count_documents({"run_id": result["run_id"]}) == 1
    records = list(db.runs.find({"run_id": result["run_id"]}))
    assert {record["step"] for record in records} == {
        "transcribe", "extract_terms", "readback", "store_evidence", "store_intent", "confirm", "enqueue",
        "claim", "fence", "load_vendor", "load_intent", "load_rules", "payment_receipt", "done", "transaction_complete",
    }
    for record in records:
        assert isinstance(record["inputs"], dict)
        assert isinstance(record["outputs"], dict)
        assert isinstance(record["ts"], datetime)
    assert sum(record["step"] == "transcribe" for record in records) == 2
    assert sum(record["step"] == "extract_terms" for record in records) == 2


@pytest.mark.parametrize("collection", ["intents", "payments"])
@pytest.mark.parametrize("amount", [1.5, True, "150", 0, -1, Decimal128("1.50")])
def test_database_rejects_non_integer_cents(db, enqueue, collection, amount):
    item = enqueue()
    process_one(db)
    original = db[collection].find_one()
    original["_id"] = "invalid-amount"
    original["amount_cents"] = amount
    if collection == "payments":
        original["idempotency_key"] = "invalid-key"
    with pytest.raises(OperationFailure) as error:
        db[collection].insert_one(original)
    assert error.value.code == 121 # DocumentValidationFailure
    assert db.payments.count_documents({"idempotency_key": item["_id"]}) == 1


def test_payment_idempotency_key_is_unique(db, enqueue):
    enqueue()
    process_one(db)
    duplicate = db.payments.find_one()
    duplicate["_id"] = "different-receipt-id"
    duplicate["created_at"] = datetime.now(timezone.utc)
    with pytest.raises(DuplicateKeyError):
        db.payments.insert_one(duplicate)


def test_hold_allows_account_change_between_approval_and_payment(db):
    result = run_pipeline(db, confirmation_source="verbatim", hold=True)
    assert result["outcome"]["status"] == "pending"
    assert db.payments.count_documents({}) == 0
    intent = db.intents.find_one({"_id": result["intent_id"]})
    assert intent["approved_bank_account"] == "demo-account-001"
    assert intent["approved_fence_version"] == 0
    update_vendor_bank_account(db, "vendor-001", "demo-account-after-approval")
    outcome = process_one(db, queue_id=result["outcome"]["queue_id"])
    assert outcome["paid_to"] == "demo-account-after-approval"
    assert db.intents.find_one({"_id": result["intent_id"]})["confirmed_terms"] == intent["confirmed_terms"]


def test_only_confirmed_intents_can_be_enqueued(db):
    prepared = prepare_intent(db)
    assert prepared["status"] == "awaiting_confirmation"
    with pytest.raises(ValueError, match="Only confirmed"):
        enqueue_intent(db, prepared["intent_id"])
    assert db.queue.count_documents({}) == 0


def test_confirmation_records_current_account_and_version_and_is_immutable(db):
    prepared = prepare_intent(db)
    update_vendor_bank_account(db, "vendor-001", "demo-account-at-approval")
    terms = prepared["candidates"]["verbatim"]
    intent = confirm_intent(db, prepared["intent_id"], terms, source="verbatim")
    assert intent["approved_bank_account"] == "demo-account-at-approval"
    assert intent["approved_fence_version"] == 1
    update_vendor_bank_account(db, "vendor-001", "demo-account-later")
    assert confirm_intent(db, prepared["intent_id"], terms) == intent
    with pytest.raises(ValueError, match="immutable"):
        confirm_intent(db, prepared["intent_id"], {**terms, "amount_cents": 50})


def test_enqueue_is_idempotent_even_after_payment(db):
    result = run_pipeline(db, confirmation_source="verbatim", hold=True)
    item = enqueue_intent(db, result["intent_id"])
    assert item["_id"] == result["outcome"]["queue_id"]
    assert db.queue.count_documents({}) == 1
    process_one(db, queue_id=item["_id"])
    assert enqueue_intent(db, result["intent_id"])["status"] == "done"
    assert process_one(db) is None
    assert db.payments.count_documents({}) == 1


@pytest.mark.parametrize("field,value", [
    ("confirmed_terms", {"vendor_id": "vendor-001"}),
    ("confirmed_at", "2026-10-11"),
    ("approved_bank_account", ""),
    ("approved_fence_version", 1.5),
    ("confirmation_source", "automatic-guess"),
])
def test_confirmation_metadata_is_validated(db, enqueue, field, value):
    item = enqueue()
    with pytest.raises(OperationFailure) as error:
        db.intents.update_one({"_id": item["intent_id"]}, {"$set": {field: value}})
    assert error.value.code == 121


def test_nested_confirmed_amounts_reject_floats(db, enqueue):
    item = enqueue()
    with pytest.raises(OperationFailure) as error:
        db.intents.update_one({"_id": item["intent_id"]}, {"$set": {"confirmed_terms.amount_cents": 1.5}})
    assert error.value.code == 121


def test_preparation_without_approval_cannot_pay(db):
    result = prepare_intent(db)
    assert result["status"] == "awaiting_confirmation"
    assert db.queue.count_documents({}) == db.payments.count_documents({}) == 0


def test_baseline_defaults_to_cleaned_terms_and_records_approval(db):
    result = run_pipeline(db, "amount-correction.wav")
    assert result["status"] == "done"
    intent = db.intents.find_one({"run_id": result["run_id"]})
    assert intent["confirmation_source"] == "cleaned"
    assert intent["confirmed_terms"]["amount_cents"] == 50000
    assert intent["approved_bank_account"] == "demo-account-001"
    assert db.payments.find_one({"run_id": result["run_id"]})["amount_cents"] == 50000


def test_confirmation_source_must_match_evidence_candidate(db):
    result = prepare_intent(db)
    wrong = {**result["candidates"]["verbatim"], "amount_cents": 1}
    with pytest.raises(ValueError, match="chosen evidence"):
        confirm_intent(db, result["intent_id"], wrong, source="verbatim")
    assert db.intents.find_one({"_id": result["intent_id"]})["status"] == "draft"
