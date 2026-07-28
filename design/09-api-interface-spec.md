# KBDB — API / Interface Spec

Four external interfaces are planned over the core library: the Python library itself, a REST API, an MCP server, and a CLI. Today only the **Python library** and the **CLI** exist in the repository; the REST API and MCP server are design-stage only (see [07-repo-organization.md](07-repo-organization.md), which lists `api/` and `mcp/` as "Planned"). No business logic belongs in any interface layer — REST/MCP/CLI are meant to stay thin adapters over the Python library. Authentication and authorization are left to the deployment layer.

---

## 1. Python Library

The canonical interface. All other interfaces delegate to it. `KBSource` is an abstract class — it must be subclassed together with a concrete `GraphSource` backend via Python MRO (e.g. `class MemoryKBSource(KBSource, MemoryGraphSource): pass`). Every method below is `async` and must be awaited, **except** `create_database`/`fetch_databases` (inherited from the `GraphSource` backend, synchronous), `add_change_hook`/`remove_change_hook`, `reindex`, and the `has_semantic_search` property.

### Constructor

```python
class KBSource(GraphSource, ABC):
    def __init__(
        self,
        *args: Any,                                   # forwarded to the GraphSource backend, e.g. name=, directory=
        enable_search: bool = False,
        vector_source: VectorSource | None = None,
        embedding_fn: Callable[[str], list[float]] | None = None,
        vector_dim: int = 1536,
        corpus_dir: str | None = None,
        **kwargs: Any,
    ) -> None: ...
```

`enable_search=True` (or passing `vector_source`) constructs a `KBSearchIndexer` for BM25 keyword search; supplying `vector_source` and `embedding_fn` together additionally enables semantic search.

### `KBSource`

```python
class KBSource(GraphSource, ABC):
    # Schema — declare / update / delete
    async def declare_class(self, database: str, name: str, description: str, class_id: str | None = None) -> str: ...
    async def declare_relation_type(self, database: str, name: str, description: str, is_symmetric: bool = False, relation_id: str | None = None, *, domain: str | list[str], range: str | list[str]) -> str: ...
    async def declare_class_property(self, database: str, name: str, description: str, property_id: str | None = None, class_id: str | None = None) -> str: ...
    async def declare_relation_property(self, database: str, name: str, description: str, property_id: str | None = None, relation_id: str | None = None) -> str: ...
    async def update_class(self, database: str, class_id: str, name: str | None = None, description: str | None = None) -> None: ...
    async def update_relation(self, database: str, relation_id: str, name: str | None = None, description: str | None = None) -> None: ...
    async def update_property(self, database: str, property_id: str, name: str | None = None, description: str | None = None) -> None: ...
    async def delete_class(self, database: str, class_id: str) -> None: ...
    async def delete_relation(self, database: str, relation_id: str) -> None: ...
    async def delete_property(self, database: str, property_id: str) -> None: ...
    async def add_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None: ...
    async def remove_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None: ...

    # Schema — export / import / consolidate
    async def fetch_database_schema(self, database: str) -> DataSchema: ...
    async def import_database_schema(self, database: str, schema_json: dict) -> None: ...
    async def fetch_meta_schema(self) -> DataSchema: ...
    async def consolidate_schema(self, database: str, operation: str, source_names: list[str], target_name: str, predicate: Callable | None = None) -> None: ...

    # Search — schema and data, keyword (BM25) or semantic
    async def search_schema(self, database: str, query: str, k: int = 5, mode: str = "keyword", type_filter: str | list[str] | None = None) -> list[SchemaSearchResult]: ...
    async def search_data(self, database: str, query: str, k: int = 5, mode: str = "keyword", type_filter: str | list[str] | None = None) -> list[DataSearchResult]: ...
    def reindex(self, database: str) -> None: ...                 # raises KBError if search was not enabled
    @property
    def has_semantic_search(self) -> bool: ...

    # Knowledge — write
    async def add_entity(self, database: str, name: str, description: str | None = None, entity_id: str | None = None, class_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...
    async def add_fact(self, database: str, name: str | None = None, description: str | None = None, fact_id: str | None = None, head_entity_id: str | None = None, tail_entity_id: str | None = None, relation_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...
    async def add_entity_property_value(self, database: str, value: str | int | float | bool, name: str | None = None, description: str | None = None, literal_type: str | None = None, entity_id: str | None = None, property_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...
    async def add_fact_property_value(self, database: str, value: str | int | float | bool, name: str | None = None, description: str | None = None, literal_type: str | None = None, fact_id: str | None = None, property_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...
    async def update_entity(self, database: str, entity_id: str, name: str | None = None, description: str | None = None) -> None: ...
    async def update_fact(self, database: str, fact_id: str, name: str | None = None, description: str | None = None) -> None: ...
    async def update_entity_property_value(self, database: str, entity_id: str, property_id: str, value: str | int | float | bool | None = None, name: str | None = None, description: str | None = None) -> None: ...
    async def update_fact_property_value(self, database: str, fact_id: str, property_id: str, value: str | int | float | bool | None = None, name: str | None = None, description: str | None = None) -> None: ...
    async def delete_entity(self, database: str, entity_id: str, cascade: bool = False) -> None: ...
    async def delete_fact(self, database: str, fact_id: str) -> None: ...
    async def delete_entity_property_value(self, database: str, entity_id: str, property_id: str) -> None: ...
    async def delete_fact_property_value(self, database: str, fact_id: str, property_id: str) -> None: ...
    async def fetch_database_data(self, database: str, include_provenance: bool = False, compact: bool = False) -> dict | list: ...

    # Knowledge — read (LLM agent over the instance graph)
    async def answer(self, database: str, question: str, model: str = "gpt-4o", natural_language: bool = False, on_tool_call: Callable[[str, dict], None] | None = None) -> list[dict] | str: ...
    async def validate(self, database: str, statement: str, model: str = "gpt-4o", natural_language: bool = False, on_tool_call: Callable[[str, dict], None] | None = None) -> ValidationResult | str: ...
    async def enrich(self, database: str, query: str, depth: int = 2, model: str = "gpt-4o", on_tool_call: Callable[[str, dict], None] | None = None) -> list[EnrichmentResult]: ...

    # Provenance
    async def add_source(self, database: str, source: str, name: str | None = None, description: str | None = None, node_id: str | None = None) -> str: ...
    async def add_extraction(self, database: str, text: str, name: str | None = None, description: str | None = None, node_id: str | None = None, source_id: str | None = None) -> str: ...
    async def add_extracted_from(self, database: str, element_id: str, extraction_id: str) -> None: ...
    async def update_source(self, database: str, source_id: str, name: str | None = None, description: str | None = None) -> None: ...
    async def delete_source(self, database: str, source_id: str) -> None: ...

    # Extraction
    async def import_text(self, database: str, text: str, queries: list[str] | None = None, model: str = "gpt-4o", source_id: str | None = None, on_tool_call: Callable[[str, dict], None] | None = None) -> None: ...

    # Change hooks (database-scoped only — global hooks are internal, used to wire up KBSearchIndexer)
    def add_change_hook(self, database: str, fn: Callable[[dict], None]) -> None: ...
    def remove_change_hook(self, database: str, fn: Callable[[dict], None]) -> None: ...
```

Note: `add_domain`/`add_range` do **not** exist as separate methods — `domain`/`range` are wired inline as part of `declare_relation_type`, and are required (not optional) there.

### Sync convenience methods

Two additional synchronous methods exist for callers that can't await, both wrapping the async API via `asyncio.run()` internally:

- `execute_query(query: dict, database=None, collection=None, optional_properties=None)` — a generic op-code dispatcher (`query["op"]` such as `"KB.DECLARE_CLASS"`, `"KB.ADD_ENTITY"`, `"KB.GET_DATA"`, etc., mirroring the async methods above 1:1); `GRAPH.*` ops are forwarded to the underlying `GraphSource`.
- `execute_query_nl(query: str, database=None, ...)` — a sync convenience wrapper around `answer(..., natural_language=False)`, always returning a structured `list[dict]`.

### Result types

```python
class SchemaSearchResult:
    id: str
    type: str                       # "CLASS" | "RELATION" | "PROPERTY"
    name: str
    description: str
    score: float | None = None      # populated for mode="semantic"

class DataSearchResult:
    id: str
    type: str                       # "ENTITY" | "FACT"
    name: str
    description: str | None = None
    class_name: str | None = None   # entities only
    head_name: str | None = None    # facts only
    relation_name: str | None = None
    tail_name: str | None = None
    score: float | None = None      # populated for mode="semantic"

class ValidationResult:
    supporting: list[dict]  # [{"fact": str, "head": str, "tail": str, "relation": str}, ...]
    counter: list[dict]

class EnrichmentResult:
    entity: str | None
    relation: str | None
    value: str | None
    distance: int
```

> **`answer()` does not return `AnswerResult` objects.** Its structured-mode return value is a plain `list[dict]` — each dict shaped like `{"entity", "relation", "value", "provenance"}`, but the exact keys are whatever the LLM agent returned, not a fixed schema. `AnswerResult` is defined in `types.py` and re-exported from the package, but no code path currently constructs it; treat it as an unused/vestigial type until this is reconciled.

### Exceptions

All exceptions inherit from `KBError` (defined in `types.py`).

| Exception | Raised when |
|-----------|-------------|
| `SchemaError` | Schema declaration fails, name conflict, or unknown class/relation in domain/range |
| `KnowledgeError` | Instance creation fails or query cannot be resolved |
| `ExtractionError` | LLM extraction fails (API error, tool dispatch error, etc.) |
| `ProvenanceError` | Source or extraction node operation fails |

---

## 2. REST API — planned, not yet implemented

**Nothing under this section exists in the repository.** There is no FastAPI application, no route handlers, and `fastapi`/`uvicorn` are not runtime dependencies of either package today (see [05-software-stack.md](05-software-stack.md), where they're marked provisional). The design below is a forward-looking sketch of what a REST adapter over `KBSource` would look like, kept for when `platform/api/` is built. Treat every endpoint in this section as speculative.

FastAPI application. All handlers are `async def`. A default database may be configured server-side at startup; when configured, the `database` path parameter on database-scoped routes is optional and falls back to the server default.

---

### Database management

#### `POST /databases`

Create a new database.

**Request**
```json
{ "name": "main" }
```

**Response `201 Created`**
```json
{ "database": "main" }
```

---

#### `GET /health`

Liveness check.

**Response `200 OK`**
```json
{ "status": "ok" }
```

---

### Schema

#### `POST /databases/{database}/schema/classes`

Declare a class.

**Request**
```json
{ "name": "EMPLOYEE", "description": "A person employed by an organization", "class_id": null }
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

#### `POST /databases/{database}/schema/relations`

Declare a relation type.

**Request**
```json
{
  "name": "WORKED_AT",
  "description": "Employment relationship between a person and a company",
  "is_symmetric": false,
  "domain": "EMPLOYEE",
  "range": "COMPANY"
}
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

#### `POST /databases/{database}/schema/properties`

Declare a property on a class or relation.

**Request**
```json
{
  "name": "START_DATE",
  "description": "Date when employment began",
  "owner_id": "<relation-id>"
}
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

#### `GET /databases/{database}/schema`

Export the full schema.

**Response `200 OK`**
```json
{ "schema": { ... } }
```

---

#### `POST /databases/{database}/schema/import`

Import a schema from a `DataSchema.to_json()` dict.

**Request**
```json
{ "schema": { ... } }
```

**Response `204 No Content`**

---

#### `GET /databases/{database}/schema/search`

Search schema elements by keyword or semantic similarity.

**Query params:** `q=employment`, `mode=keyword|semantic` (default `keyword`), `type=class|relation|property` (repeatable), `top_k=10`

**Response `200 OK`**
```json
{
  "results": [
    { "id": "...", "type": "RELATION", "name": "WORKED_AT", "description": "Employment relationship between a person and a company" },
    { "id": "...", "type": "CLASS",    "name": "EMPLOYEE",  "description": "A person employed by an organization" }
  ]
}
```

---

#### `POST /databases/{database}/schema/consolidate`

Merge or split schema elements.

**Request**
```json
{
  "operation": "merge",
  "source_names": ["PERSON", "INDIVIDUAL"],
  "target_name": "PERSON"
}
```

**Response `204 No Content`**

---

### Knowledge — write

#### `POST /databases/{database}/entities`

Add an entity instance.

**Request**
```json
{
  "name": "Alice",
  "class_id": "<class-id>",
  "text": "Alice is a software engineer.",
  "source_id": "<source-id>"
}
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

#### `POST /databases/{database}/facts`

Add a fact (relation instance).

**Request**
```json
{
  "relation_id": "<relation-id>",
  "head_entity_id": "<entity-id>",
  "tail_entity_id": "<entity-id>",
  "text": "Alice joined IBM in June 1998.",
  "source_id": "<source-id>"
}
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

#### `POST /databases/{database}/entities/{entity_id}/properties`

Add a property value to an entity.

**Request**
```json
{
  "property_id": "<property-id>",
  "value": "Alice",
  "literal_type": "STRING",
  "text": "Alice is a software engineer.",
  "source_id": "<source-id>"
}
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

#### `POST /databases/{database}/facts/{fact_id}/properties`

Add a property value to a fact.

**Request**
```json
{
  "property_id": "<property-id>",
  "value": "1998-06-01",
  "literal_type": "DATE",
  "text": "Alice joined IBM in June 1998.",
  "source_id": "<source-id>"
}
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

### Data search

#### `GET /databases/{database}/data/search`

Search entities and facts by keyword or semantic similarity.

**Query params:** `q=Alice`, `mode=keyword|semantic`, `type=entity|fact` (repeatable), `top_k=10`

**Response `200 OK`**
```json
{
  "results": [
    { "id": "...", "type": "ENTITY", "name": "Alice", "class_name": "EMPLOYEE" }
  ]
}
```

---

### Knowledge — read

#### `POST /databases/{database}/knowledge/answer`

Answer a natural language question.

**Request**
```json
{ "question": "Where did Alice work?", "natural_language": false }
```

**Response `200 OK`**
```json
{
  "results": [
    { "entity": "Alice", "relation": "WORKED_AT", "value": "IBM",    "provenance": "Alice's résumé" },
    { "entity": "Alice", "relation": "WORKED_AT", "value": "Google", "provenance": "Alice's résumé" }
  ]
}
```

---

#### `POST /databases/{database}/knowledge/validate`

Return supporting and counter evidence for a statement.

**Request**
```json
{ "statement": "Alice worked at IBM." }
```

**Response `200 OK`**
```json
{
  "supporting": [{ "fact": "Alice_WORKED_AT_IBM", "head": "Alice", "tail": "IBM", "relation": "WORKED_AT" }],
  "counter": []
}
```

---

#### `POST /databases/{database}/knowledge/enrich`

Return related knowledge bearing on a query.

**Request**
```json
{ "query": "Alice's career in technology", "depth": 2 }
```

**Response `200 OK`**
```json
{
  "results": [
    { "entity": "Alice", "relation": "WORKED_AT",  "value": "IBM",    "distance": 1 },
    { "entity": "Alice", "relation": "WORKED_AT",  "value": "Google", "distance": 1 },
    { "entity": "IBM",   "relation": "INDUSTRY",   "value": "Technology", "distance": 2 }
  ]
}
```

---

### Provenance

#### `POST /databases/{database}/sources`

Register a source document.

**Request**
```json
{ "source": "resume_alice", "name": "Alice's résumé", "description": "PDF résumé" }
```

**Response `200 OK`**
```json
{ "id": "..." }
```

---

### Extraction

#### `POST /databases/{database}/extract`

Extract knowledge from free text using an LLM.

**Request**
```json
{
  "text": "Alice joined IBM as a software engineer in June 1998.",
  "queries": ["Where did Alice work?", "When did she start?"],
  "model": "gpt-4o",
  "source_id": "<source-id>"
}
```

**Response `204 No Content`**

---

### Error responses

All errors use a consistent envelope:

```json
{
  "error": {
    "code": "schema_error",
    "message": "Class 'EMPLOYEE' not found when resolving domain constraint."
  }
}
```

| HTTP status | `code` | Condition |
|------------|--------|-----------|
| 400 | `invalid_request` | Malformed request body |
| 404 | `database_not_found` | Specified database does not exist |
| 404 | `not_found` | Referenced node ID does not exist |
| 422 | `schema_error` | Schema operation failed |
| 422 | `knowledge_error` | Instance operation or query failed |
| 422 | `extraction_error` | LLM extraction failed |
| 500 | `internal_error` | Unexpected failure |

FastAPI exception handlers catch `KBError` subtypes and map them to these responses. No error handling logic lives in route handlers.

---

## 3. MCP Server — planned, not yet implemented

**Nothing under this section exists in the repository.** There is no MCP server code and the `mcp` package is not a runtime dependency of either package today (see [05-software-stack.md](05-software-stack.md), where it's marked provisional). The design below is a forward-looking sketch of what an MCP adapter over `KBSource` would look like, kept for when `platform/mcp/` is built. Treat every tool in this section as speculative.

Exposes KBDB operations as MCP tools. Tool handlers are thin wrappers over the Python library; all business logic remains in the library. A default database may be configured at server startup.

### Tool: `declare_class`

```json
{
  "name": "declare_class",
  "description": "Declare a class in the knowledge base schema. Idempotent — if a class with this name already exists its ID is returned.",
  "inputSchema": {
    "type": "object",
    "required": ["name", "description"],
    "properties": {
      "database": { "type": "string", "description": "Target database. Uses server default if omitted." },
      "name":        { "type": "string", "description": "Class name (e.g. PERSON, COMPANY)" },
      "description": { "type": "string", "description": "Human-readable description of the class" }
    }
  }
}
```

**Returns:** `{ "id": "..." }`

---

### Tool: `declare_relation`

```json
{
  "name": "declare_relation",
  "description": "Declare a relation type in the schema. Idempotent. Requires domain and range class constraints.",
  "inputSchema": {
    "type": "object",
    "required": ["name", "description", "domain", "range"],
    "properties": {
      "database":    { "type": "string" },
      "name":        { "type": "string" },
      "description": { "type": "string" },
      "domain":      { "type": "string", "description": "Name of the head entity class" },
      "range":       { "type": "string", "description": "Name of the tail entity class" }
    }
  }
}
```

**Returns:** `{ "id": "..." }`

---

### Tool: `search_schema`

```json
{
  "name": "search_schema",
  "description": "Search the current schema by keyword or semantic similarity. Use before declaring new classes or relations to check if a suitable element already exists.",
  "inputSchema": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "database": { "type": "string" },
      "query":    { "type": "string", "description": "Keywords or natural language query" },
      "mode":     { "type": "string", "enum": ["keyword", "semantic"], "description": "Default: keyword" }
    }
  }
}
```

**Returns:** array of `{ "id", "type", "name", "description" }` objects.

---

### Tool: `add_entity`

```json
{
  "name": "add_entity",
  "description": "Add an entity instance to the knowledge base.",
  "inputSchema": {
    "type": "object",
    "required": ["name"],
    "properties": {
      "database":  { "type": "string" },
      "name":      { "type": "string" },
      "class_id":  { "type": "string", "description": "ID of the entity's class (optional)" },
      "text":      { "type": "string", "description": "Verbatim source text span this entity was drawn from (optional)" },
      "source_id": { "type": "string", "description": "ID of the source document node (optional)" }
    }
  }
}
```

**Returns:** `{ "id": "..." }`

---

### Tool: `add_fact`

```json
{
  "name": "add_fact",
  "description": "Add a fact (relation instance) between two entities.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "database":        { "type": "string" },
      "relation_id":     { "type": "string" },
      "head_entity_id":  { "type": "string" },
      "tail_entity_id":  { "type": "string" },
      "text":            { "type": "string" },
      "source_id":       { "type": "string" }
    }
  }
}
```

**Returns:** `{ "id": "..." }`

---

### Tool: `answer`

```json
{
  "name": "answer",
  "description": "Answer a natural language question using the knowledge base.",
  "inputSchema": {
    "type": "object",
    "required": ["question"],
    "properties": {
      "database": { "type": "string" },
      "question": { "type": "string" }
    }
  }
}
```

**Returns:** array of flat dicts, e.g. `{ "entity", "relation", "value", "provenance" }` (keys vary by question; not a fixed schema).

---

### Tool: `validate`

```json
{
  "name": "validate",
  "description": "Return supporting and counter evidence from the knowledge base for a given statement.",
  "inputSchema": {
    "type": "object",
    "required": ["statement"],
    "properties": {
      "database":  { "type": "string" },
      "statement": { "type": "string" }
    }
  }
}
```

**Returns:** `{ "supporting": [{"fact", "head", "tail", "relation"}, ...], "counter": [...] }`

---

### Tool: `import_text`

```json
{
  "name": "import_text",
  "description": "Extract entities, facts, and property values from free text into the knowledge base. Extends the schema as needed.",
  "inputSchema": {
    "type": "object",
    "required": ["text"],
    "properties": {
      "database":  { "type": "string" },
      "text":      { "type": "string", "description": "Free-text passage to extract from" },
      "queries":   { "type": "array", "items": { "type": "string" }, "description": "Guiding questions to focus extraction" },
      "source_id": { "type": "string", "description": "ID of an existing source node for provenance" }
    }
  }
}
```

**Returns:** empty content on success. Structured MCP error content on failure.

---

## 4. CLI

Built with Click (argument parsing) and Rich (output). All commands are thin wrappers over the Python library; Click handlers use `asyncio.run()` to call async library methods (`platform/cli/src/blue_data_kb_cli/commands.py`). Per-database configuration (graph backend, search) is stored in `{KBDB_DATA_DIR}/registry.json` (default `~/.kbdb/`), managed by `platform/cli/src/blue_data_kb_cli/config.py`.

### Global options

```
kb [--database DB] GROUP [SUBGROUP] COMMAND [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--database DB` | Target database. Overrides `KBDB_DATABASE` if set; otherwise falls back to the database last chosen with `kb database select`, or `"default"`. Auto-created (file-backed) on first use if not already registered. |

---

### `kb database` — database lifecycle and configuration

```
kb database list [--format table|json]
kb database register NAME [--graph-backend file|neo4j] [--directory DIR]
                           [--neo4j-uri URI] [--neo4j-user USER] [--neo4j-password PW]
                           [--search none|bm25|semantic] [--bm25-index-dir DIR]
                           [--vector-backend memory|file|chromadb|redis]
                           [--vector-directory DIR] [--vector-path PATH]
                           [--redis-host HOST] [--redis-port PORT] [--redis-db N]
                           [--embedding-provider openai] [--embedding-model MODEL]
kb database create NAME
kb database delete NAME [--yes]
kb database select NAME
kb database config show
kb database config set [--graph-backend file|neo4j] [--directory DIR]
                        [--neo4j-uri URI] [--neo4j-user USER] [--neo4j-password PW]
kb database search config show
kb database search config set [--search none|bm25|semantic] [--bm25-index-dir DIR]
                               [--vector-backend ...] [--embedding-provider openai] [--embedding-model MODEL]
```

`register` fully configures a database (backend + search) up front; `create` is a shortcut that creates a database using the current session's file backend and auto-registers it with defaults if not already registered.

**Example**
```
$ kb database register main --search bm25
Registered database: main (graph=file, search=bm25)

$ kb database create scratch
Created database: scratch
```

---

### `kb schema` — cross-cutting schema operations

```
kb schema list [--format table|json]
kb schema search QUERY [--type class|relation|property]... [--mode keyword|semantic] [--top-k N] [--format table|json]
```

`schema list` prints classes, class properties, class hierarchy, relations, relation properties, and relation hierarchy in one pass. `schema search` runs in keyword mode by default (works without search enabled, unranked); `--mode semantic` requires the database to be registered with `--search semantic`.

**Example**
```
$ kb schema search "employment"

┌──────────┬───────────┬────────────────────────────────────────────────────────┐
│ Type     │ Name      │ Description                                            │
├──────────┼───────────┼────────────────────────────────────────────────────────┤
│ RELATION │ WORKED_AT │ Employment relationship between a person and a company │
│ CLASS    │ EMPLOYEE  │ A person employed by an organization                   │
└──────────┴───────────┴────────────────────────────────────────────────────────┘
```

---

### `kb schema class` — class declaration and hierarchy

```
kb schema class add NAME DESCRIPTION
kb schema class list [--format table|json]
kb schema class update (--class NAME | --class-id ID) [--name NEW] [--description NEW]
kb schema class delete (--class NAME | --class-id ID) [--yes]

kb schema class parent add    (--class ... | --class-id ...) (--parent ... | --parent-id ...)
kb schema class parent remove (--class ... | --class-id ...) (--parent ... | --parent-id ...)
kb schema class parent list   (--class ... | --class-id ...) [--recursive] [--format table|json|tree]
kb schema class child  list   (--class ... | --class-id ...) [--recursive] [--format table|json|tree]

kb schema class property add    NAME DESCRIPTION (--class ... | --class-id ...)
kb schema class property list   [--class ...] [--class-id ...] [--format table|json]
kb schema class property update (--property ... | --property-id ...) [--name NEW] [--description NEW]
kb schema class property delete (--property ... | --property-id ...) [--yes]
```

**Example**
```
$ kb schema class add EMPLOYEE "A person employed by an organization"
Added class: EMPLOYEE (id: ...)

$ kb schema class parent add --class EMPLOYEE --parent PERSON
EMPLOYEE is now a subclass of PERSON
```

---

### `kb schema relation` — relation declaration and hierarchy

Mirrors `kb schema class` exactly, with `--relation`/`--relation-id` in place of `--class`/`--class-id`, plus domain/range on declaration:

```
kb schema relation add NAME DESCRIPTION [--domain CLASS] [--range CLASS]
kb schema relation list [--format table|json]
kb schema relation update (--relation ... | --relation-id ...) [--name NEW] [--description NEW]
kb schema relation delete (--relation ... | --relation-id ...) [--yes]

kb schema relation parent add/remove/list   (--relation ... | --relation-id ...) (--parent ... | --parent-id ...)
kb schema relation child  list              (--relation ... | --relation-id ...)

kb schema relation property add/list/update/delete   (same shape as class property)
```

**Example**
```
$ kb schema relation add WORKED_AT "Employment relationship" --domain EMPLOYEE --range COMPANY
Added relation: WORKED_AT (id: ...)
```

---

### `kb data` — instance data, search, and reindexing

```
kb data list [--show-provenance] [--format table|json]
kb data search QUERY [--type entity|fact]... [--mode keyword|semantic] [--top-k N] [--format table|json]
kb data reindex
```

`data list` prints entities, entity property values, facts, and fact property values. `data reindex` rebuilds the BM25/vector index from the current graph contents; it errors if the database wasn't registered with `--search bm25`/`semantic`.

---

### `kb data source` — provenance sources

```
kb data source add SOURCE [--name NAME] [--description DESC]
kb data source list [--format table|json]
kb data source update (--source NAME | --source-id ID) [--name NEW] [--description NEW]
kb data source delete (--source NAME | --source-id ID) [--yes]
```

**Example**
```
$ kb data source add resume_alice --name "Alice's résumé"
Added source: resume_alice (id: ...)
```

---

### `kb data entity` — entity instances

```
kb data entity add [--name NAME] (--class NAME | --class-id ID) [--description DESC]
                    [--text TEXT] (--source NAME | --source-id ID)
kb data entity list [--show-provenance] [--format table|json]
kb data entity update (--entity NAME | --entity-id ID) [--name NEW] [--description NEW]
kb data entity delete (--entity NAME | --entity-id ID) [--cascade] [--yes]

kb data entity property set VALUE (--entity ... | --entity-id ...) (--property ... | --property-id ...)
                                   [--type NUMBER|DATE|BOOLEAN|STRING] [--text TEXT] [--source ... | --source-id ...]
kb data entity property list   (--entity ... | --entity-id ...) [--show-provenance] [--format table|json]
kb data entity property update (--entity ... | --entity-id ...) (--property ... | --property-id ...)
                                [--value NEW] [--name NEW] [--description NEW]
kb data entity property delete (--entity ... | --entity-id ...) (--property ... | --property-id ...) [--yes]
```

**Example**
```
$ kb data entity add --name Alice --class EMPLOYEE --text "Alice is a software engineer."
Added entity: Alice (id: ...)

$ kb data entity property set 30 --entity Alice --property AGE --type NUMBER
Set entity property (literal id: ...)
```

---

### `kb data fact` — facts (relation instances)

```
kb data fact add (--head NAME | --head-id ID) (--relation NAME | --relation-id ID) (--tail NAME | --tail-id ID)
                  [--name NAME] [--description DESC] [--text TEXT] (--source ... | --source-id ...)
kb data fact list [--show-provenance] [--format table|json]
kb data fact update (--fact NAME | --fact-id ID) [--name NEW] [--description NEW]
kb data fact delete (--fact NAME | --fact-id ID) [--yes]

kb data fact property set VALUE --fact-id ID (--property ... | --property-id ...)
                                 [--type NUMBER|DATE|BOOLEAN|STRING] [--text TEXT] [--source ... | --source-id ...]
kb data fact property list   --fact-id ID [--show-provenance] [--format table|json]
kb data fact property update --fact-id ID (--property ... | --property-id ...)
                              [--value NEW] [--name NEW] [--description NEW]
kb data fact property delete --fact-id ID (--property ... | --property-id ...) [--yes]
```

Note: fact-property commands identify the fact only by `--fact-id` (no `--fact` name lookup), unlike entity-property commands.

**Example**
```
$ kb data fact add --head Alice --relation WORKED_AT --tail IBM --text "Alice joined IBM in June 1998."
Added fact (id: ...)
```

---

### `kb data extract` / `kb data answer` / `kb data validate` — LLM operations

```
kb data extract TEXT [--query Q]... (--source NAME | --source-id ID) [--model MODEL] [-v|--verbose] [-l|--log]
kb data answer QUESTION [--format table|json|nl] [--model MODEL] [-v] [-l]
kb data validate STATEMENT [--natural-language] [--model MODEL] [-v] [-l]
```

All three require `OPENAI_API_KEY` (and the `openai` extra installed). `-v/--verbose` and `-l/--log` surface each tool call the LLM agent makes while the command runs.

**Example**
```
$ kb data extract "Alice joined IBM as a software engineer in June 1998." \
    --query "Where did Alice work?" --query "When did she start?"
Extraction complete.

$ kb data answer "Where did Alice work?" --format table

┌───────┬───────────┬────────┬────────────────────────────────┐
│ Entity│ Relation  │ Value  │ Provenance                     │
├───────┼───────────┼────────┼────────────────────────────────┤
│ Alice │ WORKED_AT │ IBM    │ Alice joined IBM in June 1998. │
└───────┴───────────┴────────┴────────────────────────────────┘

$ kb data validate "Alice worked at IBM."

Supporting:
  • Alice —WORKED_AT→ IBM

Counter:
  (none)
```

---

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | `KBError` (via `click.ClickException`) or a confirmation prompt aborted — message printed via Rich |
| 2 | Bad arguments (Click default, e.g. `click.UsageError`) |
