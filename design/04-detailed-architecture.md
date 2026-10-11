# blue-kb — Detailed Architecture

## External Interfaces

All four interfaces are thin adapters over the Python library. They each ultimately delegate to the same core operations on `KBSource`.

| Interface | Technology | Primary Use | Status |
|-----------|-----------|-------------|--------|
| Python Library | Native Python API | Embedding blue-kb directly in Python applications and pipelines | Implemented |
| MCP Server | MCP protocol | LLM agent tool use (e.g. Claude) | Implemented |
| CLI | Click + Rich | Development, debugging, schema inspection, and scripting | Implemented |
| REST API | FastAPI (HTTP/JSON) | Non-Python consumers and microservice deployments | **Planned, not yet implemented** |

### Python Library

`KBSource` and related classes are the native interface. Direct method calls with no serialization overhead. All other interfaces wrap this layer.

### MCP Server

Exposes schema declaration, instance writes, text extraction with its schema expansion pass, and query operations as MCP tools. Enables LLM agents to build and query a knowledge base directly within an agentic context. A separate, standalone `platform/wordnet-mcp` server exposes read-only WordNet lookups, which the schema expansion agent (below) can optionally connect to as a peer MCP server, not through this server itself.

### REST API — planned, not yet implemented

Nothing under this heading exists in the repository yet — no FastAPI application, no route handlers, `platform/api/` is an empty directory. The description below is a forward-looking sketch, not current behavior; see [09-api-interface-spec.md](09-api-interface-spec.md) §2 for the full speculative endpoint design.

HTTP interface built with FastAPI. Endpoints map to the core schema and knowledge operations. Enables non-Python services and remote clients to interact with blue-kb over the network.

### CLI

Command-line interface built with Click and Rich. Thin wrapper over the Python library for interactive development, schema inspection, expansion runs, and scripting.

---

## Information Flow Overview

The Write, Ingest, and Extraction Paths are the KB's expansion paths — the flows that add
schema and instance knowledge; the Schema Expansion Pass is the Extraction Path's
schema-growing half, run first by `import_text` or on its own. The Answer, Validate, and
Enrich Paths are the usage paths, which read the KB without expanding it, and the Schema
Path declares and manages schema by explicit call.

### Schema Path

1. Caller requests a schema operation (declare, consolidate, search, import, or export)
2. Schema Manager checks idempotency: if the element already exists by name, its ID is returned immediately
3. If new: Schema Manager creates the node, wires hierarchy and domain/range edges, returns the new ID
4. If consolidation: Schema Manager merges or splits the affected elements, then traverses and rewires all instances that reference them
5. If search: Schema Manager filters schema elements against the query and returns matching names, descriptions, and IDs

### Write Path

1. Caller requests an instance operation (add entity, fact, or property value)
2. Knowledge Manager creates the instance node and wires its typed schema edges (class membership, relation, property)
3. If `text` is provided: Provenance Tracker creates an EXTRACTION node recording the assertion (`text`: a verbatim span for `text_span` evidence, otherwise the submitted statement) with its `extraction_type` and `confidence`, wires it to the instance and optionally to a SOURCE node
4. Change Notifier fires all registered hooks for the database with an enriched event describing the change
5. The new instance ID is returned to the caller

### Ingest Path

1. Caller asserts an item by name (`assert_*`), or submits a ten-arm payload of rows (`ingest`)
2. Ingestion Manager resolves every operand read-only via the Node Resolver — exact, normalized, then search candidates — and runs any needed LLM judges (class naming, property placement, description generation); ambiguity or a judge failure raises here, before anything is written
3. Ingestion Manager writes through the Write Path: unresolved operands are created (schema elements included), a non-conforming fact widens its relation's domain/range, and an already-known item gains a new EXTRACTION instead of a duplicate
4. The result reports what happened — created nodes, widened relations, the match kind on reuse; bulk runs collect per-row failures (with resolution candidates) into an `IngestReport` rather than stopping

### Schema Expansion Pass

1. Caller invokes `expand_schema` with free text, and optionally `depth`, `reference`, `mcp_servers`, `dedup_mode`
2. Schema Expansion Engine reads the current schema from the Schema Manager and passes it to its own LLM Agent as context, along with `depth` and (if `reference="mcp"`) a note that MCP tools are available
3. If `reference="mcp"`: the engine connects every server in `mcp_servers` and adds their tools (e.g. WordNet hypernym/synonym lookups) to the agent's tool set for this call only
4. LLM Agent reads the text and identifies the categories, relationship types, and attributes it evidences — never the specific named individuals themselves (those are entities, handled only by the Extraction Path below)
5. Before declaring anything new, the agent calls `search_schema` (unless `dedup_mode="none"`) to check for an existing near-duplicate; every `declare_class`/`declare_relation_type` call additionally runs an automatic dedup check first regardless, per the same `dedup_mode`
6. For each new CLASS/RELATION, the agent also calls `search_schema` again to find a plausible existing parent/child and wires it in with `add_subclass_of` (never a declared RELATION — a taxonomic name like `IS_A`/`TYPE_OF`/`SUBCLASS_OF` is rejected by `declare_relation_type`), and calls `attach_property` instead of declaring a new PROPERTY when an existing one already covers the concept on a different class/relation
7. The loop continues until the LLM Agent reports no further schema elements to declare, or `max_turns` is reached — the Schema Expansion Engine never touches `ENTITY`/`FACT`/`LITERAL` nodes

### Extraction Path

1. Caller invokes `import_text` with free text, guiding queries, and an optional SOURCE node ID
2. With `expand_schema=True` (the default), `KBSource.import_text` first runs the full Schema Expansion Pass above — grounded by `text`/`queries` per `schema_expansion_target` — so the schema already reflects the text before extraction starts; pass `expand_schema=False` to skip this and extract only against whatever schema already exists
3. Extraction Engine reads the (possibly just-expanded) schema from the Schema Manager and passes it to its own LLM Agent as context
4. LLM Agent reads the text and, guided by the queries, identifies entities, facts, and property values, resolving each to an existing CLASS/RELATION/PROPERTY via `find_entity`/`find_fact`/`search_schema` — **the Extraction Engine's LLM Agent never declares new schema itself**; a mention with no matching schema element is simply skipped
5. For each identified item, the LLM Agent calls Knowledge Manager tools, passing the verbatim text span — triggering the full Write Path including provenance and change notification
6. The loop continues until the LLM Agent reports no further items to extract, or `max_turns` is reached

All three paths below take a common set of cross-cutting parameters not repeated in each numbered list: `model` (default `"gpt-4o"`), and `on_tool_call`/`on_tool_result` observability callbacks invoked synchronously during the agent loop. `answer` and `validate` additionally take `natural_language: bool = False` and `include_metadata: bool = False` (`enrich` has neither). `include_metadata=True` adds a `get_metadata` tool to the agent's tool set (see Query Engine Tool Set below); every call the agent makes to it is harvested into a `metadata: dict[node_id, dict]` returned alongside the result as a `(result, metadata)` tuple, regardless of whether the LLM's own structured output references that id. `natural_language=True` (on `answer`/`validate` only) additionally asks the LLM to compose prose from the structured result instead of returning it raw — with `include_metadata=True` also set, the prose has a metadata section appended and `metadata` is still returned as the second tuple element. This composition happens one layer up, in `KnowledgeManager`, not inside `QueryEngine` itself — `QueryEngine.answer`/`validate` take `include_metadata` but have no `natural_language` parameter of their own.

### Answer Path

1. Caller submits a natural language question
2. Knowledge Manager delegates to Query Engine
3. Query Engine fetches the current schema and constructs a system prompt with schema context and the question
4. LLM agent loop: the agent calls read-only graph tools (`search_entities`, `get_entity_facts`, `get_entity_properties`, `get_fact_properties`, `get_neighbors`, and `get_metadata` when `include_metadata=True`) until it has enough information
5. Agent calls `return_result` with a structured list of `{entity, relation, value, provenance}`-shaped items — the exact keys are whatever the LLM decided fit the question, not a fixed schema
6. Query Engine returns that payload as-is, as a plain `list[dict]` — unlike Validate and Enrich below, it is not wrapped in a dataclass. `KnowledgeManager` then applies `natural_language`/`include_metadata` handling on top (see above) before returning to the caller.

### Validate Path

1. Caller submits a statement to be validated
2. Knowledge Manager delegates to Query Engine
3. Query Engine fetches the current schema and constructs a system prompt directing the agent to find supporting and counter evidence
4. LLM agent loop: the agent uses graph tools to search for facts that confirm or contradict the statement
5. Agent calls `return_result` with `{supporting: [...], counter: [...]}` where each item is `{fact, head, tail, relation}`
6. Query Engine unpacks the payload into a `ValidationResult` and returns it to the caller; `KnowledgeManager` then applies `natural_language`/`include_metadata` handling on top

### Enrich Path

1. Caller submits a query and an optional depth limit
2. Knowledge Manager delegates to Query Engine
3. Query Engine fetches the current schema and constructs a system prompt directing the agent to traverse the graph up to `depth` hops from seed entities
4. LLM agent loop: the agent uses `search_entities` to find seeds, then `get_neighbors` and `get_entity_facts` to explore outward
5. Agent calls `return_result` with a list of `{entity, relation, value, distance}` items
6. Query Engine unpacks the payload into `EnrichmentResult` objects and returns them to the caller — `enrich` has no `natural_language`/`include_metadata` variance, unlike `answer`/`validate`

---

## 1. Schema Layer

The Schema Layer manages the typed vocabulary of the knowledge base. It is the authoritative source of truth for what kinds of entities, relations, and properties exist, and it enforces consistency between the schema and all instance data.

### Schema Manager

**Responsibilities**

- Declare classes, relation types, and properties (idempotent: existing names return their IDs without modification)
- Update or delete classes, relation types, and properties (`update_class`/`update_relation`/`update_property`, `delete_class`/`delete_relation`/`delete_property`) — not idempotent, and not part of the extraction/query agents' tool sets
- Maintain class and relation hierarchies (subclass, sub-relation), including removal (`remove_is_subclass_of`) — the *only* way to express an is-a/type-of/kind-of relationship between two classes or two relations; never a declared relation type
- Wire domain and range constraints from relation types to classes
- Reuse an existing property across classes/relations (`attach_property`), instead of a same-meaning duplicate being declared under a new name
- Export the schema to a portable JSON representation and import it from that representation into any database
- Expose the meta-schema — the fixed description of blue-kb's own graph structure — for agent introspection
- Search schema elements by natural language query or keywords
- Consolidate schema elements: today, only merging redundant classes, properties, or relations into one target is implemented (see below) — splitting is not
- Grow the schema itself from free text via an LLM agent (`expand_schema`, owned internally as a Schema Expansion Engine — see below) — a separate concern from the Extraction Engine (§4), which only ever *uses* the schema, never grows it

**Schema Declaration**

When a caller declares a schema element, the Schema Manager:

1. Looks up the element by name in the Graph Store
2. If found: returns the existing ID (idempotent)
3. If not found: creates the node with the provided name and description, wires any optional edges (hierarchy, domain, range), and returns the new ID

**Schema Consolidation**

Only `operation="merge"` is implemented today; any other value (including `"split"`) raises `SchemaError`. `consolidate_schema` accepts a `predicate` parameter in its signature, but nothing in the current implementation reads it — it's unused.

Merge folds each source element into the target and leaves only the target:

1. Resolve the source names and the target name, canonicalized the same way `declare_*` canonicalizes them, and create the target if no element of that label already carries the name
2. Re-point **every** edge incident to a source element at the target — instance edges (`IS_INSTANCE_OF`, `REPRESENTS`, `IS_VALUE_OF`), `IS_SUBCLASS_OF` in both directions, `DOMAIN`, `RANGE`, `DEFINE_PROPERTY`, and provenance. The rewire is generic rather than a fixed list of edge types, so an edge type added later is carried along without revisiting this code. An edge the target already has is not duplicated, and an edge between the two participants (which would become a self-loop) is dropped
3. Adopt a source's description if the target has none of its own — a merged-away element's description is otherwise the only record of what it meant
4. Delete the source node, which drops its remaining edges with it
5. Fire change events: `*_updated` for the target, `*_updated` for every rewired neighbour, and `*_deleted` for each source

Step 5 is what keeps the search index consistent. The indexer subscribes to these events, so without them `search_schema` would keep returning the merged-away element and keep indexing entities under their old class name.

After a merge, an affected instance belongs to the target only, and the source is gone from `fetch_database_schema`'s output — so code that assumes one class per entity (e.g. `get_metadata`'s `class_name`) reports the target.

**Schema Search**

When search is enabled for the database, `search_schema` is delegated to the Search Indexer, which matches the query against classes, relations, and properties using either BM25 keyword ranking or embedding-based semantic similarity, optionally restricted to a single element type. When search is not enabled, the Schema Manager falls back to a direct name/description substring match with no ranking. Either way, results are returned as a list of (name, type, ID, description) tuples.

**Example**

Query: `"employment"`

Matching schema elements:
```
EMPLOYEE     CLASS      "A person employed by an organization"
COMPANY      CLASS      "A business organization"
WORKED_AT    RELATION   "Employment relationship between a person and a company"
START_DATE   PROPERTY   "Date when employment began"
```

**Schema Expansion**

`expand_schema` delegates to a Schema Expansion Engine the Schema Manager owns internally (constructed with a back-reference to the Schema Manager itself, so its tool dispatch can call `declare_class`/`add_is_subclass_of`/`attach_property`/etc. directly) — an LLM agent that grows the schema from a text passage, symmetric in spirit to how the Knowledge Manager owns a Query Engine for reading.

- **Scope**: declares `CLASS`/`RELATION`/`PROPERTY` nodes only, grounded by the given text — never `ENTITY`/`FACT`/`LITERAL`. The system prompt draws a hard line between a CLASS (a *kind* that can have many members, e.g. `PERSON`) and an *entity* (one specific named referent, e.g. "Alice") — the model is instructed to declare the general kind a mentioned individual belongs to, never a class named after the individual itself; the individual is created later, as an entity, by the Extraction Engine (§4) against that class.
- **Hierarchy vs. relation**: an is-a/type-of/kind-of/subclass-of relationship is wired only via `add_subclass_of` (→ `add_is_subclass_of`), never a declared `RELATION` — `declare_relation_type` rejects a fixed set of taxonomic names (`IS_A`, `TYPE_OF`, `SUBCLASS_OF`, ...) outright.
- **Deduplication (`dedup_mode`)**: `"search"` (default, keyword/semantic `search_schema`), `"llm"` (a separate tool-forced LLM call judges whether an existing element already means the same thing, even under different wording — e.g. `JOIN_DATE`/`START_DATE`), or `"none"` (no dedup tooling or check at all). Beyond the `search_schema` tool the model can call, `declare_class`/`declare_relation_type` specifically also run this check automatically before creating a node, regardless of whether the model searched first — closing the gap where two differently-worded near-duplicates (e.g. `WORKED_AT`/`EMPLOYED_AT`) would otherwise both get declared, since the underlying `declare_*` calls themselves only dedup on an exact name match.
- **Grounding (`reference`)**: `"llm"` (default) proposes freely from the model's own knowledge; `"mcp"` additionally connects each server in `mcp_servers` (stdio, sse, or streamable_http) and offers the model its tools too — e.g. WordNet hypernym/synonym lookups via `platform/wordnet-mcp` — to ground new names and hierarchy placement before declaring them.
- **Hierarchy depth (`depth`)**: how many levels beyond what's directly evidenced in the text to also propose and link via `add_subclass_of` (e.g. a mentioned "poodle" also suggesting a `DOG` parent class at `depth=1`; `depth=0` sticks to only what's directly evidenced).
- **Reuse over duplication**: after declaring a new class/relation, the agent searches again for a plausible parent/child to attach via `add_subclass_of`, and calls `attach_property` instead of declaring a new property when an existing one already covers the concept on a different class/relation.

`import_text` (§4) composes with this rather than duplicating it: with `expand_schema=True` (the default), it calls `expand_schema` first, using the text/queries being extracted as the grounding passage, before running its own (schema-read-only) extraction agent.

---

## 2. Knowledge Layer

The Knowledge Layer populates the knowledge base with instance data and answers queries over it. It delegates all persistence to the Graph Store and all schema lookups to the Schema Layer.

### Knowledge Manager

**Responsibilities**

- Create entity, fact, and literal nodes; wire their typed schema edges
- Attach a further class to an existing entity (`attach_entity_class`); `add_entity(class_id=…)` wires the first class at creation. Deduplicated and add-only
- Update or delete entity, fact, and literal nodes (`update_entity`/`update_fact`/`update_entity_property_value`/`update_fact_property_value`, `delete_entity`/`delete_fact`/`delete_entity_property_value`/`delete_fact_property_value`) — `delete_entity` takes a `cascade` flag that also deletes facts referencing the entity
- Look up per-item metadata (`get_metadata`) — created/modified timestamps, a FACT's derived confidence, and per-extraction evidence for a single entity, fact, or property value
- Export instance data to a portable, `ref`-keyed JSON representation and import it into any database whose schema already has the referenced classes/relations/properties declared (`export_database_data`/`import_database_data`) — data import is **not** idempotent: re-importing the same export creates duplicate entities/facts, unlike schema import
- Delegate answer, validate, and enrich queries to the Query Engine
- Delegate provenance creation to the Provenance Tracker
- Trigger the Change Notifier after each write
- Search entities and facts by keyword or semantic similarity — delegated to the Search Indexer when search is enabled, otherwise served directly with an unranked name/description match

**Instance Creation**

For each new entity, fact, or property value:

1. Create the instance node in the Graph Store
2. Wire schema edges: IS_INSTANCE_OF (entity → class), REPRESENTS (fact → relation), HAS_PROPERTY / IS_VALUE_OF (literal → entity or fact)
3. If `text` is provided: delegate to Provenance Tracker
4. Trigger Change Notifier with a fully enriched event

Unlike schema declaration, instance creation is never idempotent — `add_entity`/`add_fact`/`add_entity_property_value`/`add_fact_property_value` always create a new node, even when an identical one already exists. Attaching more evidence to an *existing* instance instead of creating a new one uses the `add_*_extraction` family (see Provenance Tracker below), not another `add_*` call.

**Answer / Validate / Enrich**

All three operations are delegated directly to the Query Engine. `KnowledgeManager` exposes them as part of its public interface but contains no query logic itself.

### Query Engine

**Responsibilities**

- Implement answer, validate, and enrich using a shared LLM agent loop
- Provide the agent with the current database schema as context and a read-only tool set over the instance graph
- Intercept the agent's `return_result` tool call to capture the structured payload and exit the loop
- Parse the payload into typed result objects for validate and enrich (`ValidationResult`, `EnrichmentResult`); answer returns the raw `list[dict]` payload unwrapped

**Tool Set**

The LLM agent has access to the following read-only tools during each query:

| Tool | Description |
|------|-------------|
| `get_schema` | Return the current database schema (classes, relations, properties) |
| `search_schema` | Search classes, relations, and properties by keyword or semantic similarity, via the Search Indexer |
| `search_entities` | Search ENTITY nodes by keyword or semantic similarity against name, description, and property values, via the Search Indexer |
| `search_facts` | Search FACT nodes by keyword or semantic similarity against head/relation/tail names, description, and property values, via the Search Indexer |
| `get_entity_facts` | Return all facts involving an entity as head or tail |
| `get_entity_properties` | Return all property values attached to an entity |
| `get_fact_properties` | Return all property values attached to a fact |
| `get_neighbors` | BFS from an entity through facts up to a given depth |
| `get_metadata` | Return created/modified timestamps, a FACT's derived confidence, and per-extraction evidence for one entity, fact, or property value — only offered when the caller passed `include_metadata=True` |
| `return_result` | Terminal tool — delivers the final structured payload and ends the loop |

Every `get_metadata` call the agent makes is harvested into the `metadata` dict returned alongside the result when `include_metadata=True` — the engine doesn't wait for the agent to reference an id in `return_result`'s own payload.

**Agent Loop**

1. Fetch the current schema via `SchemaManager.fetch_database_schema` and serialize it into the system prompt
2. Submit the system prompt and user message to the LLM with the full tool set
3. Receive tool calls from the LLM:
   - If `return_result`: capture the `result` payload, append a `"done"` tool response, and break
   - Otherwise: dispatch to the appropriate graph traversal and append the JSON result
4. Repeat from step 3 until `return_result` is called or the LLM stops issuing tool calls
5. Parse and return the captured payload as typed result objects

### Provenance Tracker

**Responsibilities**

- Create EXTRACTION nodes recording assertions: `text` (a verbatim span for `text_span` evidence, otherwise the submitted statement), `extraction_type`, and `confidence`
- Wire EXTRACTED_FROM edges from instances to their EXTRACTION nodes
- Wire EXTRACTION nodes to SOURCE nodes when a source is provided
- Attach *additional* evidence to an entity/fact/property value that already exists, without touching its name/description or creating a duplicate (`add_entity_extraction`, `add_fact_extraction`, `add_entity_property_value_extraction`, `add_fact_property_value_extraction`) — each call adds one more EXTRACTED_FROM edge; an item can accumulate several as more sources are found, none of them replacing a prior one
- Wire an existing EXTRACTION to an entity, fact, or property value (`attach_extraction`); deduplicated, add-only, and the tracker's only write that fires a change event (`extraction_attached`)
- Create SOURCE nodes with a caller-supplied `original` date (`add_source`), and update or delete them (`update_source`/`delete_source`)

**Provenance Flow**

When an instance is created with a `text` argument:

1. Create an EXTRACTION node with the assertion `text`, its `extraction_type`, and `confidence` (`None` = unstated)
2. If `source_id` is provided: wire an EXTRACTED_FROM edge from the EXTRACTION to the SOURCE
3. Wire an EXTRACTED_FROM edge from the instance to the EXTRACTION
4. If the instance is a FACT: recompute its derived `confidence` (the highest score among its extractions that state one). This runs wherever an item-to-EXTRACTION edge is written, so `attach_extraction` refreshes it too

One EXTRACTION node is created per item, giving per-item granularity. Multiple items can point to the same SOURCE but have distinct EXTRACTION nodes even when drawn from the same passage.

### Change Notifier

**Responsibilities**

- Maintain per-database hook registries
- Fire all registered hooks synchronously after each instance write
- Enrich events with human-readable names before delivery

**Event Structure**

Every event carries `type`, `database`, and `id`. Additional fields by type — schema events fire only on first declaration (idempotent repeats of `declare_*` don't re-fire):

| Type | Additional Fields |
|------|------------------|
| `class_declared` | `name`, `description` |
| `relation_declared` | `name`, `description`, `is_symmetric`, `domain`, `range` |
| `class_property_declared` | `name`, `description`, `class_id`, `class_name` |
| `relation_property_declared` | `name`, `description`, `relation_id`, `relation_name` |
| `class_subclass` / `relation_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |
| `property_attached` | `target_id`, `target_label` (`"CLASS"`\|`"RELATION"`), `property_id` |
| `relation_domain_attached` / `relation_range_attached` | `relation_id`, `relation_name`, `class_id`, `class_name` |
| `class_updated` / `relation_updated` / `property_updated` | `id` only |
| `class_deleted` / `relation_deleted` / `property_deleted` | `id` only |
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `entity_name`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `property_id`, `property_name`, `value`, `head_entity_name`, `tail_entity_name`, `relation_name` |
| `entity_class_attached` | `entity_id`, `entity_name`, `class_id`, `class_name` |
| `extraction_attached` | `element_id`, `extraction_id` |
| `entity_updated` / `fact_updated` | `id` only |
| `entity_property_updated` / `fact_property_updated` | `id` only |
| `entity_deleted` / `fact_deleted` | `id` only |
| `entity_property_deleted` / `fact_property_deleted` | `id` only |

Note the schema-event category above (`class_declared` through `relation_range_attached`) is fired by the **Schema Manager**, not the Knowledge Manager — the Change Notifier itself is shared infrastructure, not scoped to one layer. On the Provenance Tracker, only `attach_extraction` fires an event (`extraction_attached`); `add_source`, `add_extraction`, `update_source`, and `delete_source` fire no change events at all.

Hooks are scoped to a single database: a hook registered on database `"main"` does not fire for writes to any other database. There is also an internal *global* hook mechanism (firing across all databases) used exclusively to keep the Search Indexer in sync — it is not exposed on `KBSource`.

---

## 3. Ingestion

Sits above the schema and knowledge layers, like the Extraction Engine: it consumes both, is owned by neither, and writes only through their existing operations.

### Node Resolver

**Responsibilities**

- Resolve a name to ranked candidates, one label at a time (`resolve_class`/`resolve_relation`/`resolve_property`/`resolve_entity`)
- Rank by tier: exact name matches, then normalized matches (casefold, whitespace, punctuation, accent folding), then search hits — via the database's index when configured, else an unranked substring scan
- Never pick a winner — callers own the pick-or-raise policy

Only the Ingestion Manager is wired to it today; extraction and querying keep their own lookups.

### Ingestion Manager

**Responsibilities**

- Expose the ingest path: ten `assert_*` operations plus bulk `ingest`
- Resolve every operand read-only before writing (phase 1), running any needed judges (`ingestion/judges.py`: single-shot LLM calls for property placement, class naming, and description generation) — any failure raises `IngestionError` before anything is written
- Write through the existing `declare_*`/`add_*`/`attach_*` operations (phase 2), so change notification and search indexing fire exactly as for direct writes
- Create unresolved names at every level; widen a relation's domain/range for a non-conforming fact; dedupe known triples and equal property values by attaching new evidence
- Stamp every created node with an EXTRACTION recording the serialized assertion
- Under `schema_creation_mode="none"`, run no judges: descriptions stay empty and untyped entities attach to the `EVERYTHING` catch-all class

## 4. Extraction Engine

The Extraction Engine bridges free text and the structured knowledge base using an LLM as its reasoning core. It calls into the Knowledge Layer using the same operations available to any other caller, and only *reads* the Schema Layer — it never declares new schema. Growing the schema from the given text is the Schema Expansion Engine's job (§1, "Schema Expansion"); `KBSource.import_text` composes the two by calling `expand_schema` first (when `expand_schema=True`, the default) and this engine second.

### LLM Agent

**Responsibilities**

- Read the current schema and guiding queries at the start of each extraction run
- Identify entities, facts, and property values in the text
- Resolve each one to an existing CLASS/RELATION/PROPERTY, skipping anything with no match — **never** declares a new schema element itself
- Create instances with verbatim text spans for provenance
- Terminate when no further items can be extracted

**Tool Set**

The LLM Agent's tool set is fixed — no `schema_discovery_mode`-style conditional tool filtering, and no schema-declaration tool at all:

| Tool Group | Tools |
|------------|-------|
| Schema inspection | `get_schema`, `find_class`, `find_relation_type`, `find_entity`, `find_fact`, `search_schema` |
| Instance creation | `add_entity`, `add_fact`, `add_entity_property_value`, `add_fact_property_value` |
| Instance reuse | `link_extraction` — attach a new verbatim span to an *existing* entity/fact/literal instead of creating a duplicate |

`find_class`, `find_relation_type`, `find_entity`, and `find_fact` look an element up by exact name so the LLM Agent can check idempotently before creating an instance. `search_schema` is a fuzzy lookup distinct from exact-name `find_*`, used to resolve a mention to the right schema element when the wording doesn't match exactly. This is a second, independent `search_schema` tool from the one the Schema Expansion Engine offers for `expand_schema`, and from the one the Query Engine offers for `answer`/`validate`/`enrich` — all three exist, on different agents, for different purposes.

**Extraction Loop**

1. Fetch the current schema and present it to the LLM alongside the text and guiding queries
2. The LLM identifies the next item to extract and the verbatim span it comes from
3. The LLM checks whether the item already exists (`find_entity`/`find_fact`) and resolves it to a schema element (`search_schema`) — if no schema element matches, the item is skipped
4. If it already exists: the LLM calls `link_extraction` with its id to add the new span as further evidence, rather than creating a duplicate
5. If it's new and a matching schema element was found: the LLM calls the appropriate instance tool, passing the verbatim span as `text`
6. Repeat from step 2 until the LLM signals completion, or `max_turns` is reached

**Example**

Text: `"Alice joined IBM as a software engineer in June 1998."`
Guiding query: `"Where did Alice work?"`

Assuming `EMPLOYEE`, `COMPANY`, `WORKED_AT`, and `START_DATE` are already in the schema (declared directly, or via a prior/preceding `expand_schema` call), LLM sequence:
1. Checks schema — `EMPLOYEE`, `COMPANY`, `WORKED_AT` exist
2. Calls `add_entity("Alice", class="EMPLOYEE", text="Alice joined IBM...")`
3. Calls `add_entity("IBM", class="COMPANY", text="Alice joined IBM...")`
4. Calls `add_fact(head="Alice", relation="WORKED_AT", tail="IBM", text="Alice joined IBM...")`
5. Calls `add_entity_property_value(entity="Alice", property="START_DATE", value="1998-06-01", text="...in June 1998.")`
6. Reports no further items — loop exits

---

## 5. Search Indexer

The Search Indexer maintains a search index over schema elements and instance data so that `search_schema` and `search_data` can be answered by ranked lookup instead of an LLM call. It is optional per database: enabling it registers the indexer as a global change hook, so it stays in sync automatically as writes occur elsewhere in the system.

**Responsibilities**

- Maintain a BM25 keyword corpus, always available when search is enabled
- Maintain a vector index for semantic (embedding-based) search, when a vector store and embedding function are configured
- Incrementally update the index in response to change events (add / update / delete for classes, relations, properties, entities, and facts) rather than requiring a full rebuild after every write
- Support a full reindex on demand (`reindex_all`), rebuilding the corpus from every node currently in the Graph Store — used after bulk imports or when the embedding model changes

**Index Structure**

The indexer keeps two collections per database: `schema` (classes, relations, properties) and `data` (entities, facts). Each indexed node stores a composite text built from its own name/description plus relevant neighbors — for example, an entity's text includes its class name, and a fact's text includes its head, relation, and tail names — so a query can match on context the node doesn't literally contain in its own name.

**Search**

`search_schema` and `search_data` both accept a `mode` (`keyword` or `semantic`), an optional `type_filter`, and a result limit `k`. In `keyword` mode, results are ranked by BM25 score against the corpus; in `semantic` mode, results are ranked by embedding similarity via the configured vector store. Each result carries the node's ID, type, name, and description (plus head/relation/tail names for facts), and a similarity `score` — populated only in `semantic` mode; `keyword` mode results (ranked or, if search isn't enabled at all, unranked) always report `score=None`.

**Vector Backends**

Semantic search requires a vector source and embedding function at construction time, configured via `properties["search"]["mode"]="semantic"` (see [06-library.md](06-library.md) for the full `properties["search"]` shape). Four vector backends are supported, mirroring the Graph Store's own pluggability:

| Backend | Characteristics | Typical Use |
|---------|----------------|------------|
| Memory | In-process, non-persistent | Development, testing |
| File | Persisted to disk, single-process | Persistent semantic search without a server |
| ChromaDB | Embedded, persisted to disk | No separate server to run, still persistent |
| Redis | Shared, production vector store (via Redis Stack / RediSearch) | Multi-process or multi-host deployments |

---

## 6. Graph Store

The Graph Store persists all schema and instance data as a typed graph of nodes and edges. It is fully abstract: the same blue-kb operations run without modification against any supported backend.

**Responsibilities**

- Store and retrieve nodes by ID, label, and property values
- Store and retrieve edges by type and direction
- Support neighbor traversal (outgoing and incoming edges by type)
- Support node lookup by label and property filter

**Backends**

| Backend | Characteristics | Typical Use |
|---------|----------------|------------|
| Memory | In-process, non-persistent | Development, testing, short-lived pipelines |
| File | JSON file per database, persistent, multi-process on one machine (per-database write lock) | CLI default (`~/.blue-kb/`, or the profile's `BLUE_DATA_KB_SOURCE_DATA_DIR`), no server required |
| Neo4j | Persistent, concurrent, queryable with Cypher | Production deployments |
