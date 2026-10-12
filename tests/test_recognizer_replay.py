from copy import deepcopy

import pytest
from pymongo.errors import OperationFailure

from fixtures.catalog import fixture_cases
from replay import _run_case


def case():
    base = deepcopy(next(row for row in fixture_cases() if row["case_id"] == "clean"))
    base.update(case_id="two-recognizers", expected="pay", eval_set="must_pass",
                recognizer_a_text=base["verbatim_text"], recognizer_b_text="Wrong amount in B")
    base["recognizer_a_terms"] = deepcopy(base["verbatim_terms"])
    base["recognizer_b_terms"] = {**base["verbatim_terms"], "amount_cents": 50000}
    base["cleaned_terms"] = deepcopy(base["recognizer_a_terms"])
    return base


def install(db, rule):
    db.rule_versions.insert_one({"_id": "comparator", "version": 1, "active": True, "rule": rule})


@pytest.mark.integration
def test_cached_recognizers_store_both_but_only_approve_a(db):
    scenario = case()
    row = _run_case(db, scenario)
    evidence = db.evidence.find_one({"_id": row["evidence_id"]})
    assert evidence["recognizer_a_terms"] == scenario["recognizer_a_terms"]
    assert evidence["recognizer_b_terms"] == scenario["recognizer_b_terms"]
    assert row["label"] == "ok" and row["amount_paid"] == scenario["recognizer_a_terms"]["amount_cents"]
    assert db.intents.find_one({"run_id": row["run_id"]})["confirmation_source"] == "recognizer_a"


@pytest.mark.integration
def test_missing_a_never_approves_nonnull_b(db):
    scenario = case()
    scenario.update(recognizer_a_terms=None, verbatim_terms=None, cleaned_terms=None, expected="clarify")
    row = _run_case(db, scenario)
    assert row["label"] == "ok" and row["block_reason"] == "clarify"
    assert row["approved_terms"] is None and row["payment_count"] == 0
    assert db.intents.count_documents({}) == 0


@pytest.mark.integration
def test_agreement_blocks_correct_a_while_account_suffix_rule_allows_it(db):
    install(db, {"clauses": [{"left": "evidence.recognizer_a_terms.amount_cents", "cmp": "eq",
                             "right": "evidence.recognizer_b_terms.amount_cents"}], "on_fail": "clarify"})
    assert _run_case(db, case())["label"] == "benign_block"
    db.rule_versions.update_one({"_id": "comparator"}, {"$set": {"rule": {
        "clauses": [{"left": "evidence.recognizer_a_terms.destination_account", "cmp": "suffix_match",
                     "right": "vendor.bank_account"}], "on_fail": "clarify"}}})
    assert _run_case(db, case())["label"] == "ok"


@pytest.mark.integration
def test_database_rejects_suffix_match_on_amounts(db):
    with pytest.raises(OperationFailure):
        install(db, {"clauses": [{"left": "evidence.recognizer_b_terms.amount_cents", "cmp": "suffix_match",
                                 "right": "vendor.bank_account"}], "on_fail": "clarify"})
