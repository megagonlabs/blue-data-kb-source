# blue-kb — Decision Log

Architectural decisions are recorded here as they are made, throughout the design process.

---

## ADR-001 — Programming Language: Python

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Python 3.12+; the supported range is 3.12–3.13 (`requires-python = ">=3.12,<3.14"`, #168).

**Rationale:** Python is the dominant language in the AI/ML ecosystem, providing the broadest access to LLM clients, graph database clients, and data tooling relevant to blue-kb.

**Consequences:** All library classes, interfaces, and examples are written in Python. Type hints use features available from 3.12+ (e.g. `X | Y` union syntax).

---

## ADR-002 — Package Manager: pip + pyproject.toml

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Use `pip` with `pyproject.toml` for dependency management.

**Rationale:** Standard Python tooling with no additional toolchain overhead.

**Consequences:** Dependencies declared in `pyproject.toml`. No lockfile tool (e.g. Poetry, uv) required.

---

## ADR-003 — Graph Store: GraphSource Abstraction from blue-data

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** All graph persistence is delegated to `GraphSource`, the abstract base class provided by `blue-data`. blue-kb interacts only with the `GraphSource` interface and does not import any concrete backend directly.

**Rationale:** Decouples blue-kb from specific graph database implementations. Backend selection (Memory, Neo4j, or future backends) is a deployment concern, not a library concern. Follows the same abstraction-over-concrete pattern used throughout the blue-data ecosystem.

**Consequences:** blue-kb components call only `add_node`, `add_edge`, `get_node`, `get_neighbors`, and `match_nodes`. Concrete backends are provided by `blue-data`. Initial unit tests use `MemoryGraphSource`; production deployments use `Neo4jGraphSource`.

---

## ADR-004 — Backend Selection via Python MRO

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** The graph backend is selected by subclassing `KBSource` together with the chosen `GraphSource` concrete class using Python's method resolution order (MRO). No factory function, registry, or `__init__` parameter is used *for the mixing itself*.

**Rationale:** The MRO pattern requires zero conditional logic in `KBSource` and zero configuration beyond the class definition. It is idiomatic for mixin-based composition in Python and makes the backend choice explicit and static at the class level rather than dynamic at runtime.

**Consequences:** `KBSource` cannot be instantiated directly. The three mixin classes this decision implies (`MemoryKBSource`, `FileKBSource`, `NEO4JKBSource`) now ship pre-built with the library (`lib/src/blue_data_kb_source/{memory_kb_source,file_kb_source}.py`, `neo4j/neo4j_kb_source.py`) rather than being redefined ad hoc by every application. Building a genuinely new backend still follows this ADR's original pattern directly (`class MyKBSource(KBSource, MyGraphSource): pass`). **Addendum, post-implementation:** callers that need to pick one of the three shipped classes *dynamically* (from a config string, not a fixed choice at write-time — the CLI's `registry.json` and the MCP server's `BLUE_DATA_KB_SOURCE_GRAPH_BACKEND` env var) go through `DataSource.instantiate(full_class_path, ...)` (a dotted-path factory inherited from `blue-data`'s `DataSource`) rather than an `if`/`elif` importing each concrete mixin — this doesn't contradict "no registry" above, since it's a generic mechanism already provided by the base class, not one built for this decision. See ADR-019 for the follow-on decision this enabled: making every constructor argument beyond `(name, properties)` go away entirely, so `DataSource.instantiate()` alone is enough to fully reconstruct a search-enabled `KBSource`.

---

## ADR-005 — LLM Integration: OpenAI SDK Directly

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** The Extraction Engine calls the OpenAI API directly via the `openai` Python SDK. No provider-agnostic abstraction layer (e.g. LlamaIndex) is used.

**Rationale:** blue-kb's LLM usage is narrow and specific: a single tool-use loop in `ExtractionEngine.import_text`. The overhead of a full abstraction layer is not justified at this scope. The model is configurable per call via a `model` parameter. The integration is isolated to the Extraction Engine and has no impact on other components.

**Consequences:** `openai>=1.0` is an optional dependency (the `openai` extra). The Extraction Engine is the only blue-kb component with an LLM dependency. Swapping providers requires modifying the Extraction Engine.

---

## ADR-006 — Schema / Instance Separation

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Schema elements (CLASS, RELATION, PROPERTY nodes) and instance elements (ENTITY, FACT, LITERAL nodes) are represented as distinct node types in the same graph, strictly separated by label. No instance node carries schema-level semantics, and no schema node carries instance data.

**Rationale:** Treating the knowledge base as a formal typed database — with a clear schema layer above an instance layer — enables consistent querying, schema evolution, and introspection. It mirrors the relational database distinction between a table definition and its rows.

**Consequences:** All instance creation methods require the caller to reference existing schema nodes (class, relation, property IDs). The meta-schema describes this two-layer structure uniformly.

---

## ADR-007 — Schema Idempotency

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** All schema declaration methods (`declare_class`, `declare_relation_type`, `declare_class_property`, `declare_relation_property`) are idempotent: calling them with a name that already exists returns the existing node's ID without creating a duplicate or raising an error.

**Rationale:** LLM-driven extraction calls schema declaration methods without knowing whether the element already exists. Idempotency makes the extraction loop safe to run repeatedly and allows schema declarations to serve as upsert-style operations throughout the codebase.

**Consequences:** Schema methods perform a name lookup before creation. Domain/range edges are not re-wired on a second call for an already-existing relation; only the first call wires them. Callers must not assume a new node was created simply because a declaration method returned without error.

---

## ADR-008 — Per-item Provenance via EXTRACTION Nodes

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Each entity, fact, and literal created with a `text` argument receives its own dedicated EXTRACTION node capturing the verbatim text span. One EXTRACTION node is created per item, even when multiple items are drawn from the same passage.

**Rationale:** Per-item provenance allows downstream consumers to trace any single fact or property value back to its exact source span, not just to a document. This granularity is essential for validation, confidence scoring, and citation. A single top-level extraction node per document would lose span-level attribution.

**Consequences:** Write operations that include `text` create an additional EXTRACTION node and EXTRACTED_FROM edge in the graph. High-volume ingestion produces proportionally more provenance nodes. Graph queries that need provenance must traverse EXTRACTED_FROM edges.

---

## ADR-009 — Schema Evolution During Extraction

**Date:** 2026-07-07
**Status:** Superseded by ADR-020 (2026-09-04) — schema declaration and instance creation are no longer interleaved in one agent loop; kept below for historical context on why schema evolution during ingestion was wanted in the first place.

**Decision:** The LLM Agent in `ExtractionEngine` may declare new schema elements (classes, relations, properties) during an `import_text` run when the current schema does not cover the concepts it encounters. Schema declaration and instance creation are interleaved in the same agent loop.

**Rationale:** Requiring the schema to be fully defined before any extraction begins would make it impossible to grow the knowledge base from documents that introduce new domain concepts. Allowing the LLM to extend the schema on the fly — guided by `search_schema` to avoid redundant declarations — enables continuous schema evolution without manual intervention.

**Consequences:** The schema state at the end of an `import_text` run may differ from the state at the start. Callers must not assume the schema is stable across extraction calls. The LLM Agent must call `search_schema` before declaring new elements to avoid creating near-duplicates.

---

## ADR-010 — Database-Scoped Change Hooks

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Change hooks are registered and fired per database, not per `KBSource` instance. A hook registered on database `"main"` does not fire for writes to any other database held by the same instance.

**Rationale:** A single `KBSource` instance may manage multiple independent databases simultaneously. Scoping hooks to a specific database allows downstream consumers to subscribe precisely to the data they care about without filtering in the hook body. Cross-database notification is not a supported use case.

**Consequences:** `add_change_hook(database, fn)` and `remove_change_hook(database, fn)` each take an explicit `database` argument. Hook registries are stored per database internally. Hooks fire synchronously before the write operation returns to the caller.

---

## ADR-011 — Enriched Change Events

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Change events delivered to hooks carry human-readable names for all referenced entities, relations, and properties (e.g. `head_entity_name`, `relation_name`, `property_name`), resolved at fire time from the graph.

**Rationale:** Downstream consumers commonly need to display or log changes in human-readable form. Requiring each hook to perform its own graph lookups would be redundant and error-prone. Enriching events at fire time ensures every consumer receives a complete, immediately usable payload.

**Consequences:** `ChangeNotifier.fire` performs additional graph reads to resolve names before calling hooks. Name fields are `None` when the corresponding ID was not provided on the write call (e.g. a fact added without `relation_id`).

---

## ADR-012 — Consistent Meta-Schema

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** A fixed meta-schema (`fetch_meta_schema()`) describes blue-kb's own graph structure — node labels, edge types, and their relationships — using the same CLASS/RELATION/PROPERTY vocabulary as any user-defined schema. It is never stored in a user database.

**Rationale:** Agents and tools that need to navigate or generate blue-kb graph queries must understand the graph's own type system. Exposing the meta-schema through the same API as any other schema gives agents a uniform way to introspect the graph structure without hard-coding label names. It also demonstrates that blue-kb's schema language is expressive enough to describe itself.

**Consequences:** `fetch_meta_schema()` is a static method that returns a pre-built `DataSchema` object. It is not backed by any database. Changes to node labels or edge types in blue-kb's implementation require a corresponding update to the meta-schema.

---

## ADR-013 — External Interfaces: REST API, MCP Server, CLI

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** blue-kb exposes three external interfaces beyond the Python library: a REST API (FastAPI), an MCP server (official MCP SDK), and a CLI (Click + Rich). All three are thin adapters over the Python library with no business logic of their own.

**Rationale:** Different consumers require different integration points: Python applications embed the library directly; LLM agents use MCP tools; non-Python services and remote clients use REST; developers and operators use the CLI. Providing all four from the same library ensures consistent behavior across contexts.

**Consequences:** Each interface lives in its own `platform/` sub-package, independently versioned and deployed. REST and MCP were originally planned to bundle into a Docker image; as implemented, the MCP server runs over stdio transport (launched as a subprocess by its client), not as a network listener, so that specific packaging shape needs revisiting once the REST API is actually built — see [12-deployment-ops.md](12-deployment-ops.md) §6. **Correction (post-implementation):** the CLI is not a PyPI extra of `blue-data-kb-source` — it shipped as its own separate package, `blue-data-kb-cli`, depending on `blue-data-kb-source` rather than being an `extras_require` of it. Same for the MCP server (`blue-data-kb-mcp`). See [12-deployment-ops.md](12-deployment-ops.md) §2 for the actual packaging.

---

## ADR-014 — Async Throughout

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** All I/O-bound methods are `async def` across all layers — `KBSource`, `SchemaManager`, `KnowledgeManager`, `ProvenanceTracker`, and `ExtractionEngine`. Not just at interface boundaries.

**Rationale:** Graph store access and OpenAI API calls are both I/O-bound. Making the full call stack async avoids blocking and allows FastAPI and the MCP server to benefit from async concurrency without a sync-to-async bridge mid-stack. CLI bridges sync Click handlers to async internals via `asyncio.run()`.

**Consequences:** All public and private I/O methods use `async def`. Tests require `pytest-asyncio`. CLI commands use `asyncio.run()` at the entry point.

---

## ADR-015 — Type Checker: Pyright (Strict)

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Use `pyright` in strict mode for static type checking.

**Rationale:** Pyright has faster incremental analysis, better inference for modern Python 3.12+ syntax, and tighter VS Code integration than mypy. Strict mode enforces complete annotations across the codebase.

**Consequences:** All packages include a `py.typed` marker. `typeCheckingMode = "strict"` set in `pyproject.toml`. Type errors are treated as CI failures.

---

## ADR-016 — Distribution: PyPI Package + Docker Image

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** blue-kb is distributed as both a PyPI package (`blue-data-kb-source`) and a Docker image (`blue-data-kb-server`). The PyPI package exposes the Python library with optional extras. The Docker image bundles the REST API and MCP server.

**Rationale:** The two artifacts serve distinct consumers — the PyPI package for Python applications embedding blue-kb directly, and the Docker image for operators running blue-kb as a standalone service. Distributing both avoids forcing server dependencies on library-only consumers.

**Consequences:** The release pipeline must build and publish both artifacts on every semver tag. Version numbers are kept in sync between the two.

---

## ADR-017 — Observability: OpenTelemetry + Prometheus + Structured JSON Logs

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** blue-kb emits structured JSON logs to stdout, Prometheus-compatible metrics at `/metrics`, and OpenTelemetry traces via OTLP export. Tracing is opt-in (enabled when `OTEL_EXPORTER_OTLP_ENDPOINT` is set). Operators wire these into their own stacks.

**Rationale:** Self-hosted deployments use heterogeneous observability infrastructure. Emitting to standard interfaces (structured stdout, Prometheus, OTLP) keeps blue-kb decoupled from any specific platform. Knowledge base content is never included in span attributes.

**Consequences:** blue-kb takes a dependency on the OpenTelemetry Python SDK. All significant I/O boundaries (graph store operations, OpenAI calls, interface requests) must be instrumented with spans.

---

## ADR-018 — created/modified/original Timestamps; get_metadata Kept Off execute_query

**Date:** 2026-08-07
**Status:** Accepted

**Decision:** Every node carries `created`/`modified` timestamps and every edge carries `created` (set/refreshed automatically by the write methods, not caller-supplied); `SOURCE` additionally carries a caller-supplied `original` date. A new `get_metadata(database, node_id)` method (and matching `answer`/`validate` LLM tool, gated by `include_metadata=True`) surfaces these plus provenance (extraction text, source name) for a single entity/fact/property-value id. `get_metadata` is **not** added as an `execute_query` (`KB.*`) op — it stays a direct library method (and MCP/agent tool), unlike every other read/write operation, which is mirrored 1:1 into `execute_query`.

**Rationale:** `created`/`modified`/`original` give every interface (CLI `--show-detailed`, MCP, REST) a uniform way to answer "when was this added/changed, and where did it come from" without bespoke bookkeeping per node type. Keeping `get_metadata` off `execute_query` was an explicit call during implementation: `execute_query` is the sync dict-dispatch surface mirroring the full CRUD API 1:1, whereas `get_metadata` is a narrower, read-only convenience lookup whose primary consumers (CLI table rendering, the LLM tool) already call the async method or `KBSource.get_metadata` directly — adding it to `execute_query` would be surface-area growth with no consumer.

**Consequences:** `_fetch_compact`/`get_metadata`/the `get_metadata` LLM tool share one implementation (`blue_data_kb_source.knowledge.helpers`) rather than duplicating per-type lookup logic three times. `execute_query`'s op table intentionally has no `KB.GET_METADATA` entry — this is a deliberate omission, not an oversight, and should stay that way unless a concrete `execute_query`-only consumer appears.

---

## ADR-019 — Strict DataSource Signature Compliance; Search Config Moves Into `properties`

**Date:** 2026-08-15
**Status:** Accepted

**Decision:** Every `DataSource` derivative in this codebase — `KBSource` itself and its three shipped mixins (`MemoryKBSource`, `FileKBSource`, `NEO4JKBSource`) — matches `DataSource.__init__(self, name, properties=None)` exactly, with no additional constructor parameters. `KBSource.__init__` previously took `enable_search`/`vector_source`/`embedding_fn`/`vector_dim`/`corpus_dir` as explicit keyword arguments; these are now read from `properties["search"]` after `super().__init__(name, properties=properties)` runs. Two forms are accepted there: a declarative one (`"vector": {"_class": ..., "connection": {...}}`, `"embedding": {"provider": ..., "model": ...}}`) that `KBSource.__init__` constructs itself — the vector source via `DataSource.instantiate()`, the embedding function via a small internal provider-config helper — and a live-object one (`"vector_source"`, `"embedding_fn"` holding already-constructed objects directly) for in-memory/programmatic callers with nothing to persist. The live form takes precedence when both are present.

**Rationale:** A `KBSource` built any other way couldn't be dynamically reconstructed via `DataSource.instantiate(class_path, name, properties=stored_properties)` alone — a caller resolving a backend from config (the CLI's registry, the MCP server's env vars) would need its own bespoke translation code turning stored config into constructor kwargs, exactly the kind of duplicated per-caller logic `DataSource.instantiate()` exists to avoid. Moving search/vector/embedding construction into `KBSource.__init__` itself means any caller — present or future (a REST API, another script) — gets full search reconstruction for free from nothing but a `properties` dict, without writing that logic again. The live-object escape hatch keeps existing in-memory/programmatic usage (examples, tests, ad hoc scripts using deterministic fake embeddings) exactly as ergonomic as passing constructor kwargs was, since not every embedding function is expressible as `{"provider": ..., "model": ...}` declarative config.

**Consequences:** `platform/cli/src/blue_data_kb_cli/config.py` and `platform/mcp/src/blue_data_kb_mcp/config.py` both shrank — neither constructs a live `VectorSource` or embedding callable anymore; each just builds a `properties` dict (with `_class` paths for anything dynamic) and lets `KBSource.__init__` do the rest. Every example script, and `lib/tests/benchmarks/common/query_runner.py`, updated their `MemoryKBSource(...)`/`FileKBSource(...)` calls to pass `properties=` instead of the removed kwargs — a caller passing `directory=`/`enable_search=`/etc. directly now raises `TypeError` rather than being silently accepted. `KBSearchIndexer`'s own constructor is unaffected — it's an internal helper, not a `DataSource` derivative, and still takes `vector_source=`/`embedding_fn=`/`vector_dim=`/`corpus_dir=` as explicit kwargs; only the outward-facing `KBSource` constructor changed.

---

## ADR-020 — Schema Expansion Split into Its Own Pass (`expand_schema`), Out of `import_text`

**Date:** 2026-09-04
**Status:** Accepted — supersedes ADR-009

**Decision:** Schema growth from text is no longer interleaved with instance extraction inside `ExtractionEngine`'s single agent loop (ADR-009). It is now a separate LLM agent and pass, `SchemaExpansionEngine.expand_schema` (`schema/expansion.py`), owned internally by `SchemaManager` and exposed as `KBSource.expand_schema(database, text, *, depth=1, reference="llm", mcp_servers=None, dedup_mode="search", ...)`. `ExtractionEngine`'s tool set lost every `declare_*` tool — it now only reads the schema (`get_schema`/`find_class`/`find_relation_type`/`find_entity`/`find_fact`/`search_schema`) and writes instances; a mention with no matching schema element is skipped rather than triggering a declaration. `KBSource.import_text` gained an `expand_schema: bool = True` parameter and five `schema_expansion_*` parameters (`target`, `depth`, `mode`, `mcp_servers`, `dedup_mode`) that mirror `expand_schema`'s own keyword arguments one-to-one; with the default `True`, `import_text` calls `expand_schema` first (grounded by `text`/`queries`, filtered by `schema_expansion_target`) and then runs the (now schema-read-only) extraction agent — so a single `import_text` call still does both, but as two distinct, independently reusable passes rather than one interleaved loop. `import_text`'s old `schema_discovery_mode` parameter no longer exists; the equivalent knob is `expand_schema`'s own `dedup_mode`, reached through `import_text`'s `schema_expansion_dedup_mode`.

**Rationale:** Interleaving the two meant every extraction call paid for schema-declaration reasoning even when ingesting many documents against an already-stable schema, and made it impossible to grow or review a schema independently of any particular document (e.g. from a glossary, a batch of sample documents, or with tighter human review before any data is written). Splitting the concern also lets each pass use tools and a system prompt tuned to its one job — `expand_schema`'s prompt can spend its entire budget on the CLASS-vs-entity distinction and hierarchy placement (see ADR-021) without competing for the model's attention against instance-extraction instructions, and `import_text`'s prompt can assume the schema is already correct and focus entirely on resolving mentions to it. It also made a natural extension point for external grounding (`reference="mcp"`, see ADR-022) that only makes sense for the schema-growth half of the problem, not extraction.

**Consequences:** Existing callers relying on `import_text(..., schema_discovery_mode=...)` broke — the parameter is gone, not deprecated, since this is pre-1.0 and the whole feature area was being reworked in the same change. `lib/tests/benchmarks/extraction/` (keyed on `schema_discovery_mode`) is left known-broken pending a rework to score the two passes separately (see `lib/README.md` and [11-testing-strategy.md](11-testing-strategy.md) §12) — a deliberate scope cut, not an oversight. Callers who only want extraction against a schema they've already built up (by hand, or via a prior `expand_schema`/`import_text` run) now have a real way to skip schema-growth reasoning entirely: `expand_schema=False`. The MCP server gained a new `expand_schema` tool alongside `import_text` (26 tools total, up from 24), and `blue-kb` gained `blue-kb schema expand` alongside `blue-kb data extract` (which itself gained `--expand-schema`/`--no-expand-schema` and the mirroring `--schema-expansion-*` flags).

---

## ADR-021 — CLASS-vs-Entity Discipline and Mandatory `add_subclass_of` for Hierarchy, Enforced in `expand_schema`

**Date:** 2026-09-04
**Status:** Accepted

**Decision:** `expand_schema`'s system prompt draws a hard, explicitly-worked-example line between a CLASS (a *kind* that can have many members, e.g. `PERSON`) and an entity (one specific named referent mentioned in the text, e.g. "Alice") — the model is instructed to declare the general kind an individual belongs to, never a class named after the individual itself (no `CLASS ALICE`); the individual is created later, as an entity, by `import_text`'s extraction pass. Separately, `declare_relation_type` rejects a fixed set of taxonomic relation names (`IS_A`, `TYPE_OF`, `KIND_OF`, `SUBCLASS_OF`, `SUBTYPE_OF`, `SUBTYPE`, `INSTANCE_OF`, and `IS_`-prefixed variants — `_TAXONOMIC_RELATION_NAMES` in `schema/expansion.py`) outright, with an error message pointing the model at `add_subclass_of` instead; an is-a/type-of/kind-of/subclass-of relationship between two CLASSes or two RELATIONs may **only** be expressed by wiring `IS_SUBCLASS_OF` directly (`add_subclass_of` → `SchemaManager.add_is_subclass_of`), never by declaring a RELATION for it.

**Rationale:** Before this discipline existed, nothing stopped an extraction-style agent from declaring a CLASS per named individual it encountered (`CLASS ALICE`, `CLASS UNITED_NATIONS`) — collapsing the schema/instance distinction that ADR-006 established and defeating the entire point of having a typed schema layer separate from instance data. Similarly, nothing stopped the model from expressing "a poodle is a kind of dog" as a declared `RELATION IS_A` rather than the graph's actual hierarchy edge, which would make `IS_SUBCLASS_OF`-aware tooling (subclass/superclass traversal, `add_subclass_of` itself) blind to hierarchy the model expressed the "wrong" way. Both failure modes are subtle enough that prompting alone was judged insufficient for the second one — hence a hard rejection in `declare_relation_type`, not just prompt guidance — while the first relies on prompt discipline plus worked positive/negative examples in the system prompt, since there's no purely mechanical way to distinguish "a kind with many members" from "one specific named thing" from the name string alone.

**Consequences:** An agent (or a caller driving `declare_relation_type` directly) attempting to declare `IS_A`/`TYPE_OF`/etc. as a relation gets `{"error": "... declare_relation_type is not for this. Use add_subclass_of ..."}` back as a tool result rather than a silently-created bad relation — this is a hard constraint, not merely a suggestion, unlike the CLASS-vs-entity guidance which remains prompt-only and therefore imperfect (a sufficiently unusual text passage could still fool the model). `lib/tests/unit/schema/test_expansion.py` covers both: `test_expand_schema_prompt_warns_against_entity_classes` and the taxonomic-name rejection path. Every CLASS the agent declares is additionally nudged (prompt-only, same caveat) toward having at least one RELATION connecting it to the rest of the schema — an unconnected CLASS is treated as a likely sign a relation was missed, not a valid end state.

---

## ADR-022 — WordNet Grounding via a Standalone MCP Peer Server, Not a Bundled Dependency

**Date:** 2026-09-04
**Status:** Accepted

**Decision:** External lexical grounding for `expand_schema` (`reference="mcp"`) is provided by a fourth, fully independent package — `platform/wordnet-mcp` (`blue-data-wordnet-mcp`) — rather than by bundling `nltk`/WordNet as a direct dependency of `lib` or `platform/mcp`. `SchemaExpansionEngine` connects to it (or any other MCP server passed in `mcp_servers`) the same generic way regardless of which server it is: over stdio, sse, or streamable_http, listing tools via `session.list_tools()` and exposing them to the model name-prefixed `mcp__<server>__<tool>` (`schema/expansion.py`'s `_connect_mcp_servers`/`_call_mcp_tool`). `lib`'s own `mcp` extra (`pip install blue-data-kb-source[mcp]`) pulls in only the **client** SDK (`mcp.client.stdio`/`sse`/`streamable_http`, `ClientSession`) needed to speak to *any* such server — it has no idea `platform/wordnet-mcp` exists, the same way `platform/mcp`'s server-side `mcp[cli]` dependency has no idea what MCP clients will connect to it.

**Rationale:** WordNet is one plausible grounding source, not the only one — treating the MCP bridge as generic (any `{"transport": ..., ...}` config, any server) rather than hardcoding a WordNet-shaped integration keeps the door open for other lexical/ontology resources (a domain-specific taxonomy server, a different language's WordNet) without touching `SchemaExpansionEngine` at all. Bundling `nltk` and the (non-trivially sized, separately downloaded) WordNet corpus into `lib` or `platform/mcp` would tax every install of those packages for a feature (schema-expansion grounding) most callers won't use and could instead reach over MCP when they do. A separate package also keeps `blue-data-wordnet-mcp` trivially independently useful outside this codebase entirely — any MCP client can talk to it for WordNet lookups, not just `expand_schema`.

**Consequences:** `platform/wordnet-mcp` has zero dependency on `blue-data`/`blue-data-kb-source` — it's the only one of the four packages that doesn't import anything from the other three (see [07-repo-organization.md](07-repo-organization.md)). Using `reference="mcp"` requires the operator to separately install and make `blue-data-wordnet-mcp` (or whatever other MCP server they configure) reachable — `expand_schema` raises `SchemaError` immediately if `reference="mcp"` is requested with an empty `mcp_servers`, rather than silently no-op'ing. The WordNet corpus's own first-run network fetch (`nltk.download()`, lazy on first tool call) is `platform/wordnet-mcp`'s concern alone, invisible to `lib`/`platform/mcp`/`platform/cli`. `lib/tests/unit/schema/test_expansion.py`'s one MCP-integration test is `skipif`'d when `blue-data-wordnet-mcp` isn't installed on `PATH`, rather than mocking the MCP protocol — trading a little test coverage in CI configurations that skip it for proof that the generic bridge really drives a real external server end-to-end when it is installed.

---

## ADR-023 — Post-Declaration Domain/Range Widening

**Date:** 2026-09-01
**Status:** Accepted

**Decision:** `SchemaManager` gains `attach_relation_domain` / `attach_relation_range`: add one CLASS to an existing relation's DOMAIN / RANGE. Both deduplicate — adding a class already present is a silent no-op that fires no change event — and both are add-only: there is no detach counterpart, and the CLI exposes only `attach` and `list` under `blue-kb schema relation domain` / `blue-kb schema relation range`.

**Rationale:** ADR-007 makes declarations idempotent, with the recorded consequence that domain/range edges are wired only by the first `declare_relation_type` call — a re-declaration never touches them. That left the multi-valued domain/range model (a relation may hold several DOMAIN and RANGE edges) reachable only at declare time; nothing could extend an existing relation's applicability. The ingest path needs exactly that operation: its conformance checks (`is_fact_allowed`) treat a domain/range miss as a widening trigger rather than a rejection, and these methods are the write half of that pair. Add-only keeps the operation monotonic — widening can never invalidate an existing fact, so it requires no re-check of instance data.

**Consequences:** Extends ADR-007 rather than superseding it: declarations stay idempotent and still never re-wire domain/range; widening is a separate, explicit call. Because domains and ranges only grow, every widening is permanent until a detach counterpart exists — a class added by mistake, or by one bad ingest row, stays. Bulk ingest callers should therefore surface which relations they widened as their audit trail. Events `relation_domain_attached` / `relation_range_attached` fire on actual widenings only, so downstream consumers (search indexer, audit tooling) receive a true change signal.

---

## ADR-024 — Link-Only Writes: `attach_*`, and the Creation-and-Linking Rule

**Date:** 2026-09-04
**Status:** Accepted

**Decision:** Edge-only writes between existing nodes are named `attach_<host>_<thing>` (table in `08-coding-style.md` §Naming). This release adds `attach_entity_class` and `attach_extraction`, and renames the widening pair to `attach_relation_domain` / `attach_relation_range`. The rule behind them: a node is created together with its defining link when it has no meaningful state without one; where an unlinked state is meaningful the link may follow, and then a link-only operation must exist for it.

**Rationale:** The data model always allowed several `IS_INSTANCE_OF`, `DEFINE_PROPERTY`, `DOMAIN`, and `EXTRACTED_FROM` edges per node, but every public write created a node with its first link or nothing, so a second link was reachable only by writing the edge directly — which the ingest path did, bypassing the change hooks and leaving the search index stale. Naming by host keeps each attach readable beside its creating sibling and keeps `add_` meaning "create a node" outside the two peer-edge cases that predate the rule.

**Consequences:** Extends ADR-007 and ADR-023 rather than superseding them. `ProvenanceTracker` gains an optional notifier for `attach_extraction`'s event; its creating writes stay silent, a follow-up. No `detach_*` exists, so every attach is permanent; whether unlinking becomes a counterpart or confidence on the edge is left open.

---

## ADR-025 — Assertion-Level Confidence

**Date:** 2026-09-02
**Status:** Accepted

**Decision:** Confidence is a property of the assertion, not the fact: `add_extraction` takes `confidence` (`[0.0, 1.0]`, `null` = unstated, distinct from `0.0`) and `extraction_type` (`text_span` default | `external_record` | `assertion`; `inference` reserved), both stamped on the EXTRACTION node. `text` is required: every EXTRACTION records the assertion it stands for, serialized when the input is not prose, so `confidence` always has something to describe. An out-of-range value raises `ProvenanceError`; `confidence` without `text` on the instance creators raises `KnowledgeError`. A FACT's `confidence` is derived, never set directly — the `max` over its supporting extractions, ignoring `null`s, recomputed on each new `EXTRACTED_FROM` edge.

**Rationale:** ADR-008 already reifies every assertion as a per-item EXTRACTION node, the natural home for its strength. Storing it on the FACT would conflate independent evidence: two sources asserting the same triple at 0.6 and 0.9 are two assertions, not one number. `max` makes repeated ingestion converge — re-processing a source attaches another extraction and recomputes, never lowering a fact — and is monotone like the add-only widening of ADR-023. The same scale absorbs negation without a second shape: a source that denies a triple records low or zero confidence on the positive one, so the graph never needs a negated-fact form.

**Consequences:** Extends ADR-008 rather than superseding it: EXTRACTION nodes now carry strength and evidence form alongside the span, and existing nodes need no migration since absent fields read as `null` / `text_span`. `FACT.confidence` is a write-time cache — only a new `EXTRACTED_FROM` edge triggers recomputation, so `delete_source` can leave a stale value. Negation costs a distinction: nothing separates "the source says this is false" from "the source is unsure", and under `max` a refutation cannot pull down a fact another source supports, while one overconfident source sets the value alone. Conflicts accumulate rather than resolve: arbitration happens at read time in `answer` and `validate`, which weigh supporting against counter evidence per call and persist nothing, so every query redoes it. Age is the obvious next lever: EXTRACTION already carries `created`, so decay needs no new field.

---

## ADR-026 — The Ingest Path: Resolve-or-Create with Judges

**Date:** 2026-09-02
**Status:** Accepted

**Decision:** Structured knowledge whose operands are names, not ids, enters through a dedicated ingest path: ten `assert_*` operations plus bulk `ingest`. Every assert resolves its operands read-only first (ranked candidates: exact, then normalized, then search; one identity-certain hit auto-resolves, one uncertain hit auto-resolves unless a class expectation conflicts, two or more raise `IngestionError` with the candidates) and only then writes, through the same `declare_*`/`add_*`/`attach_*` operations direct callers use. What does not resolve is created at every level, schema included, with single-shot LLM judges naming classes, placing properties, and generating descriptions (`schema_creation_mode="llm"`, the default; `"none"` substitutes the `EVERYTHING` catch-all and empty descriptions). A non-conforming fact widens its relation's domain/range rather than being rejected; an already-known triple or equal property value dedupes by attaching new evidence; every created node carries an EXTRACTION recording the serialized assertion.

**Rationale:** The `declare_*`/`add_*` write path stays exactly as ADR-007 fixed it — idempotent declarations, exact frozen lookups, ids required — which is what makes it safe for the ingest path to sit in front of it: resolution and creation happen strictly before the write, so the write layer's atomicity and validation are untouched. ADR-008's per-item EXTRACTION is what lets every assert-created node carry its submitted statement (with ADR-025's confidence), and ADR-023's add-only widening is what lets conformance act as a trigger instead of a gate. Bulk `ingest` is a loop over the per-item asserts, so bulk and single-item behavior cannot drift.

**Consequences:** Extends ADR-007, ADR-008, and ADR-024 rather than superseding them: declarations stay idempotent, the exact lookups stay frozen, the link-only writes stay the one way to widen or attach, and the ingest path sits strictly in front of the write layer. A misspelled name becomes new vocabulary — normalization catches formatting variants, not typos. A single identity-uncertain match auto-resolves, so a name that merely search-matches one existing item is absorbed into it; the report (`written`/`deduped`/`skipped`/`failures`/`created`/`widened`) is the audit surface where such absorptions and inline schema creations stay visible. The extraction path is unchanged (its agent keeps its own lookups); rewiring it through the resolver is a later constructor change. `ingest` requires `OPENAI_API_KEY` out of the box because judges default on.

---

## ADR-027 — Extraction: Staged Pipeline, Effort Passes, Opt-In Chunked Concurrency

**Date:** 2026-09-08
**Status:** Accepted

**Decision:** `ExtractionEngine.import_text` is no longer one agentic loop with every instance-creation tool available at once. It now runs four sequential stages per pass — entities, relations, entity properties, fact properties — each its own agentic loop over a narrower tool set than the last, told what prior stages already resolved (by id) instead of rediscovering it; relations/properties are never offered `find_entity`/`add_entity` and are instructed to skip anything not in the resolved list rather than invent it. `effort` (`"low"` default | `"medium"` | `"high"` → 1/2/3 passes) repeats the whole four-stage pipeline over the same text, each pass after the first told a prior pass ran and to hunt for what it missed. `max_concurrency` (default 1, unchanged behavior) splits `text` into up to that many paragraph-aligned chunks and runs a full pipeline per chunk concurrently, sharing one `openai.AsyncOpenAI` client; every tool dispatch is wrapped in an `asyncio.Lock` so two chunks can't interleave a single dispatch's reads/writes.

**Rationale:** Smaller, single-purpose agentic tasks are more reliable than one large one — this mirrors the ADR-014-era split of schema expansion into its own pass ahead of extraction, applied one level deeper inside extraction itself. `effort` is a cheap way to trade cost for recall on the same text without a different mechanism, and it composes safely with staging for free: `find_entity`/`find_fact`/`link_extraction` already dedupe (ADR-007's frozen exact lookups plus the ingest-era normalized fallback added for extraction), so a repeat pass over the same text can only add what was missed, never duplicate what a prior pass already found *within the same sequential run*. Concurrency is opt-in rather than default because it's this codebase's first departure from the fully sequential execution ADR-026 relied on for bulk ingest, and the risk profile is different in kind, not degree.

**Consequences:** Splitting entity-property and fact-property extraction into two independent passes over the same text reopened a gap: `add_entity_property_value`/`add_fact_property_value` had never enforced that a property is actually defined for its owner's class/relation, so both stages could independently claim the same value for the wrong owner. Fixed by wiring the existing (previously write-path-unused) `is_property_allowed` check into both, the same pattern ADR-023's sibling `is_fact_allowed` already established for `add_fact`. Chunked concurrency introduces a genuinely new risk this codebase hasn't had before: the dispatch lock prevents low-level write interleaving, but it cannot reach across a chunk's separate `find_*`-then-`add_*` LLM turns, so two chunks that both mention the same entity or fact can each independently decide it doesn't exist yet and create it twice — a race that is structurally impossible today only because nothing runs concurrently. This is not solved here (no cross-chunk merge pass exists); it's bounded by keeping `max_concurrency=1` the default and documenting that chunking suits text whose chunks cover mostly distinct content, not one narrative repeating the same entities throughout. `max_turns` is now a per-stage, per-pass, per-chunk budget rather than a single total, so worst-case turn count scales with all three multiplied together.

---

## ADR-028 — Extraction: Class-Driven Entity Sweep, Gap Recovery, Forward-Carried Context, Verbatim-Span Grounding

**Date:** 2026-09-15
**Status:** Accepted

**Decision:** Extends ADR-027's staged pipeline rather than superseding it. Five changes to the entity, property, and creation paths: (1) the entity stage is handed the complete CLASS list — name-sorted, with ids and descriptions — and instructed to sweep it class by class, instead of scanning the text for mentions and then resolving each one's class via `search_schema`; (2) a conditional `entity_gaps` stage runs between entities and relations, re-sweeping only the classes the entity stage produced no instance for, and only when that stage resolved something — entities it recovers are merged by id into the list every later stage receives; (3) every creation call (`add_entity`, `add_fact`, `add_entity_property_value`, `add_fact_property_value`, `link_extraction`) whose `text` argument is not a verbatim span of the source (whitespace- and case-insensitive) is refused at dispatch, with the refusal returned as the tool result rather than raised; (4) every stage is handed everything *every* prior stage resolved, not just the one adjacent list it used to get — the fact-property stage gains the entities, the entity-property stage gains the facts and the values the fact stage just attached — accumulated in a per-chunk `_ExtractionContext` that also carries across `effort` passes, so a later pass is shown what earlier ones found rather than merely told they ran; (5) fact properties now run *before* entity properties.

**Rationale:** The text-first framing was producing a *named-entity* reading of "entity": across many runs, classes whose instances are ordinary nouns rather than proper names — `SKILL` being the clearest case — were never used once, even with the class declared, `search_schema` resolving it correctly, and the extraction query explicitly asking for it. The model issued no `find_entity` call for those mentions at all, so this was never a resolution failure to fix downstream; the candidates were never proposed. Inverting the loop attacks that directly, and costs nothing at runtime since the schema is already interpolated into every stage prompt — which also makes the `search_schema` round-trip for an already-listed class redundant. Gap recovery is the cheap bound on what the sweep still misses: one extra call per pass, over a worklist that is usually empty, instead of the one-call-per-class fan-out that would guarantee coverage but scale with schema size. The grounding check moves an assertion the benchmark scorer already made after the fact (`check_provenance`) to dispatch time, where it becomes a correction the model can act on rather than a score; a fabricated span is the signature of a value inferred rather than read. Carrying the full context forward is the generalization of what ADR-027 already did ad hoc for two adjacent stages: once the handoff is a single accumulating structure rather than a per-stage placeholder, "should stage N see what stage M produced?" costs nothing to answer. It also makes `_LATER_PASS_NOTE_TEMPLATE` honest — it had been telling pass 2 to focus on what pass 1 missed while showing it nothing pass 1 found. The stage reorder addresses the ownership gap ADR-027's own consequences section opened: `is_property_allowed` rejects a property not declared for that owner at all, but where one is declared on both a class and a relation, the first stage to claim a value keeps it, and that was unconditionally the entity stage. A fact is the more specific owner, so it should claim first.

**Consequences:** `STAGES` is no longer four entries, and `max_turns` now budgets a fifth stage on any pass where some class comes back empty — the common case for a broad schema — so worst-case turn count rises accordingly. The gap stage deliberately does not fire when the entity stage resolved nothing at all: there is no partial coverage to explain, and re-listing every class would reissue the entity prompt verbatim, which is what `effort`'s additional passes already do. Grounding is enforced only when `text` is actually passed; a call that omits it entirely keeps the older permissive path, so the check closes the fabricated-span vector but not the missing-span one (the benchmark scorer still tracks `missing` separately, and `add_*` still declares `text` optional). It also does not catch a value that is invented but attached to a real quoted sentence — an `AGE` the text never states, cited against a sentence that exists — which remains a prompt-level concern; the entity-property workflow now carries an explicit "never infer from world knowledge, from the entity's name, or from what is typical" instruction for exactly that case. The forward-carried context grows the prompt for every stage after the first, worst at the entity-property stage (entities + facts + fact property values) and at every pass after pass 1, where the carried block is rendered in full with no cap — unlike `related_data_k`, which bounds its list. On long chunks with many entities this is the change most likely to need a cap, and there is a real risk that showing a model a large "already done" block suppresses new extraction rather than sharpening it, which is the opposite of what the gap stage's framing taught. The reorder means anything relying on entity properties existing before fact properties run is now wrong; nothing in-tree did.

**Amended after a live run** (gpt-4o-mini, résumé text, `effort="medium"`), which exposed three problems the unit tests could not. (a) Gap coverage was judged against only the current pass, so on pass 2 — where the entity stage re-touches only what it re-reads — classes an earlier pass had filled looked empty and sent the gap stage hunting for entities that already existed; coverage now diffs against the carried context. (b) The verbatim-span test rejected 25 of 175 calls, and one stage burned its entire turn budget retrying: asked to quote a bullet list, the model reliably splices a heading onto a genuine fragment (`"Skills: Tools: JIRA, Trello"`) or elides a middle (`"* Technical: Basic Python"`), neither of which is a contiguous substring though both are real quotes. Grounding now accepts the span's words appearing in order within a bounded window (`3n + 5` source words for an `n`-word span), which admits those and still rejects a sentence stitched from scattered words, since that needs most of the document to match. (c) Duplicate entities, facts, and property values survived every prompt-level defence — the model simply called `add_entity` without `find_entity` first, most often on a later pass over text it had already covered. Exposing a `find_literal` tool (the missing counterpart of `find_entity`/`find_fact`, now also on `KnowledgeManager`/`KBSource`) was necessary but not sufficient, because looking before writing is advisory. Dedupe is therefore enforced in `_dispatch_tool`, where every write passes: `add_entity` collapses onto a normalized-name match, `add_fact` onto an existing (head, relation, tail) triple, and the property adds onto an existing *identical value* for that property — keyed on the value, because ADR-era multi-valued attributes (two phone numbers) are legitimate and keying on the property alone would silently break them. The surviving item takes the new text span and the call returns `{id, deduped: true}`. `KnowledgeManager`'s write semantics are untouched. Re-running the same example: 8 stages instead of 9, no spurious gap sweep, 2 non-quote rejections instead of 25, and no duplicate entities, facts, or literals.

---

## ADR-029 — Extraction: Resolving Id-Typed Tool Arguments Given as Names

**Date:** 2026-09-15
**Status:** Accepted

**Decision:** `ExtractionEngine._dispatch_tool` resolves every id-typed argument before the tool body runs. `class_id`, `relation_id`, `entity_id`, `head_entity_id`, `tail_entity_id`, `fact_id` and `property_id` each declare the node label they must name; an argument already holding a node of that label passes through untouched, a value that is not one is looked up by name (`find_class`, `find_relation_type`, the entity resolver's exact-plus-normalized tiers, and an owner-scoped property lookup), and only a value that resolves neither way errors. Lookups are exact-match only. `property_id` resolves against the properties declared for the owner named in the same call (`entity_id`/`fact_id`) before falling back to `SchemaManager.find_property`. Every rewrite is reported in the tool result as `coerced: {arg: "name -> id"}`. The id parameters in `_TOOLS` gained descriptions saying they take the id returned by a `find_*`/`search_schema` call and never a name. Separately, literal dedupe (ADR-028) now compares values in the form the write path stores them.

**Rationale:** Across three instrumented `import_text` runs, 133 tool calls failed because the model passed a name where an id belonged — `property_id` 87 of them, `relation_id` 28, `class_id` 18. Issue #92 reported the same from gpt-4o traces but centred on the entity-id arguments, which never failed in the gpt-4o-mini runs; the distribution is model-dependent, so the fix covers all of them. `property_id` is owner-scoped because property names are not unique across classes (`AGE` on both PERSON and ANIMAL) and a bare `find_property` returns whichever it finds first — the call already carries its owner, which turns that guess into a lookup. Lookups stay exact because this pass extracts against the schema as it stands: a fuzzy coercion of `COMPANIES` onto `COMPANY` would grant membership the schema never declared, which is precisely what the pipeline's "skip what the schema doesn't cover" rule exists to prevent. The coercion runs for read tools as well as writes, and the reads are the sharper motivation: `find_fact` handed a relation *name* did not fail, it returned `{id: null}`, so the model concluded the fact was new and created a second one — 19 of 39 `find_fact` calls across those runs, every one of them silently wrong. ADR-028's dispatch-level dedupe was treating that symptom.

**Consequences:** On the same fixture, bad-id failures went from 58 in a run to 0, total tool calls from 164 to 95–138 (the retry churn was most of the difference), and silent `find_fact` misses from 11 to 0. Two costs. First, coercion adds one `get_node` per id argument per call — trivial next to an LLM round-trip, but it is now on the path of every tool call including reads. Second, and more important: the id error was accidentally acting as a quality filter. Calls that used to fail on a malformed `property_id` now succeed, and some of them are semantically wrong — a run produced `Alice Chen.AGE = "Senior Project Manager"`, which previously never reached the graph because the id check rejected it first. No id-resolution layer can catch that, and the properties declared here carry no literal type to validate a value against, so nothing in this change addresses it; it is a model-quality problem that this change stops masking. The literal-dedupe fix is a straight bug fix — comparing a raw `"March 2019"` against a stored `"2019-03"` reported every re-extraction of a date as new — and a second comparison via `normalize_date` catches the same date sent twice under different `literal_type`s, safe because `normalize_date` is an identity on anything that isn't a date.

---

## ADR-030 — CLI: Profile-Driven Configuration

**Date:** 2026-09-15
**Status:** Accepted

**Decision:** The `blue-kb` CLI gains a `profile` command group — `add`, `remove`, `list`, `config`, `inspect`, `select` — and every configuration value now comes from the selected profile rather than from bespoke per-command flags and ad-hoc environment variables. A profile is a named section of `~/.blue-kb/.profiles` (INI, `0600`), selected via `~/.blue-kb/.selected.profile`. The CLI's local storage moved from `~/.kbdb` to `~/.kb` at the same time, matching the CLI's own name as the checklist's `~/.<cli>/` rule requires; the MCP server's file-backend default moved with it, since it is documented as being the same directory. The parameters themselves are declared in a JSON spec: each entry gives a `default`, a `prompt`, whether it is `required`, and a `cast` (`directory`, `file`, `str`, `int`, `float`, `bool`, `secret`). The first `blue-kb` command creates the `default` profile and selects it, so a fresh install needs no setup step; initialization is keyed on the profiles *file* rather than the section, so a profile the user removes on purpose is not resurrected, and the section is left empty so an unset value keeps tracking the spec default instead of being frozen at install time. `blue-kb profile config` takes an optional parameter and an optional value — no arguments walks the required parameters, a parameter alone prompts for it, a parameter and value sets it directly. Resolution order is **command option > environment variable > profile > spec default**, with per-parameter inheritance so `BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL` falls back to `BLUE_DATA_KB_SOURCE_LLM_MODEL` and then to `BLUE_DATA_LLM_MODEL`. The pre-profile `KBDB_DATA_DIR`/`KBDB_DATABASE` variables are removed outright — not deprecated — in favour of `BLUE_DATA_KB_SOURCE_*` names matching the family convention, since a profile is now the single source of configuration and a surviving alias would be a second one. The engine lives in `blue-data`'s `blue_data.utils.config`, not here.

**Rationale:** Implements the Profiles & configuration, Secrets, and Local storage sections of the CLI compliance checklist (#89), per #94. The engine belongs in `blue-data` because no sibling CLI has implemented profiles yet — this is the family's first, and "profile behaves consistently across repositories" is a checklist requirement that a copy-paste implementation would not survive. It is kept free of any CLI framework so `blue-data`'s dependencies (pandas, numpy, jsonpath-ng) are unchanged: it decides what to ask for and what a value resolves to, while prompting and command wiring stay per-CLI. Applying a profile means *exporting* the resolved values into the process environment, because a profile is a named set of environment variables — which is what lets the OpenAI SDK, `blue_data.config`, and the remaining Click `envvar=` options keep reading configuration exactly as they did. Property of the design worth stating: a resolver reads from a snapshot of the environment taken at construction, so it never observes its own exports and `inspect` can attribute each value to the tier that really set it. Secrets are never accepted as a command-line value (they would land in shell history) and are stored either encoded or as a `$VAR` reference; the encoding is obfuscation, and the `0600` file mode plus the reference form are the real protections. Database *backend* configuration stays with `blue-kb database register` / `blue-kb database config`, since it legitimately differs per database; only environment-level defaults and credentials move to the profile.

**Consequences:** `--model` no longer defaults to a hardcoded `gpt-4o` in six places; each LLM command resolves its own function-specific parameter, so one profile value can retarget every command while a single function stays pinnable. `blue-kb data extract` gained `--effort`, `--max-concurrency`, `--max-turns` and `--related-data-k`, which the library supported but the CLI never exposed — without them the profile's extraction defaults would not have been overridable per command, as the issue requires. `--expand-schema/--no-expand-schema` now defaults to `None` so "not given" is distinguishable from an explicit choice. Test isolation had to be rebuilt: it had rested on a single environment variable name, and renaming that variable silently redirected the CLI suite into the developer's real home directory — the fixture now clears every spec parameter (so an exported `OPENAI_API_KEY` cannot change what tests observe), snapshots the environment around each invocation (a real run gets a fresh process; in-process, exports would leak into the next command), and asserts afterwards that nothing was written to the real home. That last guard is the durable part: the failure it catches is invisible until someone inspects their own home directory. The MCP server was aligned to the same variable names as part of this change: `BLUE_DATA_KB_SOURCE_DIRECTORY` became `BLUE_DATA_KB_SOURCE_DATA_DIR` and `BLUE_DATA_KB_SOURCE_DEFAULT_MODEL` became `BLUE_DATA_KB_SOURCE_LLM_MODEL`, and its five LLM tools now resolve the same function-specific chain the CLI does (`BLUE_DATA_KB_SOURCE_ENRICHMENT_LLM_MODEL` was added to the spec for the MCP-only `enrich` tool). The server still reads only the environment and has no connection to a profile — the *names* are shared so a value means the same thing in both, the *source* is not. Because the names now live in two places, a test in the MCP suite asserts that every `BLUE_DATA_KB_SOURCE_*` variable the server reads is either a CLI spec parameter or a known per-database backend setting; without it the two would drift again, which is exactly how they diverged in the first place. The durable fix is a single shared spec both packages load, which would mean moving `profile_spec.json` out of the CLI package — worth doing if a third consumer appears. The local-storage directory moved from `~/.kbdb` to `~/.kb` with no migration path, which was free to do because nothing was deployed; a later move would need one. Two checklist items are deliberately left open — `--neo4j-password` remains a command-line option pending a separate change, and the `create`/`delete`/`show` → `add`/`remove`/`inspect` verb renames on the existing groups belong to #89's naming section.

---

## ADR-031 — CLI: Rename to `blue-kb` and Move Local Storage to `~/.blue-kb`

**Date:** 2026-09-22
**Status:** Accepted

**Decision:** The CLI's console script is renamed `kb` → `blue-kb`, and its local-storage directory `~/.kb` → `~/.blue-kb`. `CLI_HOME_DEFAULT` carries the new default and `BLUE_DATA_KB_SOURCE_HOME` still overrides it, so the `~/.<cli>/` rule continues to hold under the new name. The product is called `blue-kb` in prose throughout `design/`, `docs/`, the examples and every README, replacing the former "KBDB". The Python distribution (`blue-data-kb-cli`), the import package (`blue_data_kb_cli`), the library (`blue-data-kb-source`) and every `BLUE_DATA_KB_SOURCE_*` parameter name are unchanged.

**Rationale:** `kb` did not carry the family prefix the CLI checklist asks for (#89's first item), and it is a short, collision-prone name for a tool installed globally. `blue-kb` matches the `blue-*` convention that `blue-data-web-source` already follows with `blue-web`. The storage directory moves with the command because the checklist's rule is `~/.<cli>/` — leaving it at `~/.kb` would have broken the correspondence the directory is named for, and that correspondence is the only reason the path is not simply configurable-and-forgotten. Renaming prose "KBDB" to "blue-kb" removes a third name for the same thing: the repo was already using "blue-data-kb-source" for the distribution and "kb" for the tool, and "KBDB" appeared only in design documents.

**Consequences:** No migration path, and no alias: an existing install keeps working only until the package is reinstalled, at which point `kb` disappears and any state under `~/.kb` becomes invisible to the tool. Moving it is a `mv ~/.kb ~/.blue-kb`, but nothing does that automatically and nothing warns — acceptable because nothing is deployed, and the same reasoning ADR-030 used when it moved `~/.kbdb` to `~/.kb`. A later move would need a real migration. Prose in earlier ADRs now refers to the CLI as `blue-kb` even where the entry at the time said `kb`; that is deliberate, since those entries describe the same tool, but the *path values* in ADR-030 were left as the literals that were true then (`~/.kbdb` → `~/.kb`) rather than being swept, because rewriting them would record a migration that never happened. The MCP server's file-backend default is documented as the same directory and follows the move. 574 command invocations across documentation, 69 path references and 62 prose occurrences of "KBDB" were rewritten mechanically; the Python fixture named `kb` in the test suites is untouched, as are `kb.method()` call sites in examples.

---

## ADR-032 — MCP Server: Reference MCP Servers Are Operator Configuration, Not Tool Arguments

**Date:** 2026-10-09
**Status:** Accepted

**Decision:** The MCP server's `expand_schema` and `import_text` tools no longer take `mcp_servers` / `schema_expansion_mcp_servers`. With `reference="mcp"` (`schema_expansion_mode="mcp"`) they use the servers in `BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS`, a JSON list set by whoever launches the server, and return an error when none are configured. The library (`KBSource.expand_schema`/`import_text`) and the CLI (`--mcp-server`) keep accepting server definitions directly.

**Rationale:** A reference server entry is a command to execute (`stdio`) or a URL and headers to connect to (`sse`/`streamable_http`). Tool arguments come from the agent, so from anything that can steer it — including a prompt injection in a document it was asked to ingest. Accepting them made the tools a remote-code-execution path as the user running the server (#111), behind tools the server describes as "read and additive-write only". Where the definitions come from is what matters: in the library and the CLI the caller is the operator, so the same argument is safe there. Validating or allow-listing commands at the tool boundary was rejected: any allow-list would have to live in operator configuration anyway, and a fixed list configured once is simpler than a policy layered over caller input.

**Consequences:** MCP clients that passed `mcp_servers` must now have the operator set the environment variable; nothing is deployed, so there is no migration. An agent can still choose whether to use the reference servers (`reference`), just not which ones. The variable has no CLI profile counterpart because the CLI takes servers per command.

---

## ADR-033 — Neo4j Backend: `NEO4JKBSource` Owns Its Graph Primitives

**Date:** 2026-10-09
**Status:** Accepted

**Decision:** `NEO4JKBSource` implements the ten graph primitives the KB layer uses (`add_node`, `get_node`, `update_node`, `delete_node`, `match_nodes`, `add_edge`, `update_edge`, `delete_edge`, `match_edges`, `get_neighbors`) and the `GRAPH.*` dispatch itself, with its own Cypher, instead of inheriting them from blue-data's `NEO4JSource`. Results follow the memory/file contract exactly: `{id, label, properties}` nodes, `{node, edge, direction}` neighbors, insertion order, None-valued keys kept. Ids are stable strings in `_id`; every node is tagged with its logical database in `_kb_db`, and every query filters on it.

**Rationale:** The KB layer is written against the memory/file shapes, and `NEO4JSource` returns different ones (`node_id` for `id`, neighbors without `edge`/`direction`) and runs `execute_query` as raw Cypher — 49 of the unit tests failed on Neo4j, including `get_metadata`, schema merge and the extraction agent's `find_entity` (#120). Separately, `NEO4JSource` maps every logical database onto Neo4j Community's single database, so `main` and `copy` shared one graph — which the import/export round-trip and every multi-database use silently depend on not happening. Fixing the shapes in blue-data would be the better long-term home (one contract for every `GraphSource`), but it is a cross-repo change late in the 1.0 cycle; doing it here also lets the KB own the database isolation, which is KB-level semantics.

**Consequences:** The unit suite now runs against Neo4j (`KB_TEST_BACKEND=neo4j`, see 11-testing-strategy.md §9) and passes. Graph data written to Neo4j by the earlier `NEO4JKBSource` lacks `_id`/`_kb_db` and is not visible to this one — acceptable because nothing is deployed. `add_edge` to a missing endpoint creates nothing and doesn't raise, matching the memory/file backends, where such an edge is stored but never surfaces. A raw Cypher string passed to `execute_query` still runs as native Cypher over the whole physical database and sees the internal properties; it is given the logical name as `$db` to scope itself (#136). All queries and transactions run in one physical Neo4j database, `connection["database"]` (default `neo4j`; `BLUE_DATA_KB_SOURCE_NEO4J_DATABASE` for the MCP server), so a caller-supplied `database` can no longer select another physical database on Enterprise — `system` included (#132). If blue-data adopts one result contract, these overrides can shrink to the isolation logic.


## ADR-034 — CLI: The Neo4j Password Comes From the Profile, Not a Flag or the Registry

**Date:** 2026-10-11
**Status:** Accepted

**Decision:** `blue-kb` reads the Neo4j password from a secret profile parameter, `BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD` (the same name the MCP server reads from its environment), each time it connects to a Neo4j database. `--neo4j-password` on `database register` and `database config set` is refused with a message pointing there. `database list`, `config show` and `search config show` mask every credential key in a record. Separately, `--neo4j-uri` and `BLUE_DATA_KB_SOURCE_NEO4J_URI` keep their scheme under `connection["scheme"]` — what blue-data's `NEO4JSource` builds the driver URI from — and an unknown scheme is an error; both are parsed by one library helper, `blue_data_kb_source.neo4j.uri.neo4j_uri_connection`.

**Rationale:** `register --neo4j-password` handed the password to blue-data's `DataRegistry`, which strips credentials before saving, so every later command connected with an empty one; the only workaround, `config set --neo4j-password`, stored it in plaintext in `registry.json` and printed it back (#122). Profiles already handle secrets — encoded on disk in a `0600` file, `$VAR` references, masked by `profile inspect`, refused on the command line so they stay out of shell history — so the password belongs there rather than in a second, weaker mechanism. The URI scheme had been stored under `protocol`, which the driver never reads (and `NEO4JKBSource` overwrites), so `neo4j+s://` was dialled as plain `bolt://`: unencrypted, and unable to reach TLS-only servers such as Aura (#121). Refusing an unknown scheme keeps a typo from falling back to an unencrypted connection.

**Consequences:** One password per profile: databases on Neo4j servers with different passwords need different profiles (or the environment variable set per shell). Scripts that passed `--neo4j-password` now fail with instructions instead of silently connecting without a password. A registry record written by the old `config set --neo4j-password` still holds the plaintext password; it is masked on output, still used when the profile sets none, and removed by re-registering the database. A database registered before this change with a TLS URI lost its scheme and must be given `--neo4j-uri` again. This closes the open item ADR-030 left ("`--neo4j-password` remains a command-line option pending a separate change").

## ADR-035 — Schema: Properties Declare the Type of Value They Hold

**Date:** 2026-10-11
**Status:** Accepted

**Decision:** A PROPERTY may carry a `literal_type` (`NUMBER`, `DATE`, `BOOLEAN` or `STRING`), set by `declare_class_property`/`declare_relation_property`/`update_property` and the matching CLI (`--type`) and MCP (`literal_type`) options. The value write path (`add_literal`, so every `add_*_property_value` and the `update_*_property_value` calls) casts a value to the declared type and refuses one that doesn't fit with `KnowledgeError`; a declared type overrides the caller's `literal_type`. A DATE must normalize to ISO (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`). The schema renders the type (`AGE: number`), schema export/import carries it, and schema expansion's property tools require one. The extraction agent's `search_schema` results show it, and the property stages are told to attach only a value of the kind the property describes and to skip rather than force a fit. An untyped property is unchanged: it renders as `str`, accepts any value, and stores the inferred type, as before.

**Rationale:** `is_property_allowed` only answers whether a property is declared for the owner, so a job title landed in `AGE` because `AGE` is declared on `PERSON` (#95), and the schema the model saw said every property was a string. Of the directions the issue laid out — declared types, types inferred from existing values, or prompt guidance alone — only a declared type gives a check that doesn't depend on the model; the prompt guidance is added alongside because a value of the right type can still be the wrong value (a skill in a STRING `INDUSTRY`).

**Consequences:** A STRING property still accepts any text, so the type check catches category errors across types, not within STRING; that part rests on the prompt. A fuzzy date ("Q3 2026", "early 2019") is refused by a DATE property rather than stored unnormalized. Re-declaring a property never changes a type it already has. Schema exported before this change carries every attribute as `str`, which imports as untyped. Existing extraction benchmark cases keep untyped seed properties so the baseline stays comparable; `fit_value_to_property` covers the typed case.

## ADR-036 — KBSource Meets blue-data's DataSource Contract

**Date:** 2026-10-21
**Status:** Accepted

**Decision:** `KBSource` satisfies the `DataSource` contract blue-data defines in `design/14-datasource-contract.md`, and `tests/unit/test_conformance.py` runs blue-data's conformance kit (`blue_data.testing.conformance.DataSourceConformance`) against the memory, file and Neo4j backends. Three changes make it pass. `fetch_database_schema(database)` is synchronous and returns `DataSchema.to_json()`'s dict, matching the base signature (`-> dict`); callers that need the object use `DataSchema.from_json(...)`, and the engines keep taking a `DataSchema` from `SchemaManager.fetch_database_schema`. Every `execute_query` result is a `list[dict]` — normalized at that one boundary, for `KB.*` and `GRAPH.*` operations and raw Cypher alike: a write's id becomes `[{"id": ...}]`, a write returning nothing `[]`, a single record a one-row list, search hits one dict each. The sync entry points (`execute_query`, `execute_query_nl`, `fetch_database_schema`) run the KB's async operations through one bridge that works inside a running event loop, on a worker thread, where `asyncio.run` raised `RuntimeError`. `execute_query_nl` accepts the registry's `llm_fn` but does not use it — an allowed exception — and takes its model from `optional_properties["model"]`, then the same `BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL` → `BLUE_DATA_KB_SOURCE_LLM_MODEL` → `BLUE_DATA_LLM_MODEL` chain the CLI and MCP server use, then `gpt-4o`, instead of always gpt-4o.

**Rationale:** The registry, blue-data-ops and the planner call sources through `DataSource`'s synchronous methods and treat query results as rows (#147, cross-repo XR-02). An `async` override handed them a coroutine, a bare id or a `DataSchema` broke row handling, and `asyncio.run` failed whenever the host was itself async. `llm_fn` is a plain prompt→text function, and answering a question here is a tool-calling agent loop over the KB, which it cannot drive; the contract lets a source ignore an argument it does not use.

**Consequences:** `fetch_database_schema` is no longer awaitable and no longer returns a `DataSchema` — a breaking change for library callers, made before 1.0; the CLI, MCP server, examples and tests were updated. `execute_query` results changed shape: callers that took the bare id of a write read `rows[0]["id"]`. The direct Python methods (`add_entity`, `search_schema`, `fetch_database_data`, ...) are unchanged. Inside a running event loop the sync entry points block that loop's thread while the operation runs on a worker thread; async code should keep calling the async methods directly.

## ADR-037 — Neo4j Backend: The Store Enforces Name and Id Uniqueness

**Date:** 2026-10-22
**Status:** Accepted

**Decision:** `NEO4JKBSource.setup_schema()` creates Neo4j uniqueness constraints on (`_kb_db`, `name`) for CLASS and RELATION nodes and on (`_kb_db`, `_id`) for every KB label (CLASS, RELATION, PROPERTY, ENTITY, FACT, LITERAL, SOURCE, EXTRACTION). It is idempotent (`CREATE CONSTRAINT ... IF NOT EXISTS`) and runs once per instance, from `create_database` or before the first write, so no separate initialization step is needed. A write the constraints refuse raises `DuplicateNodeError`; `declare_class` and `declare_relation_type` catch it and return the node another writer created between their lookup and their write. Where existing data already violates a constraint, `setup_schema` raises `KBError` naming the duplicates and deletes nothing.

**Rationale:** The KB enforced uniqueness only by looking before writing, which two concurrent writers — two processes, or two MCP calls — both pass (#82). The issue asked for unique `CLASS.name`, `RELATION.name`, `ENTITY.name` and `PROPERTY.name`; since ADR-033 every logical KB database shares one physical Neo4j database, so each key includes `_kb_db`, and entity and property names are left out on purpose: entity names are not unique (two "Apple"s), and a property name is unique only per owner (AGE on both PERSON and ANIMAL). Composite uniqueness constraints are available in Neo4j Community. The id constraints double as indexes for the KB's most frequent lookup, by (`_kb_db`, `_id`).

**Consequences:** On Neo4j, concurrent declares of the same class or relation now converge on one node instead of creating two; the memory and file backends are unchanged (the file backend's per-primitive lock from #127 does not cover a KB-level declare). A database holding duplicates from before this change needs them merged (`consolidate_schema`) before the constraints can be created. A raw Cypher write that breaks a constraint fails with the driver's `ConstraintError`.
