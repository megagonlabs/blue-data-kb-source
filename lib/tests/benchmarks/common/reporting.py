"""Console table and JSON report rendering for benchmark scores, plus a
separate detailed log (full prompts and tool-call inputs/outputs) for
debugging a run beyond what the aggregated score table shows.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rich.table import Table

from .runner import RunResult
from .scoring import CaseScore


def _fmt_f1(prf1) -> str:
    return f"{prf1.f1:.2f} ({prf1.matched}/{prf1.expected_count})"


def _fmt_reuse(score: CaseScore) -> str:
    return "ok" if score.schema_reuse.ok else "FAIL"


def render_table(scores: list[CaseScore]) -> Table:
    table = Table(title="Extraction benchmark")
    for col in ("case", "model", "mode", "entities F1", "facts F1", "properties F1",
                "schema", "tool calls", "errors", "tokens", "latency (s)"):
        table.add_column(col)
    for s in scores:
        table.add_row(
            s.case_id, s.model, s.schema_discovery_mode,
            _fmt_f1(s.entities), _fmt_f1(s.facts), _fmt_f1(s.properties),
            _fmt_reuse(s), str(s.tool_call_count), str(s.error_count),
            str(s.prompt_tokens + s.completion_tokens), f"{s.latency_seconds:.2f}",
        )
    return table


def write_json(scores: list[CaseScore], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(s) for s in scores], indent=2, default=str))


def write_detailed_log(results: list[RunResult], path: Path) -> None:
    """Write one JSON object per line: the task spec, full LLM prompts/responses,
    the raw tool-call trace, and pre/post KB (schema, data) snapshots for each
    (case, model, mode) run — the detail the score table/JSON report
    intentionally omits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in results:
            entry = {
                "case_id": r.case.id,
                "model": r.model,
                "schema_discovery_mode": r.schema_discovery_mode,
                "task": {
                    "description": r.case.description,
                    "input_text": r.case.input_text,
                    "seed_schema": r.case.seed_schema,
                    "queries": r.case.queries,
                },
                "expected": asdict(r.case.expected),
                "pre_kb": {"schema": r.pre_schema, "data": r.pre_data},
                "post_kb": {"schema": r.post_schema, "data": r.post_data},
                "llm_calls": [asdict(c) for c in r.llm_calls],
                "tool_calls": [asdict(c) for c in r.trace],
            }
            f.write(json.dumps(entry, default=str) + "\n")
