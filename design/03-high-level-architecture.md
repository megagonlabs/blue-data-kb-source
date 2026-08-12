# KBDB — High Level Architecture

## Diagram

```
                   External Interfaces

┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Python Lib  │  │ MCP Server  │  │  REST API   │  │     CLI     │
│(implemented)│  │(implemented)│  │  (planned)  │  │(implemented)│
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       └────────────────┴────────────────┴────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                               KBDB                                  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                       Schema Layer                          │   │
│  │                                                             │   │
│  │  ┌──────────────────────────────────────────────────────┐   │   │
│  │  │                   Schema Manager                     │   │   │
│  │  └──────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────┐  ┌────────────────────────────────┐  │
│  │    Extraction Engine     │  │        Knowledge Layer         │  │
│  │                          │  │                                │  │
│  │  ┌──────────────────┐    │  │  ┌────────────────────────┐   │  │
│  │  │    LLM Agent     │    │  │  │  Knowledge Manager     │   │  │
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
│                                └───────────────────────────────┘  │
│                                                                   │
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
                         └────────────────────────────────────────┘
```

## Components

### External Interfaces

Four interface adapters sit in front of KBDB and delegate to the core library. They share no internal logic — each is a thin translation layer over the Python library.

**Python Library** — Native Python API (`KBSource` and related classes). Used to embed KBDB directly in Python applications and agentic pipelines.

**MCP Server** — Exposes schema declaration, instance ingestion, and query operations as MCP tools, enabling LLM agents to interact with KBDB within an agentic context.

**CLI** — Command-line interface (Click + Rich). Used for interactive development, debugging, scripting, and schema inspection.

**REST API — planned, not yet implemented.** `platform/api/` is an empty directory today; nothing under this heading exists in the repository. HTTP/JSON interface (FastAPI), sketched to enable non-Python services and remote clients to call KBDB over the network — see [09-api-interface-spec.md](09-api-interface-spec.md) §2 for the speculative design.

### Schema Layer

Manages the typed vocabulary of the knowledge base — classes, relation types, properties, and class hierarchies.

- Provides idempotent declaration of schema elements: a repeated declaration for an already-existing name is a no-op
- Maintains hierarchical relationships between classes (subclass) and between relations (sub-relation)
- Supports domain and range constraints that specify the valid head and tail types for each relation
- Exports the schema to a portable representation and imports it from that representation into any database
- Exposes the meta-schema — a fixed description of KBDB's own graph structure — for introspection by agents and tools
- Supports schema consolidation: merging or splitting classes, relations, and properties, propagating the resulting changes to all affected instances
- Supports schema search: filtering schema elements by natural language query or keywords, enabling agents and tools to discover what the schema already captures before declaring new elements — delegated to the Search Indexer when search is enabled for the database, otherwise served directly by the Schema Manager

**Schema Manager** — Handles all schema declaration, hierarchy wiring, domain/range constraints, import/export, and consolidation.

### Knowledge Layer

Populates the knowledge base with concrete data and answers queries over it — entity instances, factual relations, property values, and the reasoning operations that work across them.

- All instance data is grounded in the schema: entities belong to classes, facts represent typed relations, and property values are typed literals
- Provenance is tracked at the individual item level: each entity, fact, and property value can carry a link to the exact source text span it was drawn from
- Downstream components are notified in real time whenever new data is written
- Supports natural language queries answered directly from instances, using the schema to interpret and scope the question
- Supports validation: given a statement, returns supporting and counter evidence drawn from existing facts and property values
- Supports enrichment: given a query, returns related knowledge that bears on the query without necessarily answering it directly — surfacing context, adjacent facts, and related entities
- Supports data search: filtering entities and facts by natural language query or keywords — delegated to the Search Indexer when search is enabled for the database, otherwise served directly by the Knowledge Manager

**Knowledge Manager** — Creates and stores entity, fact, and literal nodes; wires the typed edges that connect them to their schema elements and to each other; and delegates answer, validate, and enrich queries to the Query Engine.

**Query Engine** — An LLM agent embedded inside the Knowledge Manager that handles answer, validate, and enrich queries. It is given the current schema and a read-only tool set over the instance graph, reasons over the data, and returns a structured result. Not exposed at the `KBSource` level.

**Provenance Tracker** — Records the origin of each piece of knowledge by creating extraction nodes that capture the verbatim source text and linking them to both the item and the originating source document.

**Change Notifier** — Fires registered hooks synchronously after each instance write, delivering a structured event that describes the change — including the names of the affected entities, relations, and properties — to any registered listener.

### Search Indexer

Maintains a keyword (BM25) or embedding-based (semantic) search index over schema elements and instance data, kept up to date automatically as writes occur. Search is optional and configured per database; when it isn't enabled, schema and data search fall back to unindexed lookups in the Schema Manager and Knowledge Manager respectively.

- Indexes classes, relations, and properties for schema search, and entities and facts for data search
- Updates incrementally from change events rather than requiring a full rebuild after every write
- Supports a full reindex on demand, for example after a bulk import or a change in embedding model
- Backed by a pluggable vector store (in-memory, file, ChromaDB, or Redis) when semantic search is enabled

### Extraction Engine

Reads free text and populates the knowledge base by calling into the Schema Layer and Knowledge Layer using an LLM as the reasoning core.

- The LLM is given the current schema and a set of guiding queries, then asked to identify entities, facts, and property values in the text
- When the text introduces concepts not covered by the current schema, the LLM extends the schema before creating the corresponding instances
- Each item created during extraction is linked to the verbatim text span the LLM identified as its source

**LLM Agent** — Orchestrates the extraction loop: reads the schema, calls schema and instance tools in sequence, and records the source text span for each item it creates.

### Graph Store

Stores all schema and instance data as a typed graph of nodes and edges. The store is abstract — multiple backends are supported and can be swapped without changing any application code.

**Memory** — In-process graph store. Used for development, testing, and short-lived pipelines where persistence is not required.

**File** — JSON-file-backed graph store, one file per database. The default backend for the CLI, persisting databases under `~/.kbdb/` (or `KBDB_DATA_DIR`) without requiring an external database server.

**Neo4j** — Persistent graph database backend. Used for production deployments where the knowledge base must survive process restarts and support concurrent access.
