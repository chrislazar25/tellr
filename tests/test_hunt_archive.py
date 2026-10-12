import importlib
import json
from pathlib import Path

import pytest

from experiments.failure_hunt import keys

ROOT = Path(__file__).resolve().parents[1] / "experiments/failure_hunt"


def test_hunt_keys_are_loaded_only_from_ignored_files(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "process-value-must-not-be-used")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=fixture-value\n")
    (tmp_path / ".env.secrets").write_text("ANTHROPIC_API_KEY=${literal}\n")
    assert keys.get("ANTHROPIC_API_KEY") == "${literal}"
    with pytest.raises(ValueError, match="Missing ELEVENLABS_API_KEY"):
        keys.get("ELEVENLABS_API_KEY")


def test_importing_hunt_rounds_never_generates_audio_or_calls_apis(monkeypatch):
    monkeypatch.setattr(keys, "get", lambda *args: pytest.fail("No key access during import"))
    for name in ["hunt", "round1", "round2"]:
        importlib.import_module("experiments.failure_hunt." + name)


def test_archive_preserves_every_logged_attempt_and_recognizer_output():
    attempts = [json.loads(line) for line in (ROOT / "attempts.jsonl").read_text().splitlines()]
    confirmations = [json.loads(line) for line in (ROOT / "confirm_attempts.jsonl").read_text().splitlines()]
    scored = json.loads((ROOT / "scored.json").read_text())
    assert len(attempts) == 180 and len(confirmations) == 33 and len(scored) == 360
    for attempt, pair in zip(attempts, [scored[i:i + 2] for i in range(0, len(scored), 2)]):
        assert {row["recognizer"] for row in pair} == {"scribe", "whisper_small"}
        for row in pair:
            assert row["text"] == attempt["results"][row["recognizer"]]["text"]
            assert all(row[key] == attempt[key] for key in ["case", "voice", "speed", "channel"])
    assert len(list((ROOT / "audio").iterdir())) == 705


def test_rescoring_is_cache_only_without_key_access(monkeypatch):
    from experiments.failure_hunt.rescore import score
    from experiments.failure_hunt.confirm_score import score as confirm_score
    monkeypatch.setattr(keys, "get", lambda *args: pytest.fail("Cached replay needs no keys"))
    assert score() == json.loads((ROOT / "scored.json").read_text())
    assert confirm_score() == json.loads((ROOT / "confirm_scored.json").read_text())
