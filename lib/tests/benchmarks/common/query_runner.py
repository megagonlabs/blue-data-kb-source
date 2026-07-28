"""Orchestrates one (case, model, search_mode) querying benchmark run: build a
KB configured for the requested search backend, seed its schema + data, run
the case's single `answer`/`validate`/`enrich` call (plus a second
natural-language call when the case opts in), and hand back everything
`query_scoring.score_query_case` needs.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from blue_data_kb_source import KBSource
from blue_data_kb_source._constants import CLASS, PROPERTY, RELATION

from ...conftest import MemoryKBSource
from .capture import InstrumentedOpenAI, LLMCallRecord, ToolCallRecord, make_trace_callbacks
from .query_cases import QueryCase
from .runner import seed_schema

DB = "benchmark"


def _build_kb(search_mode: str) -> KBSource:
    """Construct a KB configured for *search_mode*.

    Search backend choice is a KBSource construction-time parameter
    (`enable_search`/`vector_source`/`embedding_fn`), unlike extraction's
    `schema_discovery_mode` which is a per-call `import_text` argument — so,
    unlike `runner.run_case`, this owns KB construction rather than taking a
    pre-built `kb` from the caller.
    """
    if search_mode == "keyword":
        # Real in-memory BM25 (rank-bm25), not the raw substring fallback used
        # when no indexer is configured at all.
        return MemoryKBSource(name="query-benchmark", enable_search=True)

    if search_mode == "semantic":
        import openai
        from blue_data.source.vector.memory_vector_source import MemoryVectorSource

        # KBSearchIndexer calls embedding_fn synchronously (never awaited), so
        # this must be the sync client, not AsyncOpenAI.
        sync_client = openai.OpenAI()

        def embedding_fn(text: str) -> list[float]:
            response = sync_client.embeddings.create(model="text-embedding-3-small", input=text)
            return response.data[0].embedding

        return MemoryKBSource(
            name="query-benchmark",
            enable_search=True,
            vector_source=MemoryVectorSource(),
            embedding_fn=embedding_fn,
            vector_dim=1536,
        )

    raise ValueError(f"unknown search_mode: {search_mode!r}")


async def seed_data(kb: KBSource, database: str, spec: dict[str, Any]) -> dict[str, str]:
    """Seed a case's starting entities/facts/property values.

    Returns a name/ref -> node id map: entities are keyed by their `name`,
    facts by an explicit `ref` label (facts have no natural unique key like
    entities do). `entity_properties`/`fact_properties` reference their owner
    by that same name/ref via `owner:`. Classes/relations/properties must
    already exist (from `seed_schema`) — this only creates instance data.
    """
    ids: dict[str, str] = {}

    for ent in spec.get("entities", []):
        class_id = None
        cls_name = ent.get("class")
        if cls_name:
            nodes = kb.match_nodes(database, label=CLASS, properties={"name": cls_name})
            if not nodes:
                raise ValueError(f"seed_data: entity {ent['name']!r} references unknown class {cls_name!r}")
            class_id = nodes[0]["id"]
        ids[ent["name"]] = await kb.add_entity(database, ent["name"], class_id=class_id)

    for fact in spec.get("facts", []):
        rel_nodes = kb.match_nodes(database, label=RELATION, properties={"name": fact["relation"]})
        if not rel_nodes:
            raise ValueError(f"seed_data: fact {fact.get('ref')!r} references unknown relation {fact['relation']!r}")
        ids[fact["ref"]] = await kb.add_fact(
            database,
            head_entity_id=ids[fact["head"]],
            tail_entity_id=ids[fact["tail"]],
            relation_id=rel_nodes[0]["id"],
        )

    for prop in spec.get("entity_properties", []):
        prop_nodes = kb.match_nodes(database, label=PROPERTY, properties={"name": prop["name"]})
        if not prop_nodes:
            raise ValueError(f"seed_data: entity property {prop['name']!r} was not declared in seed_schema")
        await kb.add_entity_property_value(
            database, prop["value"],
            entity_id=ids[prop["owner"]], property_id=prop_nodes[0]["id"],
            literal_type=prop.get("literal_type"),
        )

    for prop in spec.get("fact_properties", []):
        prop_nodes = kb.match_nodes(database, label=PROPERTY, properties={"name": prop["name"]})
        if not prop_nodes:
            raise ValueError(f"seed_data: fact property {prop['name']!r} was not declared in seed_schema")
        await kb.add_fact_property_value(
            database, prop["value"],
            fact_id=ids[prop["owner"]], property_id=prop_nodes[0]["id"],
            literal_type=prop.get("literal_type"),
        )

    return ids


@dataclass
class QueryRunResult:
    case: QueryCase
    model: str
    search_mode: str
    actual: Any                        # list[dict] | ValidationResult | list[EnrichmentResult]
    nl_text: str | None
    trace: list[ToolCallRecord]
    llm_calls: list[LLMCallRecord] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.llm_calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.llm_calls)

    @property
    def latency_seconds(self) -> float:
        return sum(c.latency_seconds for c in self.llm_calls)


async def run_query_case(case: QueryCase, model: str, search_mode: str) -> QueryRunResult:
    kb = _build_kb(search_mode)
    kb.create_database(DB, overwrite=True)
    await seed_schema(kb, DB, case.seed_schema)
    await seed_data(kb, DB, case.seed_data)

    on_tool_call, on_tool_result, trace = make_trace_callbacks()
    llm_calls: list[LLMCallRecord] = []

    import openai

    instrumented = functools.partial(InstrumentedOpenAI, real_client_cls=openai.AsyncOpenAI, sink=llm_calls)
    nl_text: str | None = None
    with patch("openai.AsyncOpenAI", instrumented):
        if case.operation == "answer":
            actual = await kb.answer(
                DB, case.question, model=model, on_tool_call=on_tool_call, on_tool_result=on_tool_result
            )
            if case.test_natural_language:
                nl_text = await kb.answer(
                    DB, case.question, model=model, natural_language=True,
                    on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                )
        elif case.operation == "validate":
            actual = await kb.validate(
                DB, case.statement, model=model, on_tool_call=on_tool_call, on_tool_result=on_tool_result
            )
            if case.test_natural_language:
                nl_text = await kb.validate(
                    DB, case.statement, model=model, natural_language=True,
                    on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                )
        elif case.operation == "enrich":
            actual = await kb.enrich(
                DB, case.query, depth=case.depth, model=model,
                on_tool_call=on_tool_call, on_tool_result=on_tool_result,
            )
        else:
            raise ValueError(f"unknown operation: {case.operation!r}")

    return QueryRunResult(
        case=case, model=model, search_mode=search_mode,
        actual=actual, nl_text=nl_text, trace=trace, llm_calls=llm_calls,
    )
