import hashlib
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
    for name in ["hunt", "round1", "round2", "sealed_voice", "sealed_score", "sealed2_voice", "sealed2_score"]:
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
    manifest = json.loads((ROOT / "audio_manifest.json").read_text())
    assert manifest["file_count"] == len(manifest["files"]) == 705
    for entry in manifest["files"]:
        assert hashlib.sha256((ROOT / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]


def test_rescoring_is_cache_only_without_key_access(monkeypatch):
    from experiments.failure_hunt.rescore import score
    from experiments.failure_hunt.confirm_score import score as confirm_score
    monkeypatch.setattr(keys, "get", lambda *args: pytest.fail("Cached replay needs no keys"))
    assert score() == json.loads((ROOT / "scored.json").read_text())
    assert confirm_score() == json.loads((ROOT / "confirm_scored.json").read_text())


def test_future_channel_identity_is_portable_between_checkouts(monkeypatch, tmp_path):
    from experiments.failure_hunt import hunt
    identities = []
    for name in ["checkout-a", "checkout-b"]:
        root = tmp_path / name
        monkeypatch.setattr(hunt, "ROOT", root)
        identities.append(hunt.channel_identity(root / "experiments/failure_hunt/audio/source.mp3"))
    assert identities == ["experiments/failure_hunt/audio/source.mp3"] * 2
    assert hunt.h(identities[0], "phone_snr5") == hunt.h(identities[1], "phone_snr5")


def test_supplied_manifest_links_every_cache_without_path_reconstruction():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert len(manifest["attempts"]) == 213
    for entry in manifest["attempts"]:
        for key in ["tts_file", "channel_file", "scribe_cache", "whisper_cache"]:
            assert (ROOT / entry[key]).is_file()


def test_sealed_bill_archive_is_complete_without_key_access(monkeypatch):
    from experiments.failure_hunt.sealed_score import score
    monkeypatch.setattr(keys, "get", lambda *args: pytest.fail("Sealed cache needs no keys"))
    rows = score()
    assert len(rows) == 60
    assert rows == json.loads((ROOT / "sealed_scored.json").read_text())
    manifest = json.loads((ROOT / "sealed_manifest.json").read_text())
    assert len(manifest["attempts"]) == 60
    assert len(manifest["files"]) == manifest["file_count"] == 198
    for entry in manifest["files"]:
        path = ROOT.parents[1] / entry["file"]
        assert path.stat().st_size == entry["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def test_lily_archive_preserves_every_recorded_input_without_calls(monkeypatch):
    from experiments.failure_hunt.sealed_score import score
    from experiments.failure_hunt.sealed_voice import R1, R2
    from experiments.failure_hunt import sealed2_voice
    monkeypatch.setattr(keys, "get", lambda *args: pytest.fail("No calls for Lily archive"))
    assert (sealed2_voice.R1, sealed2_voice.R2) == (R1, R2)
    assert sealed2_voice.VOICE[1] == "pFZP5JQG7iQjIQuC4Bku"
    rows = score(prefix="sealed2")
    assert rows == json.loads((ROOT / "sealed2_scored.json").read_text())
    assert len(rows) == 60
    pay = [row for row in rows if row["terms_a"] is not None and not row["off_file"]]
    assert len(pay) == 46 and sum(row["a_wrong"] for row in pay) == 6
    manifest = json.loads((ROOT / "sealed2_manifest.json").read_text())
    assert manifest["file_count"] == len(manifest["files"]) == 198
    assert len(manifest["attempts"]) == 60
    for entry in manifest["files"]:
        path = ROOT.parents[1] / entry["file"]
        assert path.stat().st_size == entry["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    preregistration = ROOT.parents[1] / manifest["preregistration_file"]
    assert hashlib.sha256(preregistration.read_bytes()).hexdigest() == manifest["preregistration_sha256"]
