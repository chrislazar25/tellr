from collections import Counter
from copy import deepcopy

import pytest

from experiments.failure_hunt.dataset import HOLDOUT_VOICE, read_dataset
from replay import replay_suite


def test_voice_split_retains_all_attempts_without_using_holdout_as_training():
    data = read_dataset()
    assert len(data["payment_attempts"]) == 180 and len(data["confirmation_attempts"]) == 33
    assert len(data["excluded"]) == 12
    assert Counter(c["eval_set"] for c in data["cases"]) == {"must_catch": 12, "must_pass": 100, "holdout": 56}
    assert {c["voice_id"] for c in data["cases"] if c["eval_set"] == "holdout"} == {HOLDOUT_VOICE}
    assert all(c["voice_id"] != HOLDOUT_VOICE for c in data["cases"] if c["eval_set"] != "holdout")
    assert sum(c["recognizer_a_terms"] is None for c in data["cases"]) == 7
    assert all(c["expected"] == "clarify" and c["true_terms"]["destination_account"] == "13" for c in data["excluded"])


@pytest.mark.integration
def test_split_predictions_match_worker_oracle_on_the_full_cached_suite(mongo_client):
    data = read_dataset()
    result = replay_suite(mongo_client, [], cases=data["cases"])
    selected = {c["case_id"]: c for c in data["cases"]}
    for row in result["cases"]:
        assert (row["label"] == "unsafe_commit") == selected[row["case_id"]]["selection_predicted_unsafe"]
        assert row["confirmation_source"] in {None, "recognizer_a"}
    assert result["counts"]["N"] == 168
    assert result["counts"]["unsafe_commit"] == 23 and result["counts"]["benign_block"] == 0
    assert result["sets"]["must_pass"]["ok"] == 100
