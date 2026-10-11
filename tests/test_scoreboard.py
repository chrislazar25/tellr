import json

import pytest

from heal import run_heal
from proposer import FixtureProposer
from scoreboard import render, report

pytestmark = pytest.mark.integration


def test_scoreboard_exposes_each_set_per_iteration_as_json_and_plain_text(db):
    result = run_heal(db, lambda *args: [])
    data = report(db)
    assert data["heal_id"] == result["heal_id"]
    assert data["status"] == "no_candidates"
    assert [row["iteration"] for row in data["iterations"]] == [0, 1]
    for row in data["iterations"]:
        assert row["sets"]["must_catch"]["N"] == 4
        assert row["sets"]["must_pass"]["N"] == 5
        assert row["sets"]["holdout"]["N"] == 5
        assert row["sets"]["must_pass"]["benign_block"] == 0
    assert json.loads(json.dumps(data)) == data
    text = render(data)
    assert "unsafe_commit" in text and "benign_block" in text and "must_catch" in text
    assert "holdout" in text and "None; payments are allowed" in text


def test_plain_active_rules_and_historical_snapshots_are_distinct(db):
    result = run_heal(db, lambda *args: [])
    rule = FixtureProposer()([], [])[1]
    db.rule_versions.insert_one({"_id": "bank-rule", "version": 1, "active": True, "rule": rule})
    data = report(db, heal_id=result["heal_id"])
    assert data["active_rules"] == [{"rule_id": "bank-rule", "rule": rule}]
    assert data["iterations"][0]["active_rules"] == []
    text = render(data)
    assert "bank account at approval equals current vendor bank account" in text
    assert "request reapproval" in text


def test_no_history_and_unknown_session_are_handled_explicitly(db):
    assert report(db)["status"] == "no_history"
    with pytest.raises(ValueError, match="No scoreboard history"):
        report(db, heal_id="not-a-session")
