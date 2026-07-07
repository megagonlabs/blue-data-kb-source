# KBDB — API / Interface Spec

All four external interfaces are thin adapters over the Python library. No business logic belongs in any interface layer. Authentication and authorization are left to the deployment layer.

---

## 1. Python Library

The canonical interface. All other interfaces delegate to it.

### `KBSource`

```python
class KBSource:
    # Database
    async def create_database(self, name: str) -> None: ...

    # Schema
    async def declare_class(self, database: str, name: str, description: str, class_id: str | None = None) -> str: ...
    async def declare_relation_type(self, database: str, name: str, description: str, is_symmetric: bool = False, relation_id: str | None = None, domain: str | list[str] | None = None, range_: str | list[str] | None = None) -> str: ...
    async def declare_class_property(self, database: str, name: str, description: str, property_id: str | None = None, class_id: str | None = None) -> str: ...
    async def declare_relation_property(self, database: str, name: str, description: str, property_id: str | None = None, relation_id: str | None = None) -> str: ...
    async def add_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None: ...
    async def fetch_database_schema(self, database: str) -> DataSchema: ...
    async def import_database_schema(self, database: str, schema_json: dict) -> None: ...
    async def search_schema(self, database: str, query: str) -> list[SchemaSearchResult]: ...
    async def consolidate_schema(self, database: str, operation: str, source_names: list[str], target_name: str, predicate: Callable | None = None) -> None: ...

    # Knowledge — write
    async def add_entity(self, database: str, name: str, description: str | None = None, entity_id: str | None = None, class_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...
    async def add_fact(self, database: str, name: str | None = None, description: str | None = None, fact_id: str | None = None, head_entity_id: str | None = None, tail_entity_id: str | None = None, relation_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...
    async def add_entity_property_value(self, database: str, value: Any, name: str | None = None, description: str | None = None, literal_type: str | None = None, entity_id: str | None = None, property_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...
    async def add_fact_property_value(self, database: str, value: Any, name: str | None = None, description: str | None = None, literal_type: str | None = None, fact_id: str | None = None, property_id: str | None = None, text: str | None = None, source_id: str | None = None) -> str: ...

    # Knowledge — read
    async def answer(self, database: str, question: str) -> list[AnswerResult]: ...
    async def validate(self, database: str, statement: str) -> ValidationResult: ...
    async def enrich(self, database: str, query: str, depth: int = 2) -> list[EnrichmentResult]: ...

    # Provenance
    async def add_source(self, database: str, source: str, name: str | None = None, description: str | None = None, node_id: str | None = None) -> str: ...
    async def add_extraction(self, database: str, text: str, name: str | None = None, description: str | None = None, node_id: str | None = None, source_id: str | None = None) -> str: ...

    # Extraction
    async def import_text(self, database: str, text: str, queries: list[str] | None = None, model: str = "gpt-4o", source_id: str | None = None) -> None: ...

    # Hooks
    def add_change_hook(self, database: str, fn: Callable[[dict], None]) -> None: ...
    def remove_change_hook(self, database: str, fn: Callable[[dict], None]) -> None: ...
```

### Result types

```python
class SchemaSearchResult:
    id: str
    type: str           # "CLASS" | "RELATION" | "PROPERTY"
    name: str
    description: str

class AnswerResult:
    entity: str | None
    relation: str | None
    value: str | None
    provenance: str | None

class ValidationResult:
    supporting: list[dict]   # [{"fact": str, "source": str}, ...]
    counter: list[dict]

class EnrichmentResult:
    entity: str | None
    relation: str | None
    value: str | None
    distance: int
```

### Exceptions

All exceptions inherit from `KBError` (defined in `types.py`).

| Exception | Raised when |
|-----------|-------------|
| `SchemaError` | Schema declaration fails, name conflict, or unknown class/relation in domain/range |
| `KnowledgeError` | Instance creation fails or query cannot be resolved |
| `ExtractionError` | LLM extraction fails (API error, tool dispatch error, etc.) |
| `ProvenanceError` | Source or extraction node operation fails |

---

## 2. REST API

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
  "range_": "COMPANY"
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

Search schema elements by query.

**Query param:** `q=employment`

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

### Knowledge — read

#### `POST /databases/{database}/knowledge/answer`

Answer a natural language question.

**Request**
```json
{ "question": "Where did Alice work?" }
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
  "supporting": [{ "fact": "Alice —WORKED_AT→ IBM", "source": "Alice's résumé" }],
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

## 3. MCP Server

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
  "description": "Declare a relation type in the schema. Idempotent. Optionally wire domain and range class constraints.",
  "inputSchema": {
    "type": "object",
    "required": ["name", "description"],
    "properties": {
      "database":    { "type": "string" },
      "name":        { "type": "string" },
      "description": { "type": "string" },
      "domain":      { "type": "string", "description": "Name of the head entity class (optional)" },
      "range_":      { "type": "string", "description": "Name of the tail entity class (optional)" }
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
  "description": "Search the current schema by keywords. Use before declaring new classes or relations to check if a suitable element already exists.",
  "inputSchema": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "database": { "type": "string" },
      "query":    { "type": "string", "description": "Keywords or natural language query" }
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

**Returns:** array of `{ "entity", "relation", "value", "provenance" }` objects.

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

**Returns:** `{ "supporting": [...], "counter": [...] }`

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

Built with Click (argument parsing) and Rich (output). All commands are thin wrappers over the Python library; Click handlers use `asyncio.run()` to call async library methods.

A default database may be configured via the environment variable `KBDB_DATABASE`.

### Global options

```
kb [--database DB] COMMAND
```

| Option | Description |
|--------|-------------|
| `--database DB` | Target database. Overrides `KBDB_DATABASE` if set. |

---

### `kb database create`

```
kb database create NAME
```

**Example**
```
$ kb database create main
Created database: main
```

---

### `kb schema declare`

```
kb schema declare class NAME DESCRIPTION
kb schema declare relation NAME DESCRIPTION [--domain CLASS] [--range CLASS]
kb schema declare property NAME DESCRIPTION [--class CLASS_ID] [--relation RELATION_ID]
```

**Example**
```
$ kb schema declare class EMPLOYEE "A person employed by an organization"
Declared class: EMPLOYEE (id: ...)

$ kb schema declare relation WORKED_AT "Employment relationship" --domain EMPLOYEE --range COMPANY
Declared relation: WORKED_AT (id: ...)
```

---

### `kb schema search`

```
kb schema search QUERY [--format (table|json)]
```

**Example**
```
$ kb schema search "employment" --format table

┌──────────┬──────────┬──────────────────────────────────────────────────────────┐
│ Type     │ Name     │ Description                                              │
├──────────┼──────────┼──────────────────────────────────────────────────────────┤
│ RELATION │ WORKED_AT│ Employment relationship between a person and a company   │
│ CLASS    │ EMPLOYEE │ A person employed by an organization                     │
│ CLASS    │ COMPANY  │ A business organization                                  │
└──────────┴──────────┴──────────────────────────────────────────────────────────┘
```

---

### `kb entity add`

```
kb entity add NAME --class CLASS_ID [--text TEXT] [--source SOURCE_ID]
```

**Example**
```
$ kb entity add Alice --class <employee-id>
Added entity: Alice (id: ...)
```

---

### `kb fact add`

```
kb fact add --head ENTITY_ID --relation RELATION_ID --tail ENTITY_ID [--text TEXT] [--source SOURCE_ID]
```

**Example**
```
$ kb fact add --head <alice-id> --relation <worked-at-id> --tail <ibm-id> --text "Alice joined IBM in June 1998."
Added fact (id: ...)
```

---

### `kb extract`

```
kb extract TEXT [--query QUERY]... [--source SOURCE_ID] [--model MODEL]
```

**Example**
```
$ kb extract "Alice joined IBM as a software engineer in June 1998." \
    --query "Where did Alice work?" --query "When did she start?"
Extracted: 1 entity, 1 fact, 1 property value
```

---

### `kb answer`

```
kb answer QUESTION [--format (table|json)]
```

**Example**
```
$ kb answer "Where did Alice work?" --format table

┌───────┬──────────┬────────┬─────────────────┐
│ Entity│ Relation │ Value  │ Provenance       │
├───────┼──────────┼────────┼─────────────────┤
│ Alice │ WORKED_AT│ IBM    │ Alice's résumé  │
│ Alice │ WORKED_AT│ Google │ Alice's résumé  │
└───────┴──────────┴────────┴─────────────────┘
```

---

### `kb validate`

```
kb validate STATEMENT
```

**Example**
```
$ kb validate "Alice worked at IBM."

Supporting:
  • Alice —WORKED_AT→ IBM  [Alice's résumé]

Counter:
  (none)
```

---

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | `KBError` — message printed via Rich to stderr |
| 2 | Bad arguments (Click default) |
