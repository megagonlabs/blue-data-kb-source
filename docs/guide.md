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

Found an existing property that already means what you need on a different class/relation? `attach_property` reuses it there instead of declaring a same-meaning duplicate under a new name:

```python
await kb.attach_property("main", target_id=company, property_id=person_name)  # e.g. reuse NAME on COMPANY too
```

---

## Adding data, with provenance

Register a source, then add entities and facts referencing it via `text`/`source_id` — this creates a per-item `EXTRACTION` node capturing the verbatim span, distinct from a document-level note. `confidence` (optional, `[0.0, 1.0]`) scores the assertion and is stored on that `EXTRACTION`; it requires `text`.

```python
src = await kb.add_source("main", source="resume_alice", name="Alice's résumé", original="2020-01-15")

alice = await kb.add_entity(
    "main", name="Alice", class_id=employee,
    text="Alice is a software engineer.", source_id=src, confidence=0.9,
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
#  "created": "...", "modified": "...", "confidence": None,   # facts only; None here
#  "evidence": [{"text": "Alice is a software engineer.", "confidence": 0.9,
#                "created": "...", "source": "Alice's résumé",
#                "source_original": "2020-01-15"}]}
```

---

## Asserting and ingesting external knowledge

Sometimes you hold names, not ids: triples produced by an external tool, statements from an agent or a person, rows out of another system. `assert_*` resolves each name for you and creates anything missing, schema included — by default an LLM picks class names and writes descriptions, and `schema_creation_mode="none"` skips the LLM, filing untyped entities under an `EVERYTHING` catch-all:

```python
result = await kb.assert_fact("main", "Alice", "WORKED_AT", "IBM",
                              source_id=src, confidence=0.8)
# result.created        -> True on a fresh graph; False + match_type="structural"
#                          when the triple already exists (new evidence attached)
# result.created_nodes  -> everything created along the way, entities and schema alike
# result.widened_relations -> relations whose domain/range grew to fit the fact
```

An ambiguous name raises `IngestionError` with the candidates it was ambiguous between, and nothing is written. For bulk, `ingest` runs a ten-arm payload (classes, relations, entities, facts, …) through the same asserts row by row and returns a report instead of raising:

```python
report = await kb.ingest("main", payload, source_id=src, confidence=0.8)
# report.written / deduped / skipped, report.created (label -> count),
# report.widened, and report.failures — each failure carrying its row, reason,
# and candidates. Re-running the same payload converges: known items just
# accumulate evidence.
```

The CLI mirrors both: `blue-kb data entity assert`, `blue-kb data fact assert`, … and `blue-kb data ingest FILE` (JSON, same ergonomics as `blue-kb data import`).

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

Without `enable_search`, both calls still work — they fall back to a keyword match without an index (whole query as a substring first, then by shared words, stopwords ignored, `PROFICIENT_IN` matching "proficient"), so application code never needs to branch on whether search is configured.

---

## Growing the schema and extracting knowledge from text (LLM)

Two separate OpenAI agents, each requiring the `openai` extra and `OPENAI_API_KEY`:

- **`expand_schema`** — grows the schema (`CLASS`/`RELATION`/`PROPERTY`), grounded by a text passage.
  Never touches entities, facts, or property values.
- **`import_text`** — extracts entities, facts, and property values *against the schema as it
  currently stands*. Never declares new schema; a mention with no matching schema element is
  skipped. By default it runs `expand_schema` first (`expand_schema=True`), so a single call still
  does both — pass `expand_schema=False` to extract only, e.g. when reusing a schema already
  expanded once across many documents.

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

src = await kb.add_source("main", source="resume_alice", name="Alice's résumé")

await kb.import_text(
    "main",
    text="Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005.",
    queries=["Where did Alice work?", "When did she start?"],
    source_id=src,
    # expand_schema=True by default — set False to extract only, against schema
    # already grown by a prior expand_schema() call.
)
```

`import_text`'s extraction pass inspects the current schema, calls `search_schema` to resolve each
mention to an existing `CLASS`/`RELATION`/`PROPERTY` (skipping anything with no match — it never
declares schema itself), then calls `add_entity`/`add_fact`/`add_entity_property_value`/etc. with
the verbatim source span as `text` so per-item `EXTRACTION` nodes get created. If an entity or fact
is found to already exist, the agent links the new text as additional evidence instead of
duplicating it.

### `expand_schema`

A CLASS is a *kind* that can have many members (`PERSON`, `ORGANIZATION`) — the model is instructed
never to declare a CLASS (or RELATION) named after a specific individual mentioned in the text (e.g.
"Alice" or "United Nations" should never become a `CLASS ALICE`/`CLASS UNITED_NATIONS`); it declares
the general kind instead and leaves the specific individual to the extraction pass, which creates it
as an entity against that CLASS. Declaring a new CLASS is also expected to lead to a RELATION
connecting it to the rest of the schema (e.g. `SKILL`/`HOBBY` classes should come with `HAS_SKILL`/
`HAS_HOBBY` relations from `PERSON`) — a class left with no relation to anything is treated as a
likely sign one was missed. An is-a/type-of/kind-of hierarchy between two CLASSes or two RELATIONs
is wired with `add_is_subclass_of`, never a declared RELATION — `expand_schema` rejects a taxonomic
name (`IS_A`, `TYPE_OF`, `SUBCLASS_OF`, ...) passed to `declare_relation_type`.

```python
await kb.expand_schema(
    "main",
    "Alice joined IBM as a software engineer in June 1998.",
    depth=1,
    reference="mcp",
    mcp_servers=[{"transport": "stdio", "command": "blue-data-wordnet-mcp"}],
)
await kb.import_text("main", text="...", expand_schema=False)  # reuse the schema above
```

- **`depth`** (default `1`) — how many hierarchy levels beyond what's directly evidenced in the text
  to also propose and link via `add_is_subclass_of` (e.g. a mentioned "poodle" also suggesting a
  `DOG` parent CLASS).
- **`reference`** — `"llm"` (default) proposes schema freely; `"mcp"` additionally connects the
  servers in `mcp_servers` and gives the model their tools too, to ground new names and hierarchy
  placement (e.g. WordNet hypernyms/synonyms via [`platform/wordnet-mcp`](../platform/wordnet-mcp)).
  Lookups are required in this mode: the prompt tells the model to look each new element up before
  declaring it, and the first `declare_class`/`declare_relation_type` of a new element whose word
  no reference tool was asked about is sent back with an error asking for that lookup (a repeated
  attempt goes through, for concepts the resource doesn't cover). Expect roughly twice the tool
  calls of `"llm"`, and hierarchy that follows the resource's taxonomy (WordNet puts `PERSON`
  under `ORGANISM`).
- **`dedup_mode`** — how it checks for an existing schema element before declaring a new one. This
  applies both to the `search_schema` tool the model can call, and — for `declare_class`/
  `declare_relation_type` specifically — as an automatic check run before the node is created,
  regardless of whether the model called `search_schema` itself:
  - `"search"` (default) — the database's configured search backend (keyword or semantic)
  - `"llm"` — asks the model to judge synonyms (e.g. `JOIN_DATE` vs `START_DATE`) — more reliable
    than keyword/semantic matching over short names, at the cost of one extra model call per check
  - `"none"` — the `search_schema` tool isn't offered, and the automatic pre-create check is skipped

`import_text`'s own schema-expansion params (`schema_expansion_target`, `schema_expansion_depth`,
`schema_expansion_mode`, `schema_expansion_mcp_servers`, `schema_expansion_dedup_mode`) mirror
`expand_schema`'s `text`/`depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one —
`schema_expansion_target` (any combination of `"text"`/`"queries"`, default both) selects what gets
concatenated into the `text` passed to the underlying `expand_schema` call.

If `source_id` resolves to an existing `SOURCE` node, its `name`/`description` are included in the
extraction agent's system prompt as context (e.g. "Alice's résumé — submitted for her job
application"), not just used for provenance wiring.

---

## Querying in natural language (LLM)

`answer`, `validate`, and `enrich` each run an LLM agent over the instance graph — no query language required. All three accept an optional `model` (default `gpt-4o`) and `on_tool_call(name, args)` to observe tool calls as they happen.

```python
answers = await kb.answer("main", "Where did Alice work?")
for a in answers:
    print(a)   # flat dict, LLM-chosen column names — not a fixed schema

result = await kb.validate("main", "Alice worked at IBM")
print(result.supporting)   # facts {fact_id, fact, head, relation, tail} and property values {literal_id, owner, property, value, ...}
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
# metadata: dict[node_id, {created, modified, confidence, evidence, ...}]
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

The CLI exposes the same round trip: `blue-kb schema export`/`blue-kb schema import`, then `blue-kb data export`/`blue-kb data import` — see [cli.md](cli.md).

---

## Benchmarks

`lib/tests/benchmarks/` scores `import_text` (extraction) and `answer`/`validate`/`enrich` (querying) against gold-standard cases, replayed from committed VCR cassettes in the default `pytest` run and re-recordable live against the real API. Report-only — a metric regression never fails the build, only a harness error does. See [`lib/README.md`](../lib/README.md#running-tests) for the full scoring methodology and how to run a live pass.

---

## Next steps

- [api-reference.md](api-reference.md) — every method, parameter, and result type
- [extending.md](extending.md) — implementing a new `GraphSource` backend
- [cli.md](cli.md) / [mcp.md](mcp.md) — the same operations from the command line or an LLM agent
