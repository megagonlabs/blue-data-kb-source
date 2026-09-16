# Architecture

## Overview

`blue-data-kb-source` has two tiers:

- **`lib/`** — the core Python library (`KBSource` and its internal managers)
- **`platform/`** — deployable components built on top of `lib/` (CLI, MCP server, and a planned REST API)

### lib/ core

```
                   External Interfaces

┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Python Lib  │  │ MCP Server  │  │  REST API   │  │     CLI     │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       └────────────────┴────────────────┴────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                              KBSource                               │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                         Schema Layer                        │    │
│  │  ┌──────────────────────────────────────────────────────┐   │    │
│  │  │                    Schema Manager                    │   │    │
│  │  │              ┌──────────────────────────┐            │   │    │
│  │  │              │ Schema Expansion Engine  │            │   │    │
│  │  │              │       (LLM Agent)        │            │   │    │
│  │  │              └──────────────────────────┘            │   │    │
│  │  └──────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  ┌──────────────────────────┐  ┌────────────────────────────────┐   │
│  │    Ingestion Manager     │  │        Knowledge Layer         │   │
│  │  ┌──────────────────┐    │  │  ┌────────────────────────┐    │   │
│  │  │  Node Resolver   │    │  │  │  Knowledge Manager     │    │   │
│  │  └──────────────────┘    │  │  │  ┌──────────────────┐  │    │   │
│  │  ┌──────────────────┐    │  │  │  │  Query Engine    │  │    │   │
│  │  │    LLM judges    │    │  │  │  │  (LLM Agent)     │  │    │   │
│  │  └──────────────────┘    │  │  │  └──────────────────┘  │    │   │
│  └──────────────────────────┘  │  └────────────────────────┘    │   │
│                                │  ┌────────────────────────┐    │   │
│  ┌──────────────────────────┐  │  │  Provenance Tracker    │    │   │
│  │    Extraction Engine     │  │  └────────────────────────┘    │   │
│  │  ┌──────────────────┐    │  │  ┌────────────────────────┐    │   │
│  │  │    LLM Agent     │    │  │  │   Change Notifier      │    │   │
│  │  └──────────────────┘    │  │  └────────────────────────┘    │   │
│  └──────────────────────────┘  └────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                        Search Indexer                       │    │
│  │        (used by Schema Manager and Knowledge Manager)       │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────┬───────────────────────┘
                                              │
                         ┌────────────────────▼───────────────────┐
                         │ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
                         │ │  Memory  │ │   File   │ │  Neo4j   │ │
                         │ └──────────┘ └──────────┘ └──────────┘ │
                         │        GraphSource (blue-data)         │
                         └────────────────────────────────────────┘
```

`KBSource` is an abstract class — it must be mixed in with a concrete `GraphSource` backend via Python MRO. Three ready-made mixins ship with the library (`MemoryKBSource`, `FileKBSource`, `NEO4JKBSource` — see [Graph backends](#graph-backends) below); building a genuinely new one follows the same pattern directly (`class MyKBSource(KBSource, MyGraphSource): pass`, see [extending.md](extending.md)). Either way, the mixed-in `GraphSource` primitives (`add_node`, `add_edge`, `get_node`, `get_neighbors`, `match_nodes`, ...) are what `KBSource` adds everything above on top of: typed schema/instance builders, provenance, search, LLM extraction, and LLM querying.

| Component | Module | Role |
|---|---|---|
| `KBSource` | `kb_source.py` | Entry point; delegates to every manager below |
| Schema Manager | `schema/manager.py` | Declares/updates/deletes classes, relations, properties; hierarchy; `attach_property`; export/import; meta-schema; owns Schema Expansion Engine |
| Schema Expansion Engine | `schema/expansion.py` | LLM agent behind `expand_schema` — grows CLASS/RELATION/PROPERTY only, grounded by text; not exposed directly on `KBSource` |
| Knowledge Manager | `knowledge/manager.py` | Creates/updates/deletes entities, facts, property values; data export/import; `get_metadata`; owns Query Engine |
| Query Engine | `knowledge/engine.py` | LLM agent behind `answer`/`validate`/`enrich`; not exposed directly on `KBSource` |
| Node Resolver | `resolution/resolver.py` | Ranked name resolution (exact, normalized, search) for the ingest path |
| Ingestion Manager | `ingestion/manager.py` | `assert_*` and bulk `ingest`: resolve-or-create with LLM judges, widening, and evidence dedupe |
| Provenance Tracker | `provenance/tracker.py` | Creates `SOURCE`/`EXTRACTION` nodes and wires `EXTRACTED_FROM` edges |
| Change Notifier | `notification/notifier.py` | Fires per-database hooks synchronously after each write |
| Search Indexer | `search/indexer.py` | BM25/semantic index over schema and instance data, kept in sync via change hooks |
| Extraction Engine | `extraction/engine.py` | LLM agent behind `import_text` — extracts entities/facts/property values against the schema as it stands; never declares schema itself |

### platform/ layer

```
  ┌───────────────────────────────────────────────────────────────────────────┐
  │                            platform/ components                           │
  │                                                                           │
  │  ┌──────────────────────┐   ┌──────────────────────┐                      │
  │  │         cli          │   │         mcp          │                      │
  │  │  (Click + Rich)      │   │  (FastMCP server)    │                      │
  │  │  terminal, one       │   │  stdio/MCP protocol  │                      │
  │  │  KBSource per        │   │  26 tools: schema,   │                      │
  │  │  registered database │   │  knowledge, query,   │                      │
  │  │  (~/.kb registry)  │   │  extraction, source  │                      │
  │  └──────────┬───────────┘   └──────────┬───────────┘                      │
  │             │                          │                                  │
  │             │            ┌─────────────┘                                  │
  │             │            │  (no shared state — each owns its own          │
  │             │            │   KBSource singleton, configured separately)   │
  │             ▼            ▼                                                │
  │  ┌──────────────────────────────────────┐   ┌──────────────────────┐      │
  │  │                                      │   │         api          │      │
  │  │               KBSource               │   │  (FastAPI, planned)  │      │
  │  │                                      │   └──────────────────────┘      │
  │  └──────────────────┬───────────────────┘                                 │
  │                     │  stdio, spawned per `expand_schema` call            │
  │                     │  only when reference="mcp"                          │
  │                     ▼                                                     │
  │           ┌──────────────────────┐                                        │
  │           │    wordnet-mcp       │                                        │
  │           │  (FastMCP server)    │                                        │
  │           │  WordNet lookups     │                                        │
  │           │  via nltk.wordnet    │                                        │
  │           └──────────────────────┘                                        │
  └───────────────────────────────────────────────────────────────────────────┘
                              │  Python import
                              ▼
                        lib/src/blue_data_kb_source/
```

| Component | Entry point | Transport | Status |
|---|---|---|---|
| `cli` | `platform/cli/` (`kb`) | Terminal (Click) | Implemented |
| `mcp` | `platform/mcp/` (`blue-data-kb-mcp`) | stdio (MCP) | Implemented |
| `wordnet-mcp` | `platform/wordnet-mcp/` (`blue-data-wordnet-mcp`) | stdio (MCP) | Implemented — optional, referenced by `expand_schema(reference="mcp")` |
| `api` | `platform/api/` | HTTP (FastAPI) | Planned — see [api.md](api.md) |

The `cli` and `mcp` processes are independent — they do not share state with each other, and each holds its own `KBSource` instance. `cli` resolves its backend per invocation from `~/.kb/registry.json` (multiple named databases, each independently configured — see [cli.md](cli.md)); `mcp` owns a single `KBSource` for the process lifetime, configured entirely by `BLUE_DATA_KB_SOURCE_*` environment variables, with `database` as a plain per-tool-call argument (see [mcp.md](mcp.md)). This is a deliberate difference, not an oversight — `mcp` depends only on `blue-data-kb-source`, not on `blue-data-kb-cli`, so each platform component stays independently deployable.

`wordnet-mcp` is a standalone read-only MCP server, not a `KBSource` dependency — the Schema Expansion Engine (`schema/expansion.py`) spawns it (or connects to it, for `sse`/`streamable_http` transports) itself when `expand_schema` is called with `reference="mcp"` and an entry for it in `mcp_servers`, offering its tools (hypernym/synonym lookups) to the LLM agent alongside the built-in schema tools for that one call. Neither `cli` nor `mcp` talk to it directly.

---

## The node/edge model

Every knowledge base is a typed graph with eight node labels and eleven edge types, all declared uniformly through `KBSource` — including a **meta-schema** (`fetch_meta_schema()`) that describes this taxonomy using the same `CLASS`/`RELATION`/`PROPERTY` vocabulary as any user-defined schema.

**Schema nodes** — the vocabulary:

| Label | Represents |
|---|---|
| `CLASS` | A category or type, e.g. `PERSON`, `COMPANY` |
| `RELATION` | The nature of a relationship between two classes, e.g. `WORKED_AT` |
| `PROPERTY` | A feature of a class, relation, entity, or fact, e.g. `AGE`, `START_DATE` |

**Instance nodes** — the data:

| Label | Represents |
|---|---|
| `ENTITY` | A specific instance of a class, e.g. `Alice` |
| `FACT` | A specific instantiation of a relation between two entities |
| `LITERAL` | A property value attached to an entity or fact |

**Provenance nodes**:

| Label | Represents |
|---|---|
| `SOURCE` | A source document |
| `EXTRACTION` | A verbatim text span an item was drawn from |

Key edges: `IS_SUBCLASS_OF`/`IS_INSTANCE_OF` (hierarchy/membership), `DEFINE_PROPERTY`/`IS_VALUE_OF`/`HAS_PROPERTY` (property wiring), `HEAD`/`TAIL`/`REPRESENTS` (fact structure), `DOMAIN`/`RANGE` (relation constraints), `EXTRACTED_FROM` (provenance chain: entity/fact/literal → `EXTRACTION` → `SOURCE`).

Every node carries `created`/`modified` timestamps (set automatically, not caller-supplied); every edge carries `created`. `SOURCE` additionally carries a caller-supplied `original` date. See [api-reference.md](api-reference.md#timestamps-and-provenance) for the full shape.

---

## Data flow: answer / validate / enrich

`answer`, `validate`, and `enrich` share one LLM agent loop (`QueryEngine._run_agent`), differing only in system prompt and result parsing:

```
caller
  → KnowledgeManager.answer/validate/enrich
      → QueryEngine.answer/validate/enrich
          → fetch_database_schema()          # schema context for the prompt
          → LLM agent loop:
              agent calls search_entities / search_facts / get_entity_facts /
                get_entity_properties / get_neighbors / search_schema / get_metadata*
              (* get_metadata only offered when include_metadata=True)
              ... until agent calls return_result
          → parse payload:
              answer    → list[dict]           (LLM-chosen column names)
              validate  → ValidationResult      ({supporting, counter})
              enrich    → list[EnrichmentResult]
```

With `include_metadata=True` (`answer`/`validate` only), the engine harvests every `get_metadata` tool call the agent makes into a `metadata: dict[node_id, dict]` returned alongside the result — independent of whether the agent's own `return_result` payload references that id. With `natural_language=True` too, a second LLM call turns the metadata into a prose provenance section appended after the main answer.

---

## Data flow: assert_* / ingest (ingestion)

```
assert_fact("main", "Alice", "WORKED_AT", "IBM")
  │
  ├─ phase 1 (read-only): Node Resolver ranks candidates per operand;
  │    LLM judges name classes / place properties / write descriptions
  │    — ambiguity or judge failure raises IngestionError, nothing written
  │
  └─ phase 2: writes through the same add_*/declare_*/attach_* operations
       direct callers use — creating what didn't resolve, widening domain/range
       on a non-conforming fact, or attaching new evidence to a known
       triple — so hooks and search indexing fire normally
```

`ingest` loops a ten-arm JSON payload through the per-item asserts in file order and
returns an `IngestReport` (written/deduped/skipped, per-label created counts, widened
relations, and per-row failures with their candidates) instead of raising.

---

## Data flow: expand_schema (the extraction path's schema pass)

```
caller
  → SchemaManager.expand_schema(text, depth, reference, mcp_servers, dedup_mode)
      → SchemaExpansionEngine.expand_schema(...)
          → fetch_database_schema()          # schema context for the prompt
          → (reference="mcp") connect each entry in mcp_servers over
              stdio/sse/streamable_http, collect its tools name-prefixed
              "mcp__<server>__<tool>"
          → LLM agent loop:
              agent calls search_schema (unless dedup_mode="none")
                to check for an existing near-duplicate before declaring
              agent calls declare_class / declare_relation_type /
                declare_class_property / declare_relation_property
                → an automatic dedup check (per dedup_mode) also runs here,
                  regardless of whether the agent called search_schema itself
              agent calls add_subclass_of to place a new CLASS/RELATION under
                an existing parent (never a declare_relation_type call — a
                taxonomic name like IS_A/TYPE_OF/SUBCLASS_OF is rejected there)
              agent calls attach_property to reuse an existing PROPERTY on
                another CLASS/RELATION instead of declaring a duplicate
              (reference="mcp") agent may also call the connected server's
                tools, e.g. WordNet hypernym/synonym lookups, to ground names
                and hierarchy placement
              ... until the agent stops issuing tool calls, or max_turns is hit
```

`expand_schema` never creates `ENTITY`/`FACT`/`LITERAL` nodes — only `CLASS`/`RELATION`/`PROPERTY` and the edges between them. Idempotent declarations mean re-running it over overlapping text converges rather than duplicating.

---

## Data flow: import_text (extraction)

```
caller
  → KBSource.import_text(text, queries, source_id, expand_schema, schema_expansion_*)
      → (expand_schema=True, the default) SchemaManager.expand_schema(...)
          — see "Data flow: expand_schema" above; schema_expansion_* params feed
            its depth/reference/mcp_servers/dedup_mode one-to-one
      → ExtractionEngine.import_text(text, queries, source_id)
          → fetch_database_schema()          # schema context for the prompt,
                                              # now including anything expand_schema just added
          → LLM agent loop:
              agent calls search_schema to resolve a mention to an existing
                CLASS/RELATION/PROPERTY — no match means skip that item;
                import_text never declares new schema itself
              agent calls add_entity / add_fact / add_entity_property_value / ...
                passing the verbatim text span as `text`
                → ProvenanceTracker creates an EXTRACTION node, wires EXTRACTED_FROM
                  to the instance and (if source_id given) to the SOURCE
                → ChangeNotifier fires registered hooks for the database
              ... until the agent stops issuing tool calls, or max_turns is hit
```

Idempotent instance resolution (`find_entity`/`find_fact` before creating) means re-running `import_text` over overlapping text converges rather than duplicating — an entity/fact already present accumulates a new `EXTRACTED_FROM` edge (via `link_extraction`) instead of a duplicate node. Pass `expand_schema=False` to skip the first phase entirely and extract only against whatever schema already exists (e.g. one grown once via a prior `expand_schema` call and reused across many documents).

---

## Search

`KBSearchIndexer` is optional per database (`properties["search"]["mode"]` in `("bm25", "semantic")`, or a live `vector_source` given directly — see [api-reference.md](api-reference.md#kbsource)) and, once enabled, registers itself as a global change hook — so it stays in sync incrementally as writes happen anywhere else in the system, without requiring a full rebuild. It maintains two collections per database (`schema`, `data`), each indexed with a composite text built from the node's own name/description plus relevant neighbors (e.g. a fact's index text includes its head/relation/tail names), so a query can match context a node doesn't literally contain in its own name.

`mode="keyword"` ranks by BM25 (`rank-bm25`, falling back to substring matching when the `search` extra isn't installed); `mode="semantic"` additionally ranks by embedding similarity through a pluggable `VectorSource` (in-memory, file, ChromaDB, or Redis). `reindex()` rebuilds the corpus from scratch — used after a bulk import or an embedding model change.

---

## Graph backends

| Backend | `KBSource` mixin | Persistence | Concurrency | Typical use |
|---|---|---|---|---|
| `MemoryGraphSource` | `MemoryKBSource` (`from blue_data_kb_source import ...`) | None (in-process) | Single process | Development, testing, short-lived pipelines |
| `FileGraphSource` | `FileKBSource` (`from blue_data_kb_source import ...`) | One JSON file per database | Single process | CLI default (`~/.kb/` or `BLUE_DATA_KB_SOURCE_DATA_DIR`) — no server required |
| `NEO4JSource` | `NEO4JKBSource` (`from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource`) | Neo4j database | Concurrent | Production deployments |

All three `GraphSource` implementations come from the sibling `blue-data` library; `kb_source.py` itself never imports a concrete backend directly — each mixin lives in its own module (`memory_kb_source.py`, `file_kb_source.py`, `neo4j/neo4j_kb_source.py`) that does the mixing. `NEO4JKBSource` is deliberately kept out of the top-level package: importing it is what pulls in the `neo4j` driver, so plain `import blue_data_kb_source` never requires it to be installed. Each mixin overrides `_initialize_connection_properties` to report its own `kb/memory`/`kb/file`/`kb/neo4j` connection protocol rather than inheriting the mixed-in `GraphSource`'s `graph/*` one. See [extending.md](extending.md) for implementing another backend the same way.
