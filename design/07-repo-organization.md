# KBDB — Repo Organization

## Directory Tree

```
blue-data-kb-source/
│
├── design/                              # design documents
│
├── lib/                                 # Python library (blue-data-kb-source PyPI package)
│   ├── pyproject.toml                   # package metadata, dependencies, tool config
│   ├── README.md                        # library usage and API reference
│   ├── scripts/
│   │   ├── build.sh                     # build the distribution package
│   │   ├── install.sh                   # install blue-data (local) then this package
│   │   └── publish.sh                   # publish to PyPI
│   ├── src/
│   │   └── blue_data_kb_source/
│   │       ├── __init__.py              # re-exports KBSource
│   │       ├── kb_source.py             # KBSource — entry point and component coordinator
│   │       ├── types.py                 # shared result types and data contracts
│   │       │
│   │       ├── schema/
│   │       │   ├── __init__.py
│   │       │   └── manager.py           # SchemaManager
│   │       │
│   │       ├── knowledge/
│   │       │   ├── __init__.py
│   │       │   └── manager.py           # KnowledgeManager
│   │       │
│   │       ├── provenance/
│   │       │   ├── __init__.py
│   │       │   └── tracker.py           # ProvenanceTracker
│   │       │
│   │       ├── notification/
│   │       │   ├── __init__.py
│   │       │   └── notifier.py          # ChangeNotifier
│   │       │
│   │       └── extraction/
│   │           ├── __init__.py
│   │           └── engine.py            # ExtractionEngine and LLM agent loop
│   │
│   └── tests/
│       ├── conftest.py                  # shared fixtures and MemoryKBSource instances
│       ├── cassettes/                   # recorded OpenAI HTTP responses (VCR)
│       └── unit/
│           ├── schema/                  # SchemaManager tests
│           ├── knowledge/               # KnowledgeManager tests
│           ├── provenance/              # ProvenanceTracker tests
│           ├── notification/            # ChangeNotifier tests
│           └── extraction/              # ExtractionEngine tests
│
└── platform/                            # External interface services
    ├── api/                             # REST API service
    │   ├── pyproject.toml
    │   ├── Dockerfile
    │   ├── scripts/
    │   │   ├── build.sh                 # build Docker image
    │   │   └── deploy.sh                # deploy service
    │   ├── src/
    │   │   └── blue_data_kb_api/
    │   │       ├── __init__.py
    │   │       ├── app.py               # FastAPI app factory
    │   │       └── routes.py            # /schema, /knowledge, /extract endpoints
    │   └── tests/
    │       └── unit/
    │
    ├── mcp/                             # MCP server service
    │   ├── pyproject.toml
    │   ├── Dockerfile
    │   ├── scripts/
    │   │   ├── build.sh
    │   │   └── deploy.sh
    │   ├── src/
    │   │   └── blue_data_kb_mcp/
    │   │       ├── __init__.py
    │   │       └── server.py            # MCP server and tool definitions
    │   └── tests/
    │       └── unit/
    │
    └── cli/                             # CLI tool
        ├── pyproject.toml
        ├── scripts/
        │   ├── build.sh                 # build distributable
        │   └── deploy.sh                # publish CLI package
        ├── src/
        │   └── blue_data_kb_cli/
        │       ├── __init__.py
        │       └── commands.py          # Click command groups
        └── tests/
            └── unit/
```

---

## Layout Rationale

### `lib/` — the Python library

All core business logic lives under `lib/src/`. This is the only artifact published to PyPI. The `src/` layout within `lib/` prevents the package from being accidentally importable from the project root during development.

`lib/scripts/` provides three scripts:

| Script | Purpose |
|--------|---------|
| `build.sh` | Builds the wheel and sdist via `python -m build` |
| `install.sh` | Installs `blue-data` from its local path, then installs this package in editable mode |
| `publish.sh` | Publishes to PyPI via `twine upload` |

### Module-to-architecture mapping

| Module | Architecture component |
|--------|----------------------|
| `kb_source.py` | `KBSource` — entry point, delegates to all components |
| `schema/manager.py` | `SchemaManager` — Schema Layer |
| `knowledge/manager.py` | `KnowledgeManager` — Knowledge Layer |
| `provenance/tracker.py` | `ProvenanceTracker` — Knowledge Layer |
| `notification/notifier.py` | `ChangeNotifier` — Knowledge Layer |
| `extraction/engine.py` | `ExtractionEngine` — Extraction Engine |
| `types.py` | Shared result types and data contracts |

### `types.py`

Shared result types used across all layers are defined in `lib/src/blue_data_kb_source/types.py`. This prevents circular imports and provides a single source of truth for data contracts between components (e.g. `AnswerResult`, `ValidationResult`, `EnrichmentResult`, `SchemaSearchResult`).

### `platform/` — external interface services

Each external interface is an independent deployable unit under `platform/`. They depend on `lib` but contain no business logic — each is a thin adapter over `KBSource`. Separating them from `lib` keeps the library free of server and CLI dependencies and allows each service to be built, versioned, and deployed independently.

Each platform service follows the same internal structure:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata and dependencies (includes `lib` as a dependency) |
| `Dockerfile` | Container image definition (`api` and `mcp` only) |
| `scripts/build.sh` | Builds the service artifact (Docker image or distribution package) |
| `scripts/deploy.sh` | Deploys the service to the target environment |
| `src/<package>/` | Interface adapter code |
| `tests/unit/` | Unit tests for the adapter layer |

### `tests/`

Library tests live under `lib/tests/`. Each platform service has its own `tests/` directory for adapter-layer tests.

```
lib/tests/
├── conftest.py          # shared fixtures: MemoryKBSource instance, pre-populated schemas
├── cassettes/           # VCR cassettes for recorded OpenAI responses (extraction tests)
└── unit/
    ├── schema/          # SchemaManager: declare, consolidate, search, import/export
    ├── knowledge/       # KnowledgeManager: add, answer, validate, enrich
    ├── provenance/      # ProvenanceTracker: source and extraction node wiring
    ├── notification/    # ChangeNotifier: hook registration, event delivery
    └── extraction/      # ExtractionEngine: tool dispatch, schema extension, provenance
```

Platform adapter tests use the same `MemoryKBSource` fixtures (imported from `lib/tests/conftest.py`) and test only the translation layer: request parsing, response serialization, and error mapping.
