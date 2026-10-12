"""Export reproducible Atlas baseline/healing measurements for a portfolio."""

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from pymongo.write_concern import WriteConcern

import config
from db.accounts import destination_matches
from db.connection import connect
from db.seed import seed
from fixtures.catalog import fixture_cases
from heal import active_rules, run_heal
from proposer import ClaudeProposer
from replay import replay_suite
from scoreboard import _field_name, report
from scripts.measure_audio import MeasuredCall, money_diverged, now, write_json


def truth_mismatches(terms, truth):
    if terms is None or truth is None:
        return [] if terms == truth else ["instruction_presence"]
    fields = [field for field in ("vendor_id", "amount_cents", "date") if terms[field] != truth[field]]
    if not destination_matches(truth["destination_account"], terms["destination_account"]):
        fields.append("destination_account")
    return fields


def measured_cases(measurement):
    """Keep independent truth fixed; choose sets from actual API observations."""
    originals = fixture_cases()
    by_id, cases = {}, []
    for clip in measurement["clips"]:
        divergent = money_diverged(clip["verbatim_terms"], clip["cleaned_terms"])
        mismatches = {mode: truth_mismatches(clip[f"{mode}_terms"], clip["true_terms"])
                      for mode in ("verbatim", "cleaned")}
        case = {"case_id": clip["case_id"], "audio_file": clip["audio_file"],
                "verbatim_text": clip["verbatim"], "cleaned_text": clip["cleaned"],
                "verbatim_terms": clip["verbatim_terms"], "cleaned_terms": clip["cleaned_terms"],
                "true_terms": clip["true_terms"], "expected": "clarify" if divergent else "pay",
                "eval_set": "must_catch" if divergent or any(mismatches.values()) else "must_pass",
                "source": "measured_live_audio"}
        cases.append(case)
        by_id[case["case_id"]] = case
    for scenario in originals:
        if "recording_case_id" in scenario:
            cases.append({**deepcopy(by_id[scenario["recording_case_id"]]),
                          **{key: deepcopy(scenario[key]) for key in ("case_id", "recording_case_id", "expected", "eval_set", "changes")},
                          "source": "controlled_vendor_edit_using_measured_audio"})
        elif scenario["eval_set"] == "holdout":
            cases.append({**deepcopy(scenario), "source": "synthetic_fixture"})
    return cases


def rule_text(rule):
    if rule is None:
        return "Invalid predicate; not activated."
    clauses = [f"{_field_name(clause['left'])} {'equals' if clause['cmp'] == 'eq' else 'differs from'} {_field_name(clause['right'])}"
               for clause in rule["clauses"]]
    action = "request clarification" if rule["on_fail"] == "clarify" else "request reapproval"
    return f"Require {' and '.join(clauses)}; otherwise {action}."


def _candidate(document):
    return {"candidate_id": document["_id"], "iteration": document["iteration"],
            "candidate_index": document["candidate_index"], "rule": document["rule"],
            "proposal": document["proposal"], "rule_text": rule_text(document["rule"]),
            "decision": document["decision"], "active": document["active"],
            "gate": document["gate"], "rejected_reason": document["rejected_reason"]}


def experiment(db, measurement, output, *, proposer, suite="measured", events=None):
    """Use a fresh audit database; every replay uses a disposable owned database."""
    if active_rules(db) or db.runs.count_documents({"step": "heal_iteration"}):
        raise ValueError("Experiment audit database must have no prior healing policy/history")
    if suite not in {"measured", "fixture"}:
        raise ValueError("Unknown experiment suite")
    events = events if events is not None else []
    cases = measured_cases(measurement) if suite == "measured" else [
        {**case, "source": "synthetic_fixture"} for case in fixture_cases()]
    started = time.perf_counter()
    result = {"schema_version": 1, "experiment_id": str(uuid.uuid4()), "suite": suite,
              "started_at": now(), "database_backend": "configured_mongodb", "audit_database": db.name,
              "reference_date": measurement["reference_date"] if measurement else "2026-10-11",
              "amount_unit": "integer_cents", "status": "running", "clips": [],
              "case_manifest": [{key: deepcopy(case[key]) for key in
                                 ("case_id", "audio_file", "eval_set", "expected", "source", "true_terms")}
                                for case in cases],
              "replay_log": [], "api_calls": events,
              "acquisition": deepcopy(measurement) if suite == "measured" else None,
              "cost": {"billed_usd": None, "reason": "No invoiced cost in provider responses; no estimated cost substituted."}}
    if suite == "measured":
        result["clips"] = [{**deepcopy(clip), "source": "measured_live_audio",
                            "truth_mismatches": {mode: truth_mismatches(clip[f"{mode}_terms"], clip["true_terms"])
                                                 for mode in ("verbatim", "cleaned")}}
                           for clip in measurement["clips"]]
    else:
        result["clips"] = [{"case_id": case["case_id"], "audio_file": case["audio_file"],
                            "source": "synthetic_fixture", "verbatim": case["verbatim_text"],
                            "cleaned": case["cleaned_text"], "verbatim_terms": case["verbatim_terms"],
                            "cleaned_terms": case["cleaned_terms"], "true_terms": case["true_terms"],
                            "money_terms_diverged": money_diverged(case["verbatim_terms"], case["cleaned_terms"])}
                           for case in cases if "recording_case_id" not in case and case["eval_set"] != "holdout"]
    try:
        result["git_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        result["git_revision"] = None

    def replay(client, current, *, candidate=None):
        replay_start = time.perf_counter()
        observed = replay_suite(client, current, candidate=candidate, cases=cases)
        phase = "candidate" if candidate else "baseline" if not result["replay_log"] else "policy_replay"
        result["replay_log"].append({"phase": phase, "candidate_id": candidate["_id"] if candidate else None,
                                     "wall_seconds": round(time.perf_counter() - replay_start, 6),
                                     "results": deepcopy(observed)})
        if phase == "baseline":
            result["baseline_replay"] = deepcopy(observed)
            result["baseline_runs"] = [deepcopy(row) for row in observed["cases"] if row["eval_set"] != "holdout"]
        write_json(output, result)
        print(f"Replay {phase}: {observed['counts']}", flush=True)
        return observed

    try:
        heal_start = time.perf_counter()
        healed = run_heal(db, proposer, replayer=replay)
        result["heal_wall_seconds"] = round(time.perf_counter() - heal_start, 6)
        result["heal"] = healed
        candidates = [_candidate(doc) for doc in db.rule_versions.find({"heal_id": healed["heal_id"]}).sort(
            [("iteration", 1), ("candidate_index", 1)])]
        scores = report(db, heal_id=healed["heal_id"])
        result["heal_iterations"] = [{**row, "candidates": [doc for doc in candidates if doc["iteration"] == row["iteration"]]}
                                     for row in scores["iterations"]]
        result["active_rules"] = [{**doc, "rule_text": rule_text(doc["rule"])} for doc in scores["active_rules"]]
        result["proposal_events"] = [{"inputs": row["inputs"], "outputs": row["outputs"], "ts": row["ts"].isoformat()}
                                     for row in db.runs.find({"run_id": healed["heal_id"], "step": "propose_rules"}).sort("ts", 1)]
        result["final_replay"] = replay(db.client, active_rules(db))
        result["status"] = "complete"
        result["finished_at"] = now()
    except Exception as error:
        result["status"] = "failed"
        result["error"] = config.safe_exception(error)
        raise
    finally:
        result["wall_seconds"] = round(time.perf_counter() - started, 6)
        write_json(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["measured", "fixture"], default="measured")
    parser.add_argument("--measurement", type=Path, default=Path("artifacts/live_clips.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/experiment.json"))
    args = parser.parse_args(argv)
    configured = proposer = None
    try:
        if not config.mongodb_uri().startswith("mongodb+srv://"):
            raise ValueError("Portfolio live experiments require the configured Atlas SRV connection")
        measurement = json.loads(args.measurement.read_text()) if args.suite == "measured" else None
        if measurement:
            if measurement.get("source") != "live_api" or not measurement.get("finished_at"):
                raise ValueError("Measurement must be completed live API output")
            for clip in measurement["clips"]:
                if hashlib.sha256(Path(clip["audio_file"]).read_bytes()).hexdigest() != clip["audio_sha256"]:
                    raise ValueError("Recording no longer matches measured audio hash")
        configured = connect()
        seed(configured)
        db = configured.client["portfolio_experiment_" + uuid.uuid4().hex].with_options(write_concern=WriteConcern("majority"))
        seed(db)
        from anthropic import Anthropic
        client = Anthropic(api_key=config.value("ANTHROPIC_API_KEY"))
        events = []
        wrapped = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=MeasuredCall(
            client.beta.messages.create, events, "anthropic", "rule_proposal"))), close=client.close)
        proposer = ClaudeProposer(wrapped)
        result = experiment(db, measurement, args.output, proposer=proposer, suite=args.suite, events=events)
        result["database_backend"] = "atlas"
        write_json(args.output, result)
        print(json.dumps({"output": str(args.output), "status": result["heal"]["status"],
                          "before": result["baseline_replay"]["sets"], "after": result["final_replay"]["sets"]}, indent=2))
        return 0
    except Exception as error:
        config.print_error(error)
        return 1
    finally:
        if proposer is not None:
            proposer.close()
        if configured is not None:
            configured.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
