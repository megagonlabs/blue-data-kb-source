"""Gold-standard judge benchmark case loading.

Cases are YAML data files (see `judges/cases/*.yaml`), not Python. A `JudgeCase`
runs exactly one of the three ingestion judges with inputs taken verbatim from
the file — judges are stateless and read no graph, so no seeding is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_JUDGES = ("placement", "class_name", "description")


@dataclass
class JudgeCase:
    id: str
    judge: str                                  # one of VALID_JUDGES
    description: str
    inputs: dict[str, Any]                      # kwargs-shaped, per judge
    expected: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None


def _load_case(path: Path) -> JudgeCase:
    raw = yaml.safe_load(path.read_text())
    judge = raw["judge"]
    if judge not in VALID_JUDGES:
        raise ValueError(f"{path.name}: unknown judge {judge!r}")
    return JudgeCase(
        id=raw["id"],
        judge=judge,
        description=raw.get("description", ""),
        inputs=raw["inputs"],
        expected=raw.get("expected", {}),
        source_path=path,
    )


def load_cases(directory: Path) -> list[JudgeCase]:
    """Load every `*.yaml` case file in *directory*, sorted by filename."""
    return [_load_case(p) for p in sorted(directory.glob("*.yaml"))]


def cassette_filename(case_id: str) -> str:
    """Cassette name for a case, matching `test_case[{case_id}]` in
    `test_judges_benchmark.py` so `run.py --record` writes exactly what
    that test replays."""
    return f"test_case[{case_id}].yaml"
