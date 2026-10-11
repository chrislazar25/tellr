"""Fixed-vocabulary conjunctions; deterministic and free of side effects."""

from typing import Callable

Evaluator = Callable[[dict, dict], str | None]
TERM_FIELDS = ("vendor_id", "amount_cents", "destination_account", "date")
VOCABULARY = tuple(
    f"{root}.{field}"
    for root in ("evidence.verbatim_terms", "evidence.cleaned_terms", "intent.confirmed_terms")
    for field in TERM_FIELDS
) + ("intent.approved_bank_account", "intent.approved_fence_version", "vendor.bank_account")

RULE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["clauses", "on_fail"],
    "properties": {
        "clauses": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
            "type": "object", "additionalProperties": False, "required": ["left", "cmp", "right"],
            "properties": {"left": {"type": "string", "enum": list(VOCABULARY)},
                           "cmp": {"type": "string", "enum": ["eq", "neq"]},
                           "right": {"type": "string", "enum": list(VOCABULARY)}},
        }},
        "on_fail": {"type": "string", "enum": ["clarify", "reapprove"]},
    },
}


def validate_rule(rule: dict) -> dict:
    if not isinstance(rule, dict) or set(rule) != {"clauses", "on_fail"}:
        raise ValueError("Rule must contain exactly clauses and on_fail")
    if rule["on_fail"] not in {"clarify", "reapprove"}:
        raise ValueError("Rule on_fail must be clarify or reapprove")
    clauses = rule["clauses"]
    if not isinstance(clauses, list) or not 1 <= len(clauses) <= 16:
        raise ValueError("Rule must have 1 to 16 clauses")
    result = []
    for clause in clauses:
        if not isinstance(clause, dict) or set(clause) != {"left", "cmp", "right"}:
            raise ValueError("Clause must contain exactly left, cmp, and right")
        if clause["left"] not in VOCABULARY or clause["right"] not in VOCABULARY:
            raise ValueError("Clause field is outside the fixed vocabulary")
        if clause["cmp"] not in {"eq", "neq"}:
            raise ValueError("Clause cmp must be eq or neq")
        result.append(dict(clause))
    return {"clauses": result, "on_fail": rule["on_fail"]}


_MISSING = object()


def field_value(context: dict, field: str):
    if field not in VOCABULARY:
        raise ValueError("Field is outside the fixed vocabulary")
    value = context
    for component in field.split("."):
        if not isinstance(value, dict) or component not in value:
            return _MISSING
        value = value[component]
    return value


def evaluate(rule: dict, context: dict) -> str | None:
    """All clauses must pass; missing/null fields fail even for neq comparisons."""
    predicate = validate_rule(rule.get("rule", rule))
    for clause in predicate["clauses"]:
        left, right = (field_value(context, clause[side]) for side in ("left", "right"))
        if left is _MISSING or right is _MISSING or left is None or right is None:
            return predicate["on_fail"]
        same_type = (isinstance(left, str) and isinstance(right, str)) or (
            isinstance(left, int) and not isinstance(left, bool)
            and isinstance(right, int) and not isinstance(right, bool)
        )
        equal = same_type and left == right
        if (clause["cmp"] == "eq" and not equal) or (clause["cmp"] == "neq" and equal):
            return predicate["on_fail"]
    return None


def bson_rule_schema() -> dict:
    def convert(value):
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {("bsonType" if key == "type" else key): convert(item) for key, item in value.items()}
    return convert(RULE_SCHEMA)
