"""Evaluation-only fixture expectations and scenario definitions."""

import json
from importlib.resources import files


def fixture_cases() -> list[dict]:
    data = json.loads(files("fixtures").joinpath("pipeline.json").read_text())
    recordings = {case["case_id"]: case for case in data["recordings"]}
    return [*recordings.values(), *[
        {**recordings[scenario["recording_case_id"]], **scenario}
        for scenario in data.get("scenarios", [])
    ]]
