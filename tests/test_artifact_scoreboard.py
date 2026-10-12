import json

import pytest

import scoreboard


def test_saved_scoreboard_reads_measured_sets_without_database_or_api(monkeypatch, capsys):
    monkeypatch.setattr(scoreboard, "connect", lambda: pytest.fail("Artifact reads must stay offline"))
    assert scoreboard.main(["--artifact", "artifacts/experiment.json", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["iterations"][0]["sets"]["must_catch"]["unsafe_commit"] == 1
    assert data["iterations"][-1]["sets"]["holdout"]["unsafe_commit"] == 2
    assert len(data["active_rules"]) == 1
    assert scoreboard.main(["--artifact", "artifacts/experiment.json"]) == 0
    text = capsys.readouterr().out
    assert "bank account at approval equals current vendor bank account" in text


def test_incomplete_artifact_is_rejected_without_connecting(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scoreboard, "connect", lambda: pytest.fail("Must stay offline"))
    path = tmp_path / "partial.json"
    path.write_text('{"schema_version":1,"status":"running"}')
    assert scoreboard.main(["--artifact", str(path)]) == 1
    assert "ValueError" in capsys.readouterr().err


def test_artifact_and_database_session_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        scoreboard.main(["--artifact", "artifacts/experiment.json", "--heal-id", "session"])
