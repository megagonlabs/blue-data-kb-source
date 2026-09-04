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
│   │       │   ├── manager.py           # SchemaManager (bootstraps __meta__ database on init)
│   │       │   └── expansion.py         # SchemaExpansionEngine — LLM agent for expand_schema
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
│       ├── conftest.py                  # shared fixtures: kb (MemoryKBSource), populated_kb
│       ├── unit/
│       │   ├── schema/                  # SchemaManager and SchemaExpansionEngine tests (OpenAI mocked)
│       │   ├── knowledge/               # KnowledgeManager and QueryEngine tests
│       │   ├── provenance/              # ProvenanceTracker tests
│       │   ├── notification/            # ChangeNotifier tests
│       │   └── extraction/              # ExtractionEngine tests (OpenAI mocked)
│       └── benchmarks/                  # gold-standard-case suites against real OpenAI (VCR cassettes)
│           ├── common/                  # shared harness: capture, (query_)cases, (query_)runner, (query_)scoring, (query_)reporting
│           ├── extraction/              # import_text cases + pytest suite + live run.py CLI
│           └── querying/                # answer/validate/enrich cases + pytest suite + live run.py CLI
│
├── docs/                                # User-facing documentation — start at docs/README.md
│
├── examples/                            # Runnable example scripts with captured output — start at examples/README.md
│
└── platform/                            # External interface services
    ├── cli/                             # CLI tool (blue-data-kb-cli PyPI package)
    │   ├── pyproject.toml
    │   ├── README.md                    # CLI usage, command reference, walkthrough
    │   ├── scripts/
    │   │   ├── build.sh                 # build distributable
    │   │   ├── deploy.sh                # publish CLI package
    │   │   └── install.sh               # install blue-data and lib locally, then this package
    │   ├── src/
    │   │   └── blue_data_kb_cli/
    │   │       ├── __init__.py
    │   │       ├── commands.py          # Click command groups; FileKBSource adapter
    │   │       └── config.py            # Database registry and KBSource factory
    │   └── tests/
    │       ├── __init__.py
    │       └── unit/
    │           ├── __init__.py
    │           ├── conftest.py          # CliRunner fixture with isolated KBDB_DATA_DIR
    │           ├── test_database.py     # database register/create/select/delete, config
    │           ├── test_schema.py       # class/relation declaration, hierarchy, search
    │           ├── test_data.py         # entity/fact/property CRUD, data search, reindex
    │           └── test_llm.py          # extract/answer/validate with mocked OpenAI
    │
    ├── mcp/                             # MCP server (blue-data-kb-mcp PyPI package)
    │   ├── pyproject.toml
    │   ├── README.md                    # tool reference, Claude Desktop config, architecture note
    │   ├── scripts/
    │   │   ├── build.sh                 # build distributable
    │   │   └── install.sh               # install blue-data and lib locally, then this package (no deploy.sh yet)
    │   ├── src/
    │   │   └── blue_data_kb_mcp/
    │   │       ├── __init__.py
    │   │       ├── server.py            # FastMCP app; 26 @mcp.tool() handlers
    │   │       └── config.py            # BLUE_DATA_KB_SOURCE_* env-var-driven KBSource singleton factory
    │   └── tests/
    │       ├── __init__.py
    │       └── test_tools.py            # all 26 tools, called directly against an injected MemoryKBSource
    │
    └── wordnet-mcp/                     # WordNet MCP server (blue-data-wordnet-mcp PyPI package)
        ├── pyproject.toml
        ├── README.md                    # tool reference, schema-expansion integration note
        ├── scripts/
        │   ├── build.sh                 # build distributable
        │   └── install.sh               # install this package (no dependency on blue-data or lib)
        ├── src/
        │   └── blue_data_wordnet_mcp/
        │       ├── __init__.py
        │       ├── server.py            # FastMCP app; 10 @mcp.tool() handlers over nltk.corpus.wordnet
        │       └── nltk_data.py         # lazy nltk.download() of the wordnet/omw-1.4 corpora
        └── tests/
            ├── __init__.py
            └── test_tools.py            # all 10 tools, called directly against the real (downloaded) corpus
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
| `schema/manager.py` | `SchemaManager` — Schema Layer; bootstraps `__meta__` database on init; owns `SchemaExpansionEngine` |
| `schema/expansion.py` | `SchemaExpansionEngine` — LLM agent for `expand_schema`; declares CLASS/RELATION/PROPERTY only, never entities/facts; not exposed at `KBSource` level |
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
| `mcp/` | Implemented | FastMCP adapter (`blue-data-kb-mcp`); env-var-configured `KBSource` singleton, no dependency on `cli/`'s registry — see [09-api-interface-spec.md](09-api-interface-spec.md) §3 |
| `wordnet-mcp/` | Implemented | FastMCP adapter (`blue-data-wordnet-mcp`) over `nltk.corpus.wordnet`; read-only, no dependency on `lib`/`blue-data` at all — a standalone lexical resource optionally connected by `expand_schema(reference="mcp")`, not a `KBSource` adapter like the other three rows |

`cli/` follows this internal structure:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata and dependencies (includes `lib` as a dependency) |
| `scripts/install.sh` | Installs `blue-data` and `lib` locally, then this package in editable mode |
| `scripts/build.sh` | Builds the distributable package |
| `scripts/deploy.sh` | Publishes to PyPI |
| `src/blue_data_kb_cli/commands.py` | Click command groups; `FileKBSource` class; all CLI logic |
| `src/blue_data_kb_cli/config.py` | Database registry (`registry.json`) and the `_make_kb` `KBSource` factory |
| `tests/unit/` | Unit tests for the CLI adapter layer, run with `click.testing.CliRunner` against an isolated `KBDB_DATA_DIR` |

`mcp/` follows a lighter version of the same shape:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata and dependencies (includes `lib` as a dependency; no `cli` dependency) |
| `scripts/install.sh` | Installs `blue-data` and `lib` locally, then this package in editable mode |
| `scripts/build.sh` | Builds the distributable package (no `deploy.sh` yet — publishing is a manual `build && twine upload`, see [12-deployment-ops.md](12-deployment-ops.md) §5) |
| `src/blue_data_kb_mcp/server.py` | `FastMCP` app; 26 `@mcp.tool()` handlers, grouped by comment banner (schema/knowledge read+write, schema expansion, query, extraction, provenance) |
| `src/blue_data_kb_mcp/config.py` | `BLUE_DATA_KB_SOURCE_*` env-var-driven singleton `KBSource` factory — independent of `cli/`'s `registry.json` |
| `tests/test_tools.py` | Calls the 26 tool functions directly against an injected `MemoryKBSource` singleton |

`wordnet-mcp/` is the simplest of the three — no `lib`/`blue-data` dependency at all, so there's no `KBSource` factory to speak of:

| Path | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata and dependencies (`mcp[cli]`, `nltk` — no `lib`/`blue-data`) |
| `scripts/install.sh` | Installs this package in editable mode (no local sibling packages to install first) |
| `scripts/build.sh` | Builds the distributable package (no `deploy.sh` yet, same as `mcp/`) |
| `src/blue_data_wordnet_mcp/server.py` | `FastMCP` app; 10 `@mcp.tool()` handlers over `nltk.corpus.wordnet` (synset lookup, synonyms, hypernyms/hyponyms, meronyms/holonyms, antonyms, similarity, lemmatization) |
| `src/blue_data_wordnet_mcp/nltk_data.py` | Lazy `nltk.download()` of the `wordnet`/`omw-1.4` corpora on first tool call, if not already present locally |
| `tests/test_tools.py` | Calls the 10 tool functions directly against the real (downloaded) WordNet corpus — no mocking, since there's no external API to mock |

### `tests/`

Library tests live under `lib/tests/`. Each platform service has its own `tests/` directory for adapter-layer tests, with its own fixtures rather than importing `lib/tests/conftest.py`.

```
lib/tests/
├── conftest.py          # shared fixtures: kb (MemoryKBSource), populated_kb
├── unit/
│   ├── schema/          # SchemaManager (test_manager.py): declare, update, delete, hierarchy, consolidate, search, import/export
│   │                    #   + SchemaExpansionEngine (test_expansion.py): expand_schema tool dispatch, dedup_mode, MCP wiring (OpenAI mocked)
│   ├── knowledge/       # KnowledgeManager and QueryEngine: add/update/delete, get_metadata, answer, validate, enrich
│   ├── provenance/      # ProvenanceTracker: source and extraction node wiring
│   ├── notification/    # ChangeNotifier: hook registration, event delivery
│   └── extraction/      # ExtractionEngine: import_text tool dispatch (OpenAI mocked)
└── benchmarks/          # opt-in, real-OpenAI gold-standard-case suites — excluded from the default `pytest` run
    ├── common/          # shared harness: capture, (query_)cases, (query_)runner, (query_)scoring, (query_)reporting
    ├── extraction/      # import_text cases (VCR cassettes) + pytest suite + live run.py CLI
    └── querying/        # answer/validate/enrich cases (VCR cassettes) + pytest suite + live run.py CLI
```

Each `unit/` subdirectory carries an `__init__.py` to prevent pytest from confusing identically-named test files (e.g. `schema/test_manager.py` and `knowledge/test_manager.py`). See [11-testing-strategy.md](11-testing-strategy.md) for the full testing approach, including why the benchmark tier is kept separate from `unit/`.

`answer`, `validate`, and `enrich` tests in `unit/knowledge/` mock `openai.AsyncOpenAI` via `unittest.mock.patch` so the agent loop runs without a live API key.

Platform adapter tests define their own fixtures rather than importing `lib/tests/conftest.py`, since each adapter exercises a different entry point. The CLI's `tests/unit/conftest.py` provides a `click.testing.CliRunner` fixture that points `KBDB_DATA_DIR` at a fresh `tmp_path` per test, so each test gets an isolated file-backed database with no cross-test state; its `extract`/`answer`/`validate` tests mock `openai.AsyncOpenAI` the same way the library tests do.
