"""Strict collection shapes. BSON doubles, decimals, and booleans are not cents."""

STRING = {"bsonType": "string", "minLength": 1}
INTEGER = {"bsonType": ["int", "long"], "minimum": 0}
CENTS = {"bsonType": ["int", "long"], "minimum": 1, "maximum": 2**63 - 1}
TIMESTAMP = {"bsonType": "date"}
DATE = {"bsonType": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
TERMS = {
    "vendor_id": STRING,
    "amount_cents": CENTS,
    "destination_account": STRING,
    "date": DATE,
}
TERM_OBJECT = {
    "bsonType": "object", "required": list(TERMS),
    "additionalProperties": False, "properties": TERMS,
}
OPTIONAL_TERMS = {"anyOf": [TERM_OBJECT, {"bsonType": "null"}]}


def schema(required: list[str], properties: dict) -> dict:
    return {"$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", *required],
        "additionalProperties": False,
        "properties": {"_id": STRING, **properties},
    }}


VALIDATORS = {
    "vendors": schema(["name", "bank_account"], {
        "name": STRING, "bank_account": STRING, "updated_at": TIMESTAMP,
    }),
    "fences": schema(["version"], {"version": INTEGER}),
    "evidence": schema([
        "run_id", "audio_path", "verbatim_text", "cleaned_text",
        "verbatim_terms", "cleaned_terms", "created_at",
    ], {
        "run_id": STRING, "audio_path": STRING,
        "verbatim_text": {"bsonType": "string"}, "cleaned_text": {"bsonType": "string"},
        "verbatim_terms": OPTIONAL_TERMS, "cleaned_terms": OPTIONAL_TERMS, "created_at": TIMESTAMP,
        "audio_hash": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"}, "adapter_key": STRING,
    }),
    "intents": schema([*TERMS, "run_id", "evidence_id", "status", "created_at"], {
        **TERMS, "run_id": STRING, "evidence_id": STRING,
        "status": {"enum": ["draft", "confirmed"]}, "created_at": TIMESTAMP,
        "confirmed_terms": TERM_OBJECT, "confirmed_at": TIMESTAMP,
        "approved_bank_account": STRING, "approved_fence_version": INTEGER,
        "confirmation_source": {"enum": ["verbatim", "cleaned", "manual"]},
    }),
    "queue": schema(["intent_id", "vendor_id", "run_id", "status", "created_at"], {
        "intent_id": STRING, "vendor_id": STRING, "run_id": STRING,
        "status": {"enum": ["pending", "claimed", "done", "blocked"]},
        "created_at": TIMESTAMP, "claim_token": STRING, "claimed_at": TIMESTAMP,
        "completed_at": TIMESTAMP, "reason": STRING, "payment_id": STRING,
    }),
    "payments": schema([
        "idempotency_key", "queue_id", "run_id", *TERMS, "vendor_account", "paid_to",
        "fence_version", "rule_version_ids", "created_at",
    ], {
        **TERMS, "idempotency_key": STRING, "queue_id": STRING, "run_id": STRING,
        "vendor_account": STRING, "paid_to": STRING, "fence_version": INTEGER,
        "rule_version_ids": {"bsonType": "array", "items": STRING},
        "created_at": TIMESTAMP,
    }),
    "rule_versions": schema(["version", "active", "rule"], {
        "version": INTEGER, "active": {"bsonType": "bool"},
        "rule": {"bsonType": "object"}, "created_at": TIMESTAMP,
    }),
    "runs": {"$jsonSchema": {
        "bsonType": "object", "additionalProperties": False,
        "required": ["_id", "run_id", "step", "inputs", "outputs", "ts"],
        "properties": {
            "_id": {"bsonType": "objectId"}, "run_id": STRING, "step": STRING,
            "inputs": {"bsonType": "object"}, "outputs": {"bsonType": "object"},
            "ts": TIMESTAMP,
        },
    }},
}

# The evidence collection also owns partial cache records. Per-run evidence is
# always complete; cached modes are filled after each successful external call.
RUN_EVIDENCE_SCHEMA = VALIDATORS["evidence"]["$jsonSchema"]
CACHE_EVIDENCE_SCHEMA = schema(["kind", "audio_hash", "adapter_key", "created_at"], {
    "kind": {"enum": ["cache"]}, "audio_hash": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"},
    "adapter_key": STRING, "created_at": TIMESTAMP,
    "verbatim_text": {"bsonType": "string"}, "cleaned_text": {"bsonType": "string"},
    "verbatim_terms": OPTIONAL_TERMS, "cleaned_terms": OPTIONAL_TERMS,
})["$jsonSchema"]
VALIDATORS["evidence"] = {"$jsonSchema": {"oneOf": [RUN_EVIDENCE_SCHEMA, CACHE_EVIDENCE_SCHEMA]}}

CONFIRMATION_FIELDS = [
    "confirmed_terms", "confirmed_at", "approved_bank_account", "approved_fence_version", "confirmation_source",
]
VALIDATORS["intents"]["$jsonSchema"]["oneOf"] = [
    {"properties": {"status": {"enum": ["draft"]}},
     "not": {"anyOf": [{"required": [field]} for field in CONFIRMATION_FIELDS]}},
    {"properties": {"status": {"enum": ["confirmed"]}}, "required": CONFIRMATION_FIELDS},
]
