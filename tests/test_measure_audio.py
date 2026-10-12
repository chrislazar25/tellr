from types import SimpleNamespace

from scripts.measure_audio import MeasuredCall, money_diverged, write_json
import wave

import pytest

from interfaces import DEFAULT_ADAPTERS, _recordings
from scripts.measure_audio import measure


def test_divergence_is_amount_or_destination_not_transcript_style():
    terms = {"amount_cents": 5000, "destination_account": "24", "vendor_id": "vendor-002", "date": "2026-10-11"}
    assert not money_diverged(terms, {**terms, "date": "2026-10-12"})
    assert money_diverged(terms, {**terms, "amount_cents": 50000})
    assert money_diverged(terms, {**terms, "destination_account": "42"})
    assert money_diverged(terms, None)
    assert not money_diverged(None, None)


def test_measurement_records_returned_usage_without_request_credentials(tmp_path):
    events = []
    response = SimpleNamespace(model="returned-model", usage={"input_tokens": 10, "output_tokens": 3})
    call = MeasuredCall(lambda **kw: response, events, "anthropic", "extraction")
    assert call(model="requested-model", api_key="DO_NOT_SAVE", messages=["private request"]) is response
    assert events[0]["usage"] == response.usage
    assert events[0]["billed_cost_usd"] is None
    path = tmp_path / "artifact.json"
    write_json(path, events)
    assert "DO_NOT_SAVE" not in path.read_text() and "private request" not in path.read_text()


@pytest.mark.integration
def test_measurement_checkpoints_all_clips_without_approvals(db, tmp_path):
    for case in _recordings():
        with wave.open(str(tmp_path / case["audio_file"]), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 160)
    output = tmp_path / "clips.json"
    result = measure(db, tmp_path, output, adapters=DEFAULT_ADAPTERS)
    assert len(result["clips"]) == 6
    assert result["finished_at"] and output.exists()
    assert result["clips"][0]["duration_seconds"] == 0.01
    assert db.intents.count_documents({}) == db.payments.count_documents({}) == 0
