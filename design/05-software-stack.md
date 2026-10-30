# blue-kb — Software Stack

> Interfaces marked **provisional** are initial suggestions based on expected usage patterns. They will be revised once the respective component is implemented and inspected.

---

## Language & Runtime

| Concern | Choice |
|---------|--------|
| Language | Python |
| Supported versions | 3.12, 3.13 |
| Package manager | pip |
| Project config | `pyproject.toml` |

---

## LLM

| Concern | Choice |
|---------|--------|
| Initial provider | OpenAI (`openai>=1.0`) |
| Interface | OpenAI function-calling (tool use) |

Four components call the OpenAI API directly, all through the `openai` Python SDK. The ingestion judges are the lightest dependency: single forced-function calls, no loop, made during `assert_*`/`ingest` under the default `schema_creation_mode="llm"` and skipped entirely under `"none"`. The Schema Expansion Engine (inside the Schema Layer) runs an OpenAI tool-use loop that drives schema declaration (`declare_class`/`declare_relation_type`/etc.); with `reference="mcp"` its tool set is additionally extended at call time with tools from connected MCP servers (e.g. WordNet via `platform/wordnet-mcp`) — see MCP Server below. The Extraction Engine runs its own tool loop that drives *only* instance creation (`add_entity`/`add_fact`/etc.) by presenting the relevant KB operations as OpenAI function definitions — it has no schema-declaration tool; `KBSource.import_text` composes the two, calling `expand_schema` first (`expand_schema=True`, the default) and then running the extraction loop against the result. The Query Engine (inside the Knowledge Layer) runs its own tool-use loop for each of `answer`, `validate`, and `enrich` over a read-only set of graph tools. The model is configurable per call (`model` parameter, defaulting to `gpt-4o`). The Graph Store has no LLM dependency; the four above all require an API key at call time, so the `openai` extra gates ingest, schema expansion, extraction, and querying alike.

**OpenAI SDK usage:**

| Class / Function | Role |
|-----------------|------|
| `openai.OpenAI` / `openai.AsyncOpenAI` | Client for API calls (Query Engine uses the async client) |
| `client.chat.completions.create` | Drives the tool-use loop |
| `client.embeddings.create` | Generates embeddings for semantic search (Search Indexer) |
| Function definitions (JSON Schema) | Expose KB operations as LLM-callable tools |

**Configuration:**

The `OPENAI_API_KEY` environment variable is read by the `openai` SDK automatically. No other global configuration is required. The model and API key can also be passed explicitly at call time.

---

## Storage

Storage is provided by `blue-data` via the `GraphSource` abstract base class. blue-kb interacts only with the `GraphSource` interface and does not depend on any specific backend implementation.

### GraphSource

Used for: all schema nodes (CLASS, RELATION, PROPERTY), all instance nodes (ENTITY, FACT, LITERAL), and all provenance nodes (SOURCE, EXTRACTION), along with the typed edges that connect them.

**Interface** (from `blue_data.source.graph`):

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_database` | `create_database(database: str) -> None` | Initialize a new named database |
| `add_node` | `add_node(database, label, properties, node_id?) -> str` | Create a node; returns its ID |
| `add_edge` | `add_edge(database, source_id, target_id, edge_type, properties?) -> str` | Create a directed edge |
| `get_node` | `get_node(database, node_id) -> dict \| None` | Retrieve a node by ID |
| `get_neighbors` | `get_neighbors(database, node_id, edge_type?, direction?) -> list[dict]` | Traverse edges from a node |
| `match_nodes` | `match_nodes(database, label, properties?) -> list[dict]` | Find nodes by label and property filter |

### Backends

| Backend | Class | Persistence | Typical Use |
|---------|-------|-------------|-------------|
| Memory | `MemoryGraphSource` | None (in-process) | Development, testing, short-lived pipelines |
| File | `FileGraphSource` | Yes (JSON file per database) | CLI default — persistence with no server; several processes may share it (per-database write lock) |
| Neo4j | `Neo4jGraphSource` | Yes | Production deployments requiring durability and concurrent access |

All three backends are provided by `blue-data`. blue-kb selects a backend via Python MRO — the application subclasses `KBSource` alongside the chosen `GraphSource` concrete class, with no conditional logic in blue-kb itself.

---

## Search

Search is optional per database and is provided by the Search Indexer, layered on top of the Graph Store rather than replacing it.

| Concern | Choice |
|---------|--------|
| Keyword search | BM25 via `rank-bm25` |
| Semantic search | Embedding similarity via a `VectorSource` backend |
| Embedding provider | OpenAI (`client.embeddings.create`, default model `text-embedding-3-small`) |

### VectorSource backends

Used only when a database is registered with `--search semantic`. Provided by `blue-data`.

| Backend | Class | Persistence | Typical Use |
|---------|-------|-------------|-------------|
| Memory | `MemoryVectorSource` | None (in-process) | Development, testing |
| File | `FileVectorSource` | Yes | Single-process semantic search without an external service |
| ChromaDB | `ChromaDBVectorSource` | Yes | Dedicated vector database deployments |
| Redis | `RedisVectorSource` | Yes | Shared/concurrent access to the vector index |

Keyword search (BM25) requires no vector backend and works out of the box once `rank-bm25` is installed; semantic search additionally requires a `VectorSource` and an embedding function.

---

## External Interfaces

### REST API

**Provisional**

| Concern | Choice |
|---------|--------|
| Framework | FastAPI |
| ASGI server | Uvicorn |

FastAPI provides automatic OpenAPI schema generation and async request handling. Endpoints map to the core schema and knowledge operations exposed by the Python library.

### MCP Server

**Implemented** — `platform/mcp/` (package `blue-data-kb-mcp`)

| Concern | Choice |
|---------|--------|
| Library | `mcp[cli]` (official MCP Python SDK), pinned `>=1.28.1,<2` |

Exposes 26 blue-kb schema, schema-expansion, knowledge, query, extraction, and provenance operations as MCP tools (`FastMCP`) for consumption by LLM agents; tool handlers delegate directly to the Python library. The version pin is deliberate: `mcp==2.0.0` renamed `mcp.server.fastmcp.FastMCP` to `mcp.server.MCPServer`, a breaking import-path change — staying on `1.x` keeps `FastMCP` importable at the path the server code uses.

### WordNet MCP Server

**Implemented** — `platform/wordnet-mcp/` (package `blue-data-wordnet-mcp`)

| Concern | Choice |
|---------|--------|
| Library | `mcp[cli]` (same pin as `platform/mcp/`, `>=1.28.1,<2`) |
| Lexical database | `nltk` (`>=3.8`), `nltk.corpus.wordnet` |

A standalone MCP server, unrelated to `KBSource` — it has no dependency on `blue-data` or `blue-data-kb-source` at all. Exposes 10 read-only WordNet lookups (synset lookup, synonyms, hypernyms/hyponyms, meronyms/holonyms, antonyms, similarity, lemmatization) as MCP tools. The WordNet corpus (`wordnet`, `omw-1.4`) is downloaded via `nltk.download()` lazily on first tool call if not already present, then cached locally — one-time network access, offline after that. Wired into schema expansion as an *optional* peer server: `expand_schema(reference="mcp", mcp_servers=[{"transport": "stdio", "command": "blue-data-wordnet-mcp"}])` spawns it over stdio and offers its tools to the Schema Expansion Engine's agent loop, to ground new class/relation names and hierarchy placement — see [09-api-interface-spec.md](09-api-interface-spec.md) §1 and `platform/wordnet-mcp/README.md`.

### CLI

**Implemented** — `platform/cli/` (package `blue-data-kb-cli`, console script `blue-kb`)

| Concern | Choice |
|---------|--------|
| Argument parsing | Click (`>=8.0`) |
| Output formatting | Rich (`>=13.0`) |
| Date parsing | `arrow` (`>=1.3`) |

Click handles command routing and argument parsing. Rich provides formatted terminal output (tables, progress indicators) for schema inspection and interactive expansion. `arrow` is used for date handling in CLI-facing date arguments/output.

---

## Testing

| Concern | Choice |
|---------|--------|
| Test runner | pytest |
| Async support | pytest-asyncio |
| Coverage | pytest-cov |
| Test scope | Unit tests (`lib/tests/unit/`) against `MemoryGraphSource`; separate opt-in benchmark suites (`lib/tests/benchmarks/`) against real OpenAI responses. No automated Neo4j-backed test exists yet — see [11-testing-strategy.md](11-testing-strategy.md) §9. |

Unit tests exercise the full KB API using the in-memory backend with no external dependencies. OpenAI calls made by the ingestion judges, the Extraction Engine, and the Query Engine during unit tests are stubbed out with `unittest.mock.patch("openai.AsyncOpenAI", ...)`, so the suite runs deterministically without a live API key. `pytest-recording` **is** used — but only by `lib/tests/benchmarks/{judges,extraction,querying}/`, which are excluded from the default `pytest` run (`testpaths = ["tests/unit"]`) and invoked explicitly; it's unused by `lib/tests/unit/` itself.

---

## External Libraries

### blue-data

Provides the `GraphSource` abstract base class and all concrete graph backends consumed by blue-kb.

**Classes used:**

| Class | Package | Role |
|-------|---------|------|
| `GraphSource` | `blue_data.source.graph` | Abstract graph store base |
| `MemoryGraphSource` | `blue_data.source.graph.memory_graph_source` | In-process graph backend |
| `FileGraphSource` | `blue_data.source.graph.file_graph_source` | JSON-file-backed graph backend |
| `Neo4jGraphSource` | `blue_data.source.graph.neo4j_graph_source` | Neo4j graph backend |
| `DataSchema` | `blue_data.core.schema` | Schema serialization / deserialization |
| `VectorSource` | `blue_data.source.vector.vector_source` | Abstract vector store base |
| `MemoryVectorSource` | `blue_data.source.vector.memory_vector_source` | In-process vector backend |
| `FileVectorSource` | `blue_data.source.vector.file_vector_source` | File-backed vector backend |
| `ChromaDBVectorSource` | `blue_data.source.vector.chromadb.chromadb_vector_source` | ChromaDB vector backend |
| `RedisVectorSource` | `blue_data.source.vector.redis.redis_vector_source` | Redis vector backend |

### Summary

| Package | Version | Source | Role |
|---------|---------|--------|------|
| `blue-data` | ≥1.0.0 | internal | Graph store base classes and backends |
| `openai` | ≥1.0 | PyPI | OpenAI SDK for LLM-driven extraction, querying, and embeddings |
| `neo4j` | latest stable | PyPI | Neo4j Python driver (optional backend) |
| `rank-bm25` | latest stable | PyPI | BM25 keyword search over schema and data |
| `chromadb` | latest stable | PyPI | ChromaDB vector backend (optional, semantic search) |
| `redis` | latest stable | PyPI | Redis vector backend (optional, semantic search) |
| `fastapi` | latest stable | PyPI | REST API framework (provisional — `platform/api/` not yet built) |
| `uvicorn` | latest stable | PyPI | ASGI server for FastAPI (provisional) |
| `mcp[cli]` | `>=1.28.1,<2` | PyPI | MCP server SDK — implemented, pinned below 2.0 (see MCP Server above); also the sole non-`nltk` dependency of `platform/wordnet-mcp/` |
| `mcp` | `>=1.28.1,<2` | PyPI | `lib`'s own optional `mcp` extra (`pip install blue-data-kb-source[mcp]`) — the MCP **client** SDK (`mcp.client.stdio`/`sse`/`streamable_http`, `ClientSession`) `expand_schema(reference="mcp")` uses to connect to external servers like `platform/wordnet-mcp`; distinct role from the `mcp[cli]` **server** SDK row above, same version pin |
| `nltk` | `>=3.8` | PyPI | WordNet lexical database access — `platform/wordnet-mcp/` only, optional peer for `expand_schema(reference="mcp")` |
| `click` | `>=8.0` | PyPI | CLI argument parsing — implemented |
| `rich` | `>=13.0` | PyPI | CLI output formatting — implemented |
| `arrow` | `>=1.3` | PyPI | CLI date handling — implemented |
| `pytest` | latest stable | PyPI | Test runner |
| `pytest-asyncio` | latest stable | PyPI | Async test support |
| `pytest-recording` | latest stable | PyPI | Used by `lib/tests/benchmarks/` only (VCR cassettes), not by `lib/tests/unit/` |
| `pytest-cov` | latest stable | PyPI | Test coverage (`lib/` only — not declared by `platform/cli/`/`platform/mcp/`) |
