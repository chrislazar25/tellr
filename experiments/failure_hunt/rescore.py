"""Re-score supplied transcripts; cached-only unless --live is explicit."""
import argparse
import hashlib
import json
from pathlib import Path

from config import safe_exception
from db.seed import SAMPLE_VENDORS
from experiments.failure_hunt import keys
from interfaces import validate_terms

ROOT = Path(__file__).resolve().parent


def score(root=ROOT, *, live=False):
    root = Path(root)
    cache_path = root / "extract_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    out, client, extractor = [], None, None
    try:
        for line in (root / "attempts.jsonl").read_text().splitlines():
            attempt = json.loads(line)
            for recognizer, result in attempt["results"].items():
                text = result["text"]
                key = hashlib.sha256(text.encode()).hexdigest()
                if key not in cache:
                    if not live:
                        raise ValueError("Missing extraction cache; use --live to authorize a new extraction")
                    if extractor is None:
                        from anthropic import Anthropic
                        from live_adapters import ClaudeExtractor
                        client = Anthropic(api_key=keys.get("ANTHROPIC_API_KEY"))
                        extractor = ClaudeExtractor(client, model="claude-sonnet-4-6",
                            vendors=[{k: v[k] for k in ("_id", "name")} for v in SAMPLE_VENDORS], reference_date="2026-10-11")
                    try:
                        cache[key] = {"terms": extractor(text)}
                    except Exception as error:
                        cache[key] = {"error": safe_exception(error)}
                    cache_path.write_text(json.dumps(cache))
                cached = cache[key]
                terms = cached.get("terms")
                issues = []
                if cached.get("error"):
                    issues.append("extract_error")
                elif terms is None:
                    issues.append("no_terms")
                else:
                    terms = validate_terms(terms)
                    if terms["amount_cents"] != round(attempt["truth"]["amount"] * 100):
                        issues.append(f"amount {terms['amount_cents']/100:g}≠{attempt['truth']['amount']}")
                    if terms["destination_account"] != attempt["truth"]["acct"]:
                        issues.append(f"acct {terms['destination_account']}≠{attempt['truth']['acct']}")
                out.append({**{k: attempt[k] for k in ("case", "voice", "speed", "channel")},
                            "recognizer": recognizer, "text": text, "terms": terms, "issues": issues})
        return out
    finally:
        if client is not None:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    rows = score(live=args.live)
    (ROOT / "scored.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(f"{len(rows)} transcripts scored; {sum(bool(r['issues']) for r in rows)} have extraction issues")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        from config import print_error
        print_error(error)
        raise SystemExit(1)
