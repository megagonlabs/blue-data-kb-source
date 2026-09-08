# KBDB — Library

> External dependencies: `blue-data` provides `DataSource` (base class, incl. the `instantiate()` dotted-path factory), `GraphSource`, `MemoryGraphSource`, `FileGraphSource`, `NEO4JSource`, `VectorSource` and its backends, `DataSchema`, and related base classes.

---

## KBSource(GraphSource)

Entry point for all KBDB operations. Abstract base class — must be subclassed together with a concrete `GraphSource` backend using Python MRO. Three such subclasses ship with the library already: `MemoryKBSource` (`memory_kb_source.py`), `FileKBSource` (`file_kb_source.py`), and `NEO4JKBSource` (`neo4j/neo4j_kb_source.py`, kept out of the package's top-level `__init__.py` so importing it — not `import blue_data_kb_source` itself — is what requires the `neo4j` driver). Each overrides `_initialize_connection_properties` to report a `kb/memory`/`kb/file`/`kb/neo4j` connection protocol instead of inheriting the mixed-in `GraphSource` backend's own `graph/*` protocol (see ADR-004 in [13-decision-log.md](13-decision-log.md)). Instantiates and coordinates the `SchemaManager` (which internally owns a `SchemaExpansionEngine`), `KnowledgeManager` (which internally owns a `QueryEngine`), `ProvenanceTracker`, `ChangeNotifier`, `ExtractionEngine`, and — when search is enabled — `KBSearchIndexer`. Every method except the `GraphSource` primitives it inherits (e.g. `create_database`) is `async` and must be awaited.

### Constructor

```python
KBSource(name: str, properties: dict[str, Any] | None = None)
```

Matches `DataSource.__init__`'s own signature exactly — no `*args`/`**kwargs`, no separate `enable_search=`/`vector_source=`/`embedding_fn=`/`vector_dim=`/`corpus_dir=` parameters. Every `KBSource` subclass in this codebase follows the same rule (no extra constructor kwargs of its own); backend connection config lives under `properties["connection"]` instead — e.g. `FileKBSource(name="x", properties={"connection": {"directory": "/data"}})`, picked up by `FileGraphSource`'s own `_initialize_connection_properties` default-filling once no `directory=` kwarg is passed.

Search config is `properties["search"]`:

| Key | Type | Meaning |
|---|---|---|
| `mode` | `"none"` \| `"bm25"` \| `"semantic"` | Default `"none"` |
| `bm25_index_dir` | `str` | Persist the BM25 corpus to disk across restarts |
| `vector_dim` | `int` | Embedding dimension, default `1536` |
| `vector` | `{"_class": str, "name"?: str, "connection": dict}` | Declarative — constructed via `DataSource.instantiate(vcfg["_class"], vcfg.get("name", "kb_vectors"), properties=vcfg)`. Only consulted when `mode="semantic"` and no live `vector_source` is given |
| `vector_source` | already-live `VectorSource` object | In-memory/programmatic use — takes precedence over `vector`, never persisted |
| `embedding` | `{"provider": str, "model"?: str}` | Declarative — `KBSource`'s own `_build_embedding_fn` helper constructs the callable (`provider="openai"` only, today) |
| `embedding_fn` | already-live `Callable[[str], list[float]]` | Takes precedence over `embedding`, never persisted |

A `KBSource` reconstructed purely via `DataSource.instantiate(class_path, name, properties=stored_properties)` — e.g. the CLI's registry, or the MCP server's env-var-driven singleton — gets its search indexer, vector source, and embedding function fully wired up from `properties["search"]` alone; no bespoke translation code needed at the call site. `KBSearchIndexer` itself (the class `__init__` actually constructs) is unaffected by this — it still takes `vector_source=`/`embedding_fn=`/`vector_dim=`/`corpus_dir=` as its own explicit kwargs; only `KBSource`'s own outward-facing constructor changed.

### Example

```python
import asyncio
from blue_data_kb_source import MemoryKBSource

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
        domain="EMPLOYEE", range="COMPANY"
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
| `declare_class(database, name, description, class_id?)` | `str` | Declare a CLASS node (idempotent); `name` is canonicalized to `UPPER_SNAKE_CASE` |
| `declare_relation_type(database, name, description, is_symmetric?, relation_id?, *, domain, range)` | `str` | Declare a RELATION node; `domain`/`range` are required (keyword-only); wires DOMAIN/RANGE edges inline; `name` and every `domain`/`range` class name are canonicalized to `UPPER_SNAKE_CASE` |
| `declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Declare a PROPERTY node; wires DEFINE_PROPERTY from a class; `name` is canonicalized to `UPPER_SNAKE_CASE` |
| `declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Declare a PROPERTY node; wires DEFINE_PROPERTY from a relation; `name` is canonicalized to `UPPER_SNAKE_CASE` |
| `update_class(database, class_id, name?, description?)` | `None` | Update a CLASS node's name/description; not idempotent |
| `update_relation(database, relation_id, name?, description?)` | `None` | Update a RELATION node |
| `update_property(database, property_id, name?, description?)` | `None` | Update a PROPERTY node |
| `delete_class(database, class_id)` | `None` | Delete a CLASS node |
| `delete_relation(database, relation_id)` | `None` | Delete a RELATION node |
| `delete_property(database, property_id)` | `None` | Delete a PROPERTY node |
| `add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire IS_SUBCLASS_OF edge; not deduplicated — calling twice adds two edges. The only way to express an is-a/type-of/kind-of hierarchy between two CLASSes or two RELATIONs — never a declared RELATION |
| `remove_is_subclass_of(database, child_id, parent_id)` | `None` | Remove an IS_SUBCLASS_OF edge |
| `attach_property(database, target_id, property_id)` | `None` | Wire DEFINE_PROPERTY from an existing CLASS/RELATION to an existing PROPERTY (idempotent) — reuse a property across classes/relations instead of declaring a same-meaning duplicate under a new name |
| `attach_relation_domain(database, relation_id, class_id)` | `None` | Attach a CLASS to the relation's DOMAIN; deduplicated, and add-only — no detach counterpart, domains only widen |
| `attach_relation_range(database, relation_id, class_id)` | `None` | Same, for RANGE |
| `get_class_ancestors(database, class_id, max_hops?)` | `list[dict]` | Ancestor CLASS nodes via IS_SUBCLASS_OF, breadth-first with cycle protection, ordered nearest-first (order within a depth is backend-dependent); `max_hops` defaults to 5, `None` = unbounded |
| `get_class_descendants(database, class_id, max_hops?)` | `list[dict]` | Descendant CLASS nodes, same walk in the other direction |
| `get_relation_ancestors(database, relation_id, max_hops?)` | `list[dict]` | Ancestor RELATION nodes — RELATION also carries IS_SUBCLASS_OF |
| `get_relation_descendants(database, relation_id, max_hops?)` | `list[dict]` | Descendant RELATION nodes |
| `get_class_entities(database, class_id, limit?)` | `list[dict]` | ENTITY instances of a class via IS_INSTANCE_OF; `limit` defaults to 10, `None` = unbounded |
| `get_relation_domain_range(database, relation_id)` | `dict` | The relation's DOMAIN and RANGE CLASS nodes, as `{"domain": [...], "range": [...]}` |
| `get_allowed_relations_between_classes(database, head_class_id, tail_class_id, expand?, max_hops?)` | `list[dict]` | RELATION nodes whose domain/range accept the two classes; `expand` defaults to true and lets an ancestor class satisfy the constraint |
| `find_class(database, name)` | `dict \| None` | CLASS node by exact, case-sensitive name |
| `find_relation_type(database, name)` | `dict \| None` | RELATION node by exact name |
| `find_property(database, name)` | `dict \| None` | PROPERTY node by exact name |
| `fetch_database_schema(database)` | `DataSchema` | Assemble a `DataSchema` object from the current graph |
| `export_database_schema(database)` | `dict` | `fetch_database_schema(database).to_json()` — portable JSON form |
| `fetch_meta_schema()` | `DataSchema` | Return the fixed KBDB meta-schema |
| `import_database_schema(database, schema_json)` | `None` | Replay a `DataSchema.to_json()` dict via idempotent `declare_*` calls — safe to import into a database that already has same-named elements |
| `consolidate_schema(database, operation, source_names, target_name, predicate?)` | `None` | Only `operation="merge"` is implemented; any other value raises `SchemaError`. `predicate` is accepted but unused. See SchemaManager below for what merge actually does — it does not remove source elements. |
| `expand_schema(database, text, depth?, reference?, mcp_servers?, dedup_mode?, model?, on_tool_call?, on_tool_result?, max_turns?)` | `None` | LLM agent that grows the schema (CLASS/RELATION/PROPERTY only) grounded by `text` — never touches entities/facts/property values. See SchemaExpansionEngine below |

`declare_relation_type` wires the initial DOMAIN/RANGE edges inline (`domain`/`range` are required there), and re-declaring never re-wires them — post-declaration widening is what `attach_relation_domain`/`attach_relation_range` are for.

#### Knowledge operations (delegate to `KnowledgeManager`)

| Method | Returns | Description |
|--------|---------|-------------|
| `add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create an ENTITY node; never idempotent — always creates a new node |
| `add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a FACT node; `name` auto-synthesizes as `{head}_{relation}_{tail}` if omitted. When head/tail/relation are all given, checks conformance via `is_fact_allowed` and raises `KnowledgeError` if it isn't `ALLOWED` — unlike `assert_fact`, this never widens the relation's domain/range |
| `add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a LITERAL and wire it to an entity |
| `add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a LITERAL and wire it to a fact |
| `update_entity(database, entity_id, name?, description?)` | `None` | Update an ENTITY node |
| `update_fact(database, fact_id, name?, description?)` | `None` | Update a FACT node |
| `update_entity_property_value(database, entity_id, property_id, value?, name?, description?)` | `None` | Update a LITERAL owned by an entity |
| `update_fact_property_value(database, fact_id, property_id, value?, name?, description?)` | `None` | Update a LITERAL owned by a fact |
| `delete_entity(database, entity_id, cascade?)` | `None` | Delete an entity; `cascade=True` also deletes facts that reference it |
| `delete_fact(database, fact_id)` | `None` | Delete a fact |
| `delete_entity_property_value(database, entity_id, property_id)` | `None` | Delete a LITERAL owned by an entity |
| `delete_fact_property_value(database, fact_id, property_id)` | `None` | Delete a LITERAL owned by a fact |
| `add_entity_extraction(database, entity_id, text, source_id?, confidence?, extraction_type?)` | `str` | Attach *additional* evidence to an existing entity — doesn't touch name/description, doesn't create a duplicate; multiple calls accumulate multiple EXTRACTED_FROM edges |
| `add_fact_extraction(database, fact_id, text, source_id?, confidence?, extraction_type?)` | `str` | Same, for an existing fact |
| `add_entity_property_value_extraction(database, entity_id, property_id, text, source_id?, confidence?, extraction_type?)` | `str` | Same, for an existing entity's property value |
| `add_fact_property_value_extraction(database, fact_id, property_id, text, source_id?, confidence?, extraction_type?)` | `str` | Same, for an existing fact's property value |
| `attach_entity_class(database, entity_id, class_id)` | `None` | Attach an existing CLASS to an existing ENTITY via IS_INSTANCE_OF; deduplicated, and add-only — no detach counterpart. Supplies an entity's *additional* class; `add_entity(class_id=…)` wires the first at creation |
| `fetch_database_data(database, include_provenance?, compact?, detailed?)` | `dict \| list` | Fetch all instance data in the database |
| `export_database_data(database, include_provenance?)` | `dict` | Portable, `ref`-keyed JSON export of entities/facts/property values (schema not included — export/import schema separately first) |
| `import_database_data(database, data)` | `None` | Import from `export_database_data`'s format; resolves CLASS/RELATION/PROPERTY **by name** against the target's current schema. **Not idempotent** — re-importing the same export duplicates every entity/fact/property value, unlike schema import |
| `get_metadata(database, node_id)` | `dict` | Metadata for one entity, fact, or property-value id: created/modified timestamps, `confidence` (a FACT's derived score, `None` on the other two), and `evidence`, one record per EXTRACTION; raises `KnowledgeError` for any other node type |
| `get_entity_classes(database, entity_id)` | `list[dict]` | CLASS nodes the entity is an instance of, via IS_INSTANCE_OF |
| `get_fact_parts(database, fact_id)` | `dict` | The fact's first HEAD / RELATION / TAIL nodes, as `{"head": ..., "relation": ..., "tail": ...}` with `None` for unwired parts |
| `get_property_values(database, owner_id)` | `list[dict]` | LITERAL values on an ENTITY or FACT with each value's PROPERTY, as `[{"literal": ..., "property": ...}]` |
| `is_fact_allowed(database, head_entity_id, relation_id, tail_entity_id, max_hops?)` | `dict` | Whether the relation's domain/range accept the two entities' classes, subclass-aware via ancestors; returns `{ok, reason, details}` with reasons `ALLOWED` \| `UNTYPED_ENTITY` \| `DOMAIN_RANGE_MISMATCH`. Direction-exact — `is_symmetric` is not consulted |
| `is_property_allowed(database, owner_id, property_id, max_hops?)` | `dict` | Whether the property is DEFINE_PROPERTY-owned by the entity's classes or their ancestors (entity owner), or by the fact's relation or its ancestors (fact owner); adds reasons `FACT_HAS_NO_RELATION` \| `NOT_DEFINED_FOR_OWNER` |
| `find_entity(database, name)` | `dict \| None` | First ENTITY with exactly this name — entity names are not unique, so this is a convenience, not identity resolution |
| `find_fact(database, head_entity_id, relation_id, tail_entity_id)` | `dict \| None` | First FACT matching the exact directed triple |
| `answer(database, question, model?, natural_language?, include_metadata?, on_tool_call?, on_tool_result?)` | `list[dict] \| str \| tuple[list[dict] \| str, dict]` | Answer a natural language question using an LLM agent over the instance graph; each dict is a flat `{entity, relation, value, provenance}`-shaped row (keys vary by question); returns prose when `natural_language=True`; returns a `(result, metadata)` tuple when `include_metadata=True` |
| `validate(database, statement, model?, natural_language?, include_metadata?, on_tool_call?, on_tool_result?)` | `ValidationResult \| str \| tuple[ValidationResult \| str, dict]` | Return supporting and counter evidence for a statement using an LLM agent; same `natural_language`/`include_metadata` variance as `answer` |
| `enrich(database, query, depth?, model?, on_tool_call?, on_tool_result?)` | `list[EnrichmentResult]` | Return related knowledge bearing on a query using an LLM agent; no `natural_language`/`include_metadata` variance |

`declare_*` and `add_<node>` create a node (idempotently by name for schema, never for instances); `attach_*` and `add_is_subclass_of` only add an edge between nodes that already exist, so a node's *additional* links come from them while its first ones come with creation — the verb table is in [08-coding-style.md](08-coding-style.md), the rule in [13-decision-log.md](13-decision-log.md) ADR-024.

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
| `add_source(database, source, name?, description?, node_id?, original?)` | `str` | Create a SOURCE node; `original` is a caller-supplied date (e.g. a document's publication date), distinct from the node's own `created` timestamp |
| `update_source(database, source_id, name?, description?, original?)` | `None` | Update a SOURCE node |
| `delete_source(database, source_id)` | `None` | Delete a SOURCE node |
| `add_extraction(database, text, name?, description?, node_id?, source_id?, confidence?, extraction_type?, element_id?)` | `str` | Create an EXTRACTION node; `source_id` wires EXTRACTED_FROM from it to that SOURCE, `element_id` wires EXTRACTED_FROM from that existing entity/fact/literal to it — each link independently, only when its id is given |

`add_extracted_from` is **not** a public `KBSource` method; it is a private `ProvenanceTracker` helper that `add_extraction(element_id=...)` and `attach_extraction` both call, and where a FACT's derived `confidence` is refreshed. To link an extraction to an item, pass `element_id` to `add_extraction` when creating it, or call `attach_extraction` for an extraction that already exists.
| `attach_extraction(database, element_id, extraction_id)` | `None` | Wire EXTRACTED_FROM from an ENTITY / FACT / LITERAL to an EXTRACTION that already exists — for an extraction created up front by `add_extraction` without an `element_id`, or re-wired on snapshot restore. Deduplicated and add-only. Not a way to share evidence: each item keeps its own EXTRACTION node (`10-data-model.md` §2.3) |

#### Change hook operations (delegate to `ChangeNotifier`)

| Method | Returns | Description |
|--------|---------|-------------|
| `add_change_hook(database, fn)` | `None` | Register a hook to be called on every instance write to `database` |
| `remove_change_hook(database, fn)` | `None` | Deregister a hook (no-op if not found) |

#### Ingest operations (delegate to `IngestionManager`)

| Method | Returns | Description |
|--------|---------|-------------|
| `assert_class(database, name, description?)` | `AssertResult` | Ingest path: reuse a class when one resolves, create it otherwise (trailing options shared by all `assert_*` — see the `IngestionManager` section) |
| `assert_relation_type(database, name, domain, range, description?, is_symmetric?)` | `AssertResult` | Missing domain/range classes created; an existing relation widened, not rejected |
| `assert_class_property(database, name, class_name, description?)` | `AssertResult` | Owning class created when missing |
| `assert_relation_property(database, name, relation_name, description?)` | `AssertResult` | Relation must already exist |
| `assert_subclass(database, child, parent)` | `AssertResult` | Missing classes created; edge idempotent |
| `assert_subrelation(database, child, parent)` | `AssertResult` | Both relations must already exist |
| `assert_entity(database, name, class_name?, description?)` | `AssertResult` | Resolves by name; new or classless entities get a judged class (`EVERYTHING` under `"none"`) |
| `assert_fact(database, head, relation, tail, name?, description?)` | `AssertResult` | Operands resolved or created; existing triples dedupe with new evidence; non-conformance widens |
| `assert_entity_property(database, entity, property, value, literal_type?)` | `AssertResult` | Equal values dedupe; new-property placement judged |
| `assert_fact_property(database, head, relation, tail, property, value, literal_type?)` | `AssertResult` | Fact identified structurally by its triple; created when missing |
| `ingest(database, payload)` | `IngestReport` | Ten-arm payload through the asserts in file order; per-row failures collect into the report |

#### Extraction (delegates to `ExtractionEngine`)

| Method | Returns | Description |
|--------|---------|-------------|
| `import_text(database, text, queries?, model?, source_id?, expand_schema?, schema_expansion_target?, schema_expansion_depth?, schema_expansion_mode?, schema_expansion_mcp_servers?, schema_expansion_dedup_mode?, on_tool_call?, on_tool_result?, max_turns?, related_data_k?)` | `None` | Extract entities, facts, and property values from free text using an LLM, against the schema as it currently stands — never declares new schema itself (a mention with no matching element is skipped). `expand_schema` (default `True`) runs `expand_schema()` first; the `schema_expansion_*` params mirror that method's `depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one. `related_data_k` (default `10`) surfaces that many existing-entity/fact search hits in the prompt as unconfirmed leads; `0` disables it — see ExtractionEngine and SchemaExpansionEngine below |

#### Sync convenience methods

Two additional synchronous methods exist for callers that can't `await`, both wrapping the async API internally via `asyncio.run()`:

| Method | Returns | Description |
|--------|---------|-------------|
| `execute_query(query, database?, collection?, optional_properties?)` | varies | Generic op-code dispatcher — `query["op"]` such as `"KB.DECLARE_CLASS"`, `"KB.ADD_ENTITY"`, `"KB.GET_SCHEMA"`, mirroring most async methods above 1:1; `GRAPH.*` ops forward to the underlying `GraphSource`. `get_metadata` is deliberately **not** exposed as an op — library method and MCP/agent tool only, by design (see [13-decision-log.md](13-decision-log.md) ADR-018) |
| `execute_query_nl(query, database?, ...)` | `list[dict]` | Sync convenience wrapper around `answer(..., natural_language=False)` |

---

## SchemaManager

Manages the typed vocabulary of the knowledge base. All declaration methods are idempotent: if an element with the same name already exists, its ID is returned without modification.

### Constructor

```python
SchemaManager(source, notifier=None, search_indexer=None)
```

**Parameters**

- `source` — the parent `KBSource` instance; used for all Graph Store access
- `notifier` — the `ChangeNotifier` instance; fires `class_declared`/`relation_declared`/`class_property_declared`/`relation_property_declared`/`class_subclass`/`relation_subclass`, the corresponding `*_updated`/`*_deleted` events, and `property_attached`/`relation_domain_attached`/`relation_range_attached` on a real attach
- `search_indexer` — forwarded straight through to the `SchemaExpansionEngine` this constructor creates internally (`self._expansion = SchemaExpansionEngine(source, self, search_indexer)`); backs `expand_schema`'s `search_schema` tool the same way it backs `KBSource.search_schema` itself

### Methods

#### `declare_class(database, name, description, class_id=None)`

Declares a CLASS node.

**Algorithm**

1. Canonicalize `name` to `UPPER_SNAKE_CASE` (see below)
2. Look up existing CLASS node by the canonicalized `name` in the Graph Store
3. If found: return existing ID
4. If not found: create CLASS node with the canonicalized `name`, `description`, optional `class_id`; return new ID

#### `declare_relation_type(database, name, description, is_symmetric=False, relation_id=None, *, domain, range)`

Declares a RELATION node. `domain` and `range` are required, keyword-only, and accept a class name or
list of class names — every relation must have at least one domain class and one range class; there is
no wildcard/universal relation.

**Algorithm**

1. Canonicalize `name` and every name in `domain`/`range` to `UPPER_SNAKE_CASE` (see below)
2. Raise `SchemaError` if `domain` or `range` is `None` or empty
3. Look up existing RELATION node by the canonicalized `name`
4. If found: return existing ID (DOMAIN/RANGE edges are not re-wired)
5. If not found: create RELATION node; resolve each canonicalized name in `domain` and `range` to a CLASS ID via name lookup (raising `SchemaError` if a name doesn't match an existing CLASS); wire DOMAIN and RANGE edges; return new ID

**Schema name canonicalization:** `declare_class`, `declare_relation_type`, `declare_class_property`, and `declare_relation_property` all canonicalize their `name` (and, for relations, every `domain`/`range` class name) to `UPPER_SNAKE_CASE` before the idempotency lookup and before storing — splitting camelCase at lower/digit→upper boundaries, then collapsing any run of spaces/hyphens/underscores into one `_`. `declare_class("Person", ...)` and `declare_class("PERSON", ...)` therefore return the same CLASS rather than creating two. This does not apply to ENTITY/FACT names or LITERAL values, which keep whatever casing the caller gives them — there's no canonical casing for a proper noun. The exact-match `find_class`/`find_relation_type`/`find_property` lookups are unaffected and stay case-sensitive; only the `declare_*` write path canonicalizes.

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

Only `operation="merge"` is implemented — any other value, including `"split"`, raises `SchemaError(f"consolidate_schema operation '{operation}' is not supported")`. There is no split capability today despite the general-sounding name; `predicate` is accepted in the signature but never read by the current implementation.

**Parameters**

- `operation` — must be `"merge"`
- `source_names` — names of the elements to merge away; all must resolve to the same kind of schema node (all CLASS, all RELATION, or all PROPERTY) or `SchemaError` is raised
- `target_name` — name of the element to merge into; created automatically if it doesn't already exist
- `predicate` — accepted for signature compatibility; not used

**Algorithm — merge**

Merge is additive, not a rewrite-and-remove operation:

1. Resolve each name in `source_names` to a schema element ID (same label required across all of them)
2. Look up the target element by `target_name`, or create it if absent
3. For each instance whose primary instance edge (`IS_INSTANCE_OF`/`REPRESENTS`/`IS_VALUE_OF`) points at a source element, **add a new** instance edge from that same instance to the target element

That's all it does. It does **not** remove the old instance edge, does **not** delete the source schema nodes, and does **not** touch `DEFINE_PROPERTY`/`DOMAIN`/`RANGE`/`IS_SUBCLASS_OF` edges on the sources. `fetch_database_schema` after a merge still lists the source elements. An instance that was merged now has instance edges to *both* its original class and the target class; code that assumes one class per entity (like `get_metadata`'s `class_name`) reports whichever edge it finds first — the original one, since it was created first. If you need the old edges gone or the source nodes removed, do that explicitly afterward with `delete_class`/`delete_relation`/`delete_property`.

#### `attach_property(database, target_id, property_id)`

Wires a `DEFINE_PROPERTY` edge from an existing CLASS or RELATION (`target_id`) to an existing PROPERTY (`property_id`). Idempotent — calling it again with the same pair is a no-op, not a duplicate edge. Raises `SchemaError` if `target_id` isn't a CLASS/RELATION or `property_id` isn't a PROPERTY. Exists so a property discovered via `search_schema` on one class/relation can be reused on another instead of `declare_class_property`/`declare_relation_property` creating a same-meaning duplicate under a new name — both `SchemaExpansionEngine` (below) and `kb schema class/relation property attach` use it for exactly this.

#### `expand_schema(database, text, *, depth=1, reference="llm", mcp_servers=None, dedup_mode="search", model="gpt-4o", on_tool_call=None, on_tool_result=None, max_turns=25)`

Thin delegation to `SchemaExpansionEngine.expand_schema` (below) — `SchemaManager` owns one `SchemaExpansionEngine` instance, constructed with a reference back to itself so the engine's tool dispatch can call `declare_class`/`add_is_subclass_of`/`attach_property`/etc. directly.

---

## KnowledgeManager

Populates the knowledge base with instance data and delegates natural language queries to the `QueryEngine`. Delegates provenance creation to `ProvenanceTracker` and change notification to `ChangeNotifier` after each write.

### Constructor

```python
KnowledgeManager(source, provenance_tracker, change_notifier, schema_manager, search_indexer=None)
```

`search_indexer` is `None` unless `properties["search"]["mode"]` was `"bm25"`/`"semantic"` (or a live `vector_source` was given directly) when the parent `KBSource` was constructed.

### Methods

#### `add_entity(database, name, description=None, entity_id=None, class_id=None, text=None, source_id=None)`

Creates an ENTITY node.

**Algorithm**

1. Create ENTITY node with `name`, `description`, optional `entity_id`
2. If `class_id` provided: wire IS_INSTANCE_OF edge to the CLASS node
3. If `text` provided: call `ProvenanceTracker.add_extraction(database, text, source_id=source_id, element_id=entity_id, confidence=confidence, extraction_type=extraction_type)` — which, for a FACT, also refreshes the fact's derived `confidence`
4. Call `ChangeNotifier.fire({"type": "entity_added", ...})`
5. Return entity ID

`add_fact`/`add_entity_property_value`/`add_fact_property_value` follow the same shape. On all four, `confidence` and `extraction_type` are recorded on the EXTRACTION that `text` creates, so passing `confidence` without `text` raises `KnowledgeError` rather than dropping the score silently. Attaching more evidence to an entity/fact/literal that already exists (without recreating it) goes through `add_entity_extraction`/`add_fact_extraction`/`add_entity_property_value_extraction`/`add_fact_property_value_extraction` instead — each just calls `ProvenanceTracker.add_extraction(element_id=…)` again against the existing id, requiring the target to already exist.

#### `find_data(database, query, type_filter=None)`

Filters ENTITY and FACT nodes by a case-insensitive substring match of `query` against their own name/description plus related names (class name for entities; head, relation, and tail names for facts), with no relevance ranking. This is the fallback used by `KBSource.search_data` when the database was not registered with search enabled; when it is enabled, `KBSource.search_data` calls `KBSearchIndexer.search_data` instead, which supports BM25 ranking and semantic mode. Returns a list of `DataSearchResult`.

#### `answer(database, question, model="gpt-4o", natural_language=False, include_metadata=False, on_tool_call=None, on_tool_result=None)`

Delegates to `QueryEngine.answer(..., include_metadata=...)` for the structured result, then applies `natural_language` handling **at this layer**, not inside `QueryEngine` — `QueryEngine` itself has no `natural_language` parameter. Returns a list of plain dicts (not `AnswerResult` — see note below); a prose string when `natural_language=True`; or a `(result, metadata)` tuple when `include_metadata=True` (prose composition and the metadata tuple compose — with both flags set, the prose has a metadata section appended and `metadata` is still returned as the second element). `on_tool_call`/`on_tool_result`, if given, are invoked with `(tool_name, arguments)`/`(tool_name, arguments, result)` for each tool call the agent makes.

#### `validate(database, statement, model="gpt-4o", natural_language=False, include_metadata=False, on_tool_call=None, on_tool_result=None)`

Delegates to `QueryEngine.validate`, with the same `natural_language`/`include_metadata` layering as `answer` above. Returns a `ValidationResult`, a prose string, or a `(result, metadata)` tuple.

#### `enrich(database, query, depth=2, model="gpt-4o", on_tool_call=None, on_tool_result=None)`

Delegates to `QueryEngine.enrich`. Returns a list of `EnrichmentResult` objects. No `natural_language`/`include_metadata` variance — unlike `answer`/`validate`, `enrich` doesn't take either parameter.

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

#### `answer(database, question, model="gpt-4o", include_metadata=False, on_tool_call=None, on_tool_result=None)`

Fetches the schema, constructs a system prompt, and runs the agent loop. The agent calls graph tools to find relevant entities, facts, and property values (plus `get_metadata` when `include_metadata=True`), then calls `return_result` with a list of `{entity, relation, value, provenance}` items. That payload is returned as-is — a plain `list[dict]`, not wrapped in `AnswerResult`. `QueryEngine.answer` has no `natural_language` parameter — prose composition happens one layer up, in `KnowledgeManager.answer`.

> **Note:** `AnswerResult` is defined in `types.py` and re-exported from the package, but `answer()` does not currently construct or return it — unlike `validate()` (`ValidationResult`) and `enrich()` (`EnrichmentResult`), which do wrap their results in their respective dataclasses. Treat `AnswerResult` as unused until this is reconciled in code.

**Example**

Input: `"Where did Alice work?"`

Output:
```python
[
    {"entity": "Alice",       "relation": None,        "value": None, "provenance": "Alice is a software engineer."},
    {"entity": "Alice → IBM", "relation": "WORKED_AT",  "value": None, "provenance": "Alice joined IBM in June 1998."},
]
```

#### `validate(database, statement, model="gpt-4o", include_metadata=False, on_tool_call=None, on_tool_result=None)`

Fetches the schema, constructs a system prompt directing the agent to find supporting and counter evidence, and runs the agent loop. The agent calls `return_result` with `{supporting: [...], counter: [...]}` where each item is `{fact, head, tail, relation}`. Returns a `ValidationResult` — like `answer`, no `natural_language` parameter at this layer; that's handled by `KnowledgeManager.validate`.

**Example**

Input: `"Alice worked at IBM."`

Output:
```python
ValidationResult(
    supporting=[{"fact": "Alice_WORKED_AT_IBM", "head": "Alice", "tail": "IBM", "relation": "WORKED_AT"}],
    counter=[]
)
```

#### `enrich(database, query, depth=2, model="gpt-4o", on_tool_call=None, on_tool_result=None)`

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
| `get_metadata` | Created/modified timestamps, a FACT's derived confidence, and per-extraction evidence for one entity/fact/property value — only offered when `include_metadata=True` |
| `return_result` | Terminal tool — delivers the structured payload and ends the loop |

---

## ProvenanceTracker

Records the origin of each piece of knowledge by creating and wiring EXTRACTION and SOURCE nodes, and keeps a FACT's derived `confidence` current whenever an item-to-EXTRACTION edge is written.

### Constructor

```python
ProvenanceTracker(source, notifier=None)
```

**Parameters**

- `source` — the parent `KBSource` instance; used for all Graph Store access
- `notifier` — the `ChangeNotifier` instance; fires `extraction_attached`, the tracker's only event. The creating writes (`add_source`, `add_extraction`) stay silent, so a change-hook listener sees provenance only when an existing extraction is wired to an item

### Methods

#### `add_source(database, source, name=None, description=None, node_id=None, original=None)`

Creates a SOURCE node. `name` defaults to `source` if omitted. `original` is a caller-supplied date (e.g. a document's publication date), stored separately from the node's own `created` timestamp. Returns the SOURCE node ID. Not idempotent — every call creates a new SOURCE node, even with identical arguments.

#### `update_source(database, source_id, name=None, description=None, original=None)`

Updates an existing SOURCE node's fields.

#### `delete_source(database, source_id)`

Deletes a SOURCE node.

#### `add_extraction(database, text, name=None, description=None, node_id=None, source_id=None, confidence=None, extraction_type="text_span", element_id=None)`

Creates an EXTRACTION node for `text` and, optionally, links it in the same call — the create-and-link counterpart of `attach_extraction` below. Both links are independent and each is wired only when its id is given: `source_id` adds an EXTRACTED_FROM edge from the new EXTRACTION to that SOURCE; `element_id` adds an EXTRACTED_FROM edge from that existing entity/fact/literal to the new EXTRACTION. With neither, the node is created detached, for `attach_extraction` to wire later. Returns the EXTRACTION node ID. `confidence` scores the assertion in `[0.0, 1.0]` (`None` = unstated, which is not the same as `0.0`) — a value outside the range raises `ProvenanceError`. `extraction_type` (`"text_span"` default \| `"external_record"` \| `"assertion"`) records the form of the evidence. When `element_id` names a FACT, its derived `confidence` is recomputed as part of the call. One node per call: `KnowledgeManager` routes every `text=` shortcut and the `add_*_extraction` family through it, so repeated calls for the same element accumulate EXTRACTED_FROM edges rather than replacing the earlier one, and an item can carry evidence from several sources at once.

#### `attach_extraction(database, element_id, extraction_id)`

Wires an `EXTRACTED_FROM` edge from an existing ENTITY, FACT, or LITERAL (`element_id`) to an existing EXTRACTION (`extraction_id`). Idempotent — calling it again with the same pair is a no-op, not a duplicate edge. Raises `ProvenanceError` if `element_id` isn't an ENTITY/FACT/LITERAL or `extraction_id` isn't an EXTRACTION. Exists so an extraction created up front — by `add_extraction` without an `element_id`, or rebuilt when a snapshot is restored — can be wired to its item afterwards; it does not pool evidence across items, since each item still gets its own EXTRACTION node. When `element_id` names a FACT, its derived `confidence` is recomputed as part of the call: this method and `add_extraction(element_id=…)` both wire the edge through the private `add_extracted_from`, which calls `recompute_fact_confidence`, so a FACT's derived `confidence` refreshes no matter which path attached the evidence.

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
| `class_declared` | `name`, `description` |
| `relation_declared` | `name`, `description`, `is_symmetric`, `domain`, `range` |
| `class_property_declared` | `name`, `description`, `class_id`, `class_name` |
| `relation_property_declared` | `name`, `description`, `relation_id`, `relation_name` |
| `class_subclass` / `relation_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |
| `class_updated` / `relation_updated` / `property_updated` | — (`id` only) |
| `class_deleted` / `relation_deleted` / `property_deleted` | — (`id` only) |
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `entity_name`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `property_id`, `property_name`, `value`, `head_entity_name`, `tail_entity_name`, `relation_name` |
| `entity_updated` / `fact_updated` | — (`id` only) |
| `entity_property_updated` / `fact_property_updated` | — (`id` only) |
| `entity_deleted` / `fact_deleted` | — (`id` only) |
| `entity_property_deleted` / `fact_property_deleted` | — (`id` only) |

All events also carry `type`, `database`, and `id`. Schema events (`class_declared` through `relation_subclass`, plus their `*_updated`/`*_deleted` counterparts) are fired by `SchemaManager`, not `KnowledgeManager` — `ChangeNotifier` is shared infrastructure used by both layers, not scoped to one. Schema declaration events fire only on first creation; idempotent repeats of `declare_*` don't re-fire. Note also: `ProvenanceTracker` fires only one event, `extraction_attached`, from `attach_extraction`; its other methods (`add_source`, `add_extraction`, `update_source`, `delete_source`) fire no change events at all.

---

## NodeResolver

Label-scoped name resolution for the ingest path: answers "does something that may *mean* this name exist?", unlike the exact `find_*` lookups, which answer "does exactly this name exist?" and stay frozen. Returns ranked candidates and never picks a winner — callers own the pick-or-raise policy.

### Constructor

```python
NodeResolver(source, search_indexer=None)
```

### Methods

#### `resolve_class(database, name, k=5)` / `resolve_relation(database, name, k=5)` / `resolve_property(database, name, k=5)`

Return up to `k` ranked `ResolutionCandidate`s for one label: exact name matches first, then normalized matches (equal after casefold, whitespace collapse, punctuation strip, NFKC + accent fold — deliberately no legal-suffix dropping), then search hits via the database's index when one is configured (semantic when available, else keyword; only semantic hits carry scores), falling back to an unranked name/description substring scan. A node is reported once, under its strongest match.

#### `resolve_entity(database, name, class_id=None, k=5)`

Same tiers over ENTITY nodes. `class_id` is an optional filter (applied before the `k` cap) restricting candidates to instances of that class — a convenience for direct callers; the assert path does not use it, since class expectations act as a confidence signal there, never a search filter.

---

## IngestionManager

The ingest path: resolve-or-create over operands given by name. Every assert runs in two phases — phase 1 resolves all operands read-only and runs any needed judges, so any failure (ambiguity, a judge crash, a missing API key) raises before anything is written; phase 2 writes through the existing `declare_*`/`add_*`/`attach_*` operations, so change notification and search indexing fire exactly as for direct writes.

### Constructor

```python
IngestionManager(source, schema_manager, knowledge_manager, provenance, resolver)
```

### Resolution policy

One identity-certain candidate (`exact`, `alias`, `structural`) auto-resolves; two or more raise `IngestionError` with the candidates attached. With none, a single identity-uncertain candidate (`normalized`, `search`) auto-resolves — for entities, only when its class closure does not conflict with an expected class — and two or more raise. Judges run under `schema_creation_mode="llm"` (the default): class naming for untyped entities, property placement over an entity's classes and ancestors, and description generation for created elements. Under `"none"` no judges run: descriptions stay empty, untyped entities attach to the `EVERYTHING` catch-all class, and new properties land on the entity's direct classes. Every created node carries an EXTRACTION recording the serialized assertion (e.g. `"Alice WORKED_AT IBM"`), with the caller's `source_id`/`confidence`/`extraction_type` applied.

### Methods

All eleven take trailing `source_id=None, confidence=None, extraction_type="assertion", schema_creation_mode="llm", model="gpt-4o"` keyword options, elided below. `AssertResult` reports the subject (`node_id`, `created`, `match_type`, `extraction_id`) plus the full cascade (`created_nodes`) and any `widened_relations`.

#### `assert_class(database, name, description=None)`

Reuses a class when one resolves; creates it otherwise (judge-generated description when omitted).

#### `assert_relation_type(database, name, domain, range, description=None, is_symmetric=False)`

Resolves the relation and every domain/range class, creating missing classes. An existing relation is widened (`attach_relation_domain`/`attach_relation_range`) to cover new domain/range names instead of being rejected; `is_symmetric` applies only on creation.

#### `assert_class_property(database, name, class_name, description=None)` / `assert_relation_property(database, name, relation_name, description=None)`

Owner-scoped: an exact-named property already on the owner is reused. A missing class owner is created; a missing relation owner raises `IngestionError`, since a relation cannot be created without domain and range.

#### `assert_subclass(database, child, parent)` / `assert_subrelation(database, child, parent)`

Wire `IS_SUBCLASS_OF`, idempotently. Missing classes are created; missing relations raise. A child and parent resolving to the same node raise rather than writing a self-loop. `match_type` is always `None` for these two; `created` describes the edge.

#### `assert_entity(database, name, class_name=None, description=None)`

Resolves by name. A certain match with a class mismatch attaches a second `IS_INSTANCE_OF` edge (multi-class, no hierarchy inferred); an uncertain match with a class conflict raises. New or classless entities get a class via judge or `EVERYTHING`.

#### `assert_fact(database, head, relation, tail, name=None, description=None)`

Resolves or creates all three operands (a fresh relation takes its domain/range from the operands' classes). An existing `(head, relation, tail)` triple dedupes structurally: a new EXTRACTION is attached and the fact's derived confidence recomputed. A non-conforming existing relation is widened via `is_fact_allowed`'s verdict.

#### `assert_entity_property(database, entity, property, value, literal_type=None)` / `assert_fact_property(database, head, relation, tail, property, value, literal_type=None)`

Assert the owner first (entity by name; fact structurally by its triple, created when missing), then the property (inherited properties found through the class closure are reused), then the value: an equal value on the same owner/property dedupes structurally, a different value creates a second literal.

#### `ingest(database, payload)`

Runs a ten-arm payload (`classes`, `relations`, `subclasses`, `subrelations`, `class_properties`, `relation_properties`, `entities`, `facts`, `entity_properties`, `fact_properties`) through the per-item asserts in the payload's own order. Rows are assert kwargs and may override `confidence`/`extraction_type`; an unknown arm raises before any row runs; per-row failures (`KBError`s with candidates, malformed-row `TypeError`s) collect into the `IngestReport` (`written`/`deduped`/`skipped`/`failures`/`created`/`widened`) while later rows keep running.

---

## KBSearchIndexer

Maintains a keyword (BM25) or embedding-based (semantic) search index over schema elements and instance data. Constructed only when the parent `KBSource` is created with `properties["search"]["mode"]` in `("bm25", "semantic")` (or a live `vector_source` given directly); otherwise `KBSource.search_schema`/`search_data` fall back to `SchemaManager.find_schema`/`KnowledgeManager.find_data`.

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

Global change-hook entry point, registered by `KBSource` at construction time. Dispatches added/attached/updated/deleted events for classes, relations, properties, entities, and facts to the appropriate indexing helper, keeping the index incrementally in sync without a full rebuild.

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

Bridges free text and the structured knowledge base using an LLM agent loop. Unlike an earlier
revision of this engine, it never declares schema — its tool set is fixed (no `schema_discovery_mode`-
style conditional tool filtering) and contains no `declare_*` tool at all; growing the schema from
the given text is `SchemaExpansionEngine`'s job (see below). A mention in the text with no matching
CLASS/RELATION/PROPERTY is simply skipped.

### Constructor

```python
ExtractionEngine(source, schema_manager, knowledge_manager, provenance_tracker, search_indexer=None)
```

`search_indexer` is `None` unless `properties["search"]["mode"]` was `"bm25"`/`"semantic"` (or a live `vector_source` was given directly) — it backs the agent's `search_schema` tool.

### Methods

#### `import_text(database, text, queries=None, model="gpt-4o", source_id=None, on_tool_call=None, on_tool_result=None, max_turns=25, related_data_k=10)`

Extracts entities, facts, and property values from `text` into `database`, against the schema as it
currently stands. Called directly by `ExtractionEngine`'s consumer — `KBSource.import_text` (see the
`expand_schema` note below for how the two-phase call is orchestrated).

**Parameters**

- `database` — target database
- `text` — the free-text passage to process
- `queries` — optional list of natural language questions guiding the extraction focus
- `model` — OpenAI model to use for the agent loop
- `source_id` — ID of an existing SOURCE node; passed through to each instance creation call
- `on_tool_call` / `on_tool_result` — optional callbacks invoked with `(tool_name, arguments)` / `(tool_name, arguments, result)` for each tool call the agent makes
- `max_turns` (default `25`) — caps the number of agent turns; hitting the limit stops the loop silently rather than raising
- `related_data_k` (default `10`) — number of existing ENTITY/FACT hits from `KBSearchIndexer.search_data(database, text, k=related_data_k)` to surface in the system prompt as "potentially related existing data"; `0` disables the lookup. A no-op when the database has no search indexer configured (`properties["search"]["mode"]` is `"none"`, the default) — the prompt says so explicitly rather than silently omitting the section

**Algorithm**

1. Call `SchemaManager.fetch_database_schema(database)` and serialize it as context for the LLM
2. When a search indexer is configured and `related_data_k > 0`: call `KBSearchIndexer.search_data(database, text, k=related_data_k)` and render the hits (entity name + class; fact head/relation/tail) as additional context
3. Fixed tool set: schema inspection (`get_schema`, `find_class`, `find_relation_type`, `find_entity`, `find_fact`, `search_schema`), instance creation (`add_entity`, `add_fact`, `add_entity_property_value`, `add_fact_property_value`), and `link_extraction` (attach evidence to an existing item instead of duplicating it) — no schema-declaration tool is offered
4. Submit `text`, `queries`, schema context, and the related-data context to the LLM with the tool set — the system prompt instructs the agent to resolve each mention via `find_entity`/`find_fact`/`search_schema` against the existing schema, treating the related-data hits as unconfirmed leads to verify (not to reuse blindly), and to skip anything with no match rather than invent new schema
5. Loop (up to `max_turns` turns):
   - Receive tool calls from the LLM
   - For instance creation calls: dispatch to `KnowledgeManager`, passing `text=<verbatim span>` and `source_id=source_id`
   - For `link_extraction` calls (item already exists): dispatch to `ProvenanceTracker.add_extraction(element_id=…)`, attaching the new span as further evidence rather than creating a duplicate
   - Return tool results to the LLM
6. Exit when the LLM returns without further tool calls, or `max_turns` is reached

**Where the schema expansion pass fits in:** `KBSource.import_text` (not `ExtractionEngine` itself) owns the two-phase orchestration — with `expand_schema=True` (the default), it calls `SchemaManager.expand_schema(...)` first, using `text`/`queries` (filtered by `schema_expansion_target`) as that call's grounding text, before calling `ExtractionEngine.import_text` above. Pass `expand_schema=False` to skip straight to extraction against whatever schema already exists.

---

## SchemaExpansionEngine

Grows the schema — declares `CLASS`/`RELATION`/`PROPERTY` elements grounded by a text passage — using
an LLM agent loop that calls back into `SchemaManager`. Never creates `ENTITY`/`FACT`/`LITERAL` nodes;
populating them from the given text is `ExtractionEngine`'s job (above). Owned by `SchemaManager`, not
exposed directly — callers reach it only through `SchemaManager.expand_schema`/`KBSource.expand_schema`.

### Constructor

```python
SchemaExpansionEngine(source, schema_manager, search_indexer=None)
```

### Methods

#### `expand_schema(database, text, *, depth=1, reference="llm", mcp_servers=None, dedup_mode="search", model="gpt-4o", on_tool_call=None, on_tool_result=None, max_turns=25)`

**Parameters**

- `database` — target database
- `text` — the free-text passage to ground new schema elements in
- `depth` (default `1`) — how many hierarchy levels beyond what's directly evidenced in `text` to also propose and link via `add_is_subclass_of` (e.g. a mentioned "poodle" also suggests a `DOG` parent CLASS at `depth=1`; `depth=0` sticks to only what's directly evidenced)
- `reference` — `"llm"` (default) proposes schema freely from the model's own knowledge; `"mcp"` additionally connects each server in `mcp_servers` and offers the model its tools too (e.g. WordNet hypernym/synonym lookups via `platform/wordnet-mcp`), to ground new names and hierarchy placement — raises `SchemaError` if `reference="mcp"` and `mcp_servers` is empty
- `mcp_servers` — list of `{"transport": "stdio", "command", "args"?, "env"?}` or `{"transport": "sse"|"streamable_http", "url", "headers"?}` dicts; each is connected via an `AsyncExitStack` for the duration of the call, and its tools are exposed to the model name-prefixed `mcp__<server>__<tool>`
- `dedup_mode` — how the engine checks for an existing schema element before declaring a new one; applies both to the `search_schema` tool the model can call, and — for `declare_class`/`declare_relation_type` specifically — as an automatic check run before the node is created, regardless of whether the model called `search_schema` itself (closes the gap where two differently-worded near-duplicates, e.g. `WORKED_AT`/`EMPLOYED_AT`, would otherwise both get declared, since the underlying `declare_*` calls only dedup on an exact name match):
  - `"search"` (default) — the database's configured search backend (keyword substring match, or semantic/embedding search if registered)
  - `"llm"` — asks the model to judge whether an existing element means the same thing as the proposed one, even under different wording, via a separate tool-forced completion call (`_judge_similar_schema`) — more reliable for synonyms than keyword/semantic search over short names, at the cost of one extra model call per check
  - `"none"` — the `search_schema` tool isn't offered at all, and the automatic pre-create check is skipped
- `model` — OpenAI model to use for the agent loop (and for `dedup_mode="llm"`'s judge call)
- `on_tool_call` / `on_tool_result` — optional callbacks invoked with `(tool_name, arguments)` / `(tool_name, arguments, result)` for each tool call the agent makes (including MCP tool calls)
- `max_turns` (default `25`) — caps the number of agent turns; hitting the limit stops the loop silently

**Tool set:** `get_schema`, `search_schema` (unless `dedup_mode="none"`), `declare_class`, `declare_class_property`, `declare_relation_type`, `declare_relation_property`, `add_subclass_of` (dispatches to `SchemaManager.add_is_subclass_of`), `attach_property` — plus, when `reference="mcp"`, every tool from each connected server.

**System prompt contract (`_SYSTEM_PROMPT_TEMPLATE`)** — the model is instructed to:

- Never declare a CLASS/RELATION named after a specific individual mentioned in the text (e.g. "Alice" or "United Nations" are entities, not classes named `ALICE`/`UNITED_NATIONS`) — only the general kind (`PERSON`, `ORGANIZATION`) the individual belongs to. This is the CLASS-vs-entity distinction: a CLASS is a *kind* that can have many members; a specific named referent is created later, as an entity, by the extraction pass.
- Treat an is-a/type-of/kind-of/subclass-of relationship as a *structural* link, wired only via `add_subclass_of` — never a declared RELATION. `declare_relation_type` rejects a name in `_TAXONOMIC_RELATION_NAMES` (`IS_A`, `TYPE_OF`, `KIND_OF`, `SUBCLASS_OF`, `SUBTYPE_OF`, `SUBTYPE`, `INSTANCE_OF`, and their `IS_`-prefixed variants) with an error pointing the model at `add_subclass_of` instead.
- Call `search_schema` before every `declare_*` call to avoid a near-duplicate (reusing an existing element under different wording, e.g. `JOIN_DATE`/`START_DATE`), and call `attach_property` instead of `declare_class_property`/`declare_relation_property` when `search_schema` turns up an existing PROPERTY that already covers the concept on a *different* class/relation.
- After declaring a new CLASS or RELATION, `search_schema` again for a plausible existing parent/child of the same kind and call `add_subclass_of` if one clearly fits — and, beyond what's directly evidenced, also propose up to `depth` level(s) of generalization/specialization this way.
- After declaring a new CLASS, check whether the text evidences a RELATION connecting it to another CLASS (existing or new) — a CLASS left with no RELATION connecting it to anything is treated as a likely sign one was missed, not that none is needed.
- Use `UPPER_SNAKE_CASE` for CLASS/RELATION/PROPERTY names.

**Algorithm**

1. Call `SchemaManager.fetch_database_schema(database)` and serialize it (simplified form) into the system prompt, along with `depth` and the MCP note (if `reference="mcp"`)
2. If `reference="mcp"`: connect every entry in `mcp_servers` (stdio/sse/streamable_http) inside an `AsyncExitStack`, collecting each server's tools name-prefixed `mcp__<server>__<tool>` and a dispatch table back to the right `ClientSession`
3. Loop (up to `max_turns` turns):
   - Receive tool calls from the LLM
   - `search_schema`: dispatch to `KBSearchIndexer.search_schema` (if configured) or `SchemaManager.find_schema`, unless `dedup_mode="llm"` — then dispatch to the LLM-judge path instead
   - `declare_class`/`declare_relation_type`: run the automatic dedup check first (`_auto_dedup_check`, per `dedup_mode`); if a close-enough element is already found, return its id instead of creating a new one; `declare_relation_type` additionally rejects a taxonomic name outright before this check
   - `declare_class_property`/`declare_relation_property`/`add_subclass_of`/`attach_property`: dispatch straight to the matching `SchemaManager` method
   - an `mcp__`-prefixed call: dispatch to the connected MCP session instead of local tool dispatch
   - Return tool results to the LLM
4. Exit when the LLM returns without further tool calls, or `max_turns` is reached
