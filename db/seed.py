"""Idempotently install validators/indexes and three fictional vendors."""

import uuid
import sys

from pymongo.database import Database
from pymongo.errors import CollectionInvalid

from db.connection import connect
from db.schemas import VALIDATORS
from db.transactions import run_transaction
from observability import log_step

SAMPLE_VENDORS = (
    {"_id": "vendor-001", "name": "Demo Paper Supply", "bank_account": "demo-account-001"},
    {"_id": "vendor-002", "name": "Demo Catering", "bank_account": "demo-account-002"},
    {"_id": "vendor-003", "name": "Demo Equipment", "bank_account": "demo-account-003"},
)


def seed(db: Database) -> None:
    for name, validator in VALIDATORS.items():
        try:
            db.create_collection(name, validator=validator, validationLevel="strict", validationAction="error")
        except CollectionInvalid:
            db.command("collMod", name, validator=validator, validationLevel="strict", validationAction="error")

    db.payments.create_index("idempotency_key", unique=True)
    db.queue.create_index([("status", 1), ("created_at", 1), ("_id", 1)])
    db.rule_versions.create_index("active")
    db.runs.create_index([("run_id", 1), ("ts", 1)])

    def insert_defaults(session):
        for vendor in SAMPLE_VENDORS:
            db.vendors.update_one({"_id": vendor["_id"]}, {"$setOnInsert": vendor}, upsert=True, session=session)
            fence_id = f"fence:{vendor['_id']}"
            db.fences.update_one(
                {"_id": fence_id}, {"$setOnInsert": {"_id": fence_id, "version": 0}},
                upsert=True, session=session,
            )

    run_id = str(uuid.uuid4())
    run_transaction(db, insert_defaults, run_id=run_id)
    log_step(db, run_id, "seed", {}, {"collections": list(VALIDATORS), "sample_vendors": 3})


def main() -> int:
    db = None
    try:
        db = connect()
        seed(db)
        print("Seeded eight validated collections, indexes, and three demo vendors.")
        return 0
    except Exception:
        print("fail", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
