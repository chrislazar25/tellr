import os
import uuid
from datetime import datetime, timezone

import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.uri_parser import parse_uri
from pymongo.write_concern import WriteConcern

from db.seed import seed

TEST_URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true"


@pytest.fixture(autouse=True)
def default_static_guard(monkeypatch):
    monkeypatch.setenv("STATIC_GUARD", "off")


@pytest.fixture(scope="session")
def mongo_client():
    # Never consult MONGODB_URI or load .env: tests cannot reach Atlas by accident.
    uri = os.getenv("TEST_MONGODB_URI", "").strip() or TEST_URI
    if not uri.startswith("mongodb://"):
        pytest.fail("TEST_MONGODB_URI must point to a local mongodb:// replica set")
    parsed = parse_uri(uri)
    if any(host not in {"127.0.0.1", "localhost", "::1"} for host, _ in parsed["nodelist"]):
        pytest.fail("Tests permit only loopback MongoDB hosts")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    try:
        hello = client.admin.command("hello")
    except PyMongoError:
        client.close()
        pytest.fail("Local MongoDB unavailable. Run: docker compose up -d --wait", pytrace=False)
    if hello.get("setName") != "rs0" or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail("Tests require a writable rs0 replica set")
    yield client
    client.close()


@pytest.fixture
def db(mongo_client):
    # Each test owns a fresh database; cleanup cannot affect app/Atlas data.
    name = "mongodb_hackathon_test_" + uuid.uuid4().hex
    database = mongo_client[name].with_options(write_concern=WriteConcern("majority"))
    try:
        seed(database)
        yield database
    finally:
        mongo_client.drop_database(name)


@pytest.fixture
def enqueue(db):
    def create(*, vendor_id="vendor-001", destination_account="demo-account-001", amount_cents=12550):
        now = datetime.now(timezone.utc)
        run_id, evidence_id, intent_id, queue_id = (str(uuid.uuid4()) for _ in range(4))
        db.evidence.insert_one({
            "_id": evidence_id, "run_id": run_id, "audio_path": "demo-invoice.wav",
            "verbatim": True, "text": "Fictional invoice", "created_at": now,
        })
        db.intents.insert_one({
            "_id": intent_id, "run_id": run_id, "evidence_id": evidence_id,
            "vendor_id": vendor_id, "amount_cents": amount_cents,
            "destination_account": destination_account, "date": "2026-10-11", "created_at": now,
        })
        item = {
            "_id": queue_id, "run_id": run_id, "intent_id": intent_id,
            "vendor_id": vendor_id, "status": "pending", "created_at": now,
        }
        db.queue.insert_one(item)
        return item
    return create
