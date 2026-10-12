"""Classify confirmations from supplied cache; --live permits missing labels."""
import argparse
import json
from pathlib import Path

from experiments.failure_hunt import keys

ROOT = Path(__file__).resolve().parent
SYSTEM = ("You classify a caller's reply to a payment confirmation question in a voice agent. "
          "The agent asked: 'Confirm: pay fifty dollars to Demo Paper Supply, account ending 001, today?' "
          "Return exactly one word: AFFIRM if the reply authorizes the payment, DENY if it refuses, UNCLEAR otherwise.")


def score(root=ROOT, *, live=False):
    root = Path(root)
    cache = {}
    if (root / "confirm_scored.json").exists():
        for row in json.loads((root / "confirm_scored.json").read_text()):
            for value in row["results"].values():
                text, label = value["text"], value["label"]
                if label not in {"AFFIRM", "DENY", "UNCLEAR"} or text in cache and cache[text] != label:
                    raise ValueError("Confirmation cache contains invalid or inconsistent labels")
                cache[text] = label
    rows = [json.loads(line) for line in (root / "confirm_attempts.jsonl").read_text().splitlines()]
    client = None
    try:
        for row in rows:
            for value in row["results"].values():
                text = value["text"]
                if text not in cache:
                    if not live:
                        raise ValueError("Missing confirmation label cache; use --live for new classification")
                    if client is None:
                        from anthropic import Anthropic
                        client = Anthropic(api_key=keys.get("ANTHROPIC_API_KEY"))
                    response = client.messages.create(model="claude-sonnet-4-6", max_tokens=5, system=SYSTEM,
                        messages=[{"role": "user", "content": f"Caller reply transcript: {text!r}"}])
                    label = response.content[0].text.strip().upper()
                    if response.stop_reason != "end_turn" or label not in {"AFFIRM", "DENY", "UNCLEAR"}:
                        raise ValueError("Confirmation classifier returned an incomplete label")
                    cache[text] = label
                value["label"] = cache[text]
        return rows
    finally:
        if client is not None:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    rows = score(live=args.live)
    (ROOT / "confirm_scored.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(f"{len(rows)} confirmation attempts scored")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        from config import print_error
        print_error(error)
        raise SystemExit(1)
