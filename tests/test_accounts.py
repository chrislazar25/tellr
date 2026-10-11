import pytest

from db.accounts import destination_matches
from pipeline import run_pipeline


@pytest.mark.parametrize("destination,expected", [
    ("24", True), ("0024", True), ("acct-0024", True),
    ("42", False), ("acct-0042", False), ("", False), ("２４", False),
])
def test_spoken_suffixes_compare_to_full_accounts(destination, expected):
    assert destination_matches(destination, "acct-0024") is expected


@pytest.mark.integration
@pytest.mark.parametrize("source,status", [("verbatim", "done"), ("cleaned", "blocked")])
def test_guard_distinguishes_corrected_account_suffix(db, monkeypatch, source, status):
    monkeypatch.setenv("STATIC_GUARD", "on")
    result = run_pipeline(db, "account-correction.wav", confirmation_source=source)
    assert result["status"] == status
    assert db.vendors.find_one({"_id": "vendor-002"})["bank_account"] == "acct-0024"
    if status == "done":
        payment = db.payments.find_one({"run_id": result["run_id"]})
        assert payment["paid_to"] == "acct-0024"
        assert payment["destination_account"] == "24"
    else:
        assert db.payments.count_documents({}) == 0
