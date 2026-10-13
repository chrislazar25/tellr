"""Evaluate a saved policy on sealed inputs without proposing or selecting rules."""
import argparse
import hashlib
import json
import time
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from config import print_error
from db.accounts import destination_matches
from db.connection import connect
from db.seed import SAMPLE_VENDORS, seed
from experiments.failure_hunt import keys
from experiments.failure_hunt.compare import AGREEMENT, metrics
from experiments.failure_hunt.sealed_score import ROOT, score
from replay import replay_suite
from rules import validate_rule
from scripts.measure_audio import now, write_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def frozen_policy(path):
    """Capture expressions from a completed artifact, never from mutable DB state."""
    path = Path(path)
    raw = path.read_bytes()
    artifact = json.loads(raw)
    if artifact.get("status") != "complete" or not artifact.get("active_rules"):
        raise ValueError("Frozen policy requires a completed artifact with saved active rules")
    expressions = [validate_rule(deepcopy(row["rule"])) for row in artifact["active_rules"]]
    return {"source_file": path.name, "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_experiment_id": artifact["experiment_id"], "source_finished_at": artifact["finished_at"],
            "rules_sha256": digest(expressions), "rules": expressions,
            "source_rule_ids": [row["rule_id"] for row in artifact["active_rules"]]}


def extraction_issues(terms, truth):
    """Describe errors without equating garbled identifiers with wrong digits."""
    if terms is None:
        return ["missing_instruction"]
    issues = []
    for field, issue in (("vendor_id", "vendor_error"), ("amount_cents", "amount_error"), ("date", "date_error")):
        if terms[field] != truth[field]:
            issues.append(issue)
    account = next(v["bank_account"] for v in SAMPLE_VENDORS if v["_id"] == truth["vendor_id"])
    destination = terms["destination_account"]
    agrees_with_truth = destination == truth["destination_account"] or (
        destination_matches(truth["destination_account"], account) and destination_matches(destination, account)
    )
    if not agrees_with_truth:
        if destination.isascii() and destination.isdigit():
            issues.append("wrong_numeric_destination")
        elif destination in {v["bank_account"] for v in SAMPLE_VENDORS}:
            issues.append("destination_identifier_mismatch")
        else:
            issues.append("garbled_destination")
    return issues


def read_sealed(root=ROOT, *, prefix="sealed"):
    """Read one explicitly selected sealed manifest, without audio discovery."""
    if prefix not in {"sealed", "sealed2"}:
        raise ValueError("Unknown sealed dataset prefix")
    root = Path(root)
    repo = root.parents[1]
    attempts = [json.loads(line) for line in (root / f"{prefix}_attempts.jsonl").read_text().splitlines()]
    scored = score(root, prefix=prefix)
    manifest = json.loads((root / f"{prefix}_manifest.json").read_text())
    if len(attempts) != manifest["attempt_count"] or len(manifest["attempts"]) != len(attempts):
        raise ValueError("Sealed manifest coverage mismatch")
    entries = {entry["file"]: entry for entry in manifest["files"]}
    if len(entries) != manifest["file_count"]:
        raise ValueError("Sealed file coverage mismatch")
    for name, entry in entries.items():
        path = repo / name
        if path.resolve().parent != (root / "audio").resolve():
            raise ValueError("Sealed manifest must reference this archive's audio directory")
        raw = path.read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("Sealed audio/cache bytes differ from their frozen manifest")
    preregistration = None
    if prefix == "sealed2":
        path = root / "sealed2_preregistration.md"
        if manifest.get("preregistration_file") != path.relative_to(repo).as_posix():
            raise ValueError("Sealed preregistration link differs")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest.get("preregistration_sha256"):
            raise ValueError("Sealed preregistration differs from its pinned bytes")
        preregistration = {"file": manifest["preregistration_file"], "sha256": manifest["preregistration_sha256"], "text": raw.decode()}
        if manifest["voice_id"] not in preregistration["text"]:
            raise ValueError("Sealed voice differs from its preregistration")
    cases, records, exclusions, errors = [], [], [], []
    for index, (attempt, row, link) in enumerate(zip(attempts, scored, manifest["attempts"])):
        if attempt["voice"] != manifest["voice_id"]:
            raise ValueError("A sealed set must use its declared voice")
        if any(link[key] != attempt[key] for key in ("tts_file", "channel_file")):
            raise ValueError("Sealed attempt audio link differs")
        for field, recognizer in (("scribe_cache", "scribe"), ("whisper_cache", "whisper_small")):
            if link[field] not in entries or json.loads((repo / link[field]).read_text())["text"] != attempt["results"][recognizer]["text"]:
                raise ValueError("Sealed cached transcript link differs")
        vendors = [v for v in SAMPLE_VENDORS if v["name"].lower() in attempt["text"].lower()]
        if len(vendors) != 1:
            raise ValueError("Sealed truth requires one independently labeled vendor")
        vendor = vendors[0]
        cents = Decimal(str(attempt["truth"]["amount"])) * 100
        if cents != cents.to_integral_value():
            raise ValueError("Truth must use integer cents")
        truth = {"vendor_id": vendor["_id"], "amount_cents": int(cents),
                 "destination_account": attempt["truth"]["acct"], "date": "2026-10-11"}
        a, b = deepcopy(row["terms_a"]), deepcopy(row["terms_b"])
        on_file = destination_matches(truth["destination_account"], vendor["bank_account"])
        issues = extraction_issues(a, truth)
        differs = a is not None and bool(issues)
        record = {"case_id": f"sealed-{manifest['voice'].lower()}-{index + 1:03d}-{attempt['case']}", "attempt_index": index,
                  "voice_name": manifest["voice"], "voice_id": attempt["voice"], "prompt": attempt["text"],
                  "speed": attempt["speed"], "channel": attempt["channel"], "audio_file": link["channel_file"],
                  "tts_file": link["tts_file"], "audio_sha256": entries[link["channel_file"]]["sha256"],
                  "recognizer_a_text": row["scribe"], "recognizer_b_text": row["whisper"],
                  "recognizer_a_terms": a, "recognizer_b_terms": b, "true_terms": truth,
                  "expected": "clarify" if a is None or differs or not on_file else "pay",
                  "eval_set": "sealed" if on_file else "excluded", "extraction_issues": issues}
        if on_file:
            cases.append({**record, "verbatim_text": row["scribe"], "cleaned_text": row["scribe"],
                          "verbatim_terms": a, "cleaned_terms": a, "eval_set": "holdout"})
            if differs:
                errors.append({"case_id": record["case_id"], "true_terms": truth, "observed_terms": a, "extraction_issues": issues})
        else:
            record["disposition"] = "spoken_account_not_on_file"
            exclusions.append(record)
        records.append(record)
    return {"cases": cases, "payment_attempts": records, "excluded": exclusions, "scribe_errors": errors,
            "voice": manifest["voice"], "preregistration": preregistration, "input_hashes": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in (f"{prefix}_attempts.jsonl", f"{prefix}_scored.json", f"{prefix}_manifest.json",
                             *(["sealed2_preregistration.md"] if preregistration else []))}}


def evaluate_sealed(client, policy, output, *, data=None, backend="configured_mongodb"):
    """Read-only policy snapshot; scratch transactions only, no gate or activation."""
    data = read_sealed() if data is None else deepcopy(data)
    policy = deepcopy(policy)
    if digest(policy["rules"]) != policy["rules_sha256"]:
        raise ValueError("Frozen rule fingerprint mismatch")
    for rule in policy["rules"]:
        validate_rule(rule)
    started = time.perf_counter()
    artifact = {"schema_version": 1, "status": "running", "evaluation_type": "sealed_frozen_policy",
                "started_at": now(), "database_backend": backend, "voice": data["voice"],
                "policy": policy, "selection_performed": False, "approval_source": "recognizer_a",
                "coverage": {"payment_attempts": len(data["payment_attempts"]), "sealed_eval_cases": len(data["cases"]),
                             "excluded_spoken_account_not_on_file": len(data["excluded"]),
                             "intake_clarifications": sum(c["recognizer_a_terms"] is None for c in data["cases"])},
                "payment_attempts": data["payment_attempts"], "excluded": data["excluded"],
                "scribe_errors": data["scribe_errors"], "input_hashes": data["input_hashes"],
                "api_calls": [], "comparators": {}}
    if data.get("preregistration"):
        artifact["preregistration"] = deepcopy(data["preregistration"])
    baseline = None
    for name, expressions in (("empty_rules", []), ("recognizer_agreement", [AGREEMENT]), ("frozen_learned", policy["rules"])):
        progress = []
        def on_case(row):
            progress.append(row)
            if len(progress) % 10 == 0 or len(progress) == len(data["cases"]):
                print(f"{name}: {len(progress)}/{len(data['cases'])}", flush=True)
        documents = [{"_id": f"frozen:{index}", "version": 1, "rule": deepcopy(rule)} for index, rule in enumerate(expressions)]
        observed = replay_suite(client, documents, sets=("holdout",), cases=data["cases"], on_case=on_case)
        # Internal replay routing reuses the isolated worker path; exported outcomes
        # are sealed, never a holdout used for candidate gating.
        observed["sets"] = {"sealed": observed["sets"]["holdout"]}
        for row in observed["cases"]:
            row["eval_set"] = "sealed"
        if baseline is None:
            baseline = observed
        artifact["comparators"][name] = {"rules": deepcopy(expressions), "results": observed, "metrics": metrics(baseline, observed)}
        write_json(output, artifact)
    for record in artifact["payment_attempts"]:
        record["outcomes"] = {name: next((deepcopy(row) for row in value["results"]["cases"] if row["case_id"] == record["case_id"]),
                                 {"status": "excluded", "expected": "clarify", "reason": "spoken_account_not_on_file"})
                              for name, value in artifact["comparators"].items()}
    artifact.update(status="complete", finished_at=now(), wall_seconds=round(time.perf_counter() - started, 6))
    write_json(output, artifact)
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", choices=("bill", "lily"), default="bill", help="Choose one sealed set explicitly")
    parser.add_argument("--policy-artifact", type=Path, default=Path("artifacts/failure_hunt_experiment.json"))
    parser.add_argument("--output", type=Path, help="Default: artifacts/sealed_<set>_experiment.json")
    args = parser.parse_args(argv)
    output = args.output or Path(f"artifacts/sealed_{args.set}_experiment.json")
    configured = None
    try:
        policy, data = frozen_policy(args.policy_artifact), read_sealed(prefix="sealed2" if args.set == "lily" else "sealed")
        uri = keys.get("MONGODB_URI")
        if not uri.startswith("mongodb+srv://"):
            raise ValueError("Sealed live evaluation requires the configured Atlas SRV connection")
        configured = connect(uri)
        seed(configured)
        artifact = evaluate_sealed(configured.client, policy, output, data=data, backend="atlas")
        print(json.dumps({name: value["results"]["counts"] for name, value in artifact["comparators"].items()}, indent=2))
        return 0
    except Exception as error:
        print_error(error)
        return 1
    finally:
        if configured is not None:
            configured.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
