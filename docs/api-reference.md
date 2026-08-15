# API Reference

Full method-by-method reference for the Python library. For a task-oriented introduction, see [guide.md](guide.md). For the design rationale behind the shapes described here, see [`design/09-api-interface-spec.md`](../design/09-api-interface-spec.md), which this page is kept in sync with.

> **Platform docs:** for the CLI command reference see [cli.md](cli.md); for the MCP tool reference see [mcp.md](mcp.md).

---

## `KBSource`

```python
from blue_data_kb_source import KBSource
```

Abstract base class. Three ready-made backends ship alongside it — `MemoryKBSource`, `FileKBSource` (both `from blue_data_kb_source import ...`), and `NEO4JKBSource` (`from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource`, kept out of the top-level package so importing it is what pulls in the `neo4j` driver, not `import blue_data_kb_source` itself). To add a genuinely new backend, subclass `KBSource` together with a concrete `GraphSource` implementation directly (`class MyKBSource(KBSource, MyGraphSource): pass`; see [extending.md](extending.md)). Inherits all `GraphSource` primitives (`add_node`, `add_edge`, `get_node`, `get_neighbors`, `match_nodes`, ...).

```python
KBSource(name: str, properties: dict | None = None)
```

Matches `DataSource`'s own signature exactly — no other constructor arguments. Everything backend/search-specific is config under `properties`: `properties["connection"]` for the graph backend (e.g. `{"directory": ...}` for `FileKBSource`), `properties["search"]` for search —

| `properties["search"]` key | Meaning |
|---|---|
| `mode` | `"none"` (default) \| `"bm25"` \| `"semantic"` |
| `bm25_index_dir` | Persist the BM25 corpus to disk across restarts |
| `vector_dim` | Embedding dimension, default `1536` |
| `vector` | `{"_class": "<dotted VectorSource path>", "name": ..., "connection": {...}}` — constructed internally via `DataSource.instantiate()`; only used when `mode="semantic"` |
| `vector_source` | An already-live `VectorSource` object — takes precedence over `vector` if both are given; for in-memory/programmatic use, not persistable |
| `embedding` | `{"provider": "openai", "model": ...}` — `KBSource` builds the embedding callable itself |
| `embedding_fn` | An already-live `Callable[[str], list[float]]` — takes precedence over `embedding`; not persistable |

A database reconstructed purely from `DataSource.instantiate(class_path, name, properties=stored_properties)` — e.g. from the CLI's registry — gets its search indexer, vector source, and embedding function fully wired up from `properties["search"]` alone, no other code required.

All methods below are `async def` and must be awaited, **except** `create_database`/`fetch_databases` (inherited from the `GraphSource` backend, synchronous), `add_change_hook`/`remove_change_hook`, `reindex`, and the `has_semantic_search` property.

### Timestamps and provenance

Every node (`CLASS`, `RELATION`, `PROPERTY`, `ENTITY`, `FACT`, `LITERAL`, `SOURCE`, `EXTRACTION`) carries `created`/`modified` timestamps (ISO-8601 UTC) in its `properties`, set/refreshed automatically by every `declare_*`/`add_*`/`update_*` call below — there is no parameter for them. Every edge carries a `created` timestamp only. `SOURCE` additionally carries an `original` attribute (a caller-supplied date, e.g. a document's publication date, distinct from `created`), settable via `add_source`/`update_source`.

### Schema operations

All `declare_*` methods are **idempotent**: if a node with the same name already exists they return its id without inserting a duplicate.

| Method | Returns | Description |
|---|---|---|
| `await declare_class(database, name, description, class_id?)` | `str` | Add a `CLASS` node |
| `await declare_relation_type(database, name, description, is_symmetric?, relation_id?, *, domain, range)` | `str` | Add a `RELATION` node; `domain`/`range` are required (keyword-only), accept a class name or list of names, and wire `DOMAIN`/`RANGE` edges |
| `await declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Add a `PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a class |
| `await declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Add a `PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a relation |
| `await update_class(database, class_id, name?, description?)` | `None` | Update a class's name/description (refreshes `modified`) |
| `await update_relation(database, relation_id, name?, description?)` | `None` | Update a relation's name/description |
| `await update_property(database, property_id, name?, description?)` | `None` | Update a property's name/description |
| `await delete_class(database, class_id)` | `None` | Delete a class (errors if instances or subclasses exist) |
| `await delete_relation(database, relation_id)` | `None` | Delete a relation (errors if facts or sub-relations exist) |
| `await delete_property(database, property_id)` | `None` | Delete a property (errors if values exist) |
| `await add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire `IS_SUBCLASS_OF` between two `CLASS` or two `RELATION` nodes |
| `await remove_is_subclass_of(database, child_id, parent_id)` | `None` | Remove an `IS_SUBCLASS_OF` edge |
| `await fetch_database_schema(database)` | `DataSchema` | Build a `DataSchema` from the classes/relations declared in *database* |
| `await export_database_schema(database)` | `dict` | `fetch_database_schema(database).to_json()` — portable JSON, symmetric with `import_database_schema` |
| `await import_database_schema(database, schema_json)` | `None` | Populate *database* from a `DataSchema.to_json()`/`export_database_schema` dict |
| `await fetch_meta_schema()` | `DataSchema` | The fixed `DataSchema` describing the KB's own node/edge taxonomy (not database-specific) |
| `await search_schema(database, query, k?, mode?, type_filter?)` | `list[SchemaSearchResult]` | Search classes/relations/properties by keyword or semantic similarity |
| `await consolidate_schema(database, operation, source_names, target_name, predicate?)` | `None` | Merge or split schema elements and rewire instances; `operation="merge"` supported today |

Note: `add_domain`/`add_range` do **not** exist as separate methods — `domain`/`range` are wired inline as part of `declare_relation_type`, required there.

### Knowledge operations — write

| Method | Returns | Description |
|---|---|---|
| `await add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?)` | `str` | Add an `ENTITY` node; optionally wires `IS_INSTANCE_OF`; `text` creates a per-item `EXTRACTION` linked to `source_id` |
| `await add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?)` | `str` | Add a `FACT` node; auto-names from head/relation/tail if `name` omitted; wires `HEAD`/`TAIL`/`REPRESENTS` |
| `await add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?)` | `str` | Create a `LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from an entity |
| `await add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?)` | `str` | Create a `LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from a fact |
| `await update_entity(database, entity_id, name?, description?)` | `None` | Update an entity's name/description |
| `await update_fact(database, fact_id, name?, description?)` | `None` | Update a fact's name/description |
| `await update_entity_property_value(database, entity_id, property_id, value?, name?, description?)` | `None` | Update a property value on an entity |
| `await update_fact_property_value(database, fact_id, property_id, value?, name?, description?)` | `None` | Update a property value on a fact |
| `await delete_entity(database, entity_id, cascade?)` | `None` | Delete an entity and its property values; `cascade=True` also deletes referencing facts |
| `await delete_fact(database, fact_id)` | `None` | Delete a fact and its property values |
| `await delete_entity_property_value(database, entity_id, property_id)` | `None` | Delete a property value from an entity |
| `await delete_fact_property_value(database, fact_id, property_id)` | `None` | Delete a property value from a fact |
| `await add_entity_extraction(database, entity_id, text, source_id?)` | `str` | Link a new text span to an *existing* entity — additional `EXTRACTED_FROM` edge, no duplicate node |
| `await add_fact_extraction(database, fact_id, text, source_id?)` | `str` | Same, for an existing fact |
| `await add_entity_property_value_extraction(database, entity_id, property_id, text, source_id?)` | `str` | Link a new text span to an entity's existing property value; raises if none exists |
| `await add_fact_property_value_extraction(database, fact_id, property_id, text, source_id?)` | `str` | Same, for a fact's property value |

### Knowledge operations — read and export

| Method | Returns | Description |
|---|---|---|
| `await fetch_database_data(database, include_provenance?, compact?, detailed?)` | `dict \| list` | `compact=False` (default): raw `{nodes, edges}` graph form. `compact=True`: flat denormalized rows. `include_provenance=True` adds each row's `extraction` text and linked `source` name. `detailed=True` adds each row's own `description`/`created`/`modified`; together with `include_provenance` also adds `source_original` |
| `await export_database_data(database, include_provenance?)` | `dict` | Portable, ref-based export of entities/facts/property values; symmetric with `import_database_data` |
| `await import_database_data(database, data)` | `None` | Populate *database* from an `export_database_data` dict; resolves classes/relations/properties **by name** against the target's current schema |
| `await get_metadata(database, node_id)` | `dict` | `created`/`modified`/extraction/source metadata for a single entity, fact, or property-value (`LITERAL`) id — raises `KnowledgeError` for any other node type |
| `await search_data(database, query, k?, mode?, type_filter?)` | `list[DataSearchResult]` | Search entities/facts by keyword or semantic similarity |
| `reindex(database)` | `None` | Rebuild the BM25/vector index from current graph contents; raises `KBError` if search wasn't enabled |
| `has_semantic_search` (property) | `bool` | Whether the instance has a vector backend configured |

### Knowledge operations — LLM querying

| Method | Returns | Description |
|---|---|---|
| `await answer(database, question, model?, natural_language?, include_metadata?, on_tool_call?, on_tool_result?)` | `list[dict] \| str \| tuple[..., dict]` | LLM agent answers a question by traversing the instance graph. `include_metadata=True` returns `(result, metadata)`; `metadata` is `dict[node_id, dict]`. With `natural_language=True` too, the prose result has a provenance section appended, still paired with the raw `metadata` dict |
| `await validate(database, statement, model?, natural_language?, include_metadata?, on_tool_call?, on_tool_result?)` | `ValidationResult \| str \| tuple[..., dict]` | LLM agent returns supporting/counter evidence; `include_metadata`/`natural_language` behave as in `answer` |
| `await enrich(database, query, depth?, model?, on_tool_call?, on_tool_result?)` | `list[EnrichmentResult]` | LLM agent traverses the graph up to `depth` hops from seed entities |

`on_tool_call(name, args)` / `on_tool_result(name, args, result)`, if given, are invoked synchronously as the agent calls tools — useful for driving a progress indicator.

### Provenance operations

| Method | Returns | Description |
|---|---|---|
| `await add_source(database, source, name?, description?, node_id?, original?)` | `str` | Add a `SOURCE` node; `name` defaults to `source`; `original` records the source's own date, separate from `created` |
| `await update_source(database, source_id, name?, description?, original?)` | `None` | Update a source's name/description/original date |
| `await delete_source(database, source_id)` | `None` | Delete a source and its `EXTRACTION` nodes; instance nodes are preserved |
| `await add_extraction(database, text, name?, description?, node_id?, source_id?, element_id?)` | `str` | Add an `EXTRACTION` node; `element_id` wires `EXTRACTED_FROM` to an existing entity/fact/literal in the same call |

Note: `add_extracted_from` is **not** a public `KBSource` method — use `add_extraction(..., element_id=...)`.

### Extraction

| Method | Returns | Description |
|---|---|---|
| `await import_text(database, text, queries?, model?, source_id?, schema_discovery_mode?, on_tool_call?, on_tool_result?)` | `None` | LLM agent extracts entities/facts/properties from `text` into `database`, extending the schema as needed. `schema_discovery_mode`: `"search"` (default), `"llm"`, or `"none"` — see [guide.md](guide.md#extracting-knowledge-from-text-llm). Requires `openai` |

### Change hooks

| Method | Description |
|---|---|
| `add_change_hook(database, fn)` | Register a sync `fn(event: dict)`, called after every schema/instance write to `database` |
| `remove_change_hook(database, fn)` | Deregister a previously added hook (no-op if not found) |

#### Change hook events

Every event carries `type`, `database`, `id`. Schema events fire only when a new element is actually created — idempotent repeats don't re-fire.

| `type` | Extra fields |
|---|---|
| `class_declared` | `name`, `description` |
| `relation_declared` | `name`, `description`, `is_symmetric`, `domain` (list), `range` (list) |
| `class_property_declared` | `name`, `description`, `class_id`, `class_name` |
| `relation_property_declared` | `name`, `description`, `relation_id`, `relation_name` |
| `class_subclass` / `relation_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `name`, `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `entity_name`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `fact_name`, `property_id`, `property_name`, `value`, `head_entity_id/name`, `tail_entity_id/name`, `relation_id`, `relation_name` |

Name/id fields are `None` when the corresponding id wasn't provided on the write call.

### Sync convenience methods

Two synchronous methods wrap the async API via `asyncio.run()` internally, for callers that can't `await`:

| Method | Description |
|---|---|
| `execute_query(query: dict, database=None, collection=None, optional_properties=None)` | Generic op-code dispatcher — `query["op"]` such as `"KB.DECLARE_CLASS"`, `"KB.ADD_ENTITY"`, `"KB.GET_DATA"`, mirroring most async methods above 1:1; `GRAPH.*` ops forward to the underlying `GraphSource`. `get_metadata` is deliberately **not** exposed here (library method + MCP tool only — see ADR-018 in [`design/13-decision-log.md`](../design/13-decision-log.md)) |
| `execute_query_nl(query: str, database=None, ...)` | Sync wrapper around `answer(..., natural_language=False)`, always returning a structured `list[dict]` |

---

## Result types

```python
from blue_data_kb_source.types import (
    SchemaSearchResult, DataSearchResult, ValidationResult, EnrichmentResult,
)
```

| Type | Fields |
|---|---|
| `SchemaSearchResult` | `id`, `type` (`"CLASS"`\|`"RELATION"`\|`"PROPERTY"`), `name`, `description`, `score?` (semantic mode only) |
| `DataSearchResult` | `id`, `type` (`"ENTITY"`\|`"FACT"`), `name`, `description?`, `class_name?` (entities), `head_name?`/`relation_name?`/`tail_name?` (facts), `score?` |
| `ValidationResult` | `supporting: list[dict]` (each `{fact, head, tail, relation}`), `counter: list[dict]` |
| `EnrichmentResult` | `entity`, `relation`, `value`, `distance` |

> `answer()`'s structured-mode return value is a plain `list[dict]`, **not** a list of typed objects — each dict's keys are whatever the LLM agent chose (e.g. `{"entity", "relation", "value", "provenance"}`), not a fixed schema. `AnswerResult` is defined in `types.py` and re-exported, but no code path constructs it — treat it as vestigial.

`get_metadata` returns a plain `dict`, not a dataclass — see [architecture.md](architecture.md#the-nodeedge-model) for its shape.

---

## Exceptions

```python
from blue_data_kb_source.types import KBError, SchemaError, KnowledgeError, ExtractionError, ProvenanceError
```

All inherit from `KBError`.

| Exception | Raised when |
|---|---|
| `SchemaError` | Schema validation/consistency failures — missing domain class, a `class_id` that isn't a `CLASS` node, etc. |
| `KnowledgeError` | Knowledge-layer write/query failures — an `entity_id` that doesn't exist or refers to the wrong node type, etc. |
| `ExtractionError` | LLM extraction failures (e.g. `openai` not installed) |
| `ProvenanceError` | Provenance-tracking failures |

Write methods that take an id referencing an *existing* node (`class_id`, `entity_id`, `fact_id`, `source_id`, ...) validate the node exists and is the expected type before doing anything, raising with the offending id and expected type otherwise. Ids naming a *new* node being created (e.g. `class_id` passed to `declare_class` to force a specific id) aren't checked this way.

---

## Literal types

`literal_type` must be one of `"NUMBER"`, `"DATE"`, `"BOOLEAN"`, `"STRING"`. When omitted, it's inferred from the Python type of `value` (`bool` → `BOOLEAN`, `int`/`float` → `NUMBER`, everything else → `STRING`).

---

## Internal components

Not part of the public API — reached only through `KBSource` — but useful to know about when reading the source or [architecture.md](architecture.md):

| Class | Module | Role |
|---|---|---|
| `SchemaManager` | `schema/manager.py` | Backs all Schema operations above |
| `KnowledgeManager` | `knowledge/manager.py` | Backs all Knowledge operations above; owns `QueryEngine` |
| `QueryEngine` | `knowledge/engine.py` | LLM agent behind `answer`/`validate`/`enrich`; not exposed directly |
| `ProvenanceTracker` | `provenance/tracker.py` | Backs Provenance operations |
| `ChangeNotifier` | `notification/notifier.py` | Backs change hooks |
| `KBSearchIndexer` | `search/indexer.py` | Backs `search_schema`/`search_data`/`reindex` when search is enabled |
| `ExtractionEngine` | `extraction/engine.py` | LLM agent behind `import_text` |
