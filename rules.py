"""Fixed-vocabulary conjunctions; deterministic and free of side effects."""

from typing import Callable
from db.accounts import destination_matches

Evaluator = Callable[[dict, dict], str | None]
TERM_FIELDS = ("vendor_id", "amount_cents", "destination_account", "date")
VOCABULARY = tuple(
    f"{root}.{field}"
    for root in ("evidence.verbatim_terms", "evidence.cleaned_terms", "intent.confirmed_terms",
                 "evidence.recognizer_a_terms", "evidence.recognizer_b_terms")
    for field in TERM_FIELDS
) + ("intent.approved_bank_account", "intent.approved_fence_version", "vendor.bank_account")
DESTINATION_FIELDS = tuple(field for field in VOCABULARY if field.endswith(".destination_account"))

def _clause_schema(comparisons, left_fields, right_fields):
    return {"type": "object", "additionalProperties": False, "required": ["left", "cmp", "right"],
            "properties": {"left": {"type": "string", "enum": list(left_fields)},
                           "cmp": {"type": "string", "enum": list(comparisons)},
                           "right": {"type": "string", "enum": list(right_fields)}}}


RULE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["clauses", "on_fail"],
    "properties": {
        "clauses": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"oneOf": [
            _clause_schema(("eq", "neq"), VOCABULARY, VOCABULARY),
            _clause_schema(("suffix_match",), DESTINATION_FIELDS, ("vendor.bank_account",)),
        ]}},
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
        if clause["cmp"] not in {"eq", "neq", "suffix_match"}:
            raise ValueError("Clause cmp must be eq, neq or suffix_match")
        if clause["cmp"] == "suffix_match" and (clause["left"] not in DESTINATION_FIELDS or clause["right"] != "vendor.bank_account"):
            raise ValueError("suffix_match requires a spoken destination on the left and vendor.bank_account on the right")
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
        if clause["cmp"] == "suffix_match":
            if not isinstance(left, str) or not isinstance(right, str) or not left or not right or not destination_matches(left, right):
                return predicate["on_fail"]
            continue
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
