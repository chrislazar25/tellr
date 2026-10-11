import pytest

from interfaces import extract_terms, readback, transcribe, validate_terms


def test_fixture_adapters_work_without_keys(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "RAINDROP_WRITE_KEY"):
        monkeypatch.delenv(key, raising=False)
    verbatim = transcribe("any/path/demo-invoice.wav", True)
    normalized = transcribe("demo-invoice.wav", False)
    assert verbatim.startswith("Um,")
    assert normalized != verbatim
    assert extract_terms(verbatim) == extract_terms(normalized)
    terms = extract_terms(verbatim)
    assert type(terms["amount_cents"]) is int
    assert terms["amount_cents"] == 12550
    assert "125 dollars and 50 cents" in readback(terms)


@pytest.mark.parametrize("amount", [125.5, True, "12550", 0, -1, 2**63])
def test_terms_reject_non_integer_or_invalid_cents(amount):
    terms = extract_terms(transcribe("demo-invoice.wav", True))
    terms["amount_cents"] = amount
    with pytest.raises(ValueError, match="amount_cents"):
        validate_terms(terms)


def test_unknown_fixture_fails_explicitly():
    with pytest.raises(ValueError, match="transcription fixture"):
        transcribe("unknown.wav", True)
    with pytest.raises(ValueError, match="extraction fixture"):
        extract_terms("unknown invoice")


@pytest.mark.parametrize("value", ["2026-02-30", "20261011", "2026-1-1"])
def test_terms_require_real_iso_date(value):
    terms = extract_terms(transcribe("demo-invoice.wav", True))
    terms["date"] = value
    with pytest.raises(ValueError):
        validate_terms(terms)
