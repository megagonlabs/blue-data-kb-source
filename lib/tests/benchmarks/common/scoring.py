"""Scoring a benchmark run against a case's gold-standard `expected` block.

Two different techniques, on purpose:
  * entities/facts/properties are matched to the gold list via an LLM judge
    (`judge_alignment`) — semantic equivalence, not string equality, since
    extracted names can legitimately vary in wording from the gold label.
  * schema reuse/expansion is checked deterministically (`check_schema_reuse`)
    against exact names, since the case author controls those names directly
    (they're the seed schema / the trap names), so there's no wording gap to
    bridge and no reason to spend an LLM call on it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .cases import Case, SchemaReuseSpec
from .capture import CreationCollector, ToolCallRecord

_ALIGNMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "record_alignment",
        "description": (
            "Record which extracted items correspond to which expected (gold) items, "
            "matching by meaning rather than exact wording. Not every item on either "
            "side needs a match."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "matches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "expected_index": {"type": "integer"},
                            "actual_index": {"type": "integer"},
                        },
                        "required": ["expected_index", "actual_index"],
                    },
                    "description": "One entry per matched pair.",
                },
            },
            "required": ["matches"],
        },
    },
}

_JUDGE_SYSTEM_PROMPT = (
    "You score an information-extraction benchmark. You are given a gold-standard "
    "list of expected {kind} items and the list actually produced by the system "
    "under test. Match items that refer to the same real-world {kind} even if "
    "wording differs (e.g. different phrasing of a date, or a paraphrased fact). "
    "Do not force a match when items clearly differ. Call record_alignment once."
)


@dataclass
class Match:
    expected_index: int
    actual_index: int


async def judge_alignment(
    expected: list[dict], actual: list[dict], kind: str, client: Any, model: str
) -> list[Match]:
    """Ask an LLM to align *actual* items to *expected* (gold) items by meaning."""
    if not expected or not actual:
        return []

    expected_str = "\n".join(f"{i}: {json.dumps(e)}" for i, e in enumerate(expected))
    actual_str = "\n".join(f"{i}: {json.dumps(a)}" for i, a in enumerate(actual))
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT.format(kind=kind)},
            {"role": "user", "content": f"Expected:\n{expected_str}\n\nActual:\n{actual_str}"},
        ],
        tools=[_ALIGNMENT_TOOL],
        tool_choice={"type": "function", "function": {"name": "record_alignment"}},
    )
    tc = response.choices[0].message.tool_calls[0]
    raw_matches = json.loads(tc.function.arguments).get("matches", [])
    seen_expected: set[int] = set()
    seen_actual: set[int] = set()
    matches: list[Match] = []
    for m in raw_matches:
        ei, ai = m.get("expected_index"), m.get("actual_index")
        # The model isn't always well-behaved: `required` in the tool schema doesn't
        # stop it from emitting null or omitting a field, so treat anything other
        # than a plain in-range int as a malformed (skip) match rather than crashing.
        if not isinstance(ei, int) or not isinstance(ai, int):
            continue
        if ei in seen_expected or ai in seen_actual:
            continue
        if not (0 <= ei < len(expected)) or not (0 <= ai < len(actual)):
            continue
        seen_expected.add(ei)
        seen_actual.add(ai)
        matches.append(Match(expected_index=ei, actual_index=ai))
    return matches


@dataclass
class PRF1:
    precision: float
    recall: float
    f1: float
    matched: int
    expected_count: int
    actual_count: int


def _prf1(expected_count: int, actual_count: int, matched: int) -> PRF1:
    precision = matched / actual_count if actual_count else (1.0 if expected_count == 0 else 0.0)
    recall = matched / expected_count if expected_count else (1.0 if actual_count == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return PRF1(precision, recall, f1, matched, expected_count, actual_count)


@dataclass
class SchemaReuseResult:
    reused_ok: list[str] = field(default_factory=list)
    reused_missing: list[str] = field(default_factory=list)
    wrongly_declared: list[str] = field(default_factory=list)
    expanded_ok: list[str] = field(default_factory=list)
    expansion_missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reused_missing and not self.wrongly_declared and not self.expansion_missing


def _declared_names(collector: CreationCollector) -> set[str]:
    names: set[str] = set()
    for bucket in (
        collector.classes_declared,
        collector.relations_declared,
        collector.class_properties_declared,
        collector.relation_properties_declared,
    ):
        names.update(e.get("name", "") for e in bucket)
    return names


def check_schema_reuse(spec: SchemaReuseSpec, collector: CreationCollector) -> SchemaReuseResult:
    """Pure set comparison of newly-declared schema element names, no LLM."""
    declared = _declared_names(collector)
    result = SchemaReuseResult()
    for name in spec.must_reuse:
        (result.reused_missing if name in declared else result.reused_ok).append(name)
    for name in spec.must_not_declare:
        if name in declared:
            result.wrongly_declared.append(name)
    for name in spec.must_expand:
        (result.expanded_ok if name in declared else result.expansion_missing).append(name)
    return result


@dataclass
class CaseScore:
    case_id: str
    model: str
    schema_discovery_mode: str
    entities: PRF1
    facts: PRF1
    properties: PRF1
    schema_reuse: SchemaReuseResult
    tool_call_count: int
    error_count: int
    prompt_tokens: int
    completion_tokens: int
    latency_seconds: float


async def score_case(
    case: Case,
    model: str,
    schema_discovery_mode: str,
    collector: CreationCollector,
    trace: list[ToolCallRecord],
    judge_client: Any,
    judge_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_seconds: float,
) -> CaseScore:
    entity_matches = await judge_alignment(case.expected.entities, collector.entities, "entity", judge_client, judge_model)
    fact_matches = await judge_alignment(case.expected.facts, collector.facts, "fact", judge_client, judge_model)
    prop_matches = await judge_alignment(
        case.expected.properties,
        collector.entity_properties + collector.fact_properties,
        "property value",
        judge_client,
        judge_model,
    )
    return CaseScore(
        case_id=case.id,
        model=model,
        schema_discovery_mode=schema_discovery_mode,
        entities=_prf1(len(case.expected.entities), len(collector.entities), len(entity_matches)),
        facts=_prf1(len(case.expected.facts), len(collector.facts), len(fact_matches)),
        properties=_prf1(
            len(case.expected.properties),
            len(collector.entity_properties) + len(collector.fact_properties),
            len(prop_matches),
        ),
        schema_reuse=check_schema_reuse(case.expected.schema_reuse, collector),
        tool_call_count=len(trace),
        error_count=sum(1 for t in trace if t.is_error),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_seconds=latency_seconds,
    )
