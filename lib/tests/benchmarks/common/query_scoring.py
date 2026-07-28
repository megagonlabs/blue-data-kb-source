"""Scoring a querying benchmark run against a case's gold-standard `expected` block.

Reuses `common/scoring.py`'s `judge_alignment`/`Match`/`PRF1`/`_prf1` as-is —
they're already generic (`expected: list[dict], actual: list[dict]` → LLM-judged
alignment) and don't reference extraction at all. Two new checks specific to
querying:
  * `check_schema_match` — deterministic key-set overlap over the *column
    names* `answer` rows use (the LLM chooses them freely: "choose natural
    column names based on the question and the schema"), scored off the same
    alignment `judge_alignment` already computed, same "score off the
    alignment" pattern as extraction's `check_normalization`. Doesn't apply to
    `validate`/`enrich` — their dict shapes are prompt-specified, not
    LLM-chosen, so there's no free-form column-naming behavior to score.
  * `judge_nl_fidelity` — for cases that also exercise the `natural_language=True`
    prose-output path, an LLM judge checks the prose against the gold facts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .query_cases import QueryCase
from .query_runner import QueryRunResult
from .scoring import PRF1, Match, _prf1, judge_alignment

_NL_FIDELITY_TOOL = {
    "type": "function",
    "function": {
        "name": "record_nl_fidelity",
        "description": "Record whether a natural-language answer faithfully reflects a set of gold-standard facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "faithful": {
                    "type": "boolean",
                    "description": "True if the text accurately and completely reflects the facts, without fabrication.",
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Specific problems found, e.g. 'omits the counter-evidence', "
                        "'states a fact not supported by the KB'. Empty if faithful."
                    ),
                },
            },
            "required": ["faithful", "issues"],
        },
    },
}

_NL_FIDELITY_SYSTEM_PROMPT = (
    "You judge whether a natural-language answer, produced by a knowledge-base query agent, "
    "faithfully reflects a set of gold-standard facts. Flag any fact that is missing, "
    "contradicted, or fabricated (stated but not supported by the given facts). "
    "Call record_nl_fidelity once."
)


@dataclass
class NLFidelityResult:
    faithful: bool
    issues: list[str] = field(default_factory=list)


async def judge_nl_fidelity(expected_facts: list[dict], nl_text: str, client: Any, model: str) -> NLFidelityResult:
    facts_str = json.dumps(expected_facts, indent=2)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _NL_FIDELITY_SYSTEM_PROMPT},
            {"role": "user", "content": f"Gold-standard facts:\n{facts_str}\n\nNatural-language answer:\n{nl_text}"},
        ],
        tools=[_NL_FIDELITY_TOOL],
        tool_choice={"type": "function", "function": {"name": "record_nl_fidelity"}},
    )
    tc = response.choices[0].message.tool_calls[0]
    args = json.loads(tc.function.arguments)
    faithful = args.get("faithful")
    issues = args.get("issues")
    return NLFidelityResult(
        faithful=faithful if isinstance(faithful, bool) else False,
        issues=[str(i) for i in issues] if isinstance(issues, list) else [],
    )


def check_schema_match(expected: list[dict], actual: list[dict], matches: list[Match]) -> PRF1:
    """Case-insensitive key-set overlap over matched row pairs (reusing the
    alignment already computed for value scoring). Unmatched rows don't
    contribute here — they're already penalized by the row-level PRF1."""
    total_expected_keys = 0
    total_actual_keys = 0
    total_matched_keys = 0
    for m in matches:
        ekeys = {k.lower() for k in expected[m.expected_index]}
        akeys = {k.lower() for k in actual[m.actual_index]}
        total_expected_keys += len(ekeys)
        total_actual_keys += len(akeys)
        total_matched_keys += len(ekeys & akeys)
    return _prf1(total_expected_keys, total_actual_keys, total_matched_keys)


@dataclass
class QueryCaseScore:
    case_id: str
    model: str
    search_mode: str
    operation: str
    rows: PRF1 | None = None
    schema_match: PRF1 | None = None
    supporting: PRF1 | None = None
    counter: PRF1 | None = None
    nl_fidelity: NLFidelityResult | None = None
    tool_call_count: int = 0
    error_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_seconds: float = 0.0


async def score_query_case(
    case: QueryCase,
    model: str,
    search_mode: str,
    run_result: QueryRunResult,
    judge_client: Any,
    judge_model: str,
) -> QueryCaseScore:
    score = QueryCaseScore(
        case_id=case.id,
        model=model,
        search_mode=search_mode,
        operation=case.operation,
        tool_call_count=len(run_result.trace),
        error_count=sum(1 for t in run_result.trace if t.is_error),
        prompt_tokens=run_result.prompt_tokens,
        completion_tokens=run_result.completion_tokens,
        latency_seconds=run_result.latency_seconds,
    )

    if case.operation == "answer":
        actual_rows = run_result.actual
        matches = await judge_alignment(case.expected.rows, actual_rows, "answer row", judge_client, judge_model)
        score.rows = _prf1(len(case.expected.rows), len(actual_rows), len(matches))
        score.schema_match = check_schema_match(case.expected.rows, actual_rows, matches)
    elif case.operation == "enrich":
        actual_rows = [asdict(r) for r in run_result.actual]
        matches = await judge_alignment(case.expected.rows, actual_rows, "enrichment row", judge_client, judge_model)
        score.rows = _prf1(len(case.expected.rows), len(actual_rows), len(matches))
    elif case.operation == "validate":
        supporting_matches = await judge_alignment(
            case.expected.supporting, run_result.actual.supporting, "supporting fact", judge_client, judge_model
        )
        counter_matches = await judge_alignment(
            case.expected.counter, run_result.actual.counter, "counter fact", judge_client, judge_model
        )
        score.supporting = _prf1(
            len(case.expected.supporting), len(run_result.actual.supporting), len(supporting_matches)
        )
        score.counter = _prf1(len(case.expected.counter), len(run_result.actual.counter), len(counter_matches))

    if run_result.nl_text is not None:
        expected_facts = (
            case.expected.supporting + case.expected.counter if case.operation == "validate" else case.expected.rows
        )
        score.nl_fidelity = await judge_nl_fidelity(expected_facts, run_result.nl_text, judge_client, judge_model)

    return score
