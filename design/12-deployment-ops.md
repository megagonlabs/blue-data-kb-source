# KBDB — Deployment / Ops

## Overview

KBDB is distributed today as four independent PyPI packages, installed and run locally or embedded in another Python process — there is no server deployment, container image, or CI/CD pipeline yet. The sections below are split into **Today** (what actually exists and how to use it) and **Planned** (the target operational picture for when a server-shaped deployment — REST API, Docker image, managed observability — is built). Do not treat anything under a "Planned" heading as available now.

---

## 1. Versioning

KBDB follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`). Each of the four packages below declares its own version in its own `pyproject.toml` and is versioned independently — there is no single repo-wide version today.

- **PATCH** — bug fixes, no interface changes
- **MINOR** — backwards-compatible new features
- **MAJOR** — breaking changes to a package's public API or the graph data model

---

## 2. Packages (Today)

Four separate PyPI packages, none of them published yet — installed today from source via each package's `scripts/install.sh`:

| Package | Path | Console script | Depends on |
|---|---|---|---|
| `blue-data-kb-source` | `lib/` | — (library only) | `blue-data` (local path) |
| `blue-data-kb-cli` | `platform/cli/` | `kb` | `blue-data-kb-source>=0.1.0` |
| `blue-data-kb-mcp` | `platform/mcp/` | `blue-data-kb-mcp` | `blue-data-kb-source>=0.1.0` |
| `blue-data-wordnet-mcp` | `platform/wordnet-mcp/` | `blue-data-wordnet-mcp` | none (`mcp[cli]`, `nltk` only — no `blue-data-kb-source`/`blue-data` at all) |

The CLI and MCP server are **not** optional extras of `blue-data-kb-source` — they are their own packages, each depending on it. This is deliberate (see [07-repo-organization.md](07-repo-organization.md)'s rationale on independently deployable `platform/` services) but means there is no single `pip install blue-data-kb-source[cli]`/`[server]` incantation; install each package you need separately. `blue-data-wordnet-mcp` goes a step further — it's not a `KBSource` adapter at all, just an optional peer server `expand_schema(reference="mcp")` can connect to for WordNet grounding, so it can be installed and run entirely independently of the other three.

`blue-data-kb-source`'s real optional extras (`lib/pyproject.toml`):

```
pip install blue-data-kb-source                  # library only, MemoryGraphSource/FileGraphSource
pip install blue-data-kb-source[neo4j]           # + Neo4j graph backend
pip install blue-data-kb-source[openai]          # + LLM schema expansion/extraction/querying (expand_schema, import_text, answer, validate, enrich)
pip install blue-data-kb-source[search]          # + semantic search vector backends
pip install blue-data-kb-source[mcp]             # + MCP client SDK, for expand_schema(reference="mcp")
pip install blue-data-kb-source[all]             # everything
pip install blue-data-kb-source[dev]             # + pytest, pytest-asyncio, etc.
```

None of the four packages are published to PyPI yet — `pip install blue-data-kb-source` as written above doesn't resolve today. Install from the local checkout instead:

```bash
cd lib && bash scripts/install.sh                 # installs blue-data (local path) + this package
cd ../platform/cli && bash scripts/install.sh      # + kb CLI
cd ../platform/mcp && bash scripts/install.sh      # + blue-data-kb-mcp server
cd ../platform/wordnet-mcp && bash scripts/install.sh  # + blue-data-wordnet-mcp server (independent of the above)
```

---

## 3. Configuration (Today)

The CLI is configured by **profiles** — `~/.kb/.profiles`, managed with `kb profile config`; the MCP server has no profile support and is configured purely by environment variables. The two share **variable names** deliberately — a `BLUE_DATA_KB_SOURCE_*` value means the same thing in both — but not configuration: the MCP server never reads the CLI's profiles or its `registry.json`, and takes every value from the environment.

### CLI (`kb`) — `platform/cli/src/blue_data_kb_cli/config.py`

| Variable | Default | Description |
|---|---|---|
| `BLUE_DATA_KB_SOURCE_HOME` | `~/.kb` | CLI state: `.profiles`, `.selected.profile`, `.selected.database`. Environment-only — it is where the profiles themselves are read from, so it cannot come from one |
| `BLUE_DATA_KB_SOURCE_DATA_DIR` | `~/.kb` | Directory holding `registry.json` (per-database backend/search config) and file-backed graph data |
| `BLUE_DATA_KB_SOURCE_DATABASE` | `default` | Which database `--database` falls back to, when no `kb database select` has been made |
| `OPENAI_API_KEY` | — | Required for `kb schema expand`/`kb data extract`/`answer`/`validate`, and for the `assert`/`ingest` commands under `--schema-creation-mode llm` (the default; `none` needs no key) |
| `BLUE_DATA_LLM_MODEL`, `BLUE_DATA_KB_SOURCE_*_LLM_MODEL` | `gpt-4o` | The model each LLM command uses. A function-specific value falls back to `BLUE_DATA_KB_SOURCE_LLM_MODEL`, then to `BLUE_DATA_LLM_MODEL` |
| `BLUE_DATA_KB_SOURCE_EXTRACTION_*` | — | Extraction defaults (`EFFORT`, `MAX_CONCURRENCY`, `MAX_TURNS`, `RELATED_DATA_K`, `EXPAND_SCHEMA`), each overridable by the matching `kb data extract` option |
| `BLUE_DATA_CLI_FORMAT`, `BLUE_DATA_CLI_LOG_LIMIT` | `table`, `80` | Family-wide CLI output defaults |

Every row except `BLUE_DATA_KB_SOURCE_HOME` is normally set in a profile rather than exported — `kb profile config` writes them to `~/.kb/.profiles` (mode `0600`), and the CLI exports the resolved values into its own environment at startup so everything that already reads configuration from the environment keeps working unchanged. Exporting one directly still wins, which is the documented precedence: **command option > environment > profile > default**. The full parameter table is in [docs/cli.md](../docs/cli.md#profiles-and-configuration); the engine is `blue_data.utils.config` in the `blue-data` repo, shared with the rest of the CLI family.

Per-database backend and search configuration (graph backend, Neo4j connection, search mode, vector backend) is **not** environment-variable-driven — it's set via `kb database register ...` flags and persisted in `{BLUE_DATA_KB_SOURCE_DATA_DIR}/registry.json`. See [09-api-interface-spec.md](09-api-interface-spec.md) §4 for the full flag reference.

### MCP server (`blue-data-kb-mcp`) — `platform/mcp/src/blue_data_kb_mcp/config.py`

A completely separate `BLUE_DATA_KB_SOURCE_*` namespace, since the server owns one `KBSource` singleton for its process lifetime rather than a multi-database registry:

| Variable | Default | Description |
|---|---|---|
| `BLUE_DATA_KB_SOURCE_GRAPH_BACKEND` | `file` | `file` \| `neo4j` |
| `BLUE_DATA_KB_SOURCE_DATA_DIR` | `~/.kb` | File backend directory |
| `BLUE_DATA_KB_SOURCE_NEO4J_URI` / `_USER` / `_PASSWORD` | — | Neo4j connection, when `GRAPH_BACKEND=neo4j` |
| `BLUE_DATA_KB_SOURCE_SEARCH` | `none` | `none` \| `bm25` \| `semantic` |
| `BLUE_DATA_KB_SOURCE_VECTOR_BACKEND` | `memory` | `memory` \| `file` \| `chromadb` \| `redis`, when `SEARCH=semantic` |
| `BLUE_DATA_KB_SOURCE_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model, when `SEARCH=semantic` |
| `BLUE_DATA_KB_SOURCE_LLM_MODEL` | `gpt-4o` | Default model for `answer`/`validate`/`enrich`/`expand_schema`/`import_text` |
| `OPENAI_API_KEY` | — | Required for LLM features |

Full table with examples: [platform/mcp/README.md](../platform/mcp/README.md) Configuration reference. Pointing both `BLUE_DATA_KB_SOURCE_DATA_DIR` and `BLUE_DATA_KB_SOURCE_DATA_DIR` at the same path gives the CLI and MCP server practical (not automatic) access to the same file-backed data — they don't otherwise coordinate.

The MCP server runs over **stdio transport** (`mcp.run()`, launched as a subprocess by an MCP client like Claude Desktop or Claude Code) — it is not a network service and has no port to configure.

### WordNet MCP server (`blue-data-wordnet-mcp`) — `platform/wordnet-mcp/src/blue_data_wordnet_mcp/server.py`

No environment variables at all — the server is fully self-contained, holding no `KBSource` and needing no backend configuration. It runs over stdio the same way the MCP server does, either standalone (`blue-data-wordnet-mcp`) or spawned automatically by `expand_schema(reference="mcp", mcp_servers=[{"transport": "stdio", "command": "blue-data-wordnet-mcp"}])`/`kb schema expand --reference mcp --mcp-server '...'`. The one piece of "configuration" is the WordNet corpus itself: downloaded lazily via `nltk.download()` on first tool call (one-time network access), or pre-fetched with `python -m nltk.downloader wordnet omw-1.4`.

---

## 4. Storage Backend (Today)

KBDB does not bundle or manage a graph backend. Three are available (see [05-software-stack.md](05-software-stack.md)):

- **Memory** — in-process, no persistence, no setup. Default for tests and short-lived scripts.
- **File** — JSON-file-backed, one file per database, no server required. The CLI's and MCP server's default.
- **Neo4j** — the operator provisions their own instance and provides connection details via `kb database register --neo4j-uri ...` (CLI) or `BLUE_DATA_KB_SOURCE_NEO4J_*` (MCP server). Neo4j must be reachable before KBDB will use it; there is no bundled Neo4j container or `docker-compose.yml` in this repository.

---

## 5. Release Process (Today)

There is no CI, no `CHANGELOG.md`, and no automated publish pipeline. Each package publishes independently and manually:

```bash
cd lib && bash scripts/publish.sh              # twine upload dist/*
cd platform/cli && bash scripts/deploy.sh      # python -m build && twine upload dist/*
```

`platform/mcp/scripts/` and `platform/wordnet-mcp/scripts/` each have `build.sh` and `install.sh` but **no publish/deploy script yet** — publishing either `blue-data-kb-mcp` or `blue-data-wordnet-mcp` today would mean running `python -m build && twine upload dist/*` by hand from the respective directory.

None of the four packages have been published to PyPI as of this writing — all current installation is from local source via each package's `scripts/install.sh`.

---

## 6. Planned: Server Deployment

Everything below this line is a forward-looking design sketch for when a server-shaped deployment exists (i.e., once [platform/api/](07-repo-organization.md) — the REST API — is built). **Nothing in this section is implemented.** No `Dockerfile`, no `docker-compose.yml`, no container registry entry, no `/metrics` or `/health` endpoint, and no logging/tracing instrumentation exist anywhere in the repository today.

### Docker image (planned)

A `blue-data-kb-server` image would package the REST API. The MCP server should **not** be assumed to join it as a second network port the way an earlier draft of this document sketched — the MCP server runs over stdio, launched per-client as a subprocess, not as a long-lived network listener, so it doesn't fit the "one image, two ports" shape a REST service does. Revisit this shape once the REST API actually exists; for now, treat MCP deployment as "run `blue-data-kb-mcp` wherever the MCP client that spawns it runs."

Sketch, once a REST API exists:

| Port | Service |
|---|---|
| `8000` | REST API (HTTP) |

**Base image:** `python:3.12-slim` (proposed). **Health check:** `GET /health` (proposed).

### Configuration (planned, REST API)

A future REST API would likely need its own env vars, distinct from the CLI's and MCP server's namespaces above — e.g. `BLUE_DATA_KB_SOURCE_REST_PORT`, a default `BLUE_DATA_KB_SOURCE_DATABASE`, and its own graph-backend selection. Design these when `platform/api/` is actually started, informed by whatever the CLI/MCP split above has already taught about keeping per-service config independent.

### Observability (planned)

The target shape: structured JSON logs to stdout, a Prometheus-compatible `/metrics` endpoint, and OpenTelemetry traces via OTLP export (opt-in via `OTEL_EXPORTER_OTLP_ENDPOINT`), covering interface requests, Schema/Knowledge Manager operations, Provenance Tracker operations, graph store I/O, and OpenAI calls. No knowledge base content would be included in span attributes. None of this exists yet — no logging framework, metrics endpoint, or tracing dependency is wired into any package today.

### CI/CD (planned)

No CI/CD platform is selected yet. Target pipeline shape, once one is:

| Stage | Trigger | Actions |
|---|---|---|
| **Verify** | Every push / PR | Run unit tests, type check (`pyright`), lint (`ruff`) |
| **Build** | Every push to `main` | Build each package's wheel/sdist; build the Docker image once it exists |
| **Publish** | Git tag (`v*.*.*`) | Publish each changed package to PyPI; push the Docker image to a registry |

### Release process (planned)

Once CI exists: bump the relevant package's version, add a `CHANGELOG.md` entry (doesn't exist yet — would need to be created), merge, tag, and let CI publish — replacing the manual `scripts/publish.sh`/`scripts/deploy.sh` invocations in §5 above.
