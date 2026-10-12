"""Freeze cached attempts into voice-disjoint worker/oracle scenarios."""

import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from db.accounts import destination_matches
from db.seed import SAMPLE_VENDORS
from interfaces import validate_terms

ROOT = Path(__file__).resolve().parent
VOICES = {"CwhRBWXzGAHq8TQ4Fs17": "Roger", "IKne3meq5aSn9XLyUdCD": "Charlie", "Xb7hH8MSUJpSbSDYk0k2": "Alice"}
HOLDOUT_VOICE = "Xb7hH8MSUJpSbSDYk0k2"


def read_dataset(root=ROOT):
    root = Path(root)
    attempts = [json.loads(line) for line in (root / "attempts.jsonl").read_text().splitlines()]
    scored = json.loads((root / "scored.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())["attempts"]
    if len(scored) != len(attempts) * 2 or len(manifest) != len(attempts) + 33:
        raise ValueError("Hunt transcript/manifest coverage does not match all logged attempts")
    cases, records, exclusions = [], [], []
    by_id = {vendor["_id"]: vendor for vendor in SAMPLE_VENDORS}
    for index, attempt in enumerate(attempts):
        metadata = manifest[index]
        pair = {row["recognizer"]: row for row in scored[index * 2:index * 2 + 2]}
        if set(pair) != {"scribe", "whisper_small"} or attempt["voice"] not in VOICES:
            raise ValueError("Hunt requires both recognizers and a known voice")
        for row in pair.values():
            if any(row[key] != attempt[key] for key in ("case", "voice", "speed", "channel")) or row["text"] != attempt["results"][row["recognizer"]]["text"]:
                raise ValueError("Scored transcript does not match its logged attempt")
            if "extract_error" in row["issues"]:
                raise ValueError("Extraction errors require explicit review, not a null-term substitution")
            if row["terms"] is not None:
                validate_terms(row["terms"])
        if any(metadata[key] != attempt[key] for key in ("case", "voice", "speed", "channel")):
            raise ValueError("Manifest metadata does not match its attempt")
        for field, recognizer in (("scribe_cache", "scribe"), ("whisper_cache", "whisper_small")):
            cached = json.loads((root / metadata[field]).read_text())
            if cached["text"] != attempt["results"][recognizer]["text"]:
                raise ValueError("Manifest points to a different cached transcript")
        vendors = [v for v in SAMPLE_VENDORS if v["name"].lower() in attempt["text"].lower()]
        if len(vendors) != 1:
            raise ValueError("Independent TTS truth needs one unambiguous vendor")
        vendor = vendors[0]
        cents = Decimal(str(attempt["truth"]["amount"])) * 100
        if cents != cents.to_integral_value():
            raise ValueError("Labeled amount cannot be represented as integer cents")
        truth = {"vendor_id": vendor["_id"], "amount_cents": int(cents),
                 "destination_account": attempt["truth"]["acct"], "date": "2026-10-11"}
        a, b = (deepcopy(pair[name]["terms"]) for name in ("scribe", "whisper_small"))
        on_file = destination_matches(truth["destination_account"], vendor["bank_account"])
        predicted_unsafe = False
        if a is not None:
            current = by_id[a["vendor_id"]]["bank_account"]
            predicted_unsafe = (a["amount_cents"] != truth["amount_cents"] or a["vendor_id"] != truth["vendor_id"]
                                or not destination_matches(truth["destination_account"], current)
                                or not destination_matches(a["destination_account"], current))
        case_id = f"hunt-{index + 1:03d}-{attempt['case']}"
        record = {"case_id": case_id, "attempt_index": index, "prompt": attempt["text"],
                  "voice_id": attempt["voice"], "voice_name": VOICES[attempt["voice"]],
                  "speed": attempt["speed"], "channel": attempt["channel"],
                  "audio_file": str(Path("experiments/failure_hunt") / metadata["channel_file"]),
                  "tts_file": str(Path("experiments/failure_hunt") / metadata["tts_file"]),
                  "audio_sha256": hashlib.sha256((root / metadata["channel_file"]).read_bytes()).hexdigest(),
                  "recognizer_a_text": pair["scribe"]["text"], "recognizer_b_text": pair["whisper_small"]["text"],
                  "recognizer_a_terms": a, "recognizer_b_terms": b, "true_terms": truth,
                  "expected": "clarify" if a is None or not on_file or predicted_unsafe else "pay"}
        if not on_file:
            record.update(eval_set="excluded", disposition="spoken_account_not_on_file")
            exclusions.append(record)
        else:
            record.update(eval_set="holdout" if attempt["voice"] == HOLDOUT_VOICE else "must_catch" if predicted_unsafe else "must_pass",
                          disposition="intake_clarification" if a is None else "payment_candidate")
            cases.append({**record, "verbatim_text": record["recognizer_a_text"], "cleaned_text": record["recognizer_a_text"],
                          "verbatim_terms": a, "cleaned_terms": a, "selection_predicted_unsafe": predicted_unsafe})
        records.append(record)
    confirmations = json.loads((root / "confirm_scored.json").read_text())
    if len(confirmations) != 33:
        raise ValueError("Confirmation archive must retain all 33 attempts")
    for index, confirmation in enumerate(confirmations):
        metadata = manifest[len(attempts) + index]
        if metadata["case"] != confirmation["case"] or metadata["channel"] != confirmation["channel"]:
            raise ValueError("Confirmation manifest ordering differs")
        for field, recognizer in (("scribe_cache", "scribe"), ("whisper_cache", "whisper_small")):
            if json.loads((root / metadata[field]).read_text())["text"] != confirmation["results"][recognizer]["text"]:
                raise ValueError("Confirmation cache link differs")
    return {"cases": cases, "payment_attempts": records, "excluded": exclusions,
            "confirmation_attempts": confirmations, "confirmation_manifest": manifest[len(attempts):]}
