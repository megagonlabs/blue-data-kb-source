"""Console table and detailed-log rendering for querying benchmark scores.

`write_json` is reused unchanged from `common/reporting.py` — it's already
generic (`json.dumps([asdict(s) for s in scores])`).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rich.table import Table

from .query_runner import QueryRunResult
from .query_scoring import QueryCaseScore


def _fmt_f1(prf1) -> str:
    if prf1 is None:
        return "—"
    return f"{prf1.f1:.2f} ({prf1.matched}/{prf1.expected_count})"


def _fmt_nl(nl_fidelity) -> str:
    if nl_fidelity is None:
        return "—"
    return "ok" if nl_fidelity.faithful else "FAIL"


def render_query_table(scores: list[QueryCaseScore]) -> Table:
    table = Table(title="Querying benchmark")
    for col in ("case", "model", "search mode", "operation", "rows F1", "schema F1",
                "supporting F1", "counter F1", "NL", "tool calls", "errors", "tokens", "latency (s)"):
        table.add_column(col)
    for s in scores:
        table.add_row(
            s.case_id, s.model, s.search_mode, s.operation,
            _fmt_f1(s.rows), _fmt_f1(s.schema_match),
            _fmt_f1(s.supporting), _fmt_f1(s.counter), _fmt_nl(s.nl_fidelity),
            str(s.tool_call_count), str(s.error_count),
            str(s.prompt_tokens + s.completion_tokens), f"{s.latency_seconds:.2f}",
        )
    return table


def write_detailed_log(results: list[QueryRunResult], path: Path) -> None:
    """Write one JSON object per line: the task spec, full LLM prompts/responses,
    the raw tool-call trace, the actual result (and NL prose when tested) for
    each (case, model, search_mode) run — the detail the score table/JSON
    report intentionally omits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in results:
            case = r.case
            if case.operation == "answer":
                actual = r.actual                              # already list[dict]
            elif case.operation == "validate":
                actual = asdict(r.actual)                        # ValidationResult
            else:
                actual = [asdict(item) for item in r.actual]      # list[EnrichmentResult]
            entry = {
                "case_id": case.id,
                "model": r.model,
                "search_mode": r.search_mode,
                "operation": case.operation,
                "task": {
                    "description": case.description,
                    "question": case.question,
                    "statement": case.statement,
                    "query": case.query,
                    "depth": case.depth,
                    "seed_schema": case.seed_schema,
                    "seed_data": case.seed_data,
                },
                "expected": asdict(case.expected),
                "actual": actual,
                "nl_text": r.nl_text,
                "llm_calls": [asdict(c) for c in r.llm_calls],
                "tool_calls": [asdict(c) for c in r.trace],
            }
            f.write(json.dumps(entry, default=str) + "\n")
