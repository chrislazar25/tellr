from types import SimpleNamespace

from scripts.measure_audio import MeasuredCall, money_diverged, write_json


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
