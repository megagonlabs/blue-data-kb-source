# Developer Guide

This is a task-oriented walkthrough of the Python library. For the full method-by-method reference, see [api-reference.md](api-reference.md). For the CLI equivalent of everything here, see [cli.md](cli.md).

## Setup

`KBSource` is abstract — pick a ready-made backend (see [architecture.md](architecture.md#graph-backends)): `MemoryKBSource`, `FileKBSource`, or `NEO4JKBSource`.

> All I/O-bound methods are `async def`. Call them from inside an `async` function and run with `asyncio.run(...)` or an async framework.

```python
import asyncio
from blue_data_kb_source import MemoryKBSource

async def main():
    kb = MemoryKBSource(name="my-kb")
    kb.create_database("main")
    ...

asyncio.run(main())
```

---

## Declaring a schema

Classes, relations, and properties form the typed vocabulary. `declare_*` methods are **idempotent** — calling them again with the same name returns the existing id rather than creating a duplicate, so you never need to check existence first.

```python
person = await kb.declare_class("main", name="PERSON", description="A human individual")
person_name = await kb.declare_class_property(
    "main", name="NAME", description="Name of the individual", class_id=person
)

company = await kb.declare_class("main", name="COMPANY", description="A business organization")
employee = await kb.declare_class("main", name="EMPLOYEE", description="A person employed by an organization")
await kb.add_is_subclass_of("main", employee, person)

worked_at = await kb.declare_relation_type(
    "main", name="WORKED_AT",
    description="Employment relationship between a person and a company",
    domain="EMPLOYEE",   # required — accepts a name or list of names
    range="COMPANY",     # required
)
start_date_prop = await kb.declare_relation_property(
    "main", name="START_DATE", description="Date when employment began", relation_id=worked_at
)
```

`domain`/`range` are keyword-only and required on `declare_relation_type` — there's no separate `add_domain`/`add_range` step.

---

## Adding data, with provenance

Register a source, then add entities and facts referencing it via `text`/`source_id` — this creates a per-item `EXTRACTION` node capturing the verbatim span, distinct from a document-level note.

```python
src = await kb.add_source("main", source="resume_alice", name="Alice's résumé", original="2020-01-15")

alice = await kb.add_entity(
    "main", name="Alice", class_id=employee,
    text="Alice is a software engineer.", source_id=src,
)
ibm = await kb.add_entity("main", name="IBM", class_id=company)

fact = await kb.add_fact(
    "main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm,
    text="Alice joined IBM in June 1998.", source_id=src,
)
await kb.add_fact_property_value(
    "main", value="1998-06-01", literal_type="DATE",
    fact_id=fact, property_id=start_date_prop,
    text="Alice joined IBM in June 1998.", source_id=src,
)
```

`original` on `add_source` is a caller-supplied date (e.g. a document's publication date) — distinct from `created`, which every node gets automatically the moment it's written. Every node also gets `modified`, refreshed whenever an `update_*` call changes its own attributes (wiring an edge doesn't count).

If an item already exists and you just found another passage confirming it, link the new span instead of re-creating the node:

```python
await kb.add_entity_extraction("main", entity_id=ibm, text="...also mentioned IBM here...", source_id=src2)
```

**Inspecting provenance for one item:**

```python
meta = await kb.get_metadata("main", alice)
# {"id": ..., "type": "ENTITY", "name": "Alice", "class_name": "EMPLOYEE",
#  "created": "...", "modified": "...", "extraction": "Alice is a software engineer.",
#  "source": "Alice's résumé", "source_original": "2020-01-15"}
```

---

## Search

Enable BM25 keyword search (and, with a vector backend, semantic search) per database via `properties["search"]` at construction — this is the only way to configure it, since `KBSource.__init__(name, properties=None)` takes no other constructor arguments:

```python
kb = MemoryKBSource(name="my-kb", properties={"search": {"mode": "bm25"}})   # keyword only

kb = MemoryKBSource(
    name="my-kb",
    properties={
        "search": {
            "mode": "semantic",
            "vector_source": my_vector_source,   # a live object — or "vector": {"_class": "...", "connection": {...}}
            "embedding_fn": my_embedding_fn,     # a live callable — or "embedding": {"provider": "openai", "model": "..."}
        }
    },
)
```

```python
hits = await kb.search_schema("main", "employment")
for h in hits:
    print(h.type, h.name, h.description)

hits = await kb.search_data("main", "Alice", mode="semantic")
```

Without `enable_search`, both calls still work — they fall back to an unranked substring match, so application code never needs to branch on whether search is configured.

---

## Extracting knowledge from text (LLM)

`import_text` runs an OpenAI agent that reads a passage, extends the schema as needed, and populates entities, facts, and property values — each with its own provenance. Requires the `openai` extra and `OPENAI_API_KEY`.

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

src = await kb.add_source("main", source="resume_alice", name="Alice's résumé")

await kb.import_text(
    "main",
    text="Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005.",
    queries=["Where did Alice work?", "When did she start?"],
    source_id=src,
)
```

The agent: inspects the current schema; before declaring anything new, checks whether an existing element already covers the meaning (`schema_discovery_mode`, below); declares any missing classes/relations/properties; then calls `add_entity`/`add_fact`/`add_entity_property_value`/etc. with the verbatim source span as `text` so per-item `EXTRACTION` nodes get created. If an entity or fact is found to already exist, the agent links the new text as additional evidence instead of duplicating it.

`schema_discovery_mode` controls how the agent checks for near-duplicates before declaring:

- `"search"` (default) — the database's configured search backend (keyword or semantic)
- `"llm"` — asks the model to judge synonyms (e.g. `JOIN_DATE` vs `START_DATE`) — more reliable than keyword/semantic matching over short names, at the cost of one extra model call per check
- `"none"` — the check is skipped entirely

If `source_id` resolves to an existing `SOURCE` node, its `name`/`description` are included in the agent's system prompt as context (e.g. "Alice's résumé — submitted for her job application"), not just used for provenance wiring.

---

## Querying in natural language (LLM)

`answer`, `validate`, and `enrich` each run an LLM agent over the instance graph — no query language required. All three accept an optional `model` (default `gpt-4o`) and `on_tool_call(name, args)` to observe tool calls as they happen.

```python
answers = await kb.answer("main", "Where did Alice work?")
for a in answers:
    print(a)   # flat dict, LLM-chosen column names — not a fixed schema

result = await kb.validate("main", "Alice worked at IBM")
print(result.supporting)   # [{fact, head, tail, relation}, ...]
print(result.counter)      # contradicting evidence

related = await kb.enrich("main", "Alice", depth=2)
for r in related:
    print(r.entity, r.relation, r.distance)
```

**Prose instead of structured rows:**

```python
text = await kb.answer("main", "Where did Alice work?", natural_language=True)
```

**Collecting provenance alongside the answer:**

```python
results, metadata = await kb.answer("main", "Where did Alice work?", include_metadata=True)
# metadata: dict[node_id, {created, modified, extraction, source, source_original, ...}]
```

The agent calls `get_metadata` for each entity/fact/property it cites; the engine harvests every such call into `metadata`, regardless of whether the agent's own structured answer references the id. With `natural_language=True` as well, `results` is prose with a provenance section appended after the main answer, and `metadata` is still the raw dict.

---

## Change hooks

Register a synchronous callback to observe schema declarations and instance writes on a specific database.

```python
def on_kb_change(event: dict) -> None:
    t = event["type"]
    if t == "entity_added":
        print(f"entity {event['name']!r} (class: {event['class_name']})")
    elif t == "fact_added":
        print(f"fact {event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']}")

kb.add_change_hook("main", on_kb_change)

alice = await kb.add_entity("main", name="Alice", class_id=person)
# → entity 'Alice' (class: PERSON)

kb.remove_change_hook("main", on_kb_change)
```

Idempotent re-declarations don't re-fire. Hooks are scoped per database — a hook on `"main"` never sees writes to another database on the same `KBSource` instance. See [api-reference.md](api-reference.md#change-hook-events) for the full event schema.

---

## Exporting and importing a schema and its data

Round-trip a database, or migrate one database's contents into another, as portable JSON.

```python
# --- schema ---
main_schema_json = await kb.export_database_schema("main")
kb.create_database("copy")
await kb.import_database_schema("copy", main_schema_json)

# --- data (schema first — data import resolves classes/relations/properties by name) ---
main_data_json = await kb.export_database_data("main", include_provenance=True)
await kb.import_database_data("copy", main_data_json)
```

Entities/facts in the data export carry their original id as a `ref`, used purely as an in-payload join key — entity names aren't guaranteed unique (`add_entity` never dedups), so facts/property values reference their owner by `ref` rather than by name to avoid mis-linking when two entities share a name.

The CLI exposes the same round trip: `kb schema export`/`kb schema import`, then `kb data export`/`kb data import` — see [cli.md](cli.md).

---

## Benchmarks

`lib/tests/benchmarks/` scores `import_text` (extraction) and `answer`/`validate`/`enrich` (querying) against gold-standard cases, replayed from committed VCR cassettes in the default `pytest` run and re-recordable live against the real API. Report-only — a metric regression never fails the build, only a harness error does. See [`lib/README.md`](../lib/README.md#running-tests) for the full scoring methodology and how to run a live pass.

---

## Next steps

- [api-reference.md](api-reference.md) — every method, parameter, and result type
- [extending.md](extending.md) — implementing a new `GraphSource` backend
- [cli.md](cli.md) / [mcp.md](mcp.md) — the same operations from the command line or an LLM agent
