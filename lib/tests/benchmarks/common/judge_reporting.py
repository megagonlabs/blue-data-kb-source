"""Console table and JSON report rendering for judge benchmark scores."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rich.table import Table

from .judge_scoring import JudgeScore


def render_table(scores: list[JudgeScore]) -> Table:
    table = Table(title="Judge benchmark")
    for col in ("case", "judge", "model", "result", "output", "detail"):
        table.add_column(col)
    for s in scores:
        table.add_row(s.case_id, s.judge, s.model,
                      "ok" if s.passed else "FAIL", s.output, s.detail)
    return table


def write_json(scores: list[JudgeScore], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(s) for s in scores], indent=2))
