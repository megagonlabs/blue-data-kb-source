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
    # Correct but not required: extracting one neither raises recall nor costs
    # precision. Keeps a legitimately extracted extra (an END_DATE the query didn't ask
    # for, a job title) from scoring as a false positive (#134).
    optional_entities: list[dict] = field(default_factory=list)
    optional_facts: list[dict] = field(default_factory=list)
    optional_properties: list[dict] = field(default_factory=list)


@dataclass
class Step:
    """One `import_text` call within a case. Almost every case has exactly one
    step; multi-step cases (e.g. cross-document fusion) run several in sequence
    against the same database, so later steps can find/reuse what earlier ones
    created."""

    input_text: str
    queries: list[str] = field(default_factory=list)


@dataclass
class Case:
    id: str
    description: str
    steps: list[Step]
    seed_schema: dict[str, Any] = field(default_factory=dict)
    seed_data: dict[str, Any] = field(default_factory=dict)  # instances present before the run
    dedup_modes: tuple[str, ...] = _ALL_MODES
    # False: extraction only, against the seed schema (the "expansion off" configuration).
    expand_schema: bool = True
    # One of the #134 categories, for per-category reporting.
    category: str = "uncategorized"
    expected: ExpectedResults = field(default_factory=ExpectedResults)
    source_path: Path | None = None


def _load_case(path: Path) -> Case:
    raw = yaml.safe_load(path.read_text())
    expected_raw = raw.get("expected", {})
    reuse_raw = expected_raw.get("schema_reuse", {})

    if "steps" in raw:
        steps = [
            Step(input_text=s["input_text"], queries=s.get("queries", []) or [])
            for s in raw["steps"]
        ]
    else:
        # Single-step shorthand: top-level input_text/queries instead of a steps list.
        steps = [Step(input_text=raw["input_text"], queries=raw.get("queries", []) or [])]

    return Case(
        id=raw["id"],
        description=raw.get("description", ""),
        steps=steps,
        seed_schema=raw.get("seed_schema", {}),
        seed_data=raw.get("seed_data", {}),
        # `schema_discovery_modes` is the key's pre-#134 name, still accepted.
        dedup_modes=tuple(raw.get("dedup_modes", raw.get("schema_discovery_modes", _ALL_MODES))),
        expand_schema=bool(raw.get("expand_schema", True)),
        category=raw.get("category", "uncategorized"),
        expected=ExpectedResults(
            entities=expected_raw.get("entities", []),
            facts=expected_raw.get("facts", []),
            properties=expected_raw.get("properties", []),
            optional_entities=(expected_raw.get("optional") or {}).get("entities", []),
            optional_facts=(expected_raw.get("optional") or {}).get("facts", []),
            optional_properties=(expected_raw.get("optional") or {}).get("properties", []),
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
    """Flatten cases into (case_id, dedup_mode) pairs for parametrization."""
    return [(case.id, mode) for case in cases for mode in case.dedup_modes]


def cassette_filename(case_id: str, mode: str) -> str:
    """Cassette name for a (case_id, mode) pair.

    Matches pytest's default `request.node.name` for
    `test_case[{case_id}-{mode}]` in `test_extraction_benchmark.py` (function
    name `test_case`, `ids=[f"{case_id}-{mode}", ...]`), so cassettes recorded
    by `run.py --record` are exactly what that test replays.
    """
    return f"test_case[{case_id}-{mode}].yaml"
