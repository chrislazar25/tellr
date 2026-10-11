from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread

import pytest
from pymongo.collection import Collection

from db.vendors import update_vendor_bank_account
from worker import claim_one, process_claimed, process_one

pytestmark = pytest.mark.integration


def test_two_workers_race_on_one_item_exactly_one_payment(db, enqueue):
    item = enqueue()
    start = Barrier(2)

    def work():
        start.wait(timeout=10)
        return process_one(db)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(work) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]

    assert sum(result is None for result in results) == 1
    assert next(result for result in results if result is not None)["status"] == "done"
    assert db.payments.count_documents({}) == 1
    assert db.payments.find_one()["idempotency_key"] == item["_id"]
    assert db.queue.find_one({"_id": item["_id"]})["status"] == "done"
    assert db.fences.find_one({"_id": "fence:vendor-001"})["version"] == 1


def test_empty_rules_is_allow_all_baseline(db, enqueue):
    item = enqueue()
    assert db.rule_versions.count_documents({}) == 0
    assert process_one(db)["status"] == "done"
    receipt = db.payments.find_one({"idempotency_key": item["_id"]})
    assert receipt["amount_cents"] == 12550
    assert receipt["rule_version_ids"] == []
    assert receipt["destination_account"] == receipt["vendor_account"] == "demo-account-001"


def test_vendor_account_change_racing_commit_conflicts_on_fence(db, enqueue):
    """Payment holds the fence; account updater must abort/retry behind it."""
    item = enqueue()
    db.rule_versions.insert_one({"_id": "pause-rule", "version": 1, "active": True, "rule": {}})
    vendor_read, release_payment, updater_conflicted = Event(), Event(), Event()
    observed_accounts, conflicts = [], []

    def evaluate(rule, context):
        observed_accounts.append(context["vendor"]["bank_account"])
        vendor_read.set()
        assert release_payment.wait(timeout=15)
        return None

    def on_retry(error):
        conflicts.append(error)
        updater_conflicted.set()
        assert release_payment.wait(timeout=15)

    with ThreadPoolExecutor(max_workers=2) as executor:
        payment_future = executor.submit(process_one, db, evaluator=evaluate)
        try:
            assert vendor_read.wait(timeout=10)
            update_future = executor.submit(
                update_vendor_bank_account, db, "vendor-001", "demo-account-new", on_retry=on_retry,
            )
            assert updater_conflicted.wait(timeout=10), "Expected a real fence write conflict"
        finally:
            release_payment.set()
        assert payment_future.result(timeout=20)["status"] == "done"
        assert update_future.result(timeout=20)["bank_account"] == "demo-account-new"

    assert any(error.code == 112 and error.has_error_label("TransientTransactionError") for error in conflicts)
    receipt = db.payments.find_one({"idempotency_key": item["_id"]})
    assert observed_accounts == ["demo-account-001"]
    assert receipt["destination_account"] == receipt["vendor_account"] == observed_accounts[0]
    assert receipt["fence_version"] == 1
    assert db.fences.find_one({"_id": "fence:vendor-001"})["version"] == 2


def test_account_update_wins_stale_worker_snapshot_retries_and_blocks(db, enqueue, monkeypatch):
    """Pause after the worker's snapshot starts, immediately before its fence write."""
    item = enqueue()
    snapshot_started, update_committed = Event(), Event()
    conflicts = []
    original = Collection.find_one_and_update

    def pause_before_fence(collection, query, *args, **kwargs):
        if (collection.name == "fences" and current_thread().name.startswith("stale-worker")
                and not snapshot_started.is_set()):
            assert kwargs["session"].in_transaction
            snapshot_started.set()
            assert update_committed.wait(timeout=15)
        return original(collection, query, *args, **kwargs)

    monkeypatch.setattr(Collection, "find_one_and_update", pause_before_fence)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-worker") as executor:
        future = executor.submit(process_one, db, on_retry=conflicts.append)
        try:
            assert snapshot_started.wait(timeout=10)
            update_vendor_bank_account(db, "vendor-001", "demo-account-new")
        finally:
            update_committed.set()
        outcome = future.result(timeout=20)

    assert any(error.code == 112 and error.has_error_label("TransientTransactionError") for error in conflicts)
    assert outcome["status"] == "blocked"
    assert "Destination account" in outcome["reason"]
    assert db.payments.count_documents({}) == 0
    assert db.queue.find_one({"_id": item["_id"]})["status"] == "blocked"
    assert db.fences.find_one({"_id": "fence:vendor-001"})["version"] == 2
    assert db.runs.count_documents({"run_id": item["run_id"], "step": "transaction_retry"}) >= 1
    # Only the successfully retried attempt's transactional logs survive.
    assert db.runs.count_documents({"run_id": item["run_id"], "step": "fence"}) == 1


def test_blocking_hook_produces_reason_and_no_receipt(db, enqueue):
    item = enqueue()
    db.rule_versions.insert_one({"_id": "deny-rule", "version": 1, "active": True, "rule": {}})
    result = process_one(db, evaluator=lambda rule, context: "Approval required")
    assert result["status"] == "blocked"
    assert result["reason"] == "Approval required"
    assert db.payments.count_documents({}) == 0
    assert db.queue.find_one({"_id": item["_id"]})["reason"] == "Approval required"


def test_inactive_rules_are_not_evaluated(db, enqueue):
    enqueue()
    db.rule_versions.insert_one({"_id": "inactive", "version": 1, "active": False, "rule": {}})

    def unexpected(rule, context):
        raise AssertionError("Inactive rules must not run")

    assert process_one(db, evaluator=unexpected)["status"] == "done"


def test_processing_same_claim_again_does_not_duplicate_receipt(db, enqueue):
    enqueue()
    item = claim_one(db)
    first = process_claimed(db, item)
    assert process_claimed(db, item) == first
    assert db.payments.count_documents({}) == 1
    assert db.fences.find_one({"_id": "fence:vendor-001"})["version"] == 1


def test_rule_error_rolls_back_fence_receipt_and_transactional_logs(db, enqueue):
    item = enqueue()
    db.rule_versions.insert_one({"_id": "error-rule", "version": 1, "active": True, "rule": {}})

    def fail(rule, context):
        raise RuntimeError("Evaluation unavailable")

    with pytest.raises(RuntimeError, match="Evaluation unavailable"):
        process_one(db, evaluator=fail)
    assert db.queue.find_one({"_id": item["_id"]})["status"] == "claimed"
    assert db.payments.count_documents({}) == 0
    assert db.fences.find_one({"_id": "fence:vendor-001"})["version"] == 0
    assert db.runs.count_documents({"run_id": item["run_id"], "step": "fence"}) == 0
    assert db.runs.count_documents({"run_id": item["run_id"], "step": "worker_error"}) == 1


def test_account_update_rolls_back_fence_when_vendor_missing(db):
    db.vendors.delete_one({"_id": "vendor-001"})
    with pytest.raises(ValueError, match="Vendor is missing"):
        update_vendor_bank_account(db, "vendor-001", "demo-account-new")
    assert db.fences.find_one({"_id": "fence:vendor-001"})["version"] == 0
