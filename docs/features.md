# Features

## Typed schema over a graph

A two-layer model: `CLASS`/`RELATION`/`PROPERTY` schema nodes describe the vocabulary; `ENTITY`/`FACT`/`LITERAL` instance nodes hold the data. Every instance is required to reference existing schema nodes, so the graph is always internally consistent — no entity without a class, no fact without a relation.

```python
person = await kb.declare_class("main", "PERSON", "A human individual")
company = await kb.declare_class("main", "COMPANY", "A business organization")
worked_at = await kb.declare_relation_type(
    "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
)

alice = await kb.add_entity("main", "Alice", class_id=person)
ibm = await kb.add_entity("main", "IBM", class_id=company)
await kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)
```

Declarations are **idempotent** — declaring `PERSON` twice returns the same id, so an LLM extraction agent (or any caller) can call `declare_class` freely without checking existence first. Class and relation hierarchies (`IS_SUBCLASS_OF`) let you model specialization, e.g. `EMPLOYEE` as a subclass of `PERSON`.

---

## Provenance tracking

Every entity, fact, and property value can carry the verbatim text it was drawn from and the source document it came from — down to the individual item, not just the document level.

```python
src = await kb.add_source("main", "resume_alice", name="Alice's résumé", original="2020-01-15")
alice = await kb.add_entity(
    "main", "Alice", class_id=person,
    text="Alice is a software engineer.", source_id=src,
)
```

Every node also carries `created`/`modified` timestamps automatically (no parameter to set — they're stamped by the write itself), and `SOURCE` nodes carry a caller-supplied `original` date distinct from `created` (e.g. when a document was originally published, vs. when it was added to the KB). `get_metadata(database, node_id)` returns all of this — timestamps, extraction text, source name, source original date — for a single entity, fact, or property value.

An item can accumulate evidence from multiple passages over time: `add_entity_extraction`/`add_fact_extraction`/etc. link an *additional* text span to something that already exists, rather than creating a duplicate.

---

## Search — keyword and semantic

Optional BM25 keyword search or hybrid semantic (embedding) search over both schema and instance data, enabled per database.

```python
kb = MemoryKBSource(name="my-kb", enable_search=True)   # BM25
# or, for semantic search too:
kb = MemoryKBSource(name="my-kb", enable_search=True, vector_source=my_vector_source, embedding_fn=my_embed_fn)

hits = await kb.search_schema("main", "employment")
hits = await kb.search_data("main", "Alice", mode="semantic")
```

Without `enable_search`, `search_schema`/`search_data` still work — they fall back to an unranked substring match, so callers never need to branch on whether search is configured.

---

## LLM knowledge extraction

`import_text` reads a passage of free text, extends the schema as needed, and populates entities, facts, and property values — each wired to its own per-item provenance record.

```python
await kb.import_text(
    "main",
    text="Alice joined IBM as a software engineer in June 1998.",
    queries=["Where did Alice work?", "When did she start?"],
    source_id=src,
)
```

Before declaring a new schema element, the agent checks whether an existing one already covers the same meaning (`schema_discovery_mode`: `"search"` — keyword/semantic lookup, `"llm"` — asks the model to judge synonyms like `JOIN_DATE` vs `START_DATE`, or `"none"`) — so repeated extraction runs converge on a stable schema instead of accumulating near-duplicate properties.

---

## Natural language querying

`answer`, `validate`, and `enrich` each run an LLM agent that searches and traverses the instance graph — no query language to learn.

```python
answers = await kb.answer("main", "Where did Alice work?")
result = await kb.validate("main", "Alice worked at IBM")   # .supporting / .counter
related = await kb.enrich("main", "Alice", depth=2)          # BFS from seed entities
```

Add `include_metadata=True` to have the agent also collect `created`/`modified`/provenance for everything it cites, returned as a second `metadata` dict alongside the result — or, with `natural_language=True` too, folded into the prose as a follow-up paragraph. `model` is configurable per call (default `gpt-4o`).

---

## Schema and data export/import

Round-trip an entire database — or migrate one database's contents into another — as portable JSON.

```python
schema_json = await kb.export_database_schema("main")
await kb.import_database_schema("copy", schema_json)

data_json = await kb.export_database_data("main", include_provenance=True)
await kb.import_database_data("copy", data_json)
```

Data import resolves classes/relations/properties **by name** against whatever schema is already in the target database, so import the schema first. Entities/facts in the export carry a `ref` used purely as an in-payload join key, so property values re-link correctly even when two entities share the same name.

---

## Change hooks

Subscribe to schema declarations and instance writes on a specific database, synchronously, with human-readable names already resolved into the event payload.

```python
def on_change(event: dict) -> None:
    if event["type"] == "fact_added":
        print(f"{event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']}")

kb.add_change_hook("main", on_change)
```

Idempotent re-declarations don't re-fire; hooks are scoped per database, so a hook on `"main"` never sees writes to another database on the same `KBSource` instance.

---

## Pluggable graph backend

The same `KBSource` operations run unmodified against any `GraphSource` backend — mix in the concrete class via Python MRO:

```python
class MyKBSource(KBSource, MemoryGraphSource): ...   # dev/test
class MyKBSource(KBSource, FileGraphSource): ...      # CLI default, JSON files on disk
class MyKBSource(KBSource, NEO4JSource): ...           # production
```

See [extending.md](extending.md) for implementing a new backend.

---

## CLI

A full command-line interface (`kb`) over the library — schema, data, search, provenance, LLM operations, database/backend management.

```bash
kb schema class add PERSON "A human individual"
kb data entity add --name Alice --class PERSON
kb data answer "Where did Alice work?"
```

See [cli.md](cli.md) for the full command reference.

---

## MCP server

Exposes read and additive-write operations as MCP tools, so Claude and other MCP clients can build and query a knowledge base directly in an agentic session.

```bash
blue-data-kb-mcp
```

```json
{
  "mcpServers": {
    "blue-data-kb": { "command": "blue-data-kb-mcp" }
  }
}
```

See [mcp.md](mcp.md) for the full tool reference and Claude Desktop configuration.

---

## REST API (planned)

Not yet implemented — see [api.md](api.md) and [design/09-api-interface-spec.md](../design/09-api-interface-spec.md) for the sketch.
