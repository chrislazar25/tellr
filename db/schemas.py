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
    "evidence": schema(["run_id", "audio_path", "verbatim", "text", "created_at"], {
        "run_id": STRING, "audio_path": STRING, "verbatim": {"bsonType": "bool"},
        "text": STRING, "created_at": TIMESTAMP,
    }),
    "intents": schema([*TERMS, "run_id", "evidence_id", "created_at"], {
        **TERMS, "run_id": STRING, "evidence_id": STRING, "created_at": TIMESTAMP,
    }),
    "queue": schema(["intent_id", "vendor_id", "run_id", "status", "created_at"], {
        "intent_id": STRING, "vendor_id": STRING, "run_id": STRING,
        "status": {"enum": ["pending", "claimed", "done", "blocked"]},
        "created_at": TIMESTAMP, "claim_token": STRING, "claimed_at": TIMESTAMP,
        "completed_at": TIMESTAMP, "reason": STRING, "payment_id": STRING,
    }),
    "payments": schema([
        "idempotency_key", "queue_id", "run_id", *TERMS, "vendor_account",
        "fence_version", "rule_version_ids", "created_at",
    ], {
        **TERMS, "idempotency_key": STRING, "queue_id": STRING, "run_id": STRING,
        "vendor_account": STRING, "fence_version": INTEGER,
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
