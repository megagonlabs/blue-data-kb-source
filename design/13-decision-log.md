# KBDB — Decision Log

Architectural decisions are recorded here as they are made, throughout the design process.

---

## ADR-001 — Programming Language: Python

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** Python, minimum version 3.10.

**Rationale:** Python is the dominant language in the AI/ML ecosystem, providing the broadest access to LLM clients, graph database clients, and data tooling relevant to KBDB.

**Consequences:** All library classes, interfaces, and examples are written in Python. Type hints use features available from 3.10+ (e.g. `X | Y` union syntax).

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

**Decision:** All graph persistence is delegated to `GraphSource`, the abstract base class provided by `blue-data`. KBDB interacts only with the `GraphSource` interface and does not import any concrete backend directly.

**Rationale:** Decouples KBDB from specific graph database implementations. Backend selection (Memory, Neo4j, or future backends) is a deployment concern, not a library concern. Follows the same abstraction-over-concrete pattern used throughout the blue-data ecosystem.

**Consequences:** KBDB components call only `add_node`, `add_edge`, `get_node`, `get_neighbors`, and `match_nodes`. Concrete backends are provided by `blue-data`. Initial unit tests use `MemoryGraphSource`; production deployments use `Neo4jGraphSource`.

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

**Rationale:** KBDB's LLM usage is narrow and specific: a single tool-use loop in `ExtractionEngine.import_text`. The overhead of a full abstraction layer is not justified at this scope. The model is configurable per call via a `model` parameter. The integration is isolated to the Extraction Engine and has no impact on other components.

**Consequences:** `openai>=1.0` is an optional dependency (the `openai` extra). The Extraction Engine is the only KBDB component with an LLM dependency. Swapping providers requires modifying the Extraction Engine.

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

**Decision:** A fixed meta-schema (`fetch_meta_schema()`) describes KBDB's own graph structure — node labels, edge types, and their relationships — using the same CLASS/RELATION/PROPERTY vocabulary as any user-defined schema. It is never stored in a user database.

**Rationale:** Agents and tools that need to navigate or generate KBDB graph queries must understand the graph's own type system. Exposing the meta-schema through the same API as any other schema gives agents a uniform way to introspect the graph structure without hard-coding label names. It also demonstrates that KBDB's schema language is expressive enough to describe itself.

**Consequences:** `fetch_meta_schema()` is a static method that returns a pre-built `DataSchema` object. It is not backed by any database. Changes to node labels or edge types in KBDB's implementation require a corresponding update to the meta-schema.

---

## ADR-013 — External Interfaces: REST API, MCP Server, CLI

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** KBDB exposes three external interfaces beyond the Python library: a REST API (FastAPI), an MCP server (official MCP SDK), and a CLI (Click + Rich). All three are thin adapters over the Python library with no business logic of their own.

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

**Rationale:** Pyright has faster incremental analysis, better inference for modern Python 3.10+ syntax, and tighter VS Code integration than mypy. Strict mode enforces complete annotations across the codebase.

**Consequences:** All packages include a `py.typed` marker. `typeCheckingMode = "strict"` set in `pyproject.toml`. Type errors are treated as CI failures.

---

## ADR-016 — Distribution: PyPI Package + Docker Image

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** KBDB is distributed as both a PyPI package (`blue-data-kb-source`) and a Docker image (`blue-data-kb-server`). The PyPI package exposes the Python library with optional extras. The Docker image bundles the REST API and MCP server.

**Rationale:** The two artifacts serve distinct consumers — the PyPI package for Python applications embedding KBDB directly, and the Docker image for operators running KBDB as a standalone service. Distributing both avoids forcing server dependencies on library-only consumers.

**Consequences:** The release pipeline must build and publish both artifacts on every semver tag. Version numbers are kept in sync between the two.

---

## ADR-017 — Observability: OpenTelemetry + Prometheus + Structured JSON Logs

**Date:** 2026-07-07
**Status:** Accepted

**Decision:** KBDB emits structured JSON logs to stdout, Prometheus-compatible metrics at `/metrics`, and OpenTelemetry traces via OTLP export. Tracing is opt-in (enabled when `OTEL_EXPORTER_OTLP_ENDPOINT` is set). Operators wire these into their own stacks.

**Rationale:** Self-hosted deployments use heterogeneous observability infrastructure. Emitting to standard interfaces (structured stdout, Prometheus, OTLP) keeps KBDB decoupled from any specific platform. Knowledge base content is never included in span attributes.

**Consequences:** KBDB takes a dependency on the OpenTelemetry Python SDK. All significant I/O boundaries (graph store operations, OpenAI calls, interface requests) must be instrumented with spans.

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

**Consequences:** Existing callers relying on `import_text(..., schema_discovery_mode=...)` broke — the parameter is gone, not deprecated, since this is pre-1.0 and the whole feature area was being reworked in the same change. `lib/tests/benchmarks/extraction/` (keyed on `schema_discovery_mode`) is left known-broken pending a rework to score the two passes separately (see `lib/README.md` and [11-testing-strategy.md](11-testing-strategy.md) §12) — a deliberate scope cut, not an oversight. Callers who only want extraction against a schema they've already built up (by hand, or via a prior `expand_schema`/`import_text` run) now have a real way to skip schema-growth reasoning entirely: `expand_schema=False`. The MCP server gained a new `expand_schema` tool alongside `import_text` (26 tools total, up from 24), and `kb` gained `kb schema expand` alongside `kb data extract` (which itself gained `--expand-schema`/`--no-expand-schema` and the mirroring `--schema-expansion-*` flags).

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

**Decision:** `SchemaManager` gains `attach_relation_domain` / `attach_relation_range`: add one CLASS to an existing relation's DOMAIN / RANGE. Both deduplicate — adding a class already present is a silent no-op that fires no change event — and both are add-only: there is no detach counterpart, and the CLI exposes only `attach` and `list` under `kb schema relation domain` / `kb schema relation range`.

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
