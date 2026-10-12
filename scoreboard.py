"""Read per-iteration, per-set healing scores as plain text or JSON."""

import argparse
import json
from pathlib import Path

from config import print_error
from db.connection import connect
from heal import MAX_ACTIVE_RULES, active_rules
from replay import EVAL_SETS


def report(db, *, heal_id=None):
    if heal_id is None:
        latest = db.runs.find_one({"step": "heal_iteration"}, {"run_id": 1}, sort=[("ts", -1)])
        heal_id = latest["run_id"] if latest else None
    rows = list(db.runs.find({"run_id": heal_id, "step": "heal_iteration"}).sort("iteration", 1)) if heal_id else []
    if heal_id and not rows:
        raise ValueError("No scoreboard history for that healing session")
    iterations = [{"iteration": row["iteration"], "candidate_count": row["inputs"]["candidate_count"],
                   **row["outputs"]} for row in rows]
    return {"heal_id": heal_id, "status": iterations[-1]["status"] if iterations else "no_history",
            "iterations": iterations,
            "active_rules": [{"rule_id": rule["_id"], "rule": rule["rule"]} for rule in active_rules(db)]}


def artifact_report(path):
    """Read a saved completed experiment without contacting MongoDB or APIs."""
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1 or data.get("status") != "complete":
        raise ValueError("Scoreboard requires a completed version-1 experiment artifact")
    return {"heal_id": data["heal"]["heal_id"], "status": data["heal"]["status"],
            "iterations": [{key: row[key] for key in
                            ("iteration", "candidate_count", "counts", "sets", "status", "active_rules")}
                           for row in data["heal_iterations"]],
            "active_rules": [{key: row[key] for key in ("rule_id", "rule")} for row in data["active_rules"]]}


def _field_name(field):
    names = {"intent.approved_bank_account": "bank account at approval",
             "intent.approved_fence_version": "fence version at approval",
             "vendor.bank_account": "current vendor bank account"}
    if field in names:
        return names[field]
    root, suffix = field.rsplit(".", 1)
    root = {"evidence.verbatim_terms": "verbatim", "evidence.cleaned_terms": "cleaned",
            "intent.confirmed_terms": "approved", "evidence.recognizer_a_terms": "recognizer A",
            "evidence.recognizer_b_terms": "recognizer B"}[root]
    suffix = {"vendor_id": "vendor", "amount_cents": "amount in cents",
              "destination_account": "destination", "date": "date"}[suffix]
    return f"{root} {suffix}"


def render(data):
    lines = [f"Healing: {data['heal_id'] or 'no history'}", f"Status: {data['status']}",
             "iteration  set          N  ok  unsafe_commit  benign_block  duplicate"]
    for iteration in data["iterations"]:
        for name in EVAL_SETS:
            counts = iteration["sets"][name]
            lines.append(f"{iteration['iteration']:>9}  {name:<11}  {counts['N']:>1}  {counts['ok']:>2}  "
                         f"{counts['unsafe_commit']:>13}  {counts['benign_block']:>12}  {counts['duplicate']:>9}")
    lines.append(f"Active rules ({len(data['active_rules'])}/{MAX_ACTIVE_RULES}):")
    if not data["active_rules"]:
        lines.append("  None; payments are allowed by default.")
    for document in data["active_rules"]:
        rule = document["rule"]
        clauses = [f"{_field_name(clause['left'])} { {'eq': 'equals', 'neq': 'differs from', 'suffix_match': 'matches the suffix of'}[clause['cmp']]} "
                   f"{_field_name(clause['right'])}" for clause in rule["clauses"]]
        action = "request clarification" if rule["on_fail"] == "clarify" else "request reapproval"
        lines.append(f"  {document['rule_id']}: require {' and '.join(clauses)}; otherwise {action}.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--heal-id", help="Choose a healing session; default is latest")
    source.add_argument("--artifact", type=Path, help="Read a saved experiment without database or API access")
    parser.add_argument("--json", action="store_true", help="Structured output for a UI")
    args = parser.parse_args(argv)
    db = None
    try:
        if args.artifact:
            result = artifact_report(args.artifact)
        else:
            db = connect()
            result = report(db, heal_id=args.heal_id)
        print(json.dumps(result, indent=2) if args.json else render(result))
        return 0
    except Exception as error:
        print_error(error)
        return 1
    finally:
        if db is not None:
            db.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
