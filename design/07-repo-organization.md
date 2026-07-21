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
│   │       ├── _constants.py            # node label and edge type string constants
│   │       ├── kb_source.py             # KBSource — entry point and component coordinator
│   │       ├── types.py                 # shared result types and data contracts
│   │       ├── py.typed                 # PEP 561 marker — package ships inline types
│   │       │
│   │       ├── schema/
│   │       │   ├── __init__.py
│   │       │   └── manager.py           # SchemaManager (bootstraps __meta__ database on init)
│   │       │
│   │       ├── knowledge/
│   │       │   ├── __init__.py
│   │       │   ├── manager.py           # KnowledgeManager — instance writes and query delegation
│   │       │   └── engine.py            # QueryEngine — LLM agent for answer/validate/enrich
│   │       │
│   │       ├── provenance/
│   │       │   ├── __init__.py
│   │       │   └── tracker.py           # ProvenanceTracker
│   │       │
│   │       ├── notification/
│   │       │   ├── __init__.py
│   │       │   └── notifier.py          # ChangeNotifier
│   │       │
│   │       ├── search/
│   │       │   ├── __init__.py
│   │       │   └── indexer.py           # KBSearchIndexer — BM25 + semantic search
│   │       │
│   │       └── extraction/
│   │           ├── __init__.py
│   │           └── engine.py            # ExtractionEngine and LLM agent loop
│   │
│   └── tests/
│       ├── conftest.py                  # shared fixtures and MemoryKBSource instances
│       └── unit/
│           ├── schema/                  # SchemaManager tests
│           ├── knowledge/               # KnowledgeManager and QueryEngine tests
│           ├── provenance/              # ProvenanceTracker tests
│           └── notification/            # ChangeNotifier tests
│
└── platform/                            # External interface services
    └── cli/                             # CLI tool (blue-data-kb-cli PyPI package)
        ├── pyproject.toml
        ├── README.md                    # CLI usage, command reference, walkthrough
        ├── scripts/
        │   ├── build.sh                 # build distributable
        │   ├── deploy.sh                # publish CLI package
        │   └── install.sh               # install blue-data and lib locally, then this package
        ├── src/
        │   └── blue_data_kb_cli/
        │       ├── __init__.py
        │       └── commands.py          # Click command groups; FileKBSource adapter
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
| `_constants.py` | Node label and edge type string constants shared across all modules |
| `schema/manager.py` | `SchemaManager` — Schema Layer; bootstraps `__meta__` database on init |
| `knowledge/manager.py` | `KnowledgeManager` — Knowledge Layer; instance writes; owns `QueryEngine` |
| `knowledge/engine.py` | `QueryEngine` — LLM agent for answer, validate, enrich; not exposed at `KBSource` level |
| `provenance/tracker.py` | `ProvenanceTracker` — Knowledge Layer |
| `notification/notifier.py` | `ChangeNotifier` — Knowledge Layer |
| `search/indexer.py` | `KBSearchIndexer` — Search Indexer; BM25 and semantic search over schema and data, optional per database |
| `extraction/engine.py` | `ExtractionEngine` — Extraction Engine; LLM agent for ingestion |
| `types.py` | Shared result types: `AnswerResult`, `ValidationResult`, `EnrichmentResult`, `SchemaSearchResult`, `DataSearchResult` |

### `types.py`

Shared result types used across all layers are defined in `lib/src/blue_data_kb_source/types.py`. This prevents circular imports and provides a single source of truth for data contracts between components (e.g. `AnswerResult`, `ValidationResult`, `EnrichmentResult`, `SchemaSearchResult`, `DataSearchResult`).

### `platform/` — external interface services

Each external interface is an independent deployable unit under `platform/`. They depend on `lib` but contain no business logic — each is a thin adapter over `KBSource`. Separating them from `lib` keeps the library free of server and CLI dependencies and allows each service to be built, versioned, and deployed independently.

Currently implemented:

| Service | Status | Notes |
|---------|--------|-------|
| `cli/` | Implemented | `FileKBSource` adapter; persistent JSON-backed state per database |
| `api/` | Planned | FastAPI REST adapter |
| `mcp/` | Planned | MCP server adapter for LLM agent tool use |

`cli/` follows this internal structure:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata and dependencies (includes `lib` as a dependency) |
| `scripts/install.sh` | Installs `blue-data` and `lib` locally, then this package in editable mode |
| `scripts/build.sh` | Builds the distributable package |
| `scripts/deploy.sh` | Publishes to PyPI |
| `src/blue_data_kb_cli/commands.py` | Click command groups; `FileKBSource` class; all CLI logic |
| `tests/unit/` | Intended home for unit tests of the CLI adapter layer — currently empty; the CLI has no automated test coverage yet |

### `tests/`

Library tests live under `lib/tests/`. Each platform service has its own `tests/` directory for adapter-layer tests.

```
lib/tests/
├── conftest.py          # shared fixtures: MemoryKBSource instance, pre-populated schemas
└── unit/
    ├── schema/          # SchemaManager: declare, consolidate, search, import/export
    ├── knowledge/       # KnowledgeManager and QueryEngine: add, answer, validate, enrich
    ├── provenance/      # ProvenanceTracker: source and extraction node wiring
    └── notification/    # ChangeNotifier: hook registration, event delivery
```

Each test subdirectory carries an `__init__.py` to prevent pytest from confusing identically-named test files (e.g. `schema/test_manager.py` and `knowledge/test_manager.py`).

`answer`, `validate`, and `enrich` tests in `unit/knowledge/` mock `openai.AsyncOpenAI` via `unittest.mock.patch` so the agent loop runs without a live API key.

Platform adapter tests use the same `MemoryKBSource` fixtures (imported from `lib/tests/conftest.py`) and test only the translation layer: request parsing, response serialization, and error mapping.
