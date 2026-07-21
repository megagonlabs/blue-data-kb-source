# KBDB — Software Stack

> Interfaces marked **provisional** are initial suggestions based on expected usage patterns. They will be revised once the respective component is implemented and inspected.

---

## Language & Runtime

| Concern | Choice |
|---------|--------|
| Language | Python |
| Minimum version | 3.10 |
| Package manager | pip |
| Project config | `pyproject.toml` |

---

## LLM

| Concern | Choice |
|---------|--------|
| Initial provider | OpenAI (`openai>=1.0`) |
| Interface | OpenAI function-calling (tool use) |

The Extraction Engine calls the OpenAI API directly using the `openai` Python SDK. The model is configurable per call (`model` parameter on `import_text`, defaulting to `gpt-4o`). The tool loop drives schema declaration and instance creation by presenting the KB operations as OpenAI function definitions.

The Knowledge Layer's Query Engine has the same kind of dependency: `answer`, `validate`, and `enrich` each run their own OpenAI tool-use loop over a read-only set of graph tools. The Schema Layer and Graph Store have no LLM dependency, but the Extraction Engine and the Query Engine (inside the Knowledge Layer) both require an API key at call time.

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

Storage is provided by `blue-data` via the `GraphSource` abstract base class. KBDB interacts only with the `GraphSource` interface and does not depend on any specific backend implementation.

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
| File | `FileGraphSource` | Yes (JSON file per database) | CLI default — single-process persistence with no server |
| Neo4j | `Neo4jGraphSource` | Yes | Production deployments requiring durability and concurrent access |

All three backends are provided by `blue-data`. KBDB selects a backend via Python MRO — the application subclasses `KBSource` alongside the chosen `GraphSource` concrete class, with no conditional logic in KBDB itself.

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

**Provisional**

| Concern | Choice |
|---------|--------|
| Library | `mcp` (official MCP Python SDK) |

Exposes KBDB schema, knowledge, and extraction operations as MCP tools for consumption by LLM agents. The `mcp` package provides the server scaffolding; tool handlers delegate directly to the Python library.

### CLI

**Provisional**

| Concern | Choice |
|---------|--------|
| Argument parsing | Click |
| Output formatting | Rich |

Click handles command routing and argument parsing. Rich provides formatted terminal output (tables, progress indicators) for schema inspection and interactive ingestion.

---

## Testing

| Concern | Choice |
|---------|--------|
| Test runner | pytest |
| Async support | pytest-asyncio |
| Coverage | pytest-cov |
| Test scope | Unit tests against `MemoryGraphSource`; integration tests against Neo4j |

Unit tests exercise the full KB API using the in-memory backend with no external dependencies. OpenAI calls made by the Extraction Engine and Query Engine during unit tests are stubbed out with `unittest.mock.patch("openai.AsyncOpenAI", ...)`, so the suite runs deterministically without a live API key. `pytest-recording` is listed as a dev dependency but is not currently used by any test. Integration tests run against a live Neo4j instance to validate backend-specific behavior.

---

## External Libraries

### blue-data

Provides the `GraphSource` abstract base class and all concrete graph backends consumed by KBDB.

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
| `blue-data` | ≥0.1.0 | internal | Graph store base classes and backends |
| `openai` | ≥1.0 | PyPI | OpenAI SDK for LLM-driven extraction, querying, and embeddings |
| `neo4j` | latest stable | PyPI | Neo4j Python driver (optional backend) |
| `rank-bm25` | latest stable | PyPI | BM25 keyword search over schema and data |
| `chromadb` | latest stable | PyPI | ChromaDB vector backend (optional, semantic search) |
| `redis` | latest stable | PyPI | Redis vector backend (optional, semantic search) |
| `fastapi` | latest stable | PyPI | REST API framework (provisional) |
| `uvicorn` | latest stable | PyPI | ASGI server for FastAPI (provisional) |
| `mcp` | latest stable | PyPI | MCP server SDK (provisional) |
| `click` | latest stable | PyPI | CLI argument parsing (provisional) |
| `rich` | latest stable | PyPI | CLI output formatting (provisional) |
| `pytest` | latest stable | PyPI | Test runner |
| `pytest-asyncio` | latest stable | PyPI | Async test support |
| `pytest-recording` | latest stable | PyPI | Listed dev dependency; not currently used by any test |
| `pytest-cov` | latest stable | PyPI | Test coverage |
