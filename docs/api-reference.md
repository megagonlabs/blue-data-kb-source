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
| `await add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire `IS_SUBCLASS_OF` between two `CLASS` or two `RELATION` nodes — the only way to express an is-a/type-of/kind-of hierarchy; never a declared `RELATION` |
| `await remove_is_subclass_of(database, child_id, parent_id)` | `None` | Remove an `IS_SUBCLASS_OF` edge |
| `await attach_property(database, target_id, property_id)` | `None` | Wire `DEFINE_PROPERTY` from an existing `CLASS`/`RELATION` to an existing `PROPERTY` (idempotent) — reuse a property across classes/relations instead of declaring a same-meaning duplicate under a new name |
| `await attach_relation_domain(database, relation_id, class_id)` | `None` | Attach a `CLASS` to an existing relation's `DOMAIN` — deduplicated, add-only (no detach counterpart) |
| `await attach_relation_range(database, relation_id, class_id)` | `None` | Same, for `RANGE` |
| `await get_class_ancestors(database, class_id, max_hops?)` | `list[dict]` | Ancestor `CLASS` nodes via `IS_SUBCLASS_OF`, breadth-first with cycle protection, nearest-first; `max_hops` defaults to `5`, `None` = unbounded |
| `await get_class_descendants(database, class_id, max_hops?)` | `list[dict]` | Descendant `CLASS` nodes, same walk downward |
| `await get_relation_ancestors(database, relation_id, max_hops?)` | `list[dict]` | Ancestor `RELATION` nodes, same walk over the relation hierarchy |
| `await get_relation_descendants(database, relation_id, max_hops?)` | `list[dict]` | Descendant `RELATION` nodes |
| `await get_class_entities(database, class_id, limit?)` | `list[dict]` | `ENTITY` nodes that are instances of the class; `limit` defaults to `10`, `None` = all |
| `await get_relation_domain_range(database, relation_id)` | `dict` | The relation's `DOMAIN` and `RANGE` classes, as `{"domain": [...], "range": [...]}` |
| `await get_allowed_relations_between_classes(database, head_class_id, tail_class_id, expand?, max_hops?)` | `list[dict]` | `RELATION` nodes whose domain/range accept the two classes; `expand` defaults to `True` and lets an ancestor class satisfy the constraint |
| `await find_class(database, name)` | `dict \| None` | `CLASS` node by exact, case-sensitive name |
| `await find_relation_type(database, name)` | `dict \| None` | `RELATION` node by exact name |
| `await find_property(database, name)` | `dict \| None` | `PROPERTY` node by exact name |
| `await fetch_database_schema(database)` | `DataSchema` | Build a `DataSchema` from the classes/relations declared in *database* |
| `await export_database_schema(database)` | `dict` | `fetch_database_schema(database).to_json()` — portable JSON, symmetric with `import_database_schema` |
| `await import_database_schema(database, schema_json)` | `None` | Populate *database* from a `DataSchema.to_json()`/`export_database_schema` dict |
| `await fetch_meta_schema()` | `DataSchema` | The fixed `DataSchema` describing the KB's own node/edge taxonomy (not database-specific) |
| `await search_schema(database, query, k?, mode?, type_filter?)` | `list[SchemaSearchResult]` | Search classes/relations/properties by keyword or semantic similarity |
| `await consolidate_schema(database, operation, source_names, target_name, predicate?)` | `None` | Merge or split schema elements and rewire instances; `operation="merge"` supported today |
| `await expand_schema(database, text, depth?, reference?, mcp_servers?, dedup_mode?, model?, on_tool_call?, on_tool_result?, max_turns?)` | `None` | OpenAI LLM agent that grows the schema (`CLASS`/`RELATION`/`PROPERTY`) grounded by `text` — never touches entities, facts, or property values (that's `import_text`, below). `depth` (default `1`) — hierarchy levels beyond what's directly evidenced to also propose, linked via `add_is_subclass_of`. `reference`: `"llm"` (default) proposes freely; `"mcp"` also gives the model tools from `mcp_servers` (e.g. WordNet via `platform/wordnet-mcp`) to ground names/hierarchy. `dedup_mode` (`"search"` default \| `"llm"` \| `"none"`) controls how it checks for an existing element before declaring a new one — see [guide.md](guide.md#growing-the-schema-and-extracting-knowledge-from-text-llm). Never declares a taxonomic (is-a/type-of/kind-of) name as a `RELATION`. Requires `openai` (and `mcp` for `reference="mcp"`) |

Note: `declare_relation_type` wires the initial `DOMAIN`/`RANGE` edges inline (`domain`/`range` are required there), and re-declaring never re-wires them — post-declaration widening is what `attach_relation_domain`/`attach_relation_range` (above) are for.

### Knowledge operations — write

| Method | Returns | Description |
|---|---|---|
| `await add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Add an `ENTITY` node; optionally wires `IS_INSTANCE_OF`; `text` creates a per-item `EXTRACTION` linked to `source_id` |
| `await add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Add a `FACT` node; auto-names from head/relation/tail if `name` omitted; wires `HEAD`/`TAIL`/`REPRESENTS`. When head/tail/relation are all given, raises `KnowledgeError` unless `is_fact_allowed` reports `ALLOWED` — it never widens the relation's domain/range the way `assert_fact` does |
| `await add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a `LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from an entity |
| `await add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a `LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from a fact |
| `await update_entity(database, entity_id, name?, description?)` | `None` | Update an entity's name/description |
| `await update_fact(database, fact_id, name?, description?)` | `None` | Update a fact's name/description |
| `await update_entity_property_value(database, entity_id, property_id, value?, name?, description?)` | `None` | Update a property value on an entity |
| `await update_fact_property_value(database, fact_id, property_id, value?, name?, description?)` | `None` | Update a property value on a fact |
| `await delete_entity(database, entity_id, cascade?)` | `None` | Delete an entity and its property values; `cascade=True` also deletes referencing facts |
| `await delete_fact(database, fact_id)` | `None` | Delete a fact and its property values |
| `await delete_entity_property_value(database, entity_id, property_id)` | `None` | Delete a property value from an entity |
| `await delete_fact_property_value(database, fact_id, property_id)` | `None` | Delete a property value from a fact |
| `await add_entity_extraction(database, entity_id, text, source_id?, confidence?, extraction_type?)` | `str` | Link a new text span to an *existing* entity — additional `EXTRACTED_FROM` edge, no duplicate node |
| `await add_fact_extraction(database, fact_id, text, source_id?, confidence?, extraction_type?)` | `str` | Same, for an existing fact |
| `await add_entity_property_value_extraction(database, entity_id, property_id, text, source_id?, confidence?, extraction_type?)` | `str` | Link a new text span to an entity's existing property value; raises if none exists |
| `await add_fact_property_value_extraction(database, fact_id, property_id, text, source_id?, confidence?, extraction_type?)` | `str` | Same, for a fact's property value |
| `await attach_entity_class(database, entity_id, class_id)` | `None` | Attach an existing CLASS to an existing ENTITY — deduplicated, add-only (no detach counterpart). An entity's *additional* class; `add_entity(class_id=…)` wires the first |

### Knowledge operations — read and export

| Method | Returns | Description |
|---|---|---|
| `await fetch_database_data(database, include_provenance?, compact?, detailed?)` | `dict \| list` | `compact=False` (default): raw `{nodes, edges}` graph form. `compact=True`: flat denormalized rows. `include_provenance=True` adds each row's `evidence`, one record per EXTRACTION. `detailed=True` adds each row's own `description`/`created`/`modified`, plus confidence on fact rows |
| `await export_database_data(database, include_provenance?)` | `dict` | Portable, ref-based export of entities/facts/property values; symmetric with `import_database_data` |
| `await import_database_data(database, data)` | `None` | Populate *database* from an `export_database_data` dict; resolves classes/relations/properties **by name** against the target's current schema |
| `await get_metadata(database, node_id)` | `dict` | Metadata for a single entity, fact, or property-value (`LITERAL`) id: `created`/`modified`, `confidence` (a FACT's derived score, `None` on the other two), and an `evidence` record per EXTRACTION — raises `KnowledgeError` for any other node type |
| `await get_entity_classes(database, entity_id)` | `list[dict]` | `CLASS` nodes the entity is an instance of, via `IS_INSTANCE_OF` |
| `await get_fact_parts(database, fact_id)` | `dict` | The fact's first `HEAD`/`RELATION`/`TAIL` nodes, as `{"head": ..., "relation": ..., "tail": ...}` with `None` for unwired parts |
| `await get_property_values(database, owner_id)` | `list[dict]` | `LITERAL` values on an entity or fact with each value's `PROPERTY`, as `[{"literal": ..., "property": ...}]` |
| `await is_fact_allowed(database, head_entity_id, relation_id, tail_entity_id, max_hops?)` | `dict` | Whether the relation's domain/range accept the two entities' classes, subclass-aware via ancestors; returns `{ok, reason, details}` with reasons `ALLOWED` \| `UNTYPED_ENTITY` \| `DOMAIN_RANGE_MISMATCH`; direction-exact — `is_symmetric` is not consulted |
| `await is_property_allowed(database, owner_id, property_id, max_hops?)` | `dict` | Whether the property is `DEFINE_PROPERTY`-owned by the entity's classes/ancestors (entity owner) or the fact's relation/ancestors (fact owner); adds reasons `FACT_HAS_NO_RELATION` \| `NOT_DEFINED_FOR_OWNER` |
| `await find_entity(database, name)` | `dict \| None` | First `ENTITY` with exactly *name* — entity names are not unique |
| `await find_fact(database, head_entity_id, relation_id, tail_entity_id)` | `dict \| None` | First `FACT` matching the exact directed (head, relation, tail) triple |
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
| `await add_extraction(database, text, name?, description?, node_id?, source_id?, confidence?, extraction_type?, element_id?)` | `str` | Add an `EXTRACTION` node; `element_id` wires `EXTRACTED_FROM` to an existing entity/fact/literal in the same call |
| `await attach_extraction(database, element_id, extraction_id)` | `None` | Wire `EXTRACTED_FROM` from an `ENTITY`/`FACT`/`LITERAL` to an EXTRACTION that already exists — created up front by `add_extraction` without an `element_id`, or re-wired on snapshot restore. Deduplicated, add-only. Each item still keeps its own EXTRACTION node; this does not share evidence between items |

Note: `add_extracted_from` is **not** a public `KBSource` method. To link an extraction to an item, pass `element_id` to `add_extraction` when creating it, or call `attach_extraction` for an extraction that already exists.

### Ingest operations — assert and ingest

Name-based, resolve-or-create counterparts to the knowledge write operations: resolution happens first (ambiguity raises `IngestionError` carrying the candidates, with nothing written), and anything unresolved is created — schema elements included, with LLM judges naming classes and writing descriptions.

| Method | Returns | Description |
|---|---|---|
| `await assert_class(database, name, description?)` | `AssertResult` | Reuse a class when one resolves, create it otherwise |
| `await assert_relation_type(database, name, domain, range, description?, is_symmetric?)` | `AssertResult` | Missing domain/range classes are created; an existing relation is widened, not rejected |
| `await assert_class_property(database, name, class_name, description?)` | `AssertResult` | The owning class is created when missing |
| `await assert_relation_property(database, name, relation_name, description?)` | `AssertResult` | The relation must already exist (it cannot be created without domain/range) |
| `await assert_subclass(database, child, parent)` | `AssertResult` | Missing classes are created; the edge is idempotent |
| `await assert_subrelation(database, child, parent)` | `AssertResult` | Both relations must already exist |
| `await assert_entity(database, name, class_name?, description?)` | `AssertResult` | Resolves by name; a new or classless entity gets a judged class (`EVERYTHING` under `"none"`) |
| `await assert_fact(database, head, relation, tail, name?, description?)` | `AssertResult` | Operands resolved or created; an existing triple dedupes with new evidence; non-conforming domain/range widens |
| `await assert_entity_property(database, entity, property, value, literal_type?)` | `AssertResult` | Equal values dedupe; a new property's placement is judged over the entity's classes |
| `await assert_fact_property(database, head, relation, tail, property, value, literal_type?)` | `AssertResult` | The fact is identified structurally by its triple, and created when missing |
| `await ingest(database, payload)` | `IngestReport` | Runs a ten-arm payload of rows through the asserts in file order; rows may override `confidence`/`extraction_type`; failures collect into the report |

Note: all eleven take the same trailing keyword options — `source_id?`, `confidence?`, `extraction_type?` (default `"assertion"`), `schema_creation_mode?` (`"llm"` default, requires `OPENAI_API_KEY`; or `"none"`), and `model?`.

### Extraction

| Method | Returns | Description |
|---|---|---|
| `await import_text(database, text, queries?, model?, source_id?, expand_schema?, schema_expansion_target?, schema_expansion_depth?, schema_expansion_mode?, schema_expansion_mcp_servers?, schema_expansion_dedup_mode?, on_tool_call?, on_tool_result?, max_turns?)` | `None` | LLM agent extracts entities/facts/property values from `text` into `database`, against the schema as it currently stands — never declares new schema itself; a mention with no matching element is skipped. `expand_schema` (default `True`) runs `expand_schema()` first; its `schema_expansion_*` params mirror `expand_schema`'s `depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one, with `schema_expansion_target` (any combination of `"text"`/`"queries"`, default both) selecting what gets concatenated into that call's `text` — see [guide.md](guide.md#growing-the-schema-and-extracting-knowledge-from-text-llm). Requires `openai` |

### Change hooks

| Method | Description |
|---|---|
| `add_change_hook(database, fn)` | Register a sync `fn(event: dict)`, called after every schema/instance write to `database` |
| `remove_change_hook(database, fn)` | Deregister a previously added hook (no-op if not found) |

#### Change hook events

Every event carries `type`, `database`, `id`. Schema events fire only when something is actually created — a repeated call that creates nothing (an idempotent re-declaration, a deduplicated attach) does not re-fire.

| `type` | Extra fields |
|---|---|
| `class_declared` | `name`, `description` |
| `relation_declared` | `name`, `description`, `is_symmetric`, `domain` (list), `range` (list) |
| `class_property_declared` | `name`, `description`, `class_id`, `class_name` |
| `relation_property_declared` | `name`, `description`, `relation_id`, `relation_name` |
| `class_subclass` / `relation_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |
| `property_attached` | `target_id`, `target_label` (`"CLASS"`\|`"RELATION"`), `property_id` |
| `relation_domain_attached` / `relation_range_attached` | `relation_id`, `relation_name`, `class_id`, `class_name` |
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `name`, `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `entity_name`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `fact_name`, `property_id`, `property_name`, `value`, `head_entity_id/name`, `tail_entity_id/name`, `relation_id`, `relation_name` |
| `entity_class_attached` | `entity_id`, `entity_name`, `class_id`, `class_name` |
| `extraction_attached` | `element_id`, `extraction_id` |

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
| `ResolutionCandidate` | `node_id`, `name`, `label`, `match_type` (`exact`\|`normalized`\|`alias`\|`search`\|`structural`), `score` |
| `NodeRef` | `node_id`, `label`, `name`, `extraction_id` |
| `AssertResult` | `node_id`, `created`, `match_type`, `extraction_id`, `created_nodes: list[NodeRef]`, `widened_relations: list[NodeRef]` |
| `IngestFailure` | `arm`, `index`, `reason`, `candidates: list[ResolutionCandidate]` |
| `IngestReport` | `written`, `deduped`, `skipped`, `failures`, `created: dict[label, count]`, `widened: list[str]` |

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
| `IngestionError` | An assert's resolve phase failed — ambiguity (candidates attached), a judge crash, a collapsed edge; nothing was written |

Methods that take an id referencing an *existing* node (`class_id`, `entity_id`, `fact_id`, `source_id`, ...) validate the node exists and is the expected type before doing anything, raising with the offending id and expected type otherwise. Ids naming a *new* node being created (e.g. `class_id` passed to `declare_class` to force a specific id) aren't checked this way.

---

## Literal types

`literal_type` must be one of `"NUMBER"`, `"DATE"`, `"BOOLEAN"`, `"STRING"`. When omitted, it's inferred from the Python type of `value` (`bool` → `BOOLEAN`, `int`/`float` → `NUMBER`, everything else → `STRING`). When given explicitly, `value` is cast to match — a string `"42"` with `literal_type="NUMBER"` is stored as the `int` `42`, not the string — so callers that only produce text (CLI, MCP, the extraction agent) still get a properly typed `LITERAL`. An unparsable `NUMBER`/`BOOLEAN` string raises `KnowledgeError`.

---

## Internal components

Not part of the public API — reached only through `KBSource` — but useful to know about when reading the source or [architecture.md](architecture.md):

| Class | Module | Role |
|---|---|---|
| `SchemaManager` | `schema/manager.py` | Backs all Schema operations above |
| `SchemaExpansionEngine` | `schema/expansion.py` | LLM agent behind `expand_schema`; owned by `SchemaManager`, not exposed directly |
| `KnowledgeManager` | `knowledge/manager.py` | Backs all Knowledge operations above; owns `QueryEngine` |
| `QueryEngine` | `knowledge/engine.py` | LLM agent behind `answer`/`validate`/`enrich`; not exposed directly |
| `ProvenanceTracker` | `provenance/tracker.py` | Backs Provenance operations |
| `ChangeNotifier` | `notification/notifier.py` | Backs change hooks |
| `KBSearchIndexer` | `search/indexer.py` | Backs `search_schema`/`search_data`/`reindex` when search is enabled |
| `ExtractionEngine` | `extraction/engine.py` | LLM agent behind `import_text` |
