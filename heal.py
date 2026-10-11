"""Propose, replay, audit, and activate improvements to payment rules."""

import argparse
import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from pymongo import ReturnDocument

from config import print_error, safe_exception
from db.connection import connect
from db.seed import seed
from db.transactions import run_transaction
from observability import log_step
from proposer import ClaudeProposer, FixtureProposer
from replay import LABELS, gate_result, replay_suite, training_failures
from rules import VOCABULARY, validate_rule

MAX_ITERATIONS, MAX_ACTIVE_RULES, MAX_CANDIDATES = 3, 8, 5


class ActivationConflict(ValueError):
    pass


def active_rules(db, *, session=None):
    return list(db.rule_versions.find({"active": True}, {"_id": 1, "version": 1, "rule": 1}, session=session).sort("_id", 1))


def fingerprint(documents):
    return hashlib.sha256(json.dumps(documents, sort_keys=True).encode()).hexdigest()


def activate_candidate(db, candidate_id, base_fingerprint):
    # All healer activations write this shared fence before checking the exact
    # policy that was replayed, preventing two gated additions from racing.
    db.fences.update_one({"_id": "fence:rule-activation"}, {"$setOnInsert": {"version": 0}}, upsert=True)
    def activate(session):
        db.fences.find_one_and_update({"_id": "fence:rule-activation"}, {"$inc": {"version": 1}}, session=session)
        current = active_rules(db, session=session)
        if fingerprint(current) != base_fingerprint:
            raise ActivationConflict("Active rules changed since candidate replay")
        if len(current) >= MAX_ACTIVE_RULES:
            raise ActivationConflict("Eight active rules already exist")
        candidate = db.rule_versions.find_one({"_id": candidate_id}, session=session)
        if (candidate is None or candidate["decision"] != "accepted" or not candidate["gate"]["accepted"]
                or candidate["base_fingerprint"] != base_fingerprint):
            raise ActivationConflict("Candidate lacks an accepted gate for the current rules")
        validate_rule(candidate["rule"])
        db.rule_versions.update_one({"_id": candidate_id}, {"$set": {"active": True, "decision": "activated"}}, session=session)
    run_transaction(db, activate)


def _score(db, heal_id, iteration, result, current, status, candidate_count=0):
    db.runs.update_one({"run_id": heal_id, "step": "heal_iteration", "iteration": iteration}, {"$set": {
        "inputs": {"candidate_count": candidate_count}, "outputs": {
            "counts": result["counts"], "sets": result["sets"], "status": status,
            "active_rules": [{"rule_id": rule["_id"], "rule": rule["rule"]} for rule in current],
        }, "ts": datetime.now(timezone.utc),
    }}, upsert=True)


def _rank(entry):
    after = entry["after"]
    failures = sum(after["sets"]["must_catch"][label] for label in LABELS if label != "ok")
    return (failures, after["counts"]["unsafe_commit"], after["counts"]["benign_block"],
            len(entry["document"]["rule"]["clauses"]), entry["document"]["candidate_index"])


def run_heal(db, proposer, *, max_iterations=MAX_ITERATIONS, replayer=replay_suite):
    if type(max_iterations) is not int or not 1 <= max_iterations <= MAX_ITERATIONS:
        raise ValueError("Healing allows 1 to 3 iterations")
    heal_id = str(uuid.uuid4())
    current = active_rules(db)
    before = replayer(db.client, current)
    _score(db, heal_id, 0, before, current, "baseline")
    status, iteration = "healed", 0
    for attempt in range(1, max_iterations + 1):
        failures = training_failures(before)
        if not failures:
            status = "healed"
            break
        if len(current) >= MAX_ACTIVE_RULES:
            status = "active_rule_limit"
            break
        iteration = attempt
        base_fingerprint = fingerprint(current)
        try:
            proposals = proposer(failures, current, VOCABULARY)
        except Exception as error:
            log_step(db, heal_id, "heal_error", {"iteration": iteration}, {"error_type": type(error).__name__})
            raise
        if not isinstance(proposals, list) or len(proposals) > MAX_CANDIDATES or any(not isinstance(rule, dict) for rule in proposals):
            raise ValueError("Proposer must return zero to five rule objects")
        log_step(db, heal_id, "propose_rules", {"iteration": iteration, "failure_count": len(failures)}, {
            "candidate_count": len(proposals), "empty": not proposals,
            "model": deepcopy(getattr(proposer, "last_response", {})),
        })
        if not proposals:
            status = "no_candidates"
            _score(db, heal_id, iteration, before, current, status)
            break
        latest = db.rule_versions.find_one({}, {"version": 1}, sort=[("version", -1)])
        version = latest["version"] if latest else 0
        entries = []
        for index, proposal in enumerate(proposals):
            document = {
                "_id": f"heal:{heal_id}:{iteration:03}:{index:02}", "version": version + index + 1,
                "active": False, "proposal": deepcopy(proposal), "rule": None,
                "heal_id": heal_id, "iteration": iteration, "candidate_index": index,
                "base_fingerprint": base_fingerprint, "created_at": datetime.now(timezone.utc),
                "decision": "pending", "rejected_reason": None,
                "gate": {"accepted": False, "reasons": ["not_evaluated"], "before": before["sets"], "after": None},
            }
            try:
                document["rule"] = validate_rule(proposal)
            except (ValueError, TypeError) as error:
                document.update(decision="rejected", rejected_reason=safe_exception(error))
                document["gate"]["reasons"] = ["invalid_rule"]
            entries.append({"document": document, "after": None})
        # Audit all proposals before starting their potentially lengthy replays.
        db.rule_versions.insert_many([entry["document"] for entry in entries])
        for entry in entries:
            document = entry["document"]
            if document["decision"] == "rejected":
                continue
            try:
                after = replayer(db.client, current, candidate=document)
                gate = gate_result(before, after)
                entry["after"] = after
                document.update(gate=gate, decision="accepted" if gate["accepted"] else "rejected",
                                rejected_reason=None if gate["accepted"] else ", ".join(gate["reasons"]))
            except Exception as error:
                document.update(decision="rejected", rejected_reason=safe_exception(error), gate={
                    "accepted": False, "reasons": ["replay_error"], "before": before["sets"], "after": None,
                })
            db.rule_versions.update_one({"_id": document["_id"]}, {"$set": {
                key: document[key] for key in ("gate", "decision", "rejected_reason")
            }})
        accepted = [entry for entry in entries if entry["document"]["decision"] == "accepted"]
        status = "no_accepted_candidate"
        if accepted:
            best = min(accepted, key=_rank)
            try:
                activate_candidate(db, best["document"]["_id"], base_fingerprint)
                before = best["after"]
                current = active_rules(db)
                status = "improved"
            except ActivationConflict as error:
                document = best["document"]
                gate = {**document["gate"], "accepted": False,
                        "reasons": [*document["gate"]["reasons"], "activation_conflict"]}
                db.rule_versions.update_one({"_id": document["_id"]}, {"$set": {
                    "decision": "rejected", "rejected_reason": str(error), "gate": gate,
                }})
                current = active_rules(db)
                before = replayer(db.client, current)
                status = "activation_conflict"
        if not training_failures(before):
            status = "healed"
        elif len(current) >= MAX_ACTIVE_RULES:
            status = "active_rule_limit"
        elif iteration == max_iterations:
            status = "iteration_limit"
        _score(db, heal_id, iteration, before, current, status, len(proposals))
        if status in {"healed", "active_rule_limit"}:
            break
    return {"heal_id": heal_id, "iterations": iteration, "status": status,
            "counts": before["counts"], "sets": before["sets"], "active_rule_ids": [rule["_id"] for rule in current]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposer", choices=["live", "fixture"], default="live")
    parser.add_argument("--iterations", type=int, choices=[1, 2, 3], default=3)
    args = parser.parse_args(argv)
    db = proposer = None
    try:
        db = connect()
        seed(db)
        proposer = ClaudeProposer() if args.proposer == "live" else FixtureProposer()
        print(json.dumps(run_heal(db, proposer, max_iterations=args.iterations), indent=2))
        return 0
    except Exception as error:
        print_error(error)
        return 1
    finally:
        if proposer is not None and hasattr(proposer, "close"):
            proposer.close()
        if db is not None:
            db.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
