"""Gold-standard querying benchmark case loading.

Cases are YAML data files (see `querying/cases/*.yaml`), not Python. Unlike
the extraction benchmark's `Case` (common/cases.py), a `QueryCase` is
read-only: it seeds a fixed graph (`seed_schema` + `seed_data`) and then runs
exactly one of `answer`/`validate`/`enrich` against it. See the module
docstring in `query_runner.py` for how a case gets executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_SEARCH_MODES = ("keyword",)
_VALID_OPERATIONS = ("answer", "validate", "enrich")


@dataclass
class QueryExpected:
    rows: list[dict] = field(default_factory=list)        # operation == answer / enrich
    supporting: list[dict] = field(default_factory=list)   # operation == validate
    counter: list[dict] = field(default_factory=list)      # operation == validate


@dataclass
class QueryCase:
    id: str
    description: str
    operation: str                                  # "answer" | "validate" | "enrich"
    seed_schema: dict[str, Any] = field(default_factory=dict)
    seed_data: dict[str, Any] = field(default_factory=dict)
    question: str | None = None                      # operation == answer
    statement: str | None = None                      # operation == validate
    query: str | None = None                           # operation == enrich
    depth: int = 2                                       # operation == enrich
    search_modes: tuple[str, ...] = _DEFAULT_SEARCH_MODES  # "keyword" | "semantic"
    test_natural_language: bool = False                     # answer/validate only
    expected: QueryExpected = field(default_factory=QueryExpected)
    source_path: Path | None = None


def _load_case(path: Path) -> QueryCase:
    raw = yaml.safe_load(path.read_text())
    operation = raw["operation"]
    if operation not in _VALID_OPERATIONS:
        raise ValueError(f"{path}: operation must be one of {_VALID_OPERATIONS}, got {operation!r}")

    expected_raw = raw.get("expected", {})
    return QueryCase(
        id=raw["id"],
        description=raw.get("description", ""),
        operation=operation,
        seed_schema=raw.get("seed_schema", {}),
        seed_data=raw.get("seed_data", {}),
        question=raw.get("question"),
        statement=raw.get("statement"),
        query=raw.get("query"),
        depth=raw.get("depth", 2),
        search_modes=tuple(raw.get("search_modes", _DEFAULT_SEARCH_MODES)),
        test_natural_language=raw.get("test_natural_language", False),
        expected=QueryExpected(
            rows=expected_raw.get("rows", []),
            supporting=expected_raw.get("supporting", []),
            counter=expected_raw.get("counter", []),
        ),
        source_path=path,
    )


def load_cases(directory: Path) -> list[QueryCase]:
    """Load every `*.yaml` case file in *directory*, sorted by filename."""
    return [_load_case(p) for p in sorted(directory.glob("*.yaml"))]


def case_search_mode_params(cases: list[QueryCase]) -> list[tuple[str, str]]:
    """Flatten cases into (case_id, search_mode) pairs for parametrization."""
    return [(case.id, mode) for case in cases for mode in case.search_modes]


def cassette_filename(case_id: str, search_mode: str) -> str:
    """Cassette name for a (case_id, search_mode) pair.

    Matches pytest's default `request.node.name` for `test_case[{case_id}-{search_mode}]`
    in `test_querying_benchmark.py` (function name `test_case`,
    `ids=[f"{case_id}-{search_mode}", ...]`), so cassettes recorded by
    `run.py --record` are exactly what that test replays.
    """
    return f"test_case[{case_id}-{search_mode}].yaml"
