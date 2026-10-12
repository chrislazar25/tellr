"""Compare frozen cached hunt inputs through real Atlas worker/oracle gates."""

import argparse
import hashlib
import json
import time
import uuid
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from pymongo.write_concern import WriteConcern

from config import print_error, safe_exception
from db.connection import connect
from db.seed import seed
from experiments.failure_hunt import keys
from experiments.failure_hunt.dataset import HOLDOUT_VOICE, ROOT, VOICES, read_dataset
from heal import active_rules, run_heal
from proposer import ClaudeProposer
from replay import gate_result, replay_suite
from scoreboard import report
from scripts.measure_audio import MeasuredCall, now, write_json
from scripts.portfolio_experiment import _candidate, rule_text

AGREEMENT = {"clauses": [{"left": f"evidence.recognizer_a_terms.{field}", "cmp": "eq",
                          "right": f"evidence.recognizer_b_terms.{field}"}
                         for field in ("amount_cents", "destination_account")], "on_fail": "clarify"}
ON_FILE = {"clauses": [{"left": "evidence.recognizer_a_terms.destination_account", "cmp": "suffix_match",
                        "right": "vendor.bank_account"}], "on_fail": "clarify"}


def metrics(baseline, observed):
    before = {row["case_id"]: row for row in baseline["cases"]}
    unsafe = {key for key, row in before.items() if row["label"] == "unsafe_commit"}
    good = {key for key, row in before.items() if row["label"] == "ok" and row["payment_count"] == 1}
    return {"unsafe_baseline": len(unsafe), "good_baseline_payments": len(good),
            "caught_unsafe": sum(row["case_id"] in unsafe and row["label"] == "ok" and row["payment_count"] == 0 for row in observed["cases"]),
            "blocked_good": sum(row["case_id"] in good and row["label"] == "benign_block" for row in observed["cases"]),
            "unsafe_remaining": observed["counts"]["unsafe_commit"],
            "intake_clarifications": sum(row["label"] == "ok" and row["approved_terms"] is None for row in observed["cases"])}


def identical_contexts(baseline):
    groups = defaultdict(list)
    for row in baseline["cases"]:
        groups[json.dumps(row["rule_context"], sort_keys=True)].append(row)
    return [{"context_sha256": hashlib.sha256(signature.encode()).hexdigest(),
             "rule_context": json.loads(signature),
             "unsafe_cases": [row["case_id"] for row in rows if row["label"] == "unsafe_commit"],
             "good_cases": [row["case_id"] for row in rows if row["label"] == "ok" and row["payment_count"] == 1]}
            for signature, rows in groups.items()
            if any(row["label"] == "unsafe_commit" for row in rows)
            and any(row["label"] == "ok" and row["payment_count"] == 1 for row in rows)]


def annotate_comparator_gates(artifact):
    """Apply the same gate function to each comparator's measured outcomes."""
    baseline = artifact["comparators"]["empty_rules"]["results"]
    for name, comparator in artifact["comparators"].items():
        comparator["gate"] = None if name == "empty_rules" else gate_result(baseline, comparator["results"])


def run_experiment(db, output, *, proposer, events=None, data=None):
    data = read_dataset() if data is None else data
    if active_rules(db) or db.runs.count_documents({"step": "heal_iteration"}):
        raise ValueError("Hunt experiment needs a fresh audit database")
    cases = data["cases"]
    events = events if events is not None else []
    started = time.perf_counter()
    artifact = {"schema_version": 1, "experiment_id": str(uuid.uuid4()), "status": "running",
                "started_at": now(), "database_backend": "configured_mongodb", "audit_database": db.name,
                "amount_unit": "integer_cents", "approval_source": "recognizer_a",
                "recognizers": {"a": "Scribe v2 (supplied cached output)", "b": "faster-whisper small CPU int8 (supplied cached output)"},
                "split": {"training_voices": [name for voice, name in VOICES.items() if voice != HOLDOUT_VOICE],
                          "holdout_voice": VOICES[HOLDOUT_VOICE], "counts": dict(Counter(case["eval_set"] for case in cases)),
                          "voice_disjoint": True, "phrase_disjoint": False},
                "coverage": {"payment_attempts": len(data["payment_attempts"]), "confirmation_attempts": len(data["confirmation_attempts"]),
                             "payment_eval_cases": len(cases), "excluded_spoken_account_not_on_file": len(data["excluded"]),
                             "intake_clarifications": sum(case["recognizer_a_terms"] is None for case in cases)},
                "payment_attempts": data["payment_attempts"], "excluded": data["excluded"],
                "confirmation_attempts": data["confirmation_attempts"], "confirmation_manifest": data["confirmation_manifest"],
                "comparators": {}, "replays": [], "api_calls": events,
                "acquisition_api_usage": None,
                "cost": {"billed_usd": None, "reason": "Acquisition billing was not recorded; proposal responses do not include invoiced cost."},
                "input_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                 for name in ("attempts.jsonl", "scored.json", "confirm_attempts.jsonl", "confirm_scored.json", "manifest.json", "audio_manifest.json")}}
    baseline = None

    def measured_replay(client, current, *, candidate=None, phase="gate"):
        begun = time.perf_counter()
        progress = []
        def on_case(row):
            progress.append(row)
            if len(progress) % 20 == 0 or len(progress) == len(cases):
                artifact["progress"] = {"phase": phase, "completed": len(progress), "N": len(cases),
                                        "candidate_id": candidate["_id"] if candidate else None, "cases": deepcopy(progress)}
                write_json(output, artifact)
                print(f"{phase}: {len(progress)}/{len(cases)}", flush=True)
        observed = replay_suite(client, current, candidate=candidate, cases=cases, on_case=on_case)
        artifact.pop("progress", None)
        artifact["replays"].append({"phase": phase, "candidate_id": candidate["_id"] if candidate else None,
                                    "wall_seconds": round(time.perf_counter() - begun, 6), "results": deepcopy(observed)})
        write_json(output, artifact)
        return observed

    def healer_replay(client, current, *, candidate=None):
        # Same frozen cases and empty policy: reuse the already measured baseline,
        # not a predicted score. All candidate and final policies replay freshly.
        if not current and candidate is None:
            return deepcopy(baseline)
        return measured_replay(client, current, candidate=candidate, phase="candidate_gate" if candidate else "policy_replay")

    try:
        baseline = measured_replay(db.client, [], phase="empty_rules")
        selected = {case["case_id"]: case for case in cases}
        if any((row["label"] == "unsafe_commit") != selected[row["case_id"]]["selection_predicted_unsafe"] for row in baseline["cases"]):
            raise ValueError("Measured baseline differs from frozen split selection; review before proposing")
        artifact["comparators"]["empty_rules"] = {"rules": [], "results": baseline, "metrics": metrics(baseline, baseline)}
        artifact["identical_rule_contexts"] = identical_contexts(baseline)
        for name, rule in (("recognizer_agreement", AGREEMENT), ("spoken_account_on_file", ON_FILE)):
            observed = measured_replay(db.client, [{"_id": "comparator:" + name, "version": 1, "rule": rule}], phase=name)
            artifact["comparators"][name] = {"rules": [{"rule": rule, "rule_text": rule_text(rule)}],
                                             "results": observed, "metrics": metrics(baseline, observed)}
            write_json(output, artifact)
        heal_started = time.perf_counter()
        healed = run_heal(db, proposer, replayer=healer_replay)
        artifact["heal_wall_seconds"] = round(time.perf_counter() - heal_started, 6)
        artifact["heal"] = healed
        candidates = [_candidate(row) for row in db.rule_versions.find({"heal_id": healed["heal_id"]}).sort([("iteration", 1), ("candidate_index", 1)])]
        scores = report(db, heal_id=healed["heal_id"])
        artifact["heal_iterations"] = [{**row, "candidates": [candidate for candidate in candidates if candidate["iteration"] == row["iteration"]]}
                                       for row in scores["iterations"]]
        artifact["active_rules"] = [{**row, "rule_text": rule_text(row["rule"])} for row in scores["active_rules"]]
        artifact["proposal_events"] = [{"inputs": row["inputs"], "outputs": row["outputs"], "ts": row["ts"].isoformat()}
                                        for row in db.runs.find({"run_id": healed["heal_id"], "step": "propose_rules"}).sort("ts", 1)]
        final = measured_replay(db.client, active_rules(db), phase="final_healed_replay")
        artifact["comparators"]["live_heal"] = {"rules": artifact["active_rules"], "results": final, "metrics": metrics(baseline, final)}
        artifact["final_replay"] = final
        annotate_comparator_gates(artifact)
        # Join every measured policy outcome back to all supplied attempts; exclusions
        # remain explicit rather than pretending they received a worker verdict.
        for record in artifact["payment_attempts"]:
            record["outcomes"] = {name: next((deepcopy(row) for row in comparator["results"]["cases"] if row["case_id"] == record["case_id"]),
                                            {"status": "excluded", "expected": "clarify", "reason": "spoken_account_not_on_file"})
                                  for name, comparator in artifact["comparators"].items()}
        artifact["finished_at"] = now()
        artifact["status"] = "complete"
    except Exception as error:
        artifact["status"] = "failed"
        artifact["error"] = safe_exception(error)
        raise
    finally:
        artifact["wall_seconds"] = round(time.perf_counter() - started, 6)
        write_json(output, artifact)
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/failure_hunt_experiment.json"))
    args = parser.parse_args(argv)
    configured = proposer = None
    try:
        uri = keys.get("MONGODB_URI")
        if not uri.startswith("mongodb+srv://"):
            raise ValueError("The live hunt comparison requires the configured Atlas SRV connection")
        configured = connect(uri)
        seed(configured)
        db = configured.client["failure_hunt_experiment_" + uuid.uuid4().hex].with_options(write_concern=WriteConcern("majority"))
        seed(db)
        from anthropic import Anthropic
        client = Anthropic(api_key=keys.get("ANTHROPIC_API_KEY"))
        events = []
        proxy = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=MeasuredCall(
            client.beta.messages.create, events, "anthropic", "rule_proposal"))), close=client.close)
        proposer = ClaudeProposer(proxy)
        artifact = run_experiment(db, args.output, proposer=proposer, events=events)
        artifact["database_backend"] = "atlas"
        write_json(args.output, artifact)
        print(json.dumps({"output": str(args.output), "heal_status": artifact["heal"]["status"],
                          "comparators": {name: value["metrics"] for name, value in artifact["comparators"].items()}}, indent=2))
        return 0
    except Exception as error:
        print_error(error)
        return 1
    finally:
        if proposer is not None:
            proposer.close()
        if configured is not None:
            configured.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
