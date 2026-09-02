"""Executing one judge benchmark case.

A case maps 1:1 onto a single judge call: `inputs` are passed as kwargs, so
the YAML file is the complete record of what the judge saw.
"""

from __future__ import annotations

from typing import Any

from blue_data_kb_source.ingestion.judges import (
    judge_class_name,
    judge_description,
    judge_property_placement,
)

from .judge_cases import JudgeCase


async def run_judge_case(case: JudgeCase, model: str) -> Any:
    if case.judge == "placement":
        return await judge_property_placement(model=model, **case.inputs)
    if case.judge == "class_name":
        return await judge_class_name(model=model, **case.inputs)
    return await judge_description(model=model, **case.inputs)
