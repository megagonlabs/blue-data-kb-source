# KBDB — High Level Architecture

## Diagram

```
                   External Interfaces

┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Python Lib  │  │ MCP Server  │  │  REST API   │  │     CLI     │
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
│  │  └──────────────────┘    │  │  └────────────────────────┘   │  │
│  └──────────────────────────┘  │  ┌────────────────────────┐   │  │
│                                │  │  Provenance Tracker    │   │  │
│                                │  └────────────────────────┘   │  │
│                                │  ┌────────────────────────┐   │  │
│                                │  │   Change Notifier      │   │  │
│                                │  └────────────────────────┘   │  │
│                                └────────────────────────────────┘  │
└─────────────────────────────────────────────┬───────────────────────┘
                                              │
                              ┌───────────────▼──────────────┐
                              │  ┌──────────┐  ┌──────────┐  │
                              │  │  Memory  │  │  Neo4j   │  │
                              │  └──────────┘  └──────────┘  │
                              └──────────────────────────────┘
```

## Components

### External Interfaces

Four interface adapters sit in front of KBDB and delegate to the core library. They share no internal logic — each is a thin translation layer over the Python library.

**Python Library** — Native Python API (`KBSource` and related classes). Used to embed KBDB directly in Python applications and agentic pipelines.

**MCP Server** — Exposes schema declaration, instance ingestion, and query operations as MCP tools, enabling LLM agents to interact with KBDB within an agentic context.

**REST API** — HTTP/JSON interface (FastAPI). Enables non-Python services and remote clients to call KBDB over the network.

**CLI** — Command-line interface (Click + Rich). Used for interactive development, debugging, scripting, and schema inspection.

### Schema Layer

Manages the typed vocabulary of the knowledge base — classes, relation types, properties, and class hierarchies.

- Provides idempotent declaration of schema elements: a repeated declaration for an already-existing name is a no-op
- Maintains hierarchical relationships between classes (subclass) and between relations (sub-relation)
- Supports domain and range constraints that specify the valid head and tail types for each relation
- Exports the schema to a portable representation and imports it from that representation into any database
- Exposes the meta-schema — a fixed description of KBDB's own graph structure — for introspection by agents and tools
- Supports schema consolidation: merging or splitting classes, relations, and properties, propagating the resulting changes to all affected instances
- Supports schema search: filtering schema elements by natural language query or keywords, enabling agents and tools to discover what the schema already captures before declaring new elements

**Schema Manager** — Handles all schema declaration, hierarchy wiring, domain/range constraints, import/export, consolidation, and search operations.

### Knowledge Layer

Populates the knowledge base with concrete data and answers queries over it — entity instances, factual relations, property values, and the reasoning operations that work across them.

- All instance data is grounded in the schema: entities belong to classes, facts represent typed relations, and property values are typed literals
- Provenance is tracked at the individual item level: each entity, fact, and property value can carry a link to the exact source text span it was drawn from
- Downstream components are notified in real time whenever new data is written
- Supports natural language queries answered directly from instances, using the schema to interpret and scope the question
- Supports validation: given a statement, returns supporting and counter evidence drawn from existing facts and property values
- Supports enrichment: given a query, returns related knowledge that bears on the query without necessarily answering it directly — surfacing context, adjacent facts, and related entities

**Knowledge Manager** — Creates and stores entity, fact, and literal nodes; wires the typed edges that connect them to their schema elements and to each other; and handles answer, validate, and enrich queries over the instance graph.

**Provenance Tracker** — Records the origin of each piece of knowledge by creating extraction nodes that capture the verbatim source text and linking them to both the item and the originating source document.

**Change Notifier** — Fires registered hooks synchronously after each instance write, delivering a structured event that describes the change — including the names of the affected entities, relations, and properties — to any registered listener.

### Extraction Engine

Reads free text and populates the knowledge base by calling into the Schema Layer and Knowledge Layer using an LLM as the reasoning core.

- The LLM is given the current schema and a set of guiding queries, then asked to identify entities, facts, and property values in the text
- When the text introduces concepts not covered by the current schema, the LLM extends the schema before creating the corresponding instances
- Each item created during extraction is linked to the verbatim text span the LLM identified as its source

**LLM Agent** — Orchestrates the extraction loop: reads the schema, calls schema and instance tools in sequence, and records the source text span for each item it creates.

### Graph Store

Stores all schema and instance data as a typed graph of nodes and edges. The store is abstract — multiple backends are supported and can be swapped without changing any application code.

**Memory** — In-process graph store. Used for development, testing, and short-lived pipelines where persistence is not required.

**Neo4j** — Persistent graph database backend. Used for production deployments where the knowledge base must survive process restarts and support concurrent access.
