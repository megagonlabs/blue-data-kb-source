"""Gold-standard extraction benchmark case loading.

Cases are YAML data files (see `extraction/cases/*.yaml`), not Python, so new
cases can be added without touching harness code. See the module docstring in
`runner.py` for how a case gets executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ALL_MODES = ("search", "llm", "none")


@dataclass
class SchemaReuseSpec:
    must_reuse: list[str] = field(default_factory=list)
    must_not_declare: list[str] = field(default_factory=list)
    must_expand: list[str] = field(default_factory=list)


@dataclass
class ExpectedResults:
    entities: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    properties: list[dict] = field(default_factory=list)
    schema_reuse: SchemaReuseSpec = field(default_factory=SchemaReuseSpec)


@dataclass
class Case:
    id: str
    description: str
    input_text: str
    seed_schema: dict[str, Any] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    schema_discovery_modes: tuple[str, ...] = _ALL_MODES
    expected: ExpectedResults = field(default_factory=ExpectedResults)
    source_path: Path | None = None


def _load_case(path: Path) -> Case:
    raw = yaml.safe_load(path.read_text())
    expected_raw = raw.get("expected", {})
    reuse_raw = expected_raw.get("schema_reuse", {})
    return Case(
        id=raw["id"],
        description=raw.get("description", ""),
        input_text=raw["input_text"],
        seed_schema=raw.get("seed_schema", {}),
        queries=raw.get("queries", []) or [],
        schema_discovery_modes=tuple(raw.get("schema_discovery_modes", _ALL_MODES)),
        expected=ExpectedResults(
            entities=expected_raw.get("entities", []),
            facts=expected_raw.get("facts", []),
            properties=expected_raw.get("properties", []),
            schema_reuse=SchemaReuseSpec(
                must_reuse=reuse_raw.get("must_reuse", []),
                must_not_declare=reuse_raw.get("must_not_declare", []),
                must_expand=reuse_raw.get("must_expand", []),
            ),
        ),
        source_path=path,
    )


def load_cases(directory: Path) -> list[Case]:
    """Load every `*.yaml` case file in *directory*, sorted by filename."""
    return [_load_case(p) for p in sorted(directory.glob("*.yaml"))]


def case_mode_params(cases: list[Case]) -> list[tuple[str, str]]:
    """Flatten cases into (case_id, schema_discovery_mode) pairs for parametrization."""
    return [(case.id, mode) for case in cases for mode in case.schema_discovery_modes]


def cassette_filename(case_id: str, mode: str) -> str:
    """Cassette name for a (case_id, mode) pair.

    Matches pytest's default `request.node.name` for
    `test_case[{case_id}-{mode}]` in `test_extraction_benchmark.py` (function
    name `test_case`, `ids=[f"{case_id}-{mode}", ...]`), so cassettes recorded
    by `run.py --record` are exactly what that test replays.
    """
    return f"test_case[{case_id}-{mode}].yaml"
