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

**Consequences:** `KBSource` cannot be instantiated directly. The three mixin classes this decision implies (`MemoryKBSource`, `FileKBSource`, `NEO4JKBSource`) now ship pre-built with the library (`lib/src/blue_data_kb_source/{memory_kb_source,file_kb_source}.py`, `neo4j/neo4j_kb_source.py`) rather than being redefined ad hoc by every application. Building a genuinely new backend still follows this ADR's original pattern directly (`class MyKBSource(KBSource, MyGraphSource): pass`). **Addendum, post-implementation:** callers that need to pick one of the three shipped classes *dynamically* (from a config string, not a fixed choice at write-time — the CLI's `registry.json` and the MCP server's `BLUE_DATA_KB_SOURCE_GRAPH_BACKEND` env var) go through `DataSource.instantiate(full_class_path, ...)` (a dotted-path factory inherited from `blue-data`'s `DataSource`) rather than an `if`/`elif` importing each concrete mixin — this doesn't contradict "no registry" above, since it's a generic mechanism already provided by the base class, not one built for this decision.

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
**Status:** Accepted

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
