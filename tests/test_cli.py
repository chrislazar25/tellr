import json
import os
import subprocess
import sys

import pytest

from config import ROOT


def command(db, *args):
    host, port = db.client.address
    uri = f"mongodb://{host}:{port}/{db.name}?replicaSet=rs0&directConnection=true"
    env = {**os.environ, "MONGODB_URI": uri, "STATIC_GUARD": "off"}
    result = subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "mongodb://" not in result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.integration
def test_split_cli_enqueues_changes_account_and_processes(db):
    result = command(db, "pipeline.py", "enqueue", "--confirm-from", "verbatim")
    assert result["status"] == "pending"
    assert db.payments.count_documents({}) == 0
    changed = command(db, "scripts/change_vendor_account.py", "vendor-001", "demo-cli-updated")
    assert changed["status"] == "ok"
    paid = command(db, "pipeline.py", "process", "--queue-id", result["outcome"]["queue_id"])
    assert paid["paid_to"] == "demo-cli-updated"
    intent = db.intents.find_one({"_id": result["intent_id"]})
    assert intent["approved_bank_account"] == "demo-account-001"


@pytest.mark.integration
def test_demo_hold_stops_before_payment(db):
    result = command(db, "pipeline.py", "demo", "--hold", "--confirm-from", "verbatim")
    assert result["status"] == "pending"
    assert db.payments.count_documents({}) == 0


def test_noninteractive_cli_requires_confirmation_choice():
    result = subprocess.run([sys.executable, "pipeline.py", "enqueue"], cwd=ROOT,
                            input="", capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "requires --confirm-from" in result.stderr
    assert "mongodb" not in result.stderr.lower()
