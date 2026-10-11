"""Pluggable rule evaluation; the prototype hook allows every rule."""

from typing import Callable

Evaluator = Callable[[dict, dict], str | None]


def evaluate(rule: dict, context: dict) -> str | None:
    """Return None to allow or a non-empty reason to block.

    Real rule implementations come later. Hooks must be deterministic and free of
    external side effects because MongoDB can retry the transaction callback.
    """
    return None
