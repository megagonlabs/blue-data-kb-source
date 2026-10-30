# blue-kb — Library

> External dependencies: `blue-data` provides `DataSource` (base class, incl. the `instantiate()` dotted-path factory), `GraphSource`, `MemoryGraphSource`, `FileGraphSource`, `NEO4JSource`, `VectorSource` and its backends, `DataSchema`, and related base classes.

---

## KBSource(GraphSource)

Entry point for all blue-kb operations. Abstract base class — must be subclassed together with a concrete `GraphSource` backend using Python MRO. Three such subclasses ship with the library already: `MemoryKBSource` (`memory_kb_source.py`), `FileKBSource` (`file_kb_source.py`), and `NEO4JKBSource` (`neo4j/neo4j_kb_source.py`, kept out of the package's top-level `__init__.py` so importing it — not `import blue_data_kb_source` itself — is what requires the `neo4j` driver). Each overrides `_initialize_connection_properties` to report a `kb/memory`/`kb/file`/`kb/neo4j` connection protocol instead of inheriting the mixed-in `GraphSource` backend's own `graph/*` protocol (see ADR-004 in [13-decision-log.md](13-decision-log.md)). Instantiates and coordinates the `SchemaManager` (which internally owns a `SchemaExpansionEngine`), `KnowledgeManager` (which internally owns a `QueryEngine`), `ProvenanceTracker`, `ChangeNotifier`, `ExtractionEngine`, and — when search is enabled — `KBSearchIndexer`. Every method except the `GraphSource` primitives it inherits (e.g. `create_database`) is `async` and must be awaited.

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
| `declare_relation_type(database, name, description, is_symmetric?, relation_id?, *, domain, range)` | `str` | Declare a RELATION node; `domain`/`range` are required (keyword-only); wires DOMAIN/RANGE edges inline; `name` and every `domain`/`range` class name are canonicalized to `UPPER_SNAKE_CASE`. `IS_SUBCLASS_OF`/`IS_INSTANCE_OF` are reserved for the KB's own structural edges and raise `SchemaError` |
| `declare_class_property(database, name, description, property_id?, class_id?, literal_type?)` | `str` | Declare a PROPERTY node; wires DEFINE_PROPERTY from a class; `name` is canonicalized to `UPPER_SNAKE_CASE` |
| `declare_relation_property(database, name, description, property_id?, relation_id?, literal_type?)` | `str` | Declare a PROPERTY node; wires DEFINE_PROPERTY from a relation; `name` is canonicalized to `UPPER_SNAKE_CASE` |
| `update_class(database, class_id, name?, description?)` | `None` | Update a CLASS node's name/description; not idempotent. A new name follows `declare_*`'s rules: canonicalized to UPPER_SNAKE_CASE, and `SchemaError` if it's taken by another node of the same kind (for a property, by another property of the same owner) or, for a relation, reserved (`IS_SUBCLASS_OF`, `IS_INSTANCE_OF`) |
| `update_relation(database, relation_id, name?, description?)` | `None` | Update a RELATION node |
| `update_property(database, property_id, name?, description?, literal_type?)` | `None` | Update a PROPERTY node; `literal_type` (NUMBER/DATE/BOOLEAN/STRING) is the type of value it holds, enforced on every value written to it (ADR-035) |
| `delete_class(database, class_id)` | `None` | Delete a CLASS node |
| `delete_relation(database, relation_id)` | `None` | Delete a RELATION node |
| `delete_property(database, property_id)` | `None` | Delete a PROPERTY node |
| `add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire IS_SUBCLASS_OF edge; idempotent (a repeated call adds nothing); refuses self-loops and cycles with `SchemaError`. The only way to express an is-a/type-of/kind-of hierarchy between two CLASSes or two RELATIONs — never a declared RELATION |
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
| `fetch_database_schema(database)` | `dict` | The schema of the current graph as `DataSchema.to_json()`'s dict; synchronous, per the `DataSource` contract (ADR-036) |
| `export_database_schema(database)` | `dict` | The same dict `fetch_database_schema(database)` returns, as a coroutine — portable JSON form |
| `fetch_meta_schema()` | `DataSchema` | Return the fixed blue-kb meta-schema |
| `import_database_schema(database, schema_json)` | `None` | Replay a `DataSchema.to_json()` dict via idempotent `declare_*` calls — safe to import into a database that already has same-named elements; `IS_SUBCLASS_OF` entries (class and relation hierarchy) are re-wired with `add_is_subclass_of` after everything else is declared, so export → import → export round-trips unchanged |
| `consolidate_schema(database, operation, source_names, target_name, predicate?)` | `None` | Only `operation="merge"` is implemented; any other value raises `SchemaError`. `predicate` is accepted but unused. Merge rewires every reference onto the target, deletes the source elements, and fires change events so the search index follows. Refused with `SchemaError`, before anything is written, if the merged hierarchy would contain a cycle or a relation target name is reserved |
| `expand_schema(database, text, depth?, reference?, mcp_servers?, dedup_mode?, model?, on_tool_call?, on_tool_result?, max_turns?, timeout?)` | `None` | LLM agent that grows the schema (CLASS/RELATION/PROPERTY only) grounded by `text` — never touches entities/facts/property values. See SchemaExpansionEngine below |

`declare_relation_type` wires the initial DOMAIN/RANGE edges inline (`domain`/`range` are required there), and re-declaring never re-wires them — post-declaration widening is what `attach_relation_domain`/`attach_relation_range` are for.

#### Knowledge operations (delegate to `KnowledgeManager`)

| Method | Returns | Description |
|--------|---------|-------------|
| `add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create an ENTITY node; never idempotent — always creates a new node |
| `add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a FACT node; `name` auto-synthesizes as `{head}_{relation}_{tail}` if omitted. When head/tail/relation are all given, checks conformance via `is_fact_allowed` and raises `KnowledgeError` if it isn't `ALLOWED` — unlike `assert_fact`, this never widens the relation's domain/range |
| `add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a LITERAL and wire it to an entity. When both are given, raises `KnowledgeError` unless `is_property_allowed` reports the property is defined for the entity's class (or an ancestor) |
| `add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a LITERAL and wire it to a fact. When both are given, raises `KnowledgeError` unless `is_property_allowed` reports the property is defined for the fact's relation (or an ancestor) |
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
| `import_database_data(database, data)` | `None` | Import from `export_database_data`'s format; resolves CLASS/RELATION/PROPERTY **by name** against the target's current schema. **Not idempotent** — re-importing the same export duplicates every entity/fact/property value, unlike schema import. All or nothing: if any item fails (e.g. a fact that doesn't conform to domain/range), everything the call created — entities, their facts and values, and provenance — is removed before the error propagates |
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
| `import_text(database, text, queries?, model?, source_id?, expand_schema?, schema_expansion_target?, schema_expansion_depth?, schema_expansion_mode?, schema_expansion_mcp_servers?, schema_expansion_dedup_mode?, schema_expansion_timeout?, on_tool_call?, on_tool_result?, max_turns?, related_data_k?, effort?, max_concurrency?, on_stage?)` | `None` | Runs a staged pipeline (entities → *entity gap recovery* → relations → fact properties → entity properties, each its own agentic pass) to extract from free text using an LLM, against the schema as it currently stands — never declares new schema itself (a mention with no matching element is skipped — never coerced into the nearest class or relation of a different kind; a descriptive phrase for an entity already found, like "a staff engineer", types that entity via `attach_entity_class` rather than becoming an entity of its own). The entity stage sweeps the schema class by class from a rendered checklist rather than scanning the text for names; the gap stage re-sweeps only the classes that came back empty, and runs only when the entity stage resolved something. `expand_schema` (default `True`) runs `expand_schema()` first; the `schema_expansion_*` params mirror that method's `depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one. `max_turns` applies per stage. `related_data_k` (default `10`) surfaces that many existing-entity/fact search hits in every stage's prompt as unconfirmed leads; `0` disables it. `effort` (`"low"` default \| `"medium"` \| `"high"` → 1/2/3 passes) repeats the whole pipeline, each later pass told to hunt for what a prior pass missed rather than re-deriving what's there. `max_concurrency` (default `1`, unchanged behavior) splits `text` into up to that many paragraph-aligned chunks and runs a full pipeline per chunk concurrently, at the cost of a residual cross-chunk duplicate risk (see below) — see ExtractionEngine and SchemaExpansionEngine below |

#### Sync convenience methods

Two additional synchronous methods exist for callers that can't `await`, both wrapping the async API internally via `asyncio.run()`:

| Method | Returns | Description |
|--------|---------|-------------|
| `execute_query(query, database?, collection?, optional_properties?)` | `list[dict]` | Generic op-code dispatcher — `query["op"]` such as `"KB.DECLARE_CLASS"`, `"KB.ADD_ENTITY"`, `"KB.GET_SCHEMA"`, mirroring most async methods above 1:1; `GRAPH.*` ops forward to the underlying `GraphSource`. `get_metadata` is deliberately **not** exposed as an op — library method and MCP/agent tool only, by design (see [13-decision-log.md](13-decision-log.md) ADR-018). Every result is a `list[dict]`, as the `DataSource` contract requires: a write's id comes back as `[{"id": ...}]`, a write that returns nothing as `[]`, a single record (the schema, a node) as a one-row list, search results as one dict per hit. Safe to call from inside a running event loop (it then runs the operation on a worker thread) |
| `execute_query_nl(query, database?, ...)` | `list[dict]` | Sync convenience wrapper around `answer(..., natural_language=False)`. `llm_fn` is accepted but not used — an agent loop over KB tools can't be driven by a plain prompt→text function; the model is `optional_properties["model"]`, else `BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL`, `BLUE_DATA_KB_SOURCE_LLM_MODEL`, `BLUE_DATA_LLM_MODEL`, else `gpt-4o` |

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

Traverses all CLASS and RELATION nodes in `database` and assembles a `DataSchema` capturing classes, their properties, relations, and domain/range constraints, returned as `DataSchema.to_json()`'s dict — synchronously, as `DataSource` declares the method; `DataSchema.from_json` rebuilds the object. The extraction, query and expansion engines take the `DataSchema` straight from `SchemaManager.fetch_database_schema` instead (ADR-036).

#### `find_schema(database, query, type_filter=None)`

Ranks schema element nodes (CLASS, RELATION, PROPERTY) by keyword match of `query` against their `name` and `description`: the whole query as a substring first, then the number of shared words (names split on `_`, stopwords ignored), so "is proficient in" finds `PROFICIENT_IN` (#133). This is the fallback used by `KBSource.search_schema` when the database was not registered with search enabled; when it is enabled, `KBSource.search_schema` calls `KBSearchIndexer.search_schema` instead, which supports BM25 ranking and semantic mode. Returns a list of `SchemaSearchResult` (`{"id", "type", "name", "description"}`).

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

Merge folds each source element into the target: every edge that referenced a source is re-pointed at the target, the source nodes are deleted, and change events are fired for the target, each rewired neighbour and each deleted source so the search index reflects the merged schema. Source and target names are canonicalized as `declare_*` canonicalizes them, so `["worker"]` resolves the `WORKER` a declare call would have created.

**Parameters**

- `operation` — must be `"merge"`
- `source_names` — names of the elements to merge away; all must resolve to the same kind of schema node (all CLASS, all RELATION, or all PROPERTY) or `SchemaError` is raised
- `target_name` — name of the element to merge into; created automatically if it doesn't already exist
- `predicate` — accepted for signature compatibility; not used

**Algorithm — merge**

Merge folds the sources into the target and leaves only the target:

1. Resolve each name in `source_names` to a schema element ID, canonicalizing it the way `declare_*` does (so `"worker"` finds `WORKER`); the same label is required across all of them
2. Look up the target element by `target_name` — canonicalized likewise — or create it if absent
3. Re-point **every** edge incident to a source at the target: instance edges (`IS_INSTANCE_OF`/`REPRESENTS`/`IS_VALUE_OF`), `IS_SUBCLASS_OF` in both directions, `DOMAIN`, `RANGE`, `DEFINE_PROPERTY`, and provenance. The rewire enumerates the source's edges rather than consulting a fixed list of edge types, so a new edge type is carried along without changing this code. An edge the target already has is not duplicated; an edge between source and target, which would become a self-loop, is dropped
4. If the target has no description of its own, adopt one from a source — otherwise the merged-away element's description is lost
5. Delete the source node, which removes its remaining edges with it
6. Fire change events: `*_updated` for the target, `*_updated` for each rewired neighbour, `*_deleted` for each source

Step 6 is what keeps search consistent: the Search Indexer subscribes to these events, so without them `search_schema` would keep returning merged-away elements and entities would stay indexed under their old class name.

After a merge, `fetch_database_schema` lists the target only, and each affected instance has exactly one class edge — so `get_metadata`'s `class_name` reports the target. Merging is destructive by design; there is no flag to keep the sources.

#### `attach_property(database, target_id, property_id)`

Wires a `DEFINE_PROPERTY` edge from an existing CLASS or RELATION (`target_id`) to an existing PROPERTY (`property_id`). Idempotent — calling it again with the same pair is a no-op, not a duplicate edge. Raises `SchemaError` if `target_id` isn't a CLASS/RELATION or `property_id` isn't a PROPERTY. Exists so a property discovered via `search_schema` on one class/relation can be reused on another instead of `declare_class_property`/`declare_relation_property` creating a same-meaning duplicate under a new name — both `SchemaExpansionEngine` (below) and `blue-kb schema class/relation property attach` use it for exactly this.

#### `expand_schema(database, text, *, depth=1, reference="llm", mcp_servers=None, dedup_mode="search", model=None, on_tool_call=None, on_tool_result=None, max_turns=25, timeout=None)`

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

Ranks ENTITY and FACT nodes by keyword match of `query` (the whole query as a substring first, then the number of shared words, stopwords ignored) against their own name/description plus related names (class name for entities; head, relation, and tail names for facts), with no relevance ranking. This is the fallback used by `KBSource.search_data` when the database was not registered with search enabled; when it is enabled, `KBSource.search_data` calls `KBSearchIndexer.search_data` instead, which supports BM25 ranking and semantic mode. Returns a list of `DataSearchResult`.

#### `answer(database, question, model=None, natural_language=False, include_metadata=False, on_tool_call=None, on_tool_result=None)`

Delegates to `QueryEngine.answer(..., include_metadata=...)` for the structured result, then applies `natural_language` handling **at this layer**, not inside `QueryEngine` — `QueryEngine` itself has no `natural_language` parameter. Returns a list of plain dicts (not `AnswerResult` — see note below); a prose string when `natural_language=True`; or a `(result, metadata)` tuple when `include_metadata=True` (prose composition and the metadata tuple compose — with both flags set, the prose has a metadata section appended and `metadata` is still returned as the second element). `on_tool_call`/`on_tool_result`, if given, are invoked with `(tool_name, arguments)`/`(tool_name, arguments, result)` for each tool call the agent makes.

#### `validate(database, statement, model=None, natural_language=False, include_metadata=False, on_tool_call=None, on_tool_result=None)`

Delegates to `QueryEngine.validate`, with the same `natural_language`/`include_metadata` layering as `answer` above. Returns a `ValidationResult`, a prose string, or a `(result, metadata)` tuple.

#### `enrich(database, query, depth=2, model=None, on_tool_call=None, on_tool_result=None)`

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

#### `answer(database, question, model=None, include_metadata=False, on_tool_call=None, on_tool_result=None)`

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

#### `validate(database, statement, model=None, include_metadata=False, on_tool_call=None, on_tool_result=None)`

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

#### `enrich(database, query, depth=2, model=None, on_tool_call=None, on_tool_result=None)`

Fetches the schema and runs an agent whose only job is to pick the seed entities the query is about: it uses `search_entities` (and `get_entity_facts`/`get_neighbors` when the query names an entity only through its relations), then calls `return_result` with `{"seed_entity_ids": [...]}`. Each id is checked against the graph (a name is accepted as a fallback; anything else is dropped). A deterministic breadth-first walk from the seeds then produces the `EnrichmentResult` list: each seed and its property values at distance 0, each fact one hop out with its property values and its other endpoint at distance 1, and so on up to `depth` (clamped to 3; at most 500 rows). The traversal used to be the agent's, and so were the distances, so `depth=1` could return a two-hop fact labelled distance 1 (#163).

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

All eleven take trailing `source_id=None, confidence=None, extraction_type="assertion", schema_creation_mode="llm", model=None` keyword options, elided below. `AssertResult` reports the subject (`node_id`, `created`, `match_type`, `extraction_id`) plus the full cascade (`created_nodes`) and any `widened_relations`.

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

#### `import_text(database, text, queries=None, model=None, source_id=None, on_tool_call=None, on_tool_result=None, max_turns=25, related_data_k=10, effort="low", max_concurrency=1)`

Extracts entities, facts, and property values from `text` into `database`, against the schema as it
currently stands. Called directly by `ExtractionEngine`'s consumer — `KBSource.import_text` (see the
`expand_schema` note below for how the two-phase call is orchestrated).

**Parameters**

- `database` — target database
- `text` — the free-text passage to process
- `queries` — optional list of natural language questions guiding the extraction focus
- `model` — OpenAI model to use for the agent loop
- `source_id` — ID of an existing SOURCE node; passed through to each instance creation call
- `on_tool_call` / `on_tool_result` — optional callbacks invoked with `(tool_name, arguments)` / `(tool_name, arguments, result)` for each tool call the agent makes, across every stage
- `max_turns` (default `25`) — caps the number of agent turns *per stage*; hitting the limit stops that stage's loop silently rather than raising, and the pipeline still moves on to the next stage
- `related_data_k` (default `10`) — number of existing ENTITY/FACT hits from `KBSearchIndexer.search_data(database, text, k=related_data_k)` to surface in every stage's system prompt as "potentially related existing data"; `0` disables the lookup. A no-op when the database has no search indexer configured (`properties["search"]["mode"]` is `"none"`, the default) — the prompt says so explicitly rather than silently omitting the section
- `effort` (`"low"` default | `"medium"` | `"high"` → 1/2/3 passes) — repeats the whole pipeline that many times over the same text; each pass after the first is told a prior pass already ran, *and is shown everything those passes resolved* (entities, facts, and property values) in a "already extracted from this text" block, so "focus on what was missed" is actionable rather than aspirational. The block is omitted entirely when nothing has been carried, so a single-pass run's prompts are unchanged. Safe to repeat — `find_entity`/`find_fact`/`link_extraction` already dedupe within a sequential run — it just costs more LLM calls
- `max_concurrency` (default `1`, unchanged behavior) — splits `text` into up to that many paragraph-aligned chunks (see `_split_into_chunks`) and runs a full pipeline (all `effort` passes) per chunk *concurrently*, sharing one `openai.AsyncOpenAI` client; every tool dispatch is wrapped in an `asyncio.Lock` so two chunks can't interleave a single dispatch's own reads/writes. This does not make cross-chunk extraction fully race-free: the lock can't reach across a chunk's separate `find_*`-then-`add_*` LLM turns, so two chunks that both mention the same entity/fact can each independently decide it doesn't exist yet and create it twice — a race this codebase's prior fully-sequential execution never had. Best suited to text whose chunks cover mostly distinct content; a single unbroken block of text (no paragraph breaks) always runs as one chunk regardless of this value

**Staged pipeline:** rather than one agentic loop with all instance-creation tools available at once,
`import_text` runs a sequence of stages, each its own agentic loop (its own `max_turns` budget, its
own fresh message history) over a narrower tool set than the last — mirroring the existing
schema-expansion-then-extraction split, applied one level deeper:

| Stage | Tools | Given from prior stages |
|-------|-------|--------------------------|
| entities | `get_schema`, `search_schema`, `find_class`, `find_entity`, `add_entity`, `attach_entity_class`, `link_extraction` | the full CLASS checklist (id, name, description) |
| entity gaps *(conditional)* | same as entities | entities resolved, plus the classes that got none |
| relations | `get_schema`, `search_schema`, `find_relation_type`, `find_fact`, `add_fact`, `link_extraction` | entities |
| fact properties | `get_schema`, `search_schema`, `find_literal`, `add_fact_property_value`, `link_extraction` | entities, facts |
| entity properties | `get_schema`, `search_schema`, `find_literal`, `add_entity_property_value`, `link_extraction` | entities, facts, fact property values |

Entities render as `id=… Name (CLASS, …)` with every class they belong to, facts as `id=… Head (CLASS, id=…) —RELATION→ Tail (CLASS, id=…)`
so a stage handed both lists can line them up by id rather than by bare name, and property values as
`Owner.PROPERTY = value` (a fact owner spelled out as `(Head —RELATION→ Tail)`).

**Property ownership and stage order:** fact properties run *before* entity properties. Both stages
re-scan the same text for attribute values, and `is_property_allowed` only rejects a property that
isn't declared for that owner at all — where a PROPERTY is declared on both a class and a relation,
whichever stage runs first keeps the value. The fact is the more specific owner (a `START_DATE`
belongs to the employment, not to the person), so it claims first; the entity stage then runs with
the fact stage's values in its prompt and is told not to re-attach any of them. Each stage is also
told which kind of value is *not* its own — relationship-shaped values for the entity stage,
entity-shaped ones for the fact stage — so the lists read as disambiguation context rather than as
more things to attach values to.

**Class-driven entity sweep:** the entity stage is given the complete CLASS list (name-sorted, with
ids and descriptions) and told to work through it class by class, not to scan the text for names.
The earlier text-first framing ("for each entity mentioned in the text… then find its CLASS via
`search_schema`") reliably produced a *named-entity* reading: classes whose instances are ordinary
nouns — `SKILL`, a language, a material, a condition — were never even proposed as candidates, so no
`find_entity` call was ever made for them. Rendering the checklist also removes the `search_schema`
round-trip for classes that are already in the prompt verbatim.

**Entity gap recovery:** a conditional stage between entities and relations. After the entity stage,
any CLASS with zero resolved instances is re-swept once, on its own, with the resolved entities
shown as context. It runs only when the entity stage resolved *something* — selective blindness
(some classes covered, others never) is the failure it exists to catch, whereas a stage that
resolved nothing has no partial coverage to explain and re-listing every class would just repeat the
entity prompt verbatim; that is what `effort`'s additional passes are for. Entities it recovers are
merged (by id) into the list handed to every later stage, so a recovered entity can still take part
in facts and carry property values.

The relation and property stages are never offered `find_entity`/`add_entity` — the system prompt
instructs them to use the ids in the "already resolved" list and to skip anything not on it, rather
than rediscovering or inventing an entity. This trades roughly 4-5x the LLM round-trips of the old
single-loop design for a narrower, more reliable task per call; `max_concurrency` (above) offsets
that cost for long text by overlapping chunks' LLM round-trips. `add_entity_property_value`/`add_fact_property_value` reject a property that isn't
actually defined for the entity's class / fact's relation (`KnowledgeError`, via `is_property_allowed`)
— important now that two independent stages each re-scan the same text for attribute values and could
otherwise both claim the same value for the wrong owner.

**Algorithm (per stage)**

1. Call `SchemaManager.fetch_database_schema(database)` and serialize it as context for the LLM (shared across all stages); separately collect the CLASS nodes as an (id, name, description) checklist for the entity and gap stages
2. When a search indexer is configured and `related_data_k > 0`: call `KBSearchIndexer.search_data(database, text, k=related_data_k)` and render the hits (entity name + class; fact head/relation/tail) as additional context (shared across all stages)
3. Submit `text`, `queries`, schema context, related-data context, and the stage's own "already resolved" list to the LLM with that stage's tool set
4. Loop (up to `max_turns` turns):
   - Receive tool calls from the LLM
   - For instance creation calls: dispatch to `KnowledgeManager`, passing `text=<verbatim span>` and `source_id=source_id`
   - For `link_extraction` calls (item already exists): dispatch to `ProvenanceTracker.add_extraction(element_id=…)`, attaching the new span as further evidence rather than creating a duplicate
   - `add_entity` and `find_entity` resolve an existing entity by name *and class*: names aren't unique, so a same-named entity only counts when it is untyped or one of its classes is the requested class, an ancestor or a descendant of it — "Apple" as a FRUIT never collapses onto "Apple" the COMPANY (#115). `find_entity` returns the match's `classes` and lists other same-named entities as `other_matches`, so the model can tell them apart
   - Return tool results to the LLM
5. Exit when the LLM returns without further tool calls, or `max_turns` is reached
6. Resolve every id-typed argument (`class_id`, `relation_id`, `entity_id`, `head_entity_id`, `tail_entity_id`, `fact_id`, `property_id`) before the call runs. The agent routinely passes a *name* where an id belongs (`'AGE'`, `'WORKED_AT'`, `'COMPANY'`); an argument already holding a node of the right label passes through untouched, otherwise it is looked up by name via `find_class`/`find_relation_type`/the entity resolver, and only an unresolvable value errors. Exact-match lookups only — nothing fuzzy or semantic, because this pass extracts against the schema as it stands and a near-match would quietly invent membership the schema never granted. `property_id` resolves against the properties declared for the owner named in the same call (`entity_id`/`fact_id`) before falling back to `find_property`, since property names are not unique across classes and a bare lookup is a coin-flip. Coercions are reported back in the tool result as `coerced: {arg: "name -> id"}`, so they surface in `on_tool_result` rather than silently papering over the mistake. This runs for reads as well as writes, and the reads are why it exists: `find_fact` given a relation *name* does not fail, it returns `{id: null}`, so the model concludes the fact is new and creates a second one
7. Reject any creation call whose `text` argument is not a quote from the stage's source text, returning the refusal as the tool result so the model can correct itself rather than silently losing the turn. A call that omits `text` entirely takes the existing permissive path. "Quote" is a contiguous case/whitespace-insensitive match, or failing that the span's words appearing in the source in order and within a bounded window (`3n + 5` source words for an `n`-word span). The window is what makes the test usable on list-structured text: asked to quote a bullet list, models reliably splice a heading onto a real fragment (`"Skills: Tools: JIRA, Trello"`) or elide a middle (`"* Technical: Basic Python"` from `"* Technical: SQL, Basic Python"`), and a strict substring test rejects every one of those, costing the stage its whole turn budget — while a sentence stitched from scattered words still needs most of the document to match, and is still rejected
8. Collapse a creation call onto the item it duplicates, rather than creating a second copy: `add_entity` onto an existing entity of the same (normalized) name, `add_fact` onto the same (head, relation, tail) triple, and the property adds onto an existing value of the same *value* for the same property — keyed on the value because an owner may legitimately hold several (two phone numbers). Values are compared in the form the write path stores them (`"March 2019"` is written as `"2019-03"`, so comparing the raw argument would report every re-extraction of a date as new), and a second comparison catches the same date sent twice under different `literal_type`s. The new text span is recorded against the surviving item and the tool returns `{id, deduped: true}`. Each stage is *told* to look before it writes (`find_entity`/`find_fact`/`find_literal`), but that is advisory, and a later pass re-reading the same text is precisely when the model skips the check — so the same resolve-or-link runs at the one point every write passes through. `KnowledgeManager`'s write semantics are unchanged; this is the extraction agent declining to create a second copy of something it can already see
9. After every stage, harvest what its tool calls touched and re-fetch the current state from the graph for the following stages' prompts: entities (via `add_entity`/`find_entity`/`link_extraction`), facts (via `add_fact`/`find_fact`/`link_extraction`, with each endpoint's class and id), and property values (via `add_entity_property_value`/`add_fact_property_value`/`link_extraction`, walking `HAS_PROPERTY` back to the owner and `IS_VALUE_OF` out to the PROPERTY, since only the LITERAL id comes back from the tool)
10. After the entity stage only: diff the CLASS checklist against every class those entities belong to (including ones added via `attach_entity_class`), and run the gap stage over the remainder (skipped when the diff is empty, or when nothing is known at all). Coverage is judged against every entity known for this text, carried context included, not just the ones this pass touched — a later pass only re-reads what it re-reads, so a class an earlier pass already filled would otherwise look empty and send the gap stage hunting for entities that already exist
11. Accumulate all of the above into a per-chunk `_ExtractionContext`, deduped by id, and render it into the next *pass*'s prompts. The context is per chunk, never shared between concurrently running chunks — for the same reason the dispatch lock can't make them race-free, a chunk must not be told an entity exists because another chunk is midway through creating it

**Where the schema expansion pass fits in:** `KBSource.import_text` (not `ExtractionEngine` itself) owns the two-phase orchestration — with `expand_schema=True` (the default), it calls `SchemaManager.expand_schema(...)` first, using `text`/`queries` (filtered by `schema_expansion_target`) as that call's grounding text, before calling `ExtractionEngine.import_text` above (which then itself runs its internal stages). Pass `expand_schema=False` to skip straight to extraction against whatever schema already exists.

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

#### `expand_schema(database, text, *, depth=1, reference="llm", mcp_servers=None, dedup_mode="search", model=None, on_tool_call=None, on_tool_result=None, max_turns=25, timeout=None)`

**Parameters**

- `database` — target database
- `text` — the free-text passage to ground new schema elements in
- `depth` (default `1`) — how many hierarchy levels beyond what's directly evidenced in `text` to also propose and link via `add_is_subclass_of` (e.g. a mentioned "poodle" also suggests a `DOG` parent CLASS at `depth=1`; `depth=0` sticks to only what's directly evidenced)
- `reference` — `"llm"` (default) proposes schema freely from the model's own knowledge; `"mcp"` additionally connects each server in `mcp_servers` and offers the model its tools too (e.g. WordNet hypernym/synonym lookups via `platform/wordnet-mcp`), to ground new names and hierarchy placement — raises `SchemaError` if `reference="mcp"` and `mcp_servers` is empty
- `mcp_servers` — list of `{"transport": "stdio", "command", "args"?, "env"?}` or `{"transport": "sse"|"streamable_http", "url", "headers"?}` dicts; each is connected via an `AsyncExitStack` for the duration of the call, and its tools are exposed to the model name-prefixed `mcp__<server>__<tool>`. Each entry may also set `startup_timeout` (seconds, default 30) for its handshake and `call_timeout` (default 120) per tool call; a server that misses either fails clearly instead of hanging the run. A timed-out tool call is returned to the model as a tool error (#113)
- `dedup_mode` — how the engine checks for an existing schema element before declaring a new one; applies both to the `search_schema` tool the model can call, and — for `declare_class`/`declare_relation_type` specifically — as an automatic check run before the node is created, regardless of whether the model called `search_schema` itself (closes the gap where two differently-worded near-duplicates, e.g. `WORKED_AT`/`EMPLOYED_AT`, would otherwise both get declared, since the underlying `declare_*` calls only dedup on an exact name match):
  - `"search"` (default) — the database's configured search backend (index-free keyword match ranked by shared words, BM25, or semantic/embedding search if registered)
  - `"llm"` — asks the model to judge whether an existing element means the same thing as the proposed one, even under different wording, via a separate tool-forced completion call (`_judge_similar_schema`) — more reliable for synonyms than keyword/semantic search over short names, at the cost of one extra model call per check
  - `"none"` — the `search_schema` tool isn't offered at all, and the automatic pre-create check is skipped
- `model` — OpenAI model to use for the agent loop (and for `dedup_mode="llm"`'s judge call)
- `on_tool_call` / `on_tool_result` — optional callbacks invoked with `(tool_name, arguments)` / `(tool_name, arguments, result)` for each tool call the agent makes (including MCP tool calls)
- `max_turns` (default `25`) — caps the number of agent turns; hitting the limit stops the loop silently
- `timeout` (default `None` = no limit) — caps the whole run in seconds (every model turn, tool call, and judge call), via `asyncio.wait_for`; on expiry raises `SchemaError` naming the model and how many turns completed. Elements declared before expiry are kept. Without it, a stalled request waits out the OpenAI client's own 600 s timeout plus its silent retries

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
   - `add_subclass_of`: first ask a forced-tool LLM judge (`_judge_subclass`) whether every CHILD is necessarily a PARENT, given both names and descriptions; on "no", return `{error}` with the judge's reason instead of writing the edge. Malformed pairs (missing ids, mixed kinds, self-loops) and already-present edges skip the judge and go straight to `SchemaManager.add_is_subclass_of`, which reports or no-ops them itself
   - `declare_class_property`/`declare_relation_property`: first run `_check_property`, one forced-tool LLM call scoped to the owner — given the owner (with a relation's domain/range) and the owner's existing properties, report whether the property describes the owner and whether it duplicates one of them. A duplicate returns that property's id with `reused_existing`; a misplacement returns `{error}` with the reason. The duplicate half is skipped for `dedup_mode="none"`; an exact-name redeclaration or missing owner skips the call
   - `attach_property`: dispatch straight to `SchemaManager.attach_property`
   - an `mcp__`-prefixed call: dispatch to the connected MCP session instead of local tool dispatch
   - Return tool results to the LLM
4. Exit when the LLM returns without further tool calls, or `max_turns` is reached
