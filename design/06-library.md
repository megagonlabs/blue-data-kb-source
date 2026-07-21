# KBDB — Library

> External dependencies: `blue-data` provides `GraphSource`, `MemoryGraphSource`, `FileGraphSource`, `Neo4jGraphSource`, `VectorSource` and its backends, `DataSchema`, and related base classes.

---

## KBSource(GraphSource)

Entry point for all KBDB operations. Abstract base class — must be subclassed together with a concrete `GraphSource` backend using Python MRO. Instantiates and coordinates the `SchemaManager`, `KnowledgeManager` (which internally owns a `QueryEngine`), `ProvenanceTracker`, `ChangeNotifier`, `ExtractionEngine`, and — when search is enabled — `KBSearchIndexer`. Every method except the `GraphSource` primitives it inherits (e.g. `create_database`) is `async` and must be awaited.

### Constructor

```python
KBSource(
    *args,
    enable_search: bool = False,
    vector_source: VectorSource | None = None,
    embedding_fn: Callable[[str], list[float]] | None = None,
    vector_dim: int = 1536,
    corpus_dir: str | None = None,
    **kwargs,
)
```

`*args`/`**kwargs` are forwarded to the concrete `GraphSource` backend mixed in via MRO (e.g. `name` for `MemoryGraphSource`, `name` + `directory` for `FileGraphSource`).

`enable_search=True` (or passing a `vector_source`) constructs a `KBSearchIndexer` and registers it as a global change hook so it stays in sync automatically. Without a `vector_source`/`embedding_fn`, the indexer supports keyword (BM25) search only; supplying both enables semantic search. `corpus_dir` persists the BM25 corpus to disk across restarts.

### Example

```python
import asyncio
from blue_data_kb_source import KBSource
from blue_data.source.graph.memory_graph_source import MemoryGraphSource

class MemoryKBSource(KBSource, MemoryGraphSource):
    pass

async def main():
    kb = MemoryKBSource(name="my-kb")
    kb.create_database("main")  # inherited from GraphSource, not async

    # Schema
    person   = await kb.declare_class("main", "PERSON", "A human individual")
    company  = await kb.declare_class("main", "COMPANY", "A business organization")
    employee = await kb.declare_class("main", "EMPLOYEE", "A person employed by an organization")
    await kb.add_is_subclass_of("main", employee, person)

    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship",
        domain="EMPLOYEE", range_="COMPANY"
    )
    start_date = await kb.declare_relation_property("main", "START_DATE", "Start of employment", relation_id=worked_at)

    # Provenance
    src = await kb.add_source("main", source="resume_alice", name="Alice's résumé")

    # Instances
    alice = await kb.add_entity("main", "Alice", class_id=employee, text="Alice is a software engineer.", source_id=src)
    ibm   = await kb.add_entity("main", "IBM", class_id=company)
    fact  = await kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm,
                              text="Alice joined IBM in June 1998.", source_id=src)
    await kb.add_fact_property_value("main", "1998-06-01", fact_id=fact, property_id=start_date,
                                     text="Alice joined IBM in June 1998.", source_id=src)

    # LLM extraction
    await kb.import_text("main", "Alice later moved to Google in 2005.", source_id=src)

    # Query
    answer   = await kb.answer("main", "Where did Alice work?")
    evidence = await kb.validate("main", "Alice worked at IBM.")
    context  = await kb.enrich("main", "Alice's career in technology")

asyncio.run(main())
```

The CLI bridges these async calls to Click's synchronous command handlers via `asyncio.run()` per invocation.

### Methods

All methods on `KBSource` delegate to the appropriate component. See the component class sections below for parameter details and algorithms.

#### Schema operations (delegate to `SchemaManager`)

| Method | Returns | Description |
|--------|---------|-------------|
| `declare_class(database, name, description, class_id?)` | `str` | Declare a CLASS node (idempotent) |
| `declare_relation_type(database, name, description, is_symmetric?, relation_id?, domain?, range_?)` | `str` | Declare a RELATION node; wires DOMAIN/RANGE edges inline |
| `declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Declare a PROPERTY node; wires DEFINE_PROPERTY from a class |
| `declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Declare a PROPERTY node; wires DEFINE_PROPERTY from a relation |
| `add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire IS_SUBCLASS_OF edge |
| `add_domain(database, relation_id, class_id)` | `None` | Wire DOMAIN edge |
| `add_range(database, relation_id, class_id)` | `None` | Wire RANGE edge |
| `fetch_database_schema(database)` | `DataSchema` | Export schema to portable object |
| `fetch_meta_schema()` | `DataSchema` | Return the fixed KBDB meta-schema |
| `import_database_schema(database, schema_json)` | `KBSource` | Populate schema from a `DataSchema.to_json()` dict |
| `consolidate_schema(database, operation, ...)` | `None` | Merge or split schema elements; rewires affected instances |

#### Knowledge operations (delegate to `KnowledgeManager`)

| Method | Returns | Description |
|--------|---------|-------------|
| `add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?)` | `str` | Create an ENTITY node |
| `add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?)` | `str` | Create a FACT node |
| `add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?)` | `str` | Create a LITERAL and wire it to an entity |
| `add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?)` | `str` | Create a LITERAL and wire it to a fact |
| `fetch_database_data(database, include_provenance?, compact?)` | `dict \| list` | Export all instance data in the database |
| `answer(database, question, model?, natural_language?, on_tool_call?)` | `list[AnswerResult] \| str` | Answer a natural language question using an LLM agent over the instance graph; returns prose when `natural_language=True` |
| `validate(database, statement, model?, natural_language?, on_tool_call?)` | `ValidationResult \| str` | Return supporting and counter evidence for a statement using an LLM agent; returns prose when `natural_language=True` |
| `enrich(database, query, depth?, model?, on_tool_call?)` | `list[EnrichmentResult]` | Return related knowledge bearing on a query using an LLM agent |

#### Search operations (delegate to `KBSearchIndexer` when search is enabled, otherwise served unranked by `SchemaManager`/`KnowledgeManager`)

| Method | Returns | Description |
|--------|---------|-------------|
| `search_schema(database, query, k?, mode?, type_filter?)` | `list[SchemaSearchResult]` | Search classes, relations, and properties by keyword (`mode="keyword"`, BM25) or semantic similarity (`mode="semantic"`) |
| `search_data(database, query, k?, mode?, type_filter?)` | `list[DataSearchResult]` | Search entities and facts by keyword or semantic similarity |
| `reindex(database)` | `None` | Force a full rebuild of the search index from the current graph contents; raises if search was not enabled |
| `has_semantic_search` | `bool` (property) | Whether a vector backend + embedding function are configured for semantic search |

#### Provenance operations (delegate to `ProvenanceTracker`)

| Method | Returns | Description |
|--------|---------|-------------|
| `add_source(database, source, name?, description?, node_id?)` | `str` | Create a SOURCE node |
| `add_extraction(database, text, name?, description?, node_id?, source_id?)` | `str` | Create an EXTRACTION node; wires EXTRACTED_FROM to source |
| `add_extracted_from(database, element_id, extraction_id)` | `None` | Wire EXTRACTED_FROM from an instance to an extraction |

#### Change hook operations (delegate to `ChangeNotifier`)

| Method | Returns | Description |
|--------|---------|-------------|
| `add_change_hook(database, fn)` | `None` | Register a hook to be called on every instance write to `database` |
| `remove_change_hook(database, fn)` | `None` | Deregister a hook (no-op if not found) |

#### Extraction (delegates to `ExtractionEngine`)

| Method | Returns | Description |
|--------|---------|-------------|
| `import_text(database, text, queries?, model?, source_id?, on_tool_call?)` | `None` | Extract entities, facts, and properties from free text using an LLM |

---

## SchemaManager

Manages the typed vocabulary of the knowledge base. All declaration methods are idempotent: if an element with the same name already exists, its ID is returned without modification.

### Constructor

```python
SchemaManager(source)
```

**Parameters**

- `source` — the parent `KBSource` instance; used for all Graph Store access

### Methods

#### `declare_class(database, name, description, class_id=None)`

Declares a CLASS node.

**Algorithm**

1. Look up existing CLASS node by `name` in the Graph Store
2. If found: return existing ID
3. If not found: create CLASS node with `name`, `description`, optional `class_id`; return new ID

#### `declare_relation_type(database, name, description, is_symmetric=False, relation_id=None, domain=None, range_=None)`

Declares a RELATION node. `domain` and `range_` accept a class name or list of class names.

**Algorithm**

1. Look up existing RELATION node by `name`
2. If found: return existing ID (DOMAIN/RANGE edges are not re-wired)
3. If not found: create RELATION node; resolve each name in `domain` and `range_` to a CLASS ID via name lookup; wire DOMAIN and RANGE edges; return new ID

#### `fetch_database_schema(database)`

Traverses all CLASS and RELATION nodes in `database` and assembles a `DataSchema` object capturing classes, their properties, relations, and domain/range constraints.

#### `find_schema(database, query, type_filter=None)`

Filters schema element nodes (CLASS, RELATION, PROPERTY) by a case-insensitive substring match of `query` against their `name` and `description` fields, with no relevance ranking. This is the fallback used by `KBSource.search_schema` when the database was not registered with search enabled; when it is enabled, `KBSource.search_schema` calls `KBSearchIndexer.search_schema` instead, which supports BM25 ranking and semantic mode. Returns a list of `SchemaSearchResult` (`{"id", "type", "name", "description"}`).

**Example**

Input: `"employment"`

Output:
```python
[
    {"id": "...", "type": "RELATION", "name": "WORKED_AT",    "description": "Employment relationship between a person and a company"},
    {"id": "...", "type": "CLASS",    "name": "EMPLOYEE",     "description": "A person employed by an organization"},
    {"id": "...", "type": "CLASS",    "name": "COMPANY",      "description": "A business organization"},
    {"id": "...", "type": "PROPERTY", "name": "START_DATE",   "description": "Date when employment began"},
]
```

#### `consolidate_schema(database, operation, source_names, target_name, predicate=None)`

Merges or splits schema elements and propagates the changes to all affected instances.

**Parameters**

- `operation` — `"merge"` or `"split"`
- `source_names` — names of the elements to merge (or the single element to split)
- `target_name` — name of the resulting element (merge) or prefix for the resulting elements (split)
- `predicate` — callable used to assign instances to output classes when splitting

**Algorithm — merge**

1. Resolve each name in `source_names` to schema element IDs
2. Create (or look up) the target element with `target_name`
3. Find all instances referencing any source element
4. Rewire each instance's typed edges to point to the target element
5. Remove the source elements

**Algorithm — split**

1. Resolve `source_names[0]` to a schema element ID
2. Create both output elements from `target_name` variants
3. For each instance referencing the source element: call `predicate(instance)` to determine which output element it belongs to; rewire its typed edge accordingly
4. Remove the source element

---

## KnowledgeManager

Populates the knowledge base with instance data and delegates natural language queries to the `QueryEngine`. Delegates provenance creation to `ProvenanceTracker` and change notification to `ChangeNotifier` after each write.

### Constructor

```python
KnowledgeManager(source, provenance_tracker, change_notifier, schema_manager, search_indexer=None)
```

`search_indexer` is `None` unless the parent `KBSource` was constructed with `enable_search=True` or a `vector_source`.

### Methods

#### `add_entity(database, name, description=None, entity_id=None, class_id=None, text=None, source_id=None)`

Creates an ENTITY node.

**Algorithm**

1. Create ENTITY node with `name`, `description`, optional `entity_id`
2. If `class_id` provided: wire IS_INSTANCE_OF edge to the CLASS node
3. If `text` provided: call `ProvenanceTracker.track(database, entity_id, text, source_id)`
4. Call `ChangeNotifier.fire({"type": "entity_added", ...})`
5. Return entity ID

#### `find_data(database, query, type_filter=None)`

Filters ENTITY and FACT nodes by a case-insensitive substring match of `query` against their own name/description plus related names (class name for entities; head, relation, and tail names for facts), with no relevance ranking. This is the fallback used by `KBSource.search_data` when the database was not registered with search enabled; when it is enabled, `KBSource.search_data` calls `KBSearchIndexer.search_data` instead, which supports BM25 ranking and semantic mode. Returns a list of `DataSearchResult`.

#### `answer(database, question, model="gpt-4o", natural_language=False, on_tool_call=None)`

Delegates to `QueryEngine.answer`. Returns a list of `AnswerResult` objects, or a prose string when `natural_language=True`. `on_tool_call`, if given, is invoked with `(tool_name, arguments)` for each tool call the agent makes.

#### `validate(database, statement, model="gpt-4o", natural_language=False, on_tool_call=None)`

Delegates to `QueryEngine.validate`. Returns a `ValidationResult`, or a prose string when `natural_language=True`.

#### `enrich(database, query, depth=2, model="gpt-4o", on_tool_call=None)`

Delegates to `QueryEngine.enrich`. Returns a list of `EnrichmentResult` objects.

---

## QueryEngine

LLM agent for answering, validating, and enriching queries over the instance graph. Owned by `KnowledgeManager` — not exposed at the `KBSource` level.

### Constructor

```python
QueryEngine(source, schema_manager, search_indexer=None)
```

**Parameters**

- `source` — the parent `KBSource` instance; used for all read-only Graph Store access
- `schema_manager` — the `SchemaManager` instance; used to fetch schema context before each agent call
- `search_indexer` — the `KBSearchIndexer` instance, if search is enabled; backs the `search_schema`, `search_entities`, and `search_facts` tools

### Methods

#### `answer(database, question, model="gpt-4o", natural_language=False, on_tool_call=None)`

Fetches the schema, constructs a system prompt, and runs the agent loop. The agent calls graph tools to find relevant entities, facts, and property values, then calls `return_result` with a list of `{entity, relation, value, provenance}` items. Returns a list of `AnswerResult` objects, or a prose answer generated from those results when `natural_language=True`.

**Example**

Input: `"Where did Alice work?"`

Output:
```python
[
    AnswerResult(entity="Alice",        relation=None,       value=None, provenance="Alice is a software engineer."),
    AnswerResult(entity="Alice → IBM",  relation="WORKED_AT", value=None, provenance="Alice joined IBM in June 1998."),
]
```

#### `validate(database, statement, model="gpt-4o", natural_language=False, on_tool_call=None)`

Fetches the schema, constructs a system prompt directing the agent to find supporting and counter evidence, and runs the agent loop. The agent calls `return_result` with `{supporting: [...], counter: [...]}` where each item is `{fact, head, tail, relation}`. Returns a `ValidationResult`, or a prose verdict when `natural_language=True`.

**Example**

Input: `"Alice worked at IBM."`

Output:
```python
ValidationResult(
    supporting=[{"fact": "Alice_WORKED_AT_IBM", "head": "Alice", "tail": "IBM", "relation": "WORKED_AT"}],
    counter=[]
)
```

#### `enrich(database, query, depth=2, model="gpt-4o", on_tool_call=None)`

Fetches the schema, constructs a system prompt directing the agent to traverse the graph up to `depth` hops from seed entities, and runs the agent loop. The agent uses `search_entities` to find seeds, then `get_neighbors` and `get_entity_facts` to explore, then calls `return_result` with a list of `{entity, relation, value, distance}` items. Returns a list of `EnrichmentResult` objects.

**Example**

Input: `"Alice's career in technology"`, depth 2

Output (partial):
```python
[
    EnrichmentResult(entity="Alice → IBM",    relation="WORKED_AT", value=None, distance=1),
    EnrichmentResult(entity="Alice → Google", relation="WORKED_AT", value=None, distance=1),
]
```

### Tool Set

| Tool | Description |
|------|-------------|
| `get_schema` | Return the current database schema as a string |
| `search_schema` | Search classes, relations, and properties by keyword or semantic similarity |
| `search_entities` | Search ENTITY nodes by keyword or semantic similarity against name, description, and property values |
| `search_facts` | Search FACT nodes by keyword or semantic similarity against head/relation/tail names, description, and property values |
| `get_entity_facts` | All facts involving an entity as head or tail |
| `get_entity_properties` | All property values attached to an entity |
| `get_fact_properties` | All property values attached to a fact |
| `get_neighbors` | BFS from an entity through facts up to a given depth |
| `return_result` | Terminal tool — delivers the structured payload and ends the loop |

---

## ProvenanceTracker

Records the origin of each piece of knowledge by creating and wiring EXTRACTION and SOURCE nodes.

### Constructor

```python
ProvenanceTracker(source)
```

### Methods

#### `add_source(database, source, name=None, description=None, node_id=None)`

Creates a SOURCE node. `name` defaults to `source` if omitted. Returns the SOURCE node ID.

#### `add_extraction(database, text, name=None, description=None, node_id=None, source_id=None)`

Creates an EXTRACTION node. If `source_id` is provided, wires an EXTRACTED_FROM edge from the EXTRACTION to the SOURCE. Returns the EXTRACTION node ID.

#### `track(database, instance_id, text, source_id=None)`

Creates a per-item EXTRACTION node for `text`, optionally wires it to `source_id`, and wires an EXTRACTED_FROM edge from `instance_id` to the EXTRACTION.

This is called internally by `KnowledgeManager` when `text` is provided on any instance-creation call. One EXTRACTION node is created per item — multiple items from the same passage each get their own EXTRACTION capturing their specific text span.

---

## ChangeNotifier

Maintains per-database hook registries and fires registered callbacks synchronously after each instance write.

### Constructor

```python
ChangeNotifier()
```

### Methods

#### `add_hook(database, fn)`

Registers `fn` to be called after every instance write to `database`. `fn` receives a single event `dict`.

#### `remove_hook(database, fn)`

Deregisters `fn` from `database`. No-op if not found.

#### `add_global_hook(fn)`

Registers `fn` to be called after every instance write to *any* database. Used internally to wire up the `KBSearchIndexer` when search is enabled, so the index stays current regardless of which database changed.

#### `remove_global_hook(fn)`

Deregisters a global hook. No-op if not found.

#### `fire(event)`

Calls all hooks registered for `event["database"]`, then all global hooks, in registration order. Each hook receives the full event dict. Database-scoped hooks in other databases are not notified; global hooks fire regardless of which database changed.

**Event fields by type:**

| `type` | Additional fields |
|--------|------------------|
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `property_id`, `property_name`, `value`, `head_entity_name`, `tail_entity_name`, `relation_name` |

All events also carry `type`, `database`, and `id`.

---

## KBSearchIndexer

Maintains a keyword (BM25) or embedding-based (semantic) search index over schema elements and instance data. Constructed only when the parent `KBSource` is created with `enable_search=True` or a `vector_source`; otherwise `KBSource.search_schema`/`search_data` fall back to `SchemaManager.find_schema`/`KnowledgeManager.find_data`.

### Constructor

```python
KBSearchIndexer(
    source,
    vector_source=None,
    embedding_fn=None,
    vector_dim=1536,
    distance_metric="COSINE",
    corpus_dir=None,
)
```

A BM25 corpus is always maintained in memory (optionally persisted to `corpus_dir`). Semantic search additionally requires both `vector_source` and `embedding_fn` — without them, `mode="semantic"` searches are unavailable.

### Methods

#### `on_change(event)`

Global change-hook entry point, registered by `KBSource` at construction time. Dispatches added/updated/deleted events for classes, relations, properties, entities, and facts to the appropriate indexing helper, keeping the index incrementally in sync without a full rebuild.

#### `search_schema(database, query, k=5, mode="keyword", type_filter=None)`

Searches the `schema` collection (CLASS, RELATION, PROPERTY). In `keyword` mode, ranks by BM25 score; in `semantic` mode, ranks by embedding similarity. Returns a list of `SchemaSearchResult`, each carrying a `score` when in semantic mode.

#### `search_data(database, query, k=5, mode="keyword", type_filter=None)`

Searches the `data` collection (ENTITY, FACT) the same way. Returns a list of `DataSearchResult`.

#### `reindex_node(database, node_id)`

Re-fetches a single node from the Graph Store and rebuilds its index entry, including composite text drawn from related nodes (e.g. an entity's class name, a fact's head/relation/tail names).

#### `remove(database, node_id)`

Removes a node from both the BM25 corpus and the vector index.

#### `reindex_all(database)`

Rebuilds the entire index for a database from scratch by iterating every CLASS, RELATION, PROPERTY, ENTITY, and FACT node currently in the Graph Store. Used after bulk imports or when the embedding model changes; exposed at the `KBSource` level as `reindex(database)`.

---

## ExtractionEngine

Bridges free text and the structured knowledge base using an LLM agent loop that calls into `SchemaManager` and `KnowledgeManager` as tools.

### Constructor

```python
ExtractionEngine(source, schema_manager, knowledge_manager)
```

### Methods

#### `import_text(database, text, queries=None, model="gpt-4o", source_id=None, on_tool_call=None)`

Extracts entities, facts, and property values from `text` into `database`.

**Parameters**

- `database` — target database
- `text` — the free-text passage to process
- `queries` — optional list of natural language questions guiding the extraction focus
- `model` — OpenAI model to use for the agent loop
- `source_id` — ID of an existing SOURCE node; passed through to each instance creation call
- `on_tool_call` — optional callback invoked with `(tool_name, arguments)` for each tool call the agent makes

**Algorithm**

1. Call `SchemaManager.fetch_database_schema(database)` and serialize it as context for the LLM
2. Assemble the tool set: schema inspection, schema declaration, and instance creation functions
3. Submit `text`, `queries`, and schema context to the LLM with the tool set
4. Loop:
   - Receive tool calls from the LLM
   - For schema declaration calls: dispatch to `SchemaManager`
   - For instance creation calls: dispatch to `KnowledgeManager`, passing `text=<verbatim span>` and `source_id=source_id`
   - Return tool results to the LLM
5. Exit when the LLM returns without further tool calls
