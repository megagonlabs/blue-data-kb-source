# KBDB — API / Interface Spec

Four external interfaces are planned over the core library: the Python library itself, a REST API, an MCP server, and a CLI. Today the **Python library**, the **CLI**, and the **MCP server** exist in the repository; the REST API is design-stage only (see [07-repo-organization.md](07-repo-organization.md), which lists `api/` as "Planned" and `mcp/` as "Implemented"). No business logic belongs in any interface layer — REST/MCP/CLI are meant to stay thin adapters over the Python library. Authentication and authorization are left to the deployment layer.

---

## 1. Python Library

The canonical interface. All other interfaces delegate to it. `KBSource` is an abstract class — it must be subclassed together with a concrete `GraphSource` backend via Python MRO. `MemoryKBSource`/`FileKBSource`/`NEO4JKBSource` ship pre-built (see [06-library.md](06-library.md)); the CLI and MCP server construct them via `DataSource.instantiate("blue_data_kb_source.file_kb_source.FileKBSource", ...)` (a dotted-path factory) rather than importing the class directly, since the backend is a runtime config choice for those two callers — see [13-decision-log.md](13-decision-log.md) ADR-004. Every method below is `async` and must be awaited, **except** `create_database`/`fetch_databases` (inherited from the `GraphSource` backend, synchronous), `add_change_hook`/`remove_change_hook`, `reindex`, and the `has_semantic_search` property.

### Constructor

```python
class KBSource(GraphSource, ABC):
    def __init__(self, name: str, properties: dict[str, Any] | None = None) -> None: ...
```

Matches `DataSource.__init__`'s own signature exactly — every `KBSource` subclass in this codebase (`MemoryKBSource`, `FileKBSource`, `NEO4JKBSource`) does too, taking no other constructor kwargs of their own (backend connection config lives under `properties["connection"]`, read by each subclass's own `_initialize_connection_properties` override). Search is `properties["search"]`: `{"mode": "bm25"|"semantic", "bm25_index_dir"?, "vector_dim"?, "vector": {"_class": ..., "connection": {...}}, "embedding": {"provider": ..., "model": ...}}` — `"vector"`/`"embedding"` are declarative and JSON-safe (constructed internally via `DataSource.instantiate()` and a small provider-config helper, respectively); `"vector_source"`/`"embedding_fn"` may instead hold already-live objects for in-memory/programmatic use, taking precedence when present. See [06-library.md](06-library.md) for the full breakdown.

### `KBSource`

Every node (`CLASS`, `RELATION`, `PROPERTY`, `ENTITY`, `FACT`, `LITERAL`, `SOURCE`, `EXTRACTION`) carries `created`/`modified` timestamps (ISO-8601 UTC) in its `properties`, set/refreshed automatically by every `declare_*`/`add_*`/`update_*` call below — there is no separate parameter for them. Every edge carries a `created` timestamp only. `SOURCE` additionally carries an `original` attribute (a caller-supplied date, e.g. a document's publication date, distinct from `created`), settable via `add_source`/`update_source`.

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
    async def attach_property(self, database: str, target_id: str, property_id: str) -> None: ...
    async def attach_relation_domain(self, database: str, relation_id: str, class_id: str) -> None: ...
    async def attach_relation_range(self, database: str, relation_id: str, class_id: str) -> None: ...

    # Schema — hierarchy reads, breadth-first over IS_SUBCLASS_OF
    async def get_class_ancestors(self, database: str, class_id: str, max_hops: int | None = 5) -> list[dict]: ...
    async def get_class_descendants(self, database: str, class_id: str, max_hops: int | None = 5) -> list[dict]: ...
    async def get_relation_ancestors(self, database: str, relation_id: str, max_hops: int | None = 5) -> list[dict]: ...
    async def get_relation_descendants(self, database: str, relation_id: str, max_hops: int | None = 5) -> list[dict]: ...

    # Schema — domain / range and membership reads
    async def get_class_entities(self, database: str, class_id: str, limit: int | None = 10) -> list[dict]: ...
    async def get_relation_domain_range(self, database: str, relation_id: str) -> dict[str, list[dict]]: ...
    async def get_allowed_relations_between_classes(self, database: str, head_class_id: str, tail_class_id: str, expand: bool = True, max_hops: int | None = 5) -> list[dict]: ...

    # Schema — exact-name lookups
    async def find_class(self, database: str, name: str) -> dict | None: ...
    async def find_relation_type(self, database: str, name: str) -> dict | None: ...
    async def find_property(self, database: str, name: str) -> dict | None: ...

    # Schema — export / import / consolidate
    async def fetch_database_schema(self, database: str) -> DataSchema: ...
    async def export_database_schema(self, database: str) -> dict: ...             # fetch_database_schema(...).to_json()
    async def import_database_schema(self, database: str, schema_json: dict) -> None: ...
    async def fetch_meta_schema(self) -> DataSchema: ...                           # fixed schema describing the KB's own node/edge taxonomy
    async def consolidate_schema(self, database: str, operation: str, source_names: list[str], target_name: str, predicate: Callable | None = None) -> None: ...  # operation="merge" only, today

    # Schema — LLM-driven expansion (grows CLASS/RELATION/PROPERTY only; never touches entities/facts/property values)
    async def expand_schema(self, database: str, text: str, *, depth: int = 1, reference: Literal["llm", "mcp"] = "llm", mcp_servers: list[dict[str, Any]] | None = None, dedup_mode: Literal["search", "llm", "none"] = "search", model: str = "gpt-4o", on_tool_call: Callable[[str, dict], None] | None = None, on_tool_result: Callable[[str, dict, Any], None] | None = None, max_turns: int = 25) -> None: ...

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
    async def fetch_database_data(self, database: str, include_provenance: bool = False, compact: bool = False, detailed: bool = False) -> dict | list: ...

    # Knowledge — export / import
    async def export_database_data(self, database: str, include_provenance: bool = False) -> dict: ...    # portable, ref-based; symmetric with import
    async def import_database_data(self, database: str, data: dict) -> None: ...                          # resolves CLASS/RELATION/PROPERTY by name

    # Knowledge — metadata lookup
    async def get_metadata(self, database: str, node_id: str) -> dict: ...   # created/modified/extraction/source for one entity, fact, or property-value (literal) id; raises KnowledgeError for any other node type

    # Knowledge — instance walks
    async def get_entity_classes(self, database: str, entity_id: str) -> list[dict]: ...
    async def get_fact_parts(self, database: str, fact_id: str) -> dict: ...
    async def get_property_values(self, database: str, owner_id: str) -> list[dict]: ...

    # Knowledge — conformance checks
    async def is_fact_allowed(self, database: str, head_entity_id: str, relation_id: str, tail_entity_id: str, max_hops: int | None = 5) -> dict: ...
    async def is_property_allowed(self, database: str, owner_id: str, property_id: str, max_hops: int | None = 5) -> dict: ...

    # Knowledge — exact-name lookups
    async def find_entity(self, database: str, name: str) -> dict | None: ...
    async def find_fact(self, database: str, head_entity_id: str, relation_id: str, tail_entity_id: str) -> dict | None: ...

    # Knowledge — read (LLM agent over the instance graph)
    async def answer(self, database: str, question: str, model: str = "gpt-4o", natural_language: bool = False, include_metadata: bool = False, on_tool_call: Callable[[str, dict], None] | None = None, on_tool_result: Callable[[str, dict, Any], None] | None = None) -> list[dict] | str | tuple[list[dict] | str, dict]: ...
    async def validate(self, database: str, statement: str, model: str = "gpt-4o", natural_language: bool = False, include_metadata: bool = False, on_tool_call: Callable[[str, dict], None] | None = None, on_tool_result: Callable[[str, dict, Any], None] | None = None) -> ValidationResult | str | tuple[ValidationResult | str, dict]: ...
    async def enrich(self, database: str, query: str, depth: int = 2, model: str = "gpt-4o", on_tool_call: Callable[[str, dict], None] | None = None, on_tool_result: Callable[[str, dict, Any], None] | None = None) -> list[EnrichmentResult]: ...

    # Provenance
    async def add_source(self, database: str, source: str, name: str | None = None, description: str | None = None, node_id: str | None = None, original: str | None = None) -> str: ...
    async def add_extraction(self, database: str, text: str, name: str | None = None, description: str | None = None, node_id: str | None = None, source_id: str | None = None, element_id: str | None = None) -> str: ...  # element_id wires EXTRACTED_FROM to an existing entity/fact/literal in the same call
    async def add_entity_extraction(self, database: str, entity_id: str, text: str, source_id: str | None = None) -> str: ...       # additional text span for an *existing* entity
    async def add_fact_extraction(self, database: str, fact_id: str, text: str, source_id: str | None = None) -> str: ...
    async def add_entity_property_value_extraction(self, database: str, entity_id: str, property_id: str, text: str, source_id: str | None = None) -> str: ...
    async def add_fact_property_value_extraction(self, database: str, fact_id: str, property_id: str, text: str, source_id: str | None = None) -> str: ...
    async def update_source(self, database: str, source_id: str, name: str | None = None, description: str | None = None, original: str | None = None) -> None: ...
    async def delete_source(self, database: str, source_id: str) -> None: ...

    # Extraction — against the schema as it currently stands; never declares new schema itself
    async def import_text(self, database: str, text: str, queries: list[str] | None = None, model: str = "gpt-4o", source_id: str | None = None, expand_schema: bool = True, schema_expansion_target: list[Literal["text", "queries"]] | None = None, schema_expansion_depth: int = 1, schema_expansion_mode: Literal["llm", "mcp"] = "llm", schema_expansion_mcp_servers: list[dict[str, Any]] | None = None, schema_expansion_dedup_mode: Literal["search", "llm", "none"] = "search", on_tool_call: Callable[[str, dict], None] | None = None, on_tool_result: Callable[[str, dict, Any], None] | None = None, max_turns: int = 25) -> None: ...

    # Change hooks (database-scoped only — global hooks are internal, used to wire up KBSearchIndexer)
    def add_change_hook(self, database: str, fn: Callable[[dict], None]) -> None: ...
    def remove_change_hook(self, database: str, fn: Callable[[dict], None]) -> None: ...
```

Notes:
- `declare_relation_type` wires the initial `DOMAIN`/`RANGE` edges inline (`domain`/`range` are required there), and re-declaring never re-wires them — post-declaration widening is what `attach_relation_domain`/`attach_relation_range` (above) are for.
- `add_extracted_from` is **not** a public `KBSource` method (it exists only as a private `ProvenanceTracker` helper, invoked internally by `add_extraction` when `element_id` is given) — use `add_extraction(..., element_id=...)` instead.
- `answer`/`validate` return a 2-tuple `(result, metadata)` only when `include_metadata=True`; `metadata` is `dict[node_id, dict]` (see "Metadata lookup" below). This composes with `natural_language`: with both flags set, the prose `result` has a natural-language provenance section appended after the main answer/assessment, and `metadata` (element 1) is still the raw structured dict.
- `expand_schema` and `import_text` are two separate LLM passes (both OpenAI agents): `expand_schema` only ever creates `CLASS`/`RELATION`/`PROPERTY` nodes, grounded by a text passage; `import_text` only ever creates `ENTITY`/`FACT`/`LITERAL` nodes, against the schema as it currently stands (a mention with no matching schema element is skipped — it never declares new schema itself). `import_text`'s `expand_schema=True` default runs the former first; its `schema_expansion_*` params mirror `expand_schema`'s `depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one, and `schema_expansion_target` selects what of `text`/`queries` feeds that call. An `import_text(..., schema_discovery_mode=...)` parameter existed in an earlier revision of this spec — it no longer exists; see ADR in [13-decision-log.md](13-decision-log.md).
- An is-a/type-of/kind-of/subclass-of hierarchy between two `CLASS`es or two `RELATION`s is *only* ever expressed with `add_is_subclass_of` — never a declared `RELATION`. `expand_schema` rejects a taxonomic name (`IS_A`, `TYPE_OF`, `SUBCLASS_OF`, ...) passed to `declare_relation_type`.

### Metadata lookup

`get_metadata(database, node_id)` returns a dict describing a single entity, fact, or property value (`LITERAL`) node:

```python
{
    "id": "...", "type": "ENTITY" | "FACT" | "LITERAL",
    # type-dependent identity fields:
    #   ENTITY:  "name", "class_name"
    #   FACT:    "name", "head_name", "relation_name", "tail_name"
    #   LITERAL: "property_name", "value", "owner_label", "owner_name"
    "description": "...", "created": "...", "modified": "...",     # the node's own attributes
    "extraction": "...", "source": "...", "source_original": "...",  # via its EXTRACTED_FROM chain, if any
}
```

The `answer`/`validate` LLM agents can also call this as a tool (`get_metadata(id)`) when `include_metadata=True` — the engine harvests every such tool call into the `metadata` dict returned alongside the result, independent of whether the LLM references the id in its own structured output.

### Sync convenience methods

Two additional synchronous methods exist for callers that can't await, both wrapping the async API via `asyncio.run()` internally:

- `execute_query(query: dict, database=None, collection=None, optional_properties=None)` — a generic op-code dispatcher (`query["op"]` such as `"KB.DECLARE_CLASS"`, `"KB.ADD_ENTITY"`, `"KB.GET_DATA"`, etc., mirroring most of the async methods above 1:1); `GRAPH.*` ops are forwarded to the underlying `GraphSource`. Note: `get_metadata` is deliberately **not** exposed as an op here (library method + MCP/agent tool only, by design) — see [13-decision-log.md](13-decision-log.md).
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

#### `POST /databases/{database}/schema/properties/attach`

Wire an existing property onto another class or relation (idempotent), reusing it instead of declaring a same-meaning duplicate.

**Request**
```json
{ "target_id": "<class-or-relation-id>", "property_id": "<property-id>" }
```

**Response `204 No Content`**

---

#### `POST /databases/{database}/schema/expand`

Grow the schema (`CLASS`/`RELATION`/`PROPERTY` only — never entities/facts/property values) via an LLM agent grounded by a text passage. Mirrors `KBSource.expand_schema` (§1).

**Request**
```json
{
  "text": "Alice joined IBM as a software engineer in June 1998.",
  "depth": 1,
  "reference": "llm",
  "dedup_mode": "search",
  "model": "gpt-4o"
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

Extract entities/facts/property values from free text using an LLM, against the schema as it
currently stands (never declares new schema itself — a mention with no matching element is
skipped). Mirrors `KBSource.import_text` (§1); `expand_schema: true` (default) runs
`schema/expand` above first.

**Request**
```json
{
  "text": "Alice joined IBM as a software engineer in June 1998.",
  "queries": ["Where did Alice work?", "When did she start?"],
  "model": "gpt-4o",
  "source_id": "<source-id>",
  "expand_schema": true
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

## 3. MCP Server

Implemented at `platform/mcp/` (package `blue-data-kb-mcp`, module `blue_data_kb_mcp`), built with
the official MCP SDK's `FastMCP` (`mcp.server.fastmcp.FastMCP` — the SDK's `2.x` release renamed
this to `mcp.server.MCPServer`; the package is pinned `mcp[cli]>=1.0.0,<2.0.0` to stay on the
`FastMCP` API). Modeled closely on the sibling `blue-data` repo's `platform/registry-mcp/` (same
`_ok`/`_err` JSON-string tool-return pattern catching every exception rather than raising through
the transport, same singleton-config-factory shape) — see `platform/mcp/README.md` for the full
architecture note and `design/13-decision-log.md` for the two deliberate departures from that model
(env-var-only config instead of reusing the CLI's registry file; read + additive-write tools only,
no `update_*`/`delete_*`). Tool handlers are `async def`, directly `await`ing `KBSource` methods —
unlike `DataRegistry`/`JSONSource` (both sync), `KBSource` is fully async, and FastMCP's `@mcp.tool()`
supports async functions natively.

The server holds one `KBSource` instance for the process lifetime, built from `BLUE_DATA_KB_SOURCE_*`
env vars (`platform/mcp/src/blue_data_kb_mcp/config.py:get_kb()`) — **not** the CLI's
`~/.kbdb/registry.json`; `database` is a plain string parameter on every tool (default `"default"`),
since `KBSource` already supports multiple logical databases on one backend. See
`platform/mcp/README.md`'s Configuration reference for the full env var table.

26 tools, grouped (matching `platform/mcp/src/blue_data_kb_mcp/server.py`'s section banners):

| Group | Tools |
|---|---|
| Schema (write) | `declare_class`, `declare_relation`, `declare_class_property`, `declare_relation_property`, `add_is_subclass_of`, `attach_property` |
| Schema (read) | `search_schema`, `fetch_schema`, `fetch_meta_schema` |
| Schema expansion | `expand_schema` |
| Knowledge (write) | `add_entity`, `add_fact`, `add_entity_property`, `add_fact_property`, `add_entity_extraction`, `add_fact_extraction`, `add_entity_property_extraction`, `add_fact_property_extraction` |
| Knowledge (read) | `search_data`, `fetch_data`, `get_metadata` |
| Query | `answer`, `validate`, `enrich` |
| Extraction | `import_text` |
| Provenance (write) | `add_source` |

All are thin 1:1 wrappers over the matching `KBSource` method (§1 above) — same parameters, same
idempotency/provenance semantics — with two adaptations: every tool takes `database: str = "default"`
instead of a required positional `database`, and `answer`/`validate` wrap `include_metadata=True`'s
`(result, metadata)` tuple into a single `{"result": ..., "metadata": ...}` JSON object (`validate`
unpacks further into `{"supporting", "counter", "metadata"}`). `fetch_data`'s `compact` defaults to
`True` here (vs. `False` on `KBSource.fetch_database_data` itself) — the flat row form is the useful
one for an LLM consumer; the raw graph form is still reachable with `compact=False`. There is
deliberately no `update_*`/`delete_*` tool, and no `KB.GET_METADATA`-style raw op dispatch — see
`ADR-018` in `design/13-decision-log.md`.

Full per-tool parameter/return documentation lives in `platform/mcp/README.md`'s Tool reference
(kept there rather than duplicated here, since it's generated from the same docstrings FastMCP
surfaces to MCP clients — `platform/mcp/src/blue_data_kb_mcp/server.py`).

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
kb schema export [--output|-o FILE]
kb schema import INPUT_FILE
kb schema expand TEXT [--depth N] [--reference llm|mcp] [--mcp-server JSON]...
                 [--dedup-mode search|llm|none] [--model MODEL] [-v|--verbose] [-l|--log] [--log-limit N]
```

`schema list` prints classes, class properties, class hierarchy, relations, relation properties, and relation hierarchy in one pass. `schema search` runs in keyword mode by default (works without search enabled, unranked); `--mode semantic` requires the database to be registered with `--search semantic`. `schema export`/`schema import` round-trip the full schema as JSON (`DataSchema.to_json()`/`from_json()`); import declares idempotently, so re-importing the same file is safe. Export/import instance data separately with `kb data export`/`kb data import` below — import the schema first so data import has classes/relations/properties to resolve names against.

`schema expand` runs the schema-growth LLM agent (`KBSource.expand_schema`, §1) grounded by `TEXT` — declares `CLASS`/`RELATION`/`PROPERTY` only, never entities/facts. `--depth` (default `1`) controls how many hierarchy levels beyond what's directly evidenced also get proposed, linked via `add_is_subclass_of` (never as a declared relation — a taxonomic name like `IS_A`/`TYPE_OF`/`SUBCLASS_OF` is rejected). `--reference mcp` additionally gives the model tools from `--mcp-server` (repeatable JSON, e.g. a WordNet server via `platform/wordnet-mcp`) to ground names/hierarchy placement. `--dedup-mode` controls how it checks for an existing element before declaring a new one (`search` default / `llm` / `none`), and is auto-applied as a safety-net check before every new `CLASS`/`RELATION`, even if the model skips `search_schema`. Run once, then `kb data extract --no-expand-schema` to extract many documents against the resulting schema without re-expanding it each time.

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
kb schema class property attach (--property ... | --property-id ...) (--class ... | --class-id ...)
kb schema class property list   [--class ...] [--class-id ...] [--format table|json]
kb schema class property update (--property ... | --property-id ...) [--name NEW] [--description NEW]
kb schema class property delete (--property ... | --property-id ...) [--yes]
```

`property attach` wires an *existing* property onto a class (idempotent) — reuse it instead of declaring a same-meaning duplicate under a new name.

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

kb schema relation property add/attach/list/update/delete   (same shape as class property)
```

**Example**
```
$ kb schema relation add WORKED_AT "Employment relationship" --domain EMPLOYEE --range COMPANY
Added relation: WORKED_AT (id: ...)
```

---

### `kb data` — instance data, export/import, search, and reindexing

```
kb data list [--show-provenance] [--show-detailed] [--format table|json]
kb data export [--show-provenance] [--output|-o FILE]
kb data import INPUT_FILE
kb data search QUERY [--type entity|fact]... [--mode keyword|semantic] [--top-k N] [--format table|json]
kb data reindex
```

`data list` prints entities, entity property values, facts, and fact property values. `--show-provenance` adds each row's extraction text and linked source name (via its `EXTRACTED_FROM` chain, if any). `--show-detailed` adds each row's own `description`/`created`/`modified`; combined with `--show-provenance` it additionally shows the linked source's `original` date, after the Source column. `--format table` renders `created`/`modified` as human-readable local dates (via `arrow`, e.g. "Aug 7, 2026 5:48 PM"); `--format json` keeps raw ISO-8601 timestamps. `data export`/`data import` round-trip instance data (entities, facts, property values) as portable, ref-based JSON — schema isn't included (export/import that separately via `kb schema export`/`kb schema import` first, so `data import` has classes/relations/properties to resolve names against). `data reindex` rebuilds the BM25/vector index from the current graph contents; it errors if the database wasn't registered with `--search bm25`/`semantic`.

---

### `kb data source` — provenance sources

```
kb data source add SOURCE [--name NAME] [--description DESC] [--original DATE]
kb data source list [--show-detailed] [--format table|json]
kb data source update (--source NAME | --source-id ID) [--name NEW] [--description NEW] [--original NEW]
kb data source delete (--source NAME | --source-id ID) [--yes]
```

`--original` records the source's own date (e.g. a document's publication date) — a caller-supplied string, distinct from `created` (when the source was added to the KB). `source list --show-detailed` adds Created/Modified columns to the table (`--format json` always includes `original`/`created`/`modified`).

**Example**
```
$ kb data source add resume_alice --name "Alice's résumé" --original 2020-01-15
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

kb data entity extraction add (--entity ... | --entity-id ...) --text TEXT (--source ... | --source-id ...)

kb data entity property set VALUE (--entity ... | --entity-id ...) (--property ... | --property-id ...)
                                   [--type NUMBER|DATE|BOOLEAN|STRING] [--text TEXT] [--source ... | --source-id ...]
kb data entity property list   (--entity ... | --entity-id ...) [--show-provenance] [--format table|json]
kb data entity property update (--entity ... | --entity-id ...) (--property ... | --property-id ...)
                                [--value NEW] [--name NEW] [--description NEW]
kb data entity property delete (--entity ... | --entity-id ...) (--property ... | --property-id ...) [--yes]
kb data entity property extraction add (--entity ... | --entity-id ...) (--property ... | --property-id ...)
                                        --text TEXT (--source ... | --source-id ...)
```

`entity extraction add` / `entity property extraction add` link an *additional* text span to an entity or property value that already exists — unlike `--text` on `entity add`/`property set`, they don't create a new node, just another `EXTRACTED_FROM` edge, so multiple spans accumulate over time.

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

kb data fact extraction add (--fact ... | --fact-id ...) --text TEXT (--source ... | --source-id ...)

kb data fact property set VALUE --fact-id ID (--property ... | --property-id ...)
                                 [--type NUMBER|DATE|BOOLEAN|STRING] [--text TEXT] [--source ... | --source-id ...]
kb data fact property list   --fact-id ID [--show-provenance] [--format table|json]
kb data fact property update --fact-id ID (--property ... | --property-id ...)
                              [--value NEW] [--name NEW] [--description NEW]
kb data fact property delete --fact-id ID (--property ... | --property-id ...) [--yes]
kb data fact property extraction add --fact-id ID (--property ... | --property-id ...)
                                      --text TEXT (--source ... | --source-id ...)
```

Note: fact-property commands identify the fact only by `--fact-id` (no `--fact` name lookup), unlike entity-property commands. `fact extraction add` / `fact property extraction add` behave like their entity counterparts above.

**Example**
```
$ kb data fact add --head Alice --relation WORKED_AT --tail IBM --text "Alice joined IBM in June 1998."
Added fact (id: ...)
```

---

### `kb data extract` / `kb data answer` / `kb data validate` — LLM operations

```
kb data extract TEXT [--query Q]... (--source NAME | --source-id ID) [--model MODEL]
                 [--expand-schema|--no-expand-schema] [--schema-expansion-target text|queries]...
                 [--schema-expansion-depth N] [--schema-expansion-mode llm|mcp]
                 [--schema-expansion-mcp-server JSON]... [--schema-expansion-dedup-mode search|llm|none]
                 [-v|--verbose] [-l|--log] [--log-limit N]
kb data answer QUESTION [--format table|json|nl] [--model MODEL] [--include-metadata] [-v] [-l] [--log-limit N]
kb data validate STATEMENT [--natural-language] [--model MODEL] [--include-metadata] [-v] [-l] [--log-limit N]
```

All three require `OPENAI_API_KEY` (and the `openai` extra installed). `-v/--verbose` shows tool call arguments in the status line; `-l/--log` also prints each tool call and its result on its own persistent line; `--log-limit` sets the truncation length for verbose/log output (default 80). `extract` never declares new schema itself — it only resolves mentions against the schema as it currently stands, skipping anything with no match. `--expand-schema` (default: on) runs `kb schema expand` first, grounded by `--schema-expansion-target` (any combination of `text`/`queries`, default both); `--schema-expansion-depth`/`--schema-expansion-mode`/`--schema-expansion-mcp-server`/`--schema-expansion-dedup-mode` mirror `kb schema expand`'s `--depth`/`--reference`/`--mcp-server`/`--dedup-mode` one-to-one. Use `--no-expand-schema` to extract only, against a schema already expanded once via `kb schema expand`.

`--include-metadata` (on `answer`/`validate`) has the agent additionally call `get_metadata` for each entity/fact/property value it cites, and surfaces created/modified/extraction/source info alongside the result: a second "Metadata" table (`--format table`, or unadorned `answer`/`validate` output), a `metadata` key alongside `result` (`--format json`), or folded into the prose as a follow-up section after the main answer (`--format nl` / `--natural-language`).

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
