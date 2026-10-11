"""All bank-account changes must use this shared fence protocol."""

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.database import Database

from db.transactions import RetryObserver, run_transaction
from observability import log_step


def update_vendor_bank_account(
    db: Database, vendor_id: str, bank_account: str, *,
    run_id: str | None = None, on_retry: RetryObserver | None = None,
) -> dict:
    if not isinstance(bank_account, str) or not bank_account.strip():
        raise ValueError("bank_account must be a non-empty string")
    run_id = run_id or str(uuid.uuid4())

    def update(session):
        fence = db.fences.find_one_and_update(
            {"_id": f"fence:{vendor_id}"}, {"$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER, session=session,
        )
        if fence is None:
            raise ValueError("Vendor fence is missing")
        vendor = db.vendors.find_one_and_update(
            {"_id": vendor_id},
            {"$set": {"bank_account": bank_account, "updated_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER, session=session,
        )
        if vendor is None:
            raise ValueError("Vendor is missing")
        log_step(db, run_id, "vendor_account_update", {"vendor_id": vendor_id},
                 {"fence_version": fence["version"]}, session=session)
        return vendor

    return run_transaction(db, update, run_id=run_id, on_retry=on_retry)
