"""Persist each successful live API result before moving to the next step."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from interfaces import validate_terms
from observability import log_step


def collect_candidates(db, run_id, audio_path, adapters):
    texts, candidates = {}, {}
    metadata, cached, cached_texts, cache_id, text_cache_id = {}, {}, {}, None, None
    audio = audio_path
    if adapters.cache_key:
        # Read once: both requests and the SHA-256 refer to identical bytes even
        # if the source file changes while the pipeline is running.
        audio = Path(audio_path).read_bytes()
        audio_hash = hashlib.sha256(audio).hexdigest()
        metadata = {"audio_hash": audio_hash, "adapter_key": adapters.cache_key}
        cache_id = f"audio:{audio_hash}:{adapters.cache_key}"
        transcription_key = adapters.transcription_key or adapters.cache_key
        text_cache_id = f"audio:{audio_hash}:{transcription_key}"
        for key, document_id in ((transcription_key, text_cache_id), (adapters.cache_key, cache_id)):
            db.evidence.update_one({"_id": document_id}, {"$setOnInsert": {
                "kind": "cache", "audio_hash": audio_hash, "adapter_key": key,
                "created_at": datetime.now(timezone.utc),
            }}, upsert=True)
        cached = db.evidence.find_one({"_id": cache_id})
        cached_texts = db.evidence.find_one({"_id": text_cache_id})

    for mode in ("verbatim", "cleaned"):
        text_field, terms_field = f"{mode}_text", f"{mode}_terms"
        hit = text_field in cached_texts
        text = cached_texts[text_field] if hit else adapters.transcribe(audio, no_verbatim=mode == "cleaned")
        if not isinstance(text, str):
            raise ValueError("Transcription must return text")
        texts[mode] = text
        if text_cache_id and not hit:
            db.evidence.update_one({"_id": text_cache_id}, {"$set": {text_field: text}})
        log_step(db, run_id, "transcribe", {"audio_path": str(audio_path), "mode": mode,
                 "no_verbatim": mode == "cleaned", **metadata}, {"text": text, "cache_hit": hit})
        hit = terms_field in cached
        terms = cached[terms_field] if hit else adapters.extract_terms(text)
        terms = None if terms is None else validate_terms(terms)
        candidates[mode] = terms
        if cache_id and not hit:
            db.evidence.update_one({"_id": cache_id}, {"$set": {terms_field: terms}})
        log_step(db, run_id, "extract_terms", {"text": text, "mode": mode}, {"terms": terms, "cache_hit": hit})
    return texts, candidates, metadata
