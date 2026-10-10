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
    """F1, then matched/expected and how many were produced — "0.00 (0/0, 2 got)" is two
    false positives where nothing was expected, which "(0/0)" alone hid."""
    return f"{prf1.f1:.2f} ({prf1.matched}/{prf1.expected_count}, {prf1.actual_count} got)"


def _fmt_ok(ok: bool) -> str:
    return "ok" if ok else "FAIL"


def _checks(s: CaseScore) -> str:
    failed = [name for name, ok in (
        ("schema", s.schema_reuse.ok), ("norm", s.normalization.ok),
        ("prov", s.provenance.ok), ("dom/rng", s.domain_range_compliance.ok),
    ) if not ok]
    return "ok" if not failed else "FAIL " + ",".join(failed)


def render_table(scores: list[CaseScore]) -> Table:
    table = Table(title="Extraction benchmark")
    table.add_column("case", overflow="fold", min_width=18)
    for col in ("model", "dedup", "entities F1", "facts F1", "props F1", "checks", "calls", "err", "tokens", "s"):
        table.add_column(col, no_wrap=True)
    for s in scores:
        table.add_row(
            s.case_id, s.model, s.dedup_mode,
            _fmt_f1(s.entities), _fmt_f1(s.facts), _fmt_f1(s.properties),
            _checks(s), str(s.tool_call_count), str(s.error_count),
            f"{(s.prompt_tokens + s.completion_tokens) / 1000:.0f}k", f"{s.latency_seconds:.0f}",
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
                "dedup_mode": r.dedup_mode,
                "task": {
                    "description": r.case.description,
                    "steps": [asdict(s) for s in r.case.steps],
                    "seed_schema": r.case.seed_schema,
                },
                "expected": asdict(r.case.expected),
                "pre_kb": {"schema": r.pre_schema, "data": r.pre_data},
                "post_kb": {"schema": r.post_schema, "data": r.post_data},
                # Per step: what schema expansion added, then what extraction added.
                "steps": [asdict(st) for st in r.steps],
                "final_state": r.final_state,
                "llm_calls": [asdict(c) for c in r.llm_calls],
                "tool_calls": [asdict(c) for c in r.trace],
            }
            f.write(json.dumps(entry, default=str) + "\n")
