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

The LLM integration is isolated to the Extraction Engine. The Schema Layer, Knowledge Layer, and all other components have no LLM dependency and do not require an API key.

**OpenAI SDK usage:**

| Class / Function | Role |
|-----------------|------|
| `openai.OpenAI` | Client for API calls |
| `client.chat.completions.create` | Drives the tool-use loop |
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
| Neo4j | `Neo4jGraphSource` | Yes | Production deployments requiring durability and concurrent access |

Both backends are provided by `blue-data`. KBDB selects a backend via Python MRO — the application subclasses `KBSource` alongside the chosen `GraphSource` concrete class, with no conditional logic in KBDB itself.

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
| Coverage | pytest-cov |
| Test scope | Unit tests against `MemoryGraphSource`; integration tests against Neo4j |

Unit tests exercise the full KB API using the in-memory backend with no external dependencies. Integration tests run against a live Neo4j instance to validate backend-specific behavior.

---

## External Libraries

### blue-data

Provides the `GraphSource` abstract base class and all concrete graph backends consumed by KBDB.

**Classes used:**

| Class | Package | Role |
|-------|---------|------|
| `GraphSource` | `blue_data.source.graph` | Abstract graph store base |
| `MemoryGraphSource` | `blue_data.source.graph.memory_graph_source` | In-process graph backend |
| `Neo4jGraphSource` | `blue_data.source.graph.neo4j_graph_source` | Neo4j graph backend |
| `DataSchema` | `blue_data.core.schema` | Schema serialization / deserialization |

### Summary

| Package | Version | Source | Role |
|---------|---------|--------|------|
| `blue-data` | ≥0.1.0 | internal | Graph store base classes and backends |
| `openai` | ≥1.0 | PyPI | OpenAI SDK for LLM-driven extraction |
| `neo4j` | latest stable | PyPI | Neo4j Python driver (optional backend) |
| `fastapi` | latest stable | PyPI | REST API framework (provisional) |
| `uvicorn` | latest stable | PyPI | ASGI server for FastAPI (provisional) |
| `mcp` | latest stable | PyPI | MCP server SDK (provisional) |
| `click` | latest stable | PyPI | CLI argument parsing (provisional) |
| `rich` | latest stable | PyPI | CLI output formatting (provisional) |
| `pytest` | latest stable | PyPI | Test runner |
| `pytest-cov` | latest stable | PyPI | Test coverage |
