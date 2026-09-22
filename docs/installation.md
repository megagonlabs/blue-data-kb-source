# Installation

## Components

`blue-data-kb-source` is organized into a core library (`lib/`) and platform components (`platform/`). Install `lib` first — every platform component depends on it, and `lib` itself depends on the sibling [`blue-data`](../../blue-data) repository's `lib/` for the underlying `GraphSource`/`VectorSource` abstractions.

```
blue-data-kb-source/
├── lib/            blue-data-kb-source  — the core Python library
└── platform/
    ├── cli/        blue-data-kb-cli     — the `blue-kb` command-line tool
    ├── mcp/        blue-data-kb-mcp     — the MCP server (`blue-data-kb-mcp`)
    └── api/        (planned)            — REST API, not yet implemented
```

Each component has a `scripts/` directory. Run scripts from the component's own root directory.

| Script | Components | Purpose |
|---|---|---|
| `install.sh` | all | Editable install (`pip install -e .`), pulling in `blue-data` and `blue-data-kb-source` first |
| `build.sh` | all | Build wheel + sdist into `dist/` (requires `pip install build`) |

**Editable install (development), in dependency order:**

```bash
cd lib && bash scripts/install.sh
cd ../platform/cli && bash scripts/install.sh
cd ../mcp && bash scripts/install.sh
```

Or manually, one package at a time:

```bash
pip install -e ../blue-data/lib          # sibling dependency
pip install -e lib                       # blue-data-kb-source
pip install -e platform/cli              # blue-data-kb-cli
pip install -e platform/mcp              # blue-data-kb-mcp
```

**Build distributable packages:**

```bash
cd lib && bash scripts/build.sh
cd platform/cli && bash scripts/build.sh
cd platform/mcp && bash scripts/build.sh
```

---

## Requirements

- Python 3.10+
- [`blue-data`](../../blue-data) (local sibling dependency — not yet published)
- An OpenAI API key (`OPENAI_API_KEY`) for any LLM feature: `import_text`/`blue-kb data extract`, `answer`/`validate`/`enrich`, and semantic search embeddings. Nothing else in the library needs it — schema declaration, instance writes, keyword search, and change hooks are all plain Python with no network calls.

None of the three packages are published to PyPI yet — always install from the local checkout as shown above.

---

## Optional extras

### `blue-data-kb-source` (`lib/`)

| Extra | Packages | Required for |
|---|---|---|
| `openai` | `openai>=1.0` | `import_text`, `answer`, `validate`, `enrich` (all LLM agent operations) |
| `neo4j` | `neo4j` | The `NEO4JSource`/`NEO4JKBSource` backend |
| `search` | `rank-bm25` | `properties["search"]["mode"]="bm25"` (real BM25 ranking; without it, keyword search falls back to unranked substring matching) |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-recording`, `pytest-cov`, `pyyaml`, `rich` | Running the test suite and benchmarks |
| `all` | `neo4j`, `openai>=1.0`, `rank-bm25` | Everything above except `dev` |

```bash
pip install -e "lib[openai,search]"
```

### `blue-data-kb-cli` (`platform/cli/`) and `blue-data-kb-mcp` (`platform/mcp/`)

Both share the same extras shape (the CLI and MCP server support the same backend/search options):

| Extra | Packages | Required for |
|---|---|---|
| `neo4j` | `neo4j>=5.0` | The `--graph-backend neo4j` / `BLUE_DATA_KB_SOURCE_GRAPH_BACKEND=neo4j` option |
| `chromadb` | `chromadb>=0.4` | The `--vector-backend chromadb` / `BLUE_DATA_KB_SOURCE_VECTOR_BACKEND=chromadb` option |
| `redis` | `redis>=5.0` | The `--vector-backend redis` / `BLUE_DATA_KB_SOURCE_VECTOR_BACKEND=redis` option |
| `dev` | `pytest`, `pytest-asyncio` | Running the test suite |
| `all` | all of the above except `dev` | Everything |

The CLI additionally has an `openai` extra (LLM commands); the MCP server depends on `openai>=1.0` unconditionally, since every deployment can reach `answer`/`validate`/`enrich`/`import_text` as tools. Neither package bundles `search` — enable BM25 by installing it on `lib` (`pip install -e "lib[search]"`), which both the CLI and MCP server pick up transparently since they share the same `blue-data-kb-source` install.

```bash
cd platform/cli && pip install -e ".[neo4j,all]"
```

---

## Running tests

Run from each component's root directory:

```bash
cd lib && pytest                    # unit tests (mocked LLM calls, no API key needed)
cd platform/cli && pytest           # CLI tests
cd platform/mcp && pytest tests/    # MCP server tests
```

Install with the `dev` extra first:

```bash
cd lib && pip install -e ".[dev]"
```

The library's benchmark suites (`lib/tests/benchmarks/`) are separate from the default test run — see [guide.md](guide.md#benchmarks) and [`lib/README.md`](../lib/README.md#running-tests) for details. They require the `openai` (and, for the querying benchmark, `search`) extras, and skip cleanly without `OPENAI_API_KEY` if no VCR cassette has been recorded for a given case.

---

## Environment variables

**The CLI** keeps its configuration in a profile — `~/.blue-kb/.profiles`, managed with `blue-kb profile config` — so nothing below needs exporting in normal use:

```bash
blue-kb profile config          # walk through the required parameters
blue-kb profile inspect         # every value, and which tier set it
```

Exporting a variable directly still overrides the profile, which is the documented precedence: **command option > environment variable > profile > default**. The full parameter table is in [cli.md](cli.md#profiles-and-configuration).

**The MCP server** has no profile support and is configured purely by environment variables. It does not read the CLI's registry or profiles; see [mcp.md](mcp.md#architecture).

| Variable | Used by | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | lib, CLI, MCP | Required for any LLM operation. For the CLI, set it in a profile (`blue-kb profile config OPENAI_API_KEY`) rather than exporting it |
| `BLUE_DATA_KB_SOURCE_HOME` | CLI | Where the CLI keeps `.profiles`, `.selected.profile` and `.selected.database` (default `~/.blue-kb`). Environment-only — it is where profiles are read from, so it cannot come from one |
| `BLUE_DATA_KB_SOURCE_DATA_DIR` | CLI | Directory for `registry.json` and file-backed databases (default `~/.blue-kb`) |
| `BLUE_DATA_KB_SOURCE_DATABASE` | CLI | Default `--database` when not passed explicitly and no `blue-kb database select` has been made |
| `BLUE_DATA_KB_SOURCE_*` | MCP server | The same names as the CLI's, read from the environment only — the MCP server has no profile support. Full table in [mcp.md](mcp.md#configuration-reference) |

---

## Next steps

- New to the project? Start with [quickstart.md](quickstart.md).
- Building against the library directly? See [guide.md](guide.md) and [api-reference.md](api-reference.md).
- Using the `blue-kb` CLI or the MCP server day to day? See [cli.md](cli.md) / [mcp.md](mcp.md).
