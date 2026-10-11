from datetime import datetime, timezone

import pytest
from bson.decimal128 import Decimal128
from pymongo.errors import DuplicateKeyError, OperationFailure

from db.schemas import VALIDATORS
from db.seed import seed
from db.vendors import update_vendor_bank_account
from pipeline import run_pipeline
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
    result = run_pipeline(db, verbatim=verbatim)
    assert result["outcome"]["status"] == "done"
    assert db.evidence.count_documents({"run_id": result["run_id"], "verbatim": verbatim}) == 1
    assert db.intents.count_documents({"run_id": result["run_id"]}) == 1
    assert db.payments.count_documents({"run_id": result["run_id"]}) == 1
    records = list(db.runs.find({"run_id": result["run_id"]}))
    assert {record["step"] for record in records} == {
        "transcribe", "extract_terms", "readback", "store_evidence", "store_intent", "enqueue",
        "claim", "fence", "load_vendor", "load_intent", "load_rules", "payment_receipt", "done", "transaction_complete",
    }
    for record in records:
        assert isinstance(record["inputs"], dict)
        assert isinstance(record["outputs"], dict)
        assert isinstance(record["ts"], datetime)


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
