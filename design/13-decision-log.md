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
