# blue-data-kb-source Examples

This directory contains runnable examples organized by purpose, mirroring the sibling
[`blue-data/examples/`](../../blue-data/examples/) layout.

## Quick start

```bash
cd lib && bash scripts/install.sh
```

---

## backends/ — graph storage and search backends

Start here when integrating `blue-data-kb-source` into a new application. kb-source has
two independent backend axes: **graph storage** (where the knowledge graph itself lives)
and **search indexing** (how `search_schema`/`search_data` are ranked).

### Minimal in-memory quickstart (`memory_quickstart.py`)

~20 lines: declare a schema, add data, query it. No persistence, no search.

```bash
python3 examples/backends/memory_quickstart.py
```

**[Sample output](backends/memory_quickstart.output.txt)**

### FileGraphSource full pipeline (`file_backend_demo.py`)

Every major operation against the file-backed KB: declare schema, add data with
provenance, persistence across a simulated restart (a second `KBSource` instance
pointed at the same directory), change hooks, `get_metadata`, and schema/data
export-import into a second database.

```bash
python3 examples/backends/file_backend_demo.py
```

**API key:** not required | **[Sample output](backends/file_backend_demo.output.txt)**

### Neo4j-backed KBSource (`neo4j_backend_demo.py`)

Production graph backend — same schema/data/query shape as the two demos above, with
`NEO4JSource` mixed in instead. Degrades gracefully on Neo4j Community Edition, where
multiple databases aren't supported.

```bash
docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j
export NEO4J_PASSWORD=<password>
python3 examples/backends/neo4j_backend_demo.py
```

**Requires:** a running Neo4j instance | *(not live-verified in this session — no
reachable credentials in the sandboxed environment it was written in; code was checked
up to the point of actual connection, which is as far as it can go without one)*

### BM25 keyword search (`keyword_search_demo.py`)

`enable_search=True`, no vector backend. Compares ranked BM25 results against the
unranked substring fallback used when search isn't enabled at all, plus `type_filter`.

```bash
python3 examples/backends/keyword_search_demo.py
```

**API key:** not required | **[Sample output](backends/keyword_search_demo.output.txt)**

### Semantic (vector) search (`semantic_search_demo.py`)

Wires up `MemoryVectorSource` with a deterministic, model-free embedding function (a
text hash) — no API key needed, since this demo is about the wiring, not embedding
quality. Compares `mode="keyword"` vs `mode="semantic"`. Ends with a comment showing the
one-line swap to each production vector backend.

```bash
python3 examples/backends/semantic_search_demo.py
```

**API key:** not required | **[Sample output](backends/semantic_search_demo.output.txt)**

### ChromaDB vector backend (`chromadb_vector_backend_demo.py`)

`ChromaDBVectorSource`, embedded and persisted to disk — no server to start. Paired with
`FileGraphSource` so the demo shows the *whole* KB (graph and vector index alike)
surviving a reconnect, not just the vector side.

```bash
pip install chromadb
python3 examples/backends/chromadb_vector_backend_demo.py
```

**API key:** not required | **[Sample output](backends/chromadb_vector_backend_demo.output.txt)**

### Redis vector backend (`redis_vector_backend_demo.py`)

`RedisVectorSource` against a real Redis Stack instance. Uses a namespaced
`scan_prefix` and cleans up every key/index it creates — safe to run against a shared
Redis instance.

```bash
redis-stack-server &   # or: docker run -p 6379:6379 redis/redis-stack
python3 examples/backends/redis_vector_backend_demo.py
```

**Requires:** Redis Stack on `localhost:6379` | **[Sample output](backends/redis_vector_backend_demo.output.txt)**

---

## Summary table

| File | Folder | API key | External | Output |
|------|--------|---------|----------|--------|
| `memory_quickstart.py` | backends | none | — | [output](backends/memory_quickstart.output.txt) |
| `file_backend_demo.py` | backends | none | — | [output](backends/file_backend_demo.output.txt) |
| `neo4j_backend_demo.py` | backends | none | Neo4j | — (not live-verified) |
| `keyword_search_demo.py` | backends | none | — | [output](backends/keyword_search_demo.output.txt) |
| `semantic_search_demo.py` | backends | none | — | [output](backends/semantic_search_demo.output.txt) |
| `chromadb_vector_backend_demo.py` | backends | none | — | [output](backends/chromadb_vector_backend_demo.output.txt) |
| `redis_vector_backend_demo.py` | backends | none | Redis Stack | [output](backends/redis_vector_backend_demo.output.txt) |

---

## feature-demos/ — one feature per script, in depth

Each script here is a deep dive on a single library feature, going further than the
backends/ scripts' incidental use of the same calls.

### Schema declaration (`schema_declaration_demo.py`)

`declare_class`/`declare_relation_type`/`declare_class_property` idempotency semantics,
class hierarchy via `add_is_subclass_of`, property scoping by `class_id`, and
`consolidate_schema("merge")` — including the gotcha that merge *adds* a new instance
edge to the target class without removing the edge to the source class.

```bash
python3 examples/feature-demos/schema_declaration_demo.py
```

**API key:** not required | **[Sample output](feature-demos/schema_declaration_demo.output.txt)**

### Provenance tracking (`provenance_demo.py`)

Multiple `add_source` calls with an `original` date, provenance on entity/fact/property
writes via `text=`/`source_id=`, and the `add_*_extraction` family for attaching
additional evidence to an element that already exists — each call *accumulates* one more
record rather than replacing the last.

```bash
python3 examples/feature-demos/provenance_demo.py
```

**API key:** not required | **[Sample output](feature-demos/provenance_demo.output.txt)**

### Change hooks (`change_hooks_demo.py`)

The event catalog across schema declarations, instance writes, updates, and deletes;
that idempotent `declare_*` calls don't re-fire; that hooks are scoped per database; and
that `remove_change_hook` needs the exact function object you registered.

```bash
python3 examples/feature-demos/change_hooks_demo.py
```

**API key:** not required | **[Sample output](feature-demos/change_hooks_demo.output.txt)**

### Schema/data export-import (`schema_data_export_import_demo.py`)

Migrating a KB across backends (`MemoryKBSource` → a `FileKBSource` on disk) via
`export_database_schema`/`import_database_schema` and
`export_database_data`/`import_database_data`. Demonstrates the one real asymmetry in
this API: schema import is safely idempotent by name, but data import always mints new
node ids — importing the same export twice duplicates every entity and fact.

```bash
python3 examples/feature-demos/schema_data_export_import_demo.py
```

**API key:** not required | **[Sample output](feature-demos/schema_data_export_import_demo.output.txt)**

### LLM knowledge extraction (`llm_extraction_demo.py`)

`import_text` — hand it free text and a model drives its own schema-discovery,
entity/fact extraction, and provenance-linking tool calls, reusing existing schema/
entities by name instead of duplicating them.

```bash
export OPENAI_API_KEY=sk-...
python3 examples/feature-demos/llm_extraction_demo.py
```

**Requires:** `OPENAI_API_KEY` | *(not live-verified in this session — no key available
in the sandboxed environment it was written in; verified structurally up to the point of
the actual OpenAI call, which is as far as it can go without one)*

### Natural language querying — answer (`answer_demo.py`)

`answer()`'s full return-shape matrix: how `natural_language` and `include_metadata`
combine to return a `list[dict]`, a `str`, or a tuple of either paired with a
`get_metadata()`-shaped dict per referenced node.

```bash
export OPENAI_API_KEY=sk-...
python3 examples/feature-demos/answer_demo.py
```

**Requires:** `OPENAI_API_KEY` | *(not live-verified in this session — same reason as
llm_extraction_demo.py)*

### Natural language querying — validate (`validate_demo.py`)

`validate()` against a statement the KB supports and one it contradicts, showing the
`ValidationResult(supporting, counter)` shape, plus `natural_language`/`include_metadata`.

```bash
export OPENAI_API_KEY=sk-...
python3 examples/feature-demos/validate_demo.py
```

**Requires:** `OPENAI_API_KEY` | *(not live-verified in this session — same reason as
llm_extraction_demo.py)*

### Natural language querying — enrich (`enrich_demo.py`)

`enrich()` walks outward from the best match to a query, returning ranked
`EnrichmentResult(entity, relation, value, distance)` context within `depth` hops —
unlike `answer`/`validate`, it has no `natural_language`/`include_metadata` flags.

```bash
export OPENAI_API_KEY=sk-...
python3 examples/feature-demos/enrich_demo.py
```

**Requires:** `OPENAI_API_KEY` | *(not live-verified in this session — same reason as
llm_extraction_demo.py)*

---

## framework-demos/ — agentic framework integrations

Both scripts use [LangGraph](https://github.com/langchain-ai/langgraph) — the only
agentic framework demonstrated for now, following the sibling `blue-data` repo's own
framework-demos precedent. They show two different ways to put an LLM agent in front of
a KB, contrasted directly against each other.

### Direct Python API (`langgraph_direct_demo.py`)

A hand-built 3-node LangGraph pipeline — `discover` (`KBSource.search_data`, a raw BM25
keyword search, no LLM call) → `select` (an LLM call picks which hits are relevant, then
pulls `get_metadata` on each) → `respond` (an LLM call composes the final answer). This
is the "build your own agent on top of the library's read primitives" story, as opposed
to just calling `KBSource.answer()` as a black box.

```bash
pip install langgraph langchain-openai
export OPENAI_API_KEY=sk-...
python3 examples/framework-demos/langgraph_direct_demo.py
```

**Requires:** `OPENAI_API_KEY` | *(not live-verified in this session — no key available
in the sandboxed environment it was written in. Verified further than the other
key-gated demos, though: with a syntactically-valid but fake key, the full pipeline runs
end to end — real BM25 search, real LangGraph node execution, real prompt construction —
and only fails at the genuine OpenAI network call with a 401, which is as far as it can
go without a real key.)*

### MCP-based integration (`langgraph_mcp_demo.py`)

No KB-specific code on the LangGraph side at all — a `create_react_agent` ReAct agent
freely chooses among all 24 tools auto-discovered via `langchain_mcp_adapters` from the
real `blue-data-kb-mcp` server, launched automatically as a subprocess (stdio transport,
pointed at a scratch directory so it never touches `~/.kbdb`). Contrast with the direct
demo's fixed pipeline: here the LLM decides which tools to call and in what order.

```bash
pip install langgraph langchain-openai langchain-mcp-adapters
pip install -e platform/mcp/
export OPENAI_API_KEY=sk-...
python3 examples/framework-demos/langgraph_mcp_demo.py
```

**Requires:** `OPENAI_API_KEY` | *(not live-verified in this session — same reason as
langgraph_direct_demo.py. Verified the same way: with a fake key, the subprocess launch,
all-24-tool auto-discovery, and ReAct agent construction all succeed, failing only at the
genuine OpenAI network call with a 401.)*

---

## integration-demos/ — full pipeline on one dataset

The direct kb-source analog of the sibling `blue-data` repo's own integration-demos
category: not a new subsystem combo (that's `backends/`) and not a new external
framework (that's `framework-demos/`), but the stages `backends/` and `feature-demos/`
each show in isolation, chained into one continuous narrative on one small, realistic
dataset.

### Full lifecycle: declare, populate, observe, search, migrate (`full_lifecycle_demo.py`)

Declare schema → populate a small org (3 people, 3 companies, provenance from two
source documents) → observe the writes via change hooks → BM25 keyword and semantic
search over the same KB → migrate the whole graph to a second, file-backed KB and
verify search still works after reconnecting. Deliberately needs no API key — the same
choice blue-data made for its own hero integration demo (`llm_fn=None` there; a
deterministic fake embedding here) — this is meant to be the one script in `examples/`
that always runs, nothing gated behind credentials.

> Along the way it surfaces a real gotcha: `export_database_data`/`import_database_data`
> carry over extraction *text* but never the SOURCE node itself (name, `original` date) —
> only entities/facts/property values are exported, so a migrated node's `extraction`
> field survives but its `source`/`source_original` fields come back `None`.

```bash
python3 examples/integration-demos/full_lifecycle_demo.py
```

**API key:** not required | **[Sample output](integration-demos/full_lifecycle_demo.output.txt)**

### Extraction feeds querying (`extraction_to_query_demo.py`)

`import_text()` extracts a small KB from one unstructured bio paragraph, then
`answer()`/`validate()`/`enrich()` query the *result* of that extraction — chaining
write-side LLM extraction and read-side LLM querying against the same `KBSource`
instance, which nothing in `feature-demos/` does (its LLM scripts each only exercise one
side). This is the "paste in a document, then ask questions about it" workflow end to end.

```bash
export OPENAI_API_KEY=sk-...
python3 examples/integration-demos/extraction_to_query_demo.py
```

**Requires:** `OPENAI_API_KEY` | *(not live-verified in this session — same reason as the
other OpenAI-gated demos. Verified the same way: with a fake key, schema seeding and
`import_text`'s request construction succeed, failing only at the genuine OpenAI network
call with a 401.)*

---

## scenario-demos/ — value proposition demos

The direct kb-source analog of `blue-data`'s own scenario-demos category: not a
how-to (that's `feature-demos/`) and not a full pipeline walkthrough (that's
`integration-demos/`), but one story with a quantitative before/after result, designed
for presentations. `blue-data`'s own scenario-demos use `ANTHROPIC_API_KEY`; this one
uses `OPENAI_API_KEY` instead, to match every other LLM-gated script already in this
directory.

### Catching contradictions with provenance, not guessing (`contradiction_catching_demo.py`)

**Story:** `add_entity_property_value` is additive — a second write for the same
entity+property doesn't replace the first, it coexists alongside it. Four people each get
two conflicting `TITLE` values written out of chronological order, from sources with
different `original` dates. A lookup with no notion of provenance can't tell which value
is current and just returns whichever one it sees first; a lookup that checks
`get_metadata`'s `source_original` field on every candidate resolves the conflict
correctly.

**Result:** naive lookup 0/4 correct → provenance-aware lookup 4/4 correct — computed
live from real graph traversal and `get_metadata` calls, not scripted. No LLM involved in
the scored part at all; a bonus section shows `validate()` surfacing the same conflict
conversationally, gracefully degrading with a clear message if `OPENAI_API_KEY` isn't set
so the demo always completes.

```bash
python3 examples/scenario-demos/contradiction_catching_demo.py
```

**API key:** not required for the scored comparison (optional `OPENAI_API_KEY` for the
`validate()` bonus section) | **[Sample output](scenario-demos/contradiction_catching_demo.output.txt)**

---

## Summary table

| File | Folder | API key | External | Output |
|------|--------|---------|----------|--------|
| `memory_quickstart.py` | backends | none | — | [output](backends/memory_quickstart.output.txt) |
| `file_backend_demo.py` | backends | none | — | [output](backends/file_backend_demo.output.txt) |
| `neo4j_backend_demo.py` | backends | none | Neo4j | — (not live-verified) |
| `keyword_search_demo.py` | backends | none | — | [output](backends/keyword_search_demo.output.txt) |
| `semantic_search_demo.py` | backends | none | — | [output](backends/semantic_search_demo.output.txt) |
| `chromadb_vector_backend_demo.py` | backends | none | — | [output](backends/chromadb_vector_backend_demo.output.txt) |
| `redis_vector_backend_demo.py` | backends | none | Redis Stack | [output](backends/redis_vector_backend_demo.output.txt) |
| `schema_declaration_demo.py` | feature-demos | none | — | [output](feature-demos/schema_declaration_demo.output.txt) |
| `provenance_demo.py` | feature-demos | none | — | [output](feature-demos/provenance_demo.output.txt) |
| `change_hooks_demo.py` | feature-demos | none | — | [output](feature-demos/change_hooks_demo.output.txt) |
| `schema_data_export_import_demo.py` | feature-demos | none | — | [output](feature-demos/schema_data_export_import_demo.output.txt) |
| `llm_extraction_demo.py` | feature-demos | OpenAI | — | — (not live-verified) |
| `answer_demo.py` | feature-demos | OpenAI | — | — (not live-verified) |
| `validate_demo.py` | feature-demos | OpenAI | — | — (not live-verified) |
| `enrich_demo.py` | feature-demos | OpenAI | — | — (not live-verified) |
| `langgraph_direct_demo.py` | framework-demos | OpenAI | — | — (not live-verified) |
| `langgraph_mcp_demo.py` | framework-demos | OpenAI | — | — (not live-verified) |
| `full_lifecycle_demo.py` | integration-demos | none | — | [output](integration-demos/full_lifecycle_demo.output.txt) |
| `extraction_to_query_demo.py` | integration-demos | OpenAI | — | — (not live-verified) |
| `contradiction_catching_demo.py` | scenario-demos | optional OpenAI | — | [output](scenario-demos/contradiction_catching_demo.output.txt) |

All five proposed sections (`backends/`, `feature-demos/`, `framework-demos/`,
`integration-demos/`, `scenario-demos/`) are now built — see
[docs/README.md](../docs/README.md) for the rest of the project's documentation.
