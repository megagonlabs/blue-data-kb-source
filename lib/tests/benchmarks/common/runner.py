"""Orchestrates one (case, model, dedup_mode) benchmark run: seed the KB with the
case's starting schema, then for each `case.steps` entry (almost always just one) run
the two passes `import_text` runs by default — `expand_schema` over the text and
queries, then extraction against the result (`import_text(expand_schema=False)`) —
separately, snapshotting the KB after each. Hands back everything `scoring.score_case`
needs, plus per step exactly what each pass added (#134).
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
    class_ids: dict[str, str] = {}
    for cls in spec.get("classes", []):
        class_id = await kb.declare_class(database, cls["name"], cls.get("description", ""))
        class_ids[cls["name"]] = class_id
        for prop in cls.get("properties", []):
            await kb.declare_class_property(database, prop["name"], prop.get("description", ""), class_id=class_id)
    for cls in spec.get("classes", []):
        if cls.get("parent"):
            await kb.add_is_subclass_of(database, class_ids[cls["name"]], class_ids[cls["parent"]])

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


async def seed_data(kb: KBSource, database: str, spec: dict[str, Any]) -> None:
    """Instances that exist before the run (e.g. Apple the COMPANY, for a same-name case):
    `entities: [{name, class}]`, `facts: [{head, relation, tail}]`, by name."""
    ids: dict[str, str] = {}
    for ent in spec.get("entities", []):
        cls = await kb.find_class(database, ent["class"])
        ids[ent["name"]] = await kb.add_entity(database, ent["name"], class_id=cls["id"] if cls else None)
    for fact in spec.get("facts", []):
        rel = await kb.find_relation_type(database, fact["relation"])
        await kb.add_fact(database, head_entity_id=ids[fact["head"]], tail_entity_id=ids[fact["tail"]],
                          relation_id=rel["id"])


async def _snapshot(kb: KBSource, database: str) -> tuple[str, list[dict]]:
    """(schema, data) snapshot of *database*, kept separate per the detailed log's needs.

    Schema is the same simplified string form the extraction agent's system
    prompt shows it (`engine.py`'s `schema_str`); data is the compact
    entity/fact/property row form (`fetch_database_data(compact=True)`).
    """
    schema = await kb.fetch_database_schema(database)
    data = await kb.fetch_database_data(database, compact=True)
    return schema.to_str(simplified=True), data


def _names(node: dict) -> str:
    return (node.get("properties") or {}).get("name") or node["id"]


def graph_state(kb: KBSource, database: str) -> dict[str, list[str]]:
    """The KB as comparable, human-readable lines, one list per kind — schema
    (classes, relations, properties) and data (entities, facts, values). Two states
    diff into "what this pass/stage added". Synchronous, so it can run inside
    import_text's on_stage callback."""
    from blue_data_kb_source.knowledge.helpers import entity_classes, fact_parts, property_values

    state: dict[str, set[str]] = {k: set() for k in
                                  ("classes", "relations", "properties", "entities", "facts", "values")}

    def nbs(node_id: str, edge: str, direction: str) -> list[dict]:
        return [nb["node"] for nb in kb.get_neighbors(database, node_id, edge_type=edge, direction=direction)]

    for c in kb.match_nodes(database, label="CLASS"):
        parents = sorted(_names(n) for n in nbs(c["id"], "IS_SUBCLASS_OF", "out"))
        state["classes"].add(_names(c) + (f" ⊂ {', '.join(parents)}" if parents else ""))
    for r in kb.match_nodes(database, label="RELATION"):
        dom = "|".join(sorted(_names(n) for n in nbs(r["id"], "DOMAIN", "out"))) or "?"
        rng = "|".join(sorted(_names(n) for n in nbs(r["id"], "RANGE", "out"))) or "?"
        state["relations"].add(f"{dom} -{_names(r)}-> {rng}")
    for p in kb.match_nodes(database, label="PROPERTY"):
        for owner in [_names(n) for n in nbs(p["id"], "DEFINE_PROPERTY", "in")] or ["?"]:
            state["properties"].add(f"{owner}.{_names(p)}")
    for e in kb.match_nodes(database, label="ENTITY"):
        classes = sorted(_names(c) for c in entity_classes(kb, database, e["id"]))
        state["entities"].add(f"{_names(e)} : {', '.join(classes) or '(untyped)'}")
    for f in kb.match_nodes(database, label="FACT"):
        parts = fact_parts(kb, database, f["id"])
        head, rel, tail = (_names(parts[k]) if parts.get(k) else "?" for k in ("head", "relation", "tail"))
        state["facts"].add(f"{head} -{rel}-> {tail}")
    for owner_label in ("ENTITY", "FACT"):
        for o in kb.match_nodes(database, label=owner_label):
            for pv in property_values(kb, database, o["id"]):
                pname = _names(pv["property"]) if pv.get("property") else "?"
                value = (pv["literal"].get("properties") or {}).get("value")
                state["values"].add(f"{_names(o)}.{pname} = {value!r}")
    return {k: sorted(v) for k, v in state.items()}


def added(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: sorted(set(after[k]) - set(before.get(k, []))) for k in after}


@dataclass
class StepResult:
    """One step's two passes, as what each added to the KB."""

    input_text: str
    queries: list[str]
    expansion_ran: bool
    expansion_added: dict[str, list[str]]   # classes / relations / properties (plus anything else it wrote)
    extraction_added: dict[str, list[str]]  # entities / facts / values (plus any schema, which should be none)
    expansion_tool_calls: int = 0
    extraction_tool_calls: int = 0
    # Extraction broken down by its stages, in order: [{"stage", "pass", "added"}].
    extraction_stages: list[dict] = field(default_factory=list)


@dataclass
class RunResult:
    case: Case
    model: str
    dedup_mode: str
    collector: CreationCollector
    trace: list[ToolCallRecord]
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    pre_schema: str = ""
    pre_data: list[dict] = field(default_factory=list)
    post_schema: str = ""
    post_data: list[dict] = field(default_factory=list)
    steps: list[StepResult] = field(default_factory=list)
    final_state: dict[str, list[str]] = field(default_factory=dict)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.llm_calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.llm_calls)

    @property
    def latency_seconds(self) -> float:
        return sum(c.latency_seconds for c in self.llm_calls)


async def run_case(kb: KBSource, case: Case, model: str, dedup_mode: str) -> RunResult:
    """Run one case end to end against a freshly created database on *kb*.

    Uses `overwrite=True` so repeated calls against a shared `blue-kb` instance (as
    `run.py` does when comparing several models/modes) each start from a clean
    database instead of accumulating entities/facts from prior runs, which
    could otherwise contaminate `find_entity`/`link_extraction` lookups.
    """
    kb.create_database(DB, overwrite=True)
    await seed_schema(kb, DB, case.seed_schema)
    await seed_data(kb, DB, case.seed_data)
    pre_schema, pre_data = await _snapshot(kb, DB)

    collector = CreationCollector()
    kb.add_change_hook(DB, collector)
    on_tool_call, on_tool_result, trace = make_trace_callbacks()
    llm_calls: list[LLMCallRecord] = []

    import openai

    instrumented = functools.partial(InstrumentedOpenAI, real_client_cls=openai.AsyncOpenAI, sink=llm_calls)
    steps: list[StepResult] = []
    with patch("openai.AsyncOpenAI", instrumented):
        for step in case.steps:
            before = graph_state(kb, DB)
            calls_before = len(trace)
            if case.expand_schema:
                # The same input import_text's own expansion pass sees: text, then queries.
                context = "\n\n".join([step.input_text] + (["\n".join(step.queries)] if step.queries else []))
                await kb.expand_schema(
                    DB, context, dedup_mode=dedup_mode, model=model,
                    on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                )
            mid = graph_state(kb, DB)
            calls_mid = len(trace)
            stage_snaps: list[tuple[str, int, dict]] = []

            def on_stage(stage: str, pass_num: int) -> None:
                stage_snaps.append((stage, pass_num, graph_state(kb, DB)))

            await kb.import_text(
                DB,
                step.input_text,
                queries=step.queries or None,
                model=model,
                expand_schema=False,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                on_stage=on_stage,
            )
            stages, prev = [], mid
            for stage, pass_num, snap in stage_snaps:
                stages.append({"stage": stage, "pass": pass_num, "added": added(prev, snap)})
                prev = snap
            after = graph_state(kb, DB)
            steps.append(StepResult(
                input_text=step.input_text, queries=list(step.queries),
                expansion_ran=case.expand_schema,
                expansion_added=added(before, mid), extraction_added=added(mid, after),
                expansion_tool_calls=calls_mid - calls_before, extraction_tool_calls=len(trace) - calls_mid,
                extraction_stages=stages,
            ))

    kb.remove_change_hook(DB, collector)
    post_schema, post_data = await _snapshot(kb, DB)
    final_state = graph_state(kb, DB)
    return RunResult(
        case=case,
        model=model,
        dedup_mode=dedup_mode,
        collector=collector,
        trace=trace,
        llm_calls=llm_calls,
        pre_schema=pre_schema,
        pre_data=pre_data,
        post_schema=post_schema,
        post_data=post_data,
        steps=steps,
        final_state=final_state,
    )
