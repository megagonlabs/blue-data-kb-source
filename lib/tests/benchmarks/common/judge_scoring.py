"""Scoring a judge run against its case's gold-standard `expected` block.

All checks are deterministic (no LLM-judge): placement and naming have exact
gold answers, and descriptions get guard checks only — the report table prints
every generated description so a human can eyeball quality, which is the
decided scoring mode for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .judge_cases import JudgeCase

_NAME_CONVENTION = re.compile(r"[A-Z][A-Z0-9_]*")
_DESCRIPTION_MAX_CHARS = 300


@dataclass
class JudgeScore:
    case_id: str
    judge: str
    model: str
    passed: bool
    output: str          # what the judge produced, rendered for the report
    detail: str          # why it passed/failed, one line


def score_case(case: JudgeCase, model: str, output: Any) -> JudgeScore:
    if case.judge == "placement":
        return _score_placement(case, model, output)
    if case.judge == "class_name":
        return _score_class_name(case, model, output)
    return _score_description(case, model, output)


def _score_placement(case: JudgeCase, model: str, chosen_ids: list[str]) -> JudgeScore:
    """`expected.placed_on` is a list of acceptable name-sets; any exact set match passes."""
    by_id = {c["id"]: c["name"] for c in case.inputs["classes"]}
    chosen_names = sorted(by_id[i] for i in chosen_ids)
    acceptable = [sorted(names) for names in case.expected["placed_on"]]
    passed = chosen_names in acceptable
    want = " or ".join("{" + ", ".join(a) + "}" for a in acceptable)
    return JudgeScore(
        case_id=case.id, judge=case.judge, model=model, passed=passed,
        output=", ".join(chosen_names) or "(nothing)",
        detail="matched a gold set" if passed else f"wanted {want}",
    )


def _score_class_name(case: JudgeCase, model: str, name: str) -> JudgeScore:
    """`expected.reuse: NAME` demands that name back; `expected.new: true` demands a
    convention-conforming name that is not one of the existing classes."""
    if "reuse" in case.expected:
        want = case.expected["reuse"]
        passed = name == want
        detail = "reused the right class" if passed else f"wanted {want!r}"
    else:
        existing = {c["name"] for c in case.inputs.get("existing_classes", [])}
        conforms = _NAME_CONVENTION.fullmatch(name) is not None
        passed = conforms and name not in existing
        detail = ("new, convention-conforming name" if passed
                  else "collides with an existing class" if name in existing
                  else "not UPPERCASE_WITH_UNDERSCORES")
    return JudgeScore(case_id=case.id, judge=case.judge, model=model,
                      passed=passed, output=name or "(empty)", detail=detail)


def _score_description(case: JudgeCase, model: str, description: str) -> JudgeScore:
    """Guards only — quality is judged by a human reading the report table."""
    problems = []
    if not description.strip():
        problems.append("empty")
    if len(description) > _DESCRIPTION_MAX_CHARS:
        problems.append(f"over {_DESCRIPTION_MAX_CHARS} chars")
    if description.strip() and len(description.split()) < 3:
        problems.append("under 3 words")
    return JudgeScore(
        case_id=case.id, judge=case.judge, model=model, passed=not problems,
        output=description, detail="; ".join(problems) or "guards ok — eyeball the text",
    )
