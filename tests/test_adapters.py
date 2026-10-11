import pytest

import interfaces
from interfaces import Adapters
from pipeline import prepare_intent, run_pipeline


def test_public_transcribe_maps_verbatim_to_no_verbatim(monkeypatch):
    flags = []

    def transcriber(audio_path, *, no_verbatim):
        flags.append(no_verbatim)
        return "transcript"

    original = interfaces.DEFAULT_ADAPTERS
    monkeypatch.setattr(interfaces, "DEFAULT_ADAPTERS", Adapters(transcriber, original.extract_terms, original.readback))
    assert interfaces.transcribe("audio.wav", True) == "transcript"
    assert interfaces.transcribe("audio.wav", False) == "transcript"
    assert flags == [False, True]


@pytest.mark.integration
@pytest.mark.parametrize("manual", [False, True])
def test_injected_adapters_keep_both_candidates_and_use_confirmed_terms(db, manual):
    calls, extractions, readbacks = [], [], []
    common = {"vendor_id": "vendor-001", "destination_account": "demo-account-001", "date": "2026-10-11"}

    def transcriber(audio_path, *, no_verbatim):
        calls.append((audio_path, no_verbatim))
        return "cleaned result" if no_verbatim else "verbatim result"

    def extractor(text):
        extractions.append(text)
        return {**common, "amount_cents": 5000 if text == "cleaned result" else 1500}

    def reader(terms):
        readbacks.append(terms)
        return "Read back selected terms"

    adapters = Adapters(transcriber, extractor, reader)
    kwargs = {"confirmed_terms": {**common, "amount_cents": 2500}} if manual else {"confirmation_source": "cleaned"}
    result = run_pipeline(db, "custom-audio.wav", adapters=adapters, **kwargs)
    assert calls == [("custom-audio.wav", False), ("custom-audio.wav", True)]
    assert extractions == ["verbatim result", "cleaned result"]
    evidence = db.evidence.find_one({"_id": result["evidence_id"]})
    assert evidence["verbatim_terms"]["amount_cents"] == 1500
    assert evidence["cleaned_terms"]["amount_cents"] == 5000
    intent = db.intents.find_one({"_id": result["intent_id"]})
    expected = 2500 if manual else 5000
    assert intent["confirmed_terms"]["amount_cents"] == expected
    assert intent["confirmation_source"] == ("manual" if manual else "cleaned")
    assert readbacks == [intent["confirmed_terms"]]
    assert db.payments.find_one()["amount_cents"] == expected


@pytest.mark.integration
def test_invalid_external_adapter_amount_cannot_reach_queue(db):
    adapters = Adapters(
        lambda path, no_verbatim: "text",
        lambda text: {"vendor_id": "vendor-001", "amount_cents": 1.5,
                      "destination_account": "demo-account-001", "date": "2026-10-11"},
        lambda terms: "readback",
    )
    with pytest.raises(ValueError, match="amount_cents"):
        prepare_intent(db, "audio.wav", adapters=adapters)
    assert db.queue.count_documents({}) == db.payments.count_documents({}) == 0
