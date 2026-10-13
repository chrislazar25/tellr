"""Inspect frozen Bill extractions; new Sonnet calls require explicit --live."""
import argparse
import json
from pathlib import Path

from db.accounts import destination_matches
from db.seed import SAMPLE_VENDORS
from experiments.failure_hunt import keys
from interfaces import validate_terms

ROOT = Path(__file__).resolve().parent


def score(root=ROOT, *, live=False):
    root = Path(root)
    attempts = [json.loads(line) for line in (root / "sealed_attempts.jsonl").read_text().splitlines()]
    path = root / "sealed_scored.json"
    cached = json.loads(path.read_text()) if path.exists() else []
    if cached and len(cached) != len(attempts):
        raise ValueError("Sealed extraction coverage differs from logged attempts")
    if not cached and not live:
        raise ValueError("Missing sealed extractions; use --live for new Sonnet calls")
    client = None
    try:
        if not cached:
            from anthropic import Anthropic
            from live_adapters import ClaudeExtractor
            client = Anthropic(api_key=keys.get("ANTHROPIC_API_KEY"))
            extractor = ClaudeExtractor(client, model="claude-sonnet-4-6",
                vendors=[{k: v[k] for k in ("_id", "name")} for v in SAMPLE_VENDORS], reference_date="2026-10-11")
        accounts = {v["_id"]: v["bank_account"] for v in SAMPLE_VENDORS}
        rows = []
        for index, attempt in enumerate(attempts):
            a_text, b_text = (attempt["results"][name]["text"] for name in ("scribe", "whisper_small"))
            if cached:
                row = cached[index]
                if any(row[key] != attempt[key] for key in ("case", "speed", "channel")) or (row["scribe"], row["whisper"]) != (a_text, b_text):
                    raise ValueError("Sealed extraction does not match its logged transcript")
                for key in ("terms_a", "terms_b"):
                    if row[key] is not None:
                        validate_terms(row[key])
                rows.append(row)
                continue
            a, b = extractor(a_text), extractor(b_text)
            rows.append({"case": attempt["case"], "speed": attempt["speed"], "channel": attempt["channel"],
                "scribe": a_text, "whisper": b_text, "terms_a": a, "terms_b": b,
                "off_file": attempt["case"] == "eighteen13",
                "a_wrong": a is not None and (a["amount_cents"] != round(attempt["truth"]["amount"] * 100) or a["destination_account"] != attempt["truth"]["acct"]),
                "rule_allows": a is not None and destination_matches(a["destination_account"], accounts[a["vendor_id"]]),
                "agreement_allows": a is not None and b is not None and all(a[field] == b[field] for field in ("amount_cents", "destination_account"))})
        return rows
    finally:
        if client is not None:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    rows = score(live=args.live)
    if args.live:
        (ROOT / "sealed_scored.json").write_text(json.dumps(rows, indent=1) + "\n")
    pay = [row for row in rows if row["terms_a"] is not None and not row["off_file"]]
    good = [row for row in pay if not row["a_wrong"]]
    print(f"Cached diagnostic only: {len(rows)} clips, {len(pay)} on-file payments, {len(pay) - len(good)} Scribe errors")
    for label, key in (("Frozen account rule", "rule_allows"), ("A/B agreement", "agreement_allows")):
        print(f"{label}: {sum(not row[key] for row in good)}/{len(good)} correct blocked")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        from config import print_error
        print_error(error)
        raise SystemExit(1)
