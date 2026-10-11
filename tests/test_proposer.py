import json
from types import SimpleNamespace

import pytest

from proposer import ClaudeProposer, FixtureProposer
from rules import VOCABULARY

FAILURE = {"eval_set": "must_catch", "case_id": "train-case", "label": "unsafe_commit", "expected": "clarify",
           "reasons": ["payment_requires_clarification"], "block_reason": None,
           "field_diffs": [{"left": "evidence.verbatim_terms.amount_cents",
                            "right": "evidence.cleaned_terms.amount_cents", "left_value": 5000, "right_value": 50000}]}


def test_structured_proposer_sdk_request_sends_only_training_context_and_rule_language():
    calls = []
    candidates = FixtureProposer()([FAILURE], [])
    def create(**request):
        calls.append(request)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"candidates": candidates}))])
    adapter = ClaudeProposer(SimpleNamespace(messages=SimpleNamespace(create=create)), model="test-model")
    current = {"rule": candidates[0], "gate": {"holdout": "NEVER_SEND_HOLDOUT"}}
    assert adapter([FAILURE], [current]) == candidates
    request = calls[0]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "tools" not in request
    sent = json.loads(request["messages"][0]["content"])
    assert sent == {"failures": [FAILURE], "current_rules": [candidates[0]], "vocabulary": list(VOCABULARY)}
    assert "NEVER_SEND_HOLDOUT" not in json.dumps(request)


@pytest.mark.parametrize("result,stop_reason", [
    ({"candidates": []}, "end_turn"), ({"candidates": [{}] * 4}, "end_turn"),
    ({"candidates": [{}] * 3}, "max_tokens"), ({"candidates": [{}] * 3}, "refusal"),
    ({"candidates": [{}] * 3, "extra": True}, "end_turn"),
    ({"candidates": ["bad"] * 3}, "end_turn"),
])
def test_incomplete_or_invalid_proposal_envelope_is_rejected(result, stop_reason):
    response = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(result))])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    with pytest.raises(ValueError):
        ClaudeProposer(client)([FAILURE], [])


def test_proposer_rejects_extra_context_keys_before_any_request():
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: pytest.fail("Must not send extra context")))
    with pytest.raises(ValueError):
        ClaudeProposer(client)([{**FAILURE, "holdout_rows": []}], [])
