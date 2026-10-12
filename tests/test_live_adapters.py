import inspect
from types import SimpleNamespace

import pytest
from pymongo.errors import OperationFailure

import interfaces
from interfaces import Adapters, EXTRACT_TERMS_TOOL
from live_adapters import ClaudeExtractor, ScribeTranscriber, live_adapters


def test_reference_date_resolves_today_in_prompt_without_inventing_current_date():
    calls = []
    def create(**request):
        calls.append(request)
        return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", name="extract_payment_terms", input={"terms": None})])
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    ClaudeExtractor(client, model="test", vendors=[], reference_date="2026-10-11")("pay today")
    assert "Recording reference date: 2026-10-11" in calls[0]["system"]
    with pytest.raises(ValueError):
        ClaudeExtractor(client, model="test", vendors=[], reference_date="2026-02-30")


def test_reference_date_invalidates_terms_cache_but_preserves_audio_cache():
    clients = {"scribe_client": SimpleNamespace(), "claude_client": SimpleNamespace()}
    first = live_adapters([], reference_date="2026-10-11", **clients)
    second = live_adapters([], reference_date="2026-10-12", **clients)
    assert first.cache_key != second.cache_key
    assert first.transcription_key == second.transcription_key
from pipeline import prepare_intent

TERMS = {"vendor_id": "vendor-001", "amount_cents": 5000,
         "destination_account": "demo-account-001", "date": "2026-10-11"}


def response(payload):
    return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(
        type="tool_use", name=EXTRACT_TERMS_TOOL["name"], input=payload)])


def test_scribe_uses_same_bytes_with_both_flags(tmp_path):
    calls = []
    def convert(**kwargs):
        calls.append((kwargs["file"].read(), kwargs["model_id"], kwargs["no_verbatim"]))
        return SimpleNamespace(text="clean" if kwargs["no_verbatim"] else "verbatim")
    adapter = ScribeTranscriber(SimpleNamespace(speech_to_text=SimpleNamespace(convert=convert)))
    path = tmp_path / "audio.wav"
    path.write_bytes(b"same audio")
    assert adapter(path, no_verbatim=False) == "verbatim"
    assert adapter(b"same audio", no_verbatim=True) == "clean"
    assert calls == [(b"same audio", "scribe_v2", False), (b"same audio", "scribe_v2", True)]


def test_claude_forces_one_strict_tool_with_sdk_transformed_schema():
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return response({"terms": TERMS})
    extractor = ClaudeExtractor(SimpleNamespace(messages=SimpleNamespace(create=create)),
                               model="test-model", vendors=[])
    assert extractor("Pay 50 dollars") == TERMS
    request = calls[0]
    assert request["tool_choice"] == {"type": "tool", "name": EXTRACT_TERMS_TOOL["name"],
                                      "disable_parallel_tool_use": True}
    assert request["tools"][0]["strict"] is True
    props = request["tools"][0]["input_schema"]["properties"]["terms"]["anyOf"][0]["properties"]
    assert props["amount_cents"]["type"] == "integer"
    assert "minimum" not in props["amount_cents"]
    assert "minimum" in props["amount_cents"]["description"]
    assert EXTRACT_TERMS_TOOL["input_schema"]["properties"]["terms"]["anyOf"][0]["properties"]["amount_cents"]["minimum"] == 1
    assert request["messages"] == [{"role": "user", "content": "Pay 50 dollars"}]


@pytest.mark.parametrize("payload", [{"terms": {**TERMS, "amount_cents": 1.5}},
                                      {"terms": {**TERMS, "date": "2026-02-30"}},
                                      {"terms": {**TERMS, "amount_cents": 0}},
                                      {"terms": "bad"}, {}, {"terms": None, "extra": True}])
def test_claude_validates_constraints_after_strict_tool_output(payload):
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response(payload)))
    with pytest.raises(ValueError):
        ClaudeExtractor(client, model="test", vendors=[])("text")


@pytest.mark.parametrize("stop_reason", ["max_tokens", "end_turn", "refusal"])
def test_claude_rejects_incomplete_tool_response(stop_reason):
    result = response({"terms": TERMS})
    result.stop_reason = stop_reason
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: result))
    with pytest.raises(ValueError, match="completed extraction"):
        ClaudeExtractor(client, model="test", vendors=[])("text")


def test_claude_null_is_no_instruction():
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response({"terms": None})))
    assert ClaudeExtractor(client, model="test", vendors=[])("") is None


def test_installed_sdk_accepts_live_request_parameters():
    from elevenlabs.client import ElevenLabs
    from anthropic import Anthropic
    from anthropic.types import ToolParam
    assert {"file", "model_id", "no_verbatim"} <= inspect.signature(ElevenLabs(api_key="test").speech_to_text.convert).parameters.keys()
    assert {"tools", "tool_choice", "model", "max_tokens", "system", "messages"} <= inspect.signature(Anthropic(api_key="test").messages.create).parameters.keys()
    assert "strict" in ToolParam.__annotations__


def test_configured_adapters_choose_fixture_by_default(monkeypatch):
    monkeypatch.delenv("ADAPTERS")
    assert interfaces.configured_adapters() is interfaces.DEFAULT_ADAPTERS
    monkeypatch.setenv("ADAPTERS", "invalid")
    with pytest.raises(ValueError, match="fixture or live"):
        interfaces.configured_adapters()


def test_live_default_model_supports_forced_extraction_tool(monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    bundle = live_adapters([], scribe_client=object(), claude_client=object())
    assert bundle.extract_terms.model == "claude-sonnet-4-6"
    monkeypatch.setenv("CLAUDE_MODEL", "explicit-model")
    overridden = live_adapters([], scribe_client=object(), claude_client=object())
    assert overridden.extract_terms.model == "explicit-model"
    assert overridden.cache_key != bundle.cache_key


@pytest.mark.integration
def test_live_mode_requires_keys_without_fixture_fallback(db, monkeypatch):
    monkeypatch.setenv("ADAPTERS", "live")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        prepare_intent(db)
    assert db.evidence.count_documents({}) == 0


@pytest.mark.integration
def test_live_cache_reuses_both_apis_by_hash_across_paths_and_catalog_changes(db, tmp_path):
    calls, extracts = [], []
    path = tmp_path / "first.wav"
    path.write_bytes(b"immutable audio")
    def convert(**kw):
        calls.append((kw["file"].read(), kw["no_verbatim"]))
        # Even changing the source during the request cannot affect the second.
        path.write_bytes(b"changed while transcribing")
        return SimpleNamespace(text="cleaned" if kw["no_verbatim"] else "verbatim")
    def create(**kw):
        extracts.append(kw["messages"][0]["content"])
        return response({"terms": TERMS})
    scribe = SimpleNamespace(speech_to_text=SimpleNamespace(convert=convert))
    claude = SimpleNamespace(messages=SimpleNamespace(create=create))
    def bundle(catalog):
        return live_adapters(catalog, scribe_client=scribe, claude_client=claude)
    first = prepare_intent(db, path, adapters=bundle([]))
    copy = tmp_path / "renamed.wav"
    copy.write_bytes(b"immutable audio")
    second = prepare_intent(db, copy, adapters=bundle([]))
    assert len(calls) == len(extracts) == 2
    assert calls == [(b"immutable audio", False), (b"immutable audio", True)]
    assert first["run_id"] != second["run_id"]
    doc = db.evidence.find_one({"_id": second["evidence_id"]})
    assert len(doc["audio_hash"]) == 64
    assert doc["verbatim_terms"] == doc["cleaned_terms"] == TERMS
    assert all(log["outputs"]["cache_hit"] for log in db.runs.find({"run_id": second["run_id"], "step": {"$in": ["transcribe", "extract_terms"]}}))
    prepare_intent(db, copy, adapters=bundle([{"_id": "different catalog"}]))
    assert len(calls) == 2  # Catalog/model changes never invalidate Scribe audio.
    assert len(extracts) == 4
    copy.write_bytes(b"new audio")
    prepare_intent(db, copy, adapters=bundle([]))
    assert len(calls) == 4


@pytest.mark.integration
def test_partial_cache_resumes_without_retranscribing_or_reextracting(db, tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    calls, extracts = [], []
    def transcribe(audio, *, no_verbatim):
        calls.append(no_verbatim)
        return "cleaned" if no_verbatim else "verbatim"
    def extract(text):
        extracts.append(text)
        if text == "cleaned" and extracts.count(text) == 1:
            raise RuntimeError("provider temporarily unavailable")
        return None
    bundle = Adapters(transcribe, extract, lambda terms: "", cache_key="test")
    with pytest.raises(RuntimeError):
        prepare_intent(db, path, adapters=bundle)
    result = prepare_intent(db, path, adapters=bundle)
    assert calls == [False, True]
    assert extracts == ["verbatim", "cleaned", "cleaned"]
    assert result["status"] == "no_terms"
    assert db.payments.count_documents({}) == 0
    assert prepare_intent(db, path, adapters=bundle)["status"] == "no_terms"
    assert extracts == ["verbatim", "cleaned", "cleaned"]


@pytest.mark.integration
def test_cache_schema_rejects_invalid_hash_and_cents(db):
    from datetime import datetime, timezone
    record = {"_id": "test-cache", "kind": "cache", "audio_hash": "a" * 64,
              "adapter_key": "test", "created_at": datetime.now(timezone.utc)}
    db.evidence.insert_one(record)
    for field, value in [("audio_hash", "bad"), ("verbatim_terms", {**TERMS, "amount_cents": 1.5})]:
        with pytest.raises(OperationFailure):
            db.evidence.update_one({"_id": record["_id"]}, {"$set": {field: value}})
