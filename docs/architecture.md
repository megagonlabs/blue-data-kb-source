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
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                       Schema Layer                          │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │                   Schema Manager                     │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────┐  ┌────────────────────────────────┐  │
│  │    Extraction Engine     │  │        Knowledge Layer         │  │
│  │  ┌──────────────────┐    │  │  ┌────────────────────────┐   │  │
│  │  │    LLM Agent      │    │  │  │  Knowledge Manager     │   │  │
│  │  └──────────────────┘    │  │  │  ┌──────────────────┐  │   │  │
│  └──────────────────────────┘  │  │  │  Query Engine    │  │   │  │
│                                │  │  │  (LLM Agent)     │  │   │  │
│                                │  │  └──────────────────┘  │   │  │
│                                │  └────────────────────────┘   │  │
│                                │  ┌────────────────────────┐   │  │
│                                │  │  Provenance Tracker    │   │  │
│                                │  └────────────────────────┘   │  │
│                                │  ┌────────────────────────┐   │  │
│                                │  │   Change Notifier      │   │  │
│                                │  └────────────────────────┘   │  │
│                                └────────────────────────────────┘  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                       Search Indexer                        │   │
│  │       (used by Schema Manager and Knowledge Manager)        │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────┬───────────────────────┘
                                              │
                         ┌────────────────────▼───────────────────┐
                         │ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
                         │ │  Memory  │ │   File   │ │  Neo4j   │ │
                         │ └──────────┘ └──────────┘ └──────────┘ │
                         │        GraphSource (blue-data)         │
                         └────────────────────────────────────────┘
```

`KBSource` is an abstract class — it must be mixed in with a concrete `GraphSource` backend via Python MRO (`class MyKBSource(KBSource, MemoryGraphSource): pass`). It inherits `GraphSource`'s primitives (`add_node`, `add_edge`, `get_node`, `get_neighbors`, `match_nodes`, ...) and adds everything above on top: typed schema/instance builders, provenance, search, LLM extraction, and LLM querying.

| Component | Module | Role |
|---|---|---|
| `KBSource` | `kb_source.py` | Entry point; delegates to every manager below |
| Schema Manager | `schema/manager.py` | Declares/updates/deletes classes, relations, properties; hierarchy; export/import; meta-schema |
| Knowledge Manager | `knowledge/manager.py` | Creates/updates/deletes entities, facts, property values; data export/import; `get_metadata`; owns Query Engine |
| Query Engine | `knowledge/engine.py` | LLM agent behind `answer`/`validate`/`enrich`; not exposed directly on `KBSource` |
| Provenance Tracker | `provenance/tracker.py` | Creates `SOURCE`/`EXTRACTION` nodes and wires `EXTRACTED_FROM` edges |
| Change Notifier | `notification/notifier.py` | Fires per-database hooks synchronously after each write |
| Search Indexer | `search/indexer.py` | BM25/semantic index over schema and instance data, kept in sync via change hooks |
| Extraction Engine | `extraction/engine.py` | LLM agent behind `import_text` |

### platform/ layer

```
  ┌───────────────────────────────────────────────────────────────────────────┐
  │                          platform/ components                             │
  │                                                                           │
  │  ┌──────────────────────┐   ┌──────────────────────┐                     │
  │  │        cli            │   │        mcp            │                   │
  │  │  (Click + Rich)       │   │  (FastMCP server)     │                   │
  │  │  terminal, one         │   │  stdio/MCP protocol   │                   │
  │  │  KBSource per          │   │  24 tools: schema,    │                   │
  │  │  registered database   │   │  knowledge, query,    │                   │
  │  │  (~/.kbdb registry)     │   │  extraction, source   │                   │
  │  └──────────┬─────────────┘   └──────────┬─────────────┘                 │
  │             │                            │                                │
  │             │            ┌───────────────┘                                │
  │             │            │  (no shared state — each owns its own          │
  │             │            │   KBSource singleton, configured separately)   │
  │             ▼            ▼                                                │
  │  ┌──────────────────────────────────────┐   ┌──────────────────────┐     │
  │  │                                       │   │        api             │   │
  │  │              KBSource                 │   │  (FastAPI, planned)   │   │
  │  │                                       │   └───────────────────────┘   │
  │  └──────────────────────────────────────┘                                │
  └───────────────────────────────────────────────────────────────────────────┘
                              │  Python import
                              ▼
                        lib/src/blue_data_kb_source/
```

| Component | Entry point | Transport | Status |
|---|---|---|---|
| `cli` | `platform/cli/` (`kb`) | Terminal (Click) | Implemented |
| `mcp` | `platform/mcp/` (`blue-data-kb-mcp`) | stdio (MCP) | Implemented |
| `api` | `platform/api/` | HTTP (FastAPI) | Planned — see [api.md](api.md) |

The `cli` and `mcp` processes are independent — they do not share state with each other, and each holds its own `KBSource` instance. `cli` resolves its backend per invocation from `~/.kbdb/registry.json` (multiple named databases, each independently configured — see [cli.md](cli.md)); `mcp` owns a single `KBSource` for the process lifetime, configured entirely by `BLUE_DATA_KB_SOURCE_*` environment variables, with `database` as a plain per-tool-call argument (see [mcp.md](mcp.md)). This is a deliberate difference, not an oversight — `mcp` depends only on `blue-data-kb-source`, not on `blue-data-kb-cli`, so each platform component stays independently deployable.

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

## Data flow: import_text (extraction)

```
caller
  → ExtractionEngine.import_text(text, queries, source_id, schema_discovery_mode)
      → fetch_database_schema()          # schema context for the prompt
      → LLM agent loop:
          agent calls search_schema (unless schema_discovery_mode="none")
            to check for an existing near-duplicate before declaring
          agent calls declare_class / declare_relation_type / declare_class_property / ...
            for concepts not yet in the schema
          agent calls add_entity / add_fact / add_entity_property_value / ...
            passing the verbatim text span as `text`
            → ProvenanceTracker creates an EXTRACTION node, wires EXTRACTED_FROM
              to the instance and (if source_id given) to the SOURCE
            → ChangeNotifier fires registered hooks for the database
          ... until the agent stops issuing tool calls
```

Idempotent schema declarations mean re-running `import_text` over overlapping text converges rather than duplicating — a concept already declared is reused, and an entity/fact already present accumulates a new `EXTRACTED_FROM` edge (via `link_extraction`) instead of a duplicate node.

---

## Search

`KBSearchIndexer` is optional per database (`enable_search=True`, or passing `vector_source`) and, once enabled, registers itself as a global change hook — so it stays in sync incrementally as writes happen anywhere else in the system, without requiring a full rebuild. It maintains two collections per database (`schema`, `data`), each indexed with a composite text built from the node's own name/description plus relevant neighbors (e.g. a fact's index text includes its head/relation/tail names), so a query can match context a node doesn't literally contain in its own name.

`mode="keyword"` ranks by BM25 (`rank-bm25`, falling back to substring matching when the `search` extra isn't installed); `mode="semantic"` additionally ranks by embedding similarity through a pluggable `VectorSource` (in-memory, file, ChromaDB, or Redis). `reindex()` rebuilds the corpus from scratch — used after a bulk import or an embedding model change.

---

## Graph backends

| Backend | Persistence | Concurrency | Typical use |
|---|---|---|---|
| `MemoryGraphSource` | None (in-process) | Single process | Development, testing, short-lived pipelines |
| `FileGraphSource` | One JSON file per database | Single process | CLI default (`~/.kbdb/` or `KBDB_DATA_DIR`) — no server required |
| `NEO4JSource` | Neo4j database | Concurrent | Production deployments |

All three implement the same `GraphSource` interface from the sibling `blue-data` library; `KBSource` never imports a concrete backend directly (see [extending.md](extending.md) for implementing another one).
