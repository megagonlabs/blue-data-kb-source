# KBDB — Deployment / Ops

## Overview

KBDB is distributed in two forms:

- **PyPI package** (`blue-data-kb-source`) — the Python library, for embedding KBDB in other Python applications
- **Docker image** (`blue-data-kb-server`) — packages the REST API and MCP server for standalone deployment

Production deployments are self-hosted. Operators provision and manage their own graph backend and observability infrastructure. KBDB emits to standard interfaces and does not assume any specific platform.

---

## 1. Versioning

KBDB follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`). Version is declared once in `lib/pyproject.toml` and read from there by all build tooling.

- **PATCH** — bug fixes, no interface changes
- **MINOR** — backwards-compatible new features
- **MAJOR** — breaking changes to the Python library API or graph data model

---

## 2. PyPI Package

The `blue-data-kb-source` package exposes the Python library (`KBSource` and related classes). It does not include the REST API, MCP server, or CLI by default. Those are available as optional extras:

```
pip install blue-data-kb-source                  # library only (MemoryGraphSource backend)
pip install blue-data-kb-source[neo4j]           # library + Neo4j backend
pip install blue-data-kb-source[openai]          # library + LLM extraction support
pip install blue-data-kb-source[server]          # library + REST API + MCP server
pip install blue-data-kb-source[cli]             # library + CLI
pip install blue-data-kb-source[all]             # everything
```

---

## 3. Docker Image

The `blue-data-kb-server` image runs both the REST API (Uvicorn/FastAPI) and the MCP server as a combined process. The image is published to a container registry alongside each release.

| Port | Service |
|------|---------|
| `8000` | REST API (HTTP) |
| `8001` | MCP server |

**Base image:** `python:3.12-slim`

**Entrypoint:** starts both services; each can be disabled via environment variable (see §5).

**Health check:** `GET /health` on port 8000.

---

## 4. Storage Backend

KBDB does not bundle or manage the graph backend. The operator provisions a **Neo4j** instance and provides its connection details via environment variables.

Neo4j must be available before KBDB starts. KBDB will fail at startup if the backend is unreachable.

For local development and operator reference, a `docker-compose.yml` is provided in the repository root:

```yaml
services:
  kbdb:
    image: blue-data-kb-server:latest
    ports:
      - "8000:8000"
      - "8001:8001"
    env_file: .env
    depends_on:
      neo4j:
        condition: service_healthy

  neo4j:
    image: neo4j:5
    ports:
      - "7474:7474"   # HTTP browser
      - "7687:7687"   # Bolt protocol
    environment:
      NEO4J_AUTH: neo4j/password
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "password", "RETURN 1"]
      interval: 10s
      timeout: 5s
      retries: 5
```

---

## 5. Configuration

All configuration is via environment variables. There are no configuration files — operators use `.env` files, secrets managers, or platform-native injection.

### Core

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KBDB_DATABASE` | No | — | Default database name used when none is specified per request |
| `KBDB_GRAPH_BACKEND` | No | `memory` | Graph backend to use: `memory` or `neo4j` |

### Storage

| Variable | Required | Description |
|----------|----------|-------------|
| `KBDB_NEO4J_URI` | Yes (neo4j backend) | Neo4j Bolt URI (e.g., `bolt://localhost:7687`) |
| `KBDB_NEO4J_USER` | Yes (neo4j backend) | Neo4j username |
| `KBDB_NEO4J_PASSWORD` | Yes (neo4j backend) | Neo4j password |

### LLM

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes (extraction) | — | OpenAI API key; only required when `import_text` is used |
| `KBDB_LLM_MODEL` | No | `gpt-4o` | OpenAI model used by the Extraction Engine |

### Server

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KBDB_REST_PORT` | No | `8000` | Port for the REST API |
| `KBDB_MCP_PORT` | No | `8001` | Port for the MCP server |
| `KBDB_REST_ENABLED` | No | `true` | Set to `false` to disable the REST API |
| `KBDB_MCP_ENABLED` | No | `true` | Set to `false` to disable the MCP server |

### Observability

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | — | OpenTelemetry collector endpoint; tracing disabled if unset |
| `OTEL_SERVICE_NAME` | No | `kbdb` | Service name reported in traces and metrics |

---

## 6. Observability

KBDB emits structured logs, metrics, and traces. Operators wire these into their own observability stack.

### Structured Logging

All log output is JSON-formatted to stdout. Log level is controlled by the `LOG_LEVEL` environment variable (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default: `INFO`).

Each log line includes: `timestamp`, `level`, `service`, `database`, `trace_id` (when a trace is active), and a `message` field.

### Metrics

Metrics are exposed via a Prometheus-compatible `/metrics` endpoint on the REST API port.

| Metric | Type | Description |
|--------|------|-------------|
| `kbdb_requests_total` | Counter | Total requests by operation and status |
| `kbdb_request_duration_seconds` | Histogram | End-to-end request latency |
| `kbdb_llm_calls_total` | Counter | OpenAI API calls by status |
| `kbdb_llm_duration_seconds` | Histogram | OpenAI call latency |
| `kbdb_graph_ops_total` | Counter | Graph store operations by type (`add_node`, `add_edge`, `get_node`, `match_nodes`) and status |
| `kbdb_graph_op_duration_seconds` | Histogram | Graph operation latency |
| `kbdb_schema_elements_total` | Gauge | Count of schema nodes (CLASS, RELATION, PROPERTY) per database |
| `kbdb_instance_nodes_total` | Gauge | Count of instance nodes (ENTITY, FACT, LITERAL) per database |

### Tracing

Distributed tracing uses [OpenTelemetry](https://opentelemetry.io/) with OTLP export. Tracing is enabled when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

Trace spans are created at:

- Each external interface request
- Each Schema Manager operation (declare, search, consolidate, import/export)
- Each Knowledge Manager operation (add, answer, validate, enrich)
- Each Provenance Tracker operation
- Each graph store read/write
- Each OpenAI API call (Extraction Engine)

All spans include `database` and `operation` as attributes. No knowledge base content is ever included in span attributes.

---

## 7. CI/CD Pipeline

The CI/CD platform is not yet selected. Regardless of platform, the pipeline defines these stages:

| Stage | Trigger | Actions |
|-------|---------|---------|
| **Verify** | Every push / PR | Run unit tests, type check (`pyright`), lint (`ruff`) |
| **Integration** | Every push to `main` | Run integration tests against a Neo4j container |
| **Build** | Every push to `main` | Build PyPI package, build Docker image |
| **Publish** | Git tag (`v*.*.*`) | Publish to PyPI, push Docker image to registry |

The **Verify** stage must pass before any merge. The **Publish** stage is gated on a semver tag pushed by a maintainer.

---

## 8. Release Process

1. Bump version in `lib/pyproject.toml`
2. Update `CHANGELOG.md`
3. Merge to `main`
4. Push a tag: `git tag v1.2.3 && git push origin v1.2.3`
5. CI publishes the PyPI package and Docker image automatically

No manual upload steps. All publishing is performed by CI using credentials injected as secrets.
