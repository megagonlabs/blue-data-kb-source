"""Orchestrates one (case, model, schema_discovery_mode) benchmark run:
seed the KB with the case's starting schema, run `import_text` once per
`case.steps` entry (almost always just one), and hand back everything
`scoring.score_case` needs.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from blue_data_kb_source import KBSource, MemoryKBSource

from .capture import CreationCollector, InstrumentedOpenAI, LLMCallRecord, ToolCallRecord, make_trace_callbacks
from .cases import Case

DB = "benchmark"


def make_kb(name: str = "extraction-benchmark") -> MemoryKBSource:
    """A KB with a real search index, as every extraction case assumes.

    Search is declarative config under properties["search"]; with none, the
    indexer is never built and `search_schema` silently degrades to
    `SchemaManager.find_schema`, a substring lookup that misses any query
    phrased as anything but the element's exact name. The relation stage asks
    `search_schema("Agile skill", type="relation")`, gets nothing back, and
    correctly skips every fact — so a case can score 0 on facts with the model
    behaving perfectly. `query_runner.make_kb` already did this; extraction
    never did.
    """
    return MemoryKBSource(name=name, properties={"search": {"mode": "bm25"}})


async def seed_schema(kb: KBSource, database: str, spec: dict[str, Any]) -> None:
    """Declare a case's starting classes/relations/properties, in dependency order.

    Classes must exist before relations that reference them via domain/range
    (SchemaManager.declare_relation_type raises SchemaError otherwise).
    """
    for cls in spec.get("classes", []):
        class_id = await kb.declare_class(database, cls["name"], cls.get("description", ""))
        for prop in cls.get("properties", []):
            await kb.declare_class_property(database, prop["name"], prop.get("description", ""), class_id=class_id)

    for rel in spec.get("relations", []):
        relation_id = await kb.declare_relation_type(
            database,
            rel["name"],
            rel.get("description", ""),
            is_symmetric=rel.get("is_symmetric", False),
            domain=rel.get("domain"),
            range=rel.get("range"),
        )
        for prop in rel.get("properties", []):
            await kb.declare_relation_property(
                database, prop["name"], prop.get("description", ""), relation_id=relation_id
            )


async def _snapshot(kb: KBSource, database: str) -> tuple[str, list[dict]]:
    """(schema, data) snapshot of *database*, kept separate per the detailed log's needs.

    Schema is the same simplified string form the extraction agent's system
    prompt shows it (`engine.py`'s `schema_str`); data is the compact
    entity/fact/property row form (`fetch_database_data(compact=True)`).
    """
    schema = await kb.fetch_database_schema(database)
    data = await kb.fetch_database_data(database, compact=True)
    return schema.to_str(simplified=True), data


@dataclass
class RunResult:
    case: Case
    model: str
    schema_discovery_mode: str
    collector: CreationCollector
    trace: list[ToolCallRecord]
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    pre_schema: str = ""
    pre_data: list[dict] = field(default_factory=list)
    post_schema: str = ""
    post_data: list[dict] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.llm_calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.llm_calls)

    @property
    def latency_seconds(self) -> float:
        return sum(c.latency_seconds for c in self.llm_calls)


async def run_case(kb: KBSource, case: Case, model: str, schema_discovery_mode: str) -> RunResult:
    """Run one case end to end against a freshly created database on *kb*.

    Uses `overwrite=True` so repeated calls against a shared `kb` instance (as
    `run.py` does when comparing several models/modes) each start from a clean
    database instead of accumulating entities/facts from prior runs, which
    could otherwise contaminate `find_entity`/`link_extraction` lookups.
    """
    kb.create_database(DB, overwrite=True)
    await seed_schema(kb, DB, case.seed_schema)
    pre_schema, pre_data = await _snapshot(kb, DB)

    collector = CreationCollector()
    kb.add_change_hook(DB, collector)
    on_tool_call, on_tool_result, trace = make_trace_callbacks()
    llm_calls: list[LLMCallRecord] = []

    import openai

    instrumented = functools.partial(InstrumentedOpenAI, real_client_cls=openai.AsyncOpenAI, sink=llm_calls)
    with patch("openai.AsyncOpenAI", instrumented):
        for step in case.steps:
            await kb.import_text(
                DB,
                step.input_text,
                queries=step.queries or None,
                model=model,
                schema_expansion_dedup_mode=schema_discovery_mode,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
            )

    kb.remove_change_hook(DB, collector)
    post_schema, post_data = await _snapshot(kb, DB)
    return RunResult(
        case=case,
        model=model,
        schema_discovery_mode=schema_discovery_mode,
        collector=collector,
        trace=trace,
        llm_calls=llm_calls,
        pre_schema=pre_schema,
        pre_data=pre_data,
        post_schema=post_schema,
        post_data=post_data,
    )
