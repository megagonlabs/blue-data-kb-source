# KBDB — Testing Strategy

## Overview

**There is no CI in this repository yet** (see [12-deployment-ops.md](12-deployment-ops.md) §7) — everything below describes how to run tests locally; none of it runs automatically today.

KBDB has three real test tiers, spread across independently-installed packages:

- **`lib/tests/unit/`** — fast, offline unit tests for the library, doubling `MemoryKBSource` for the graph backend and `unittest.mock.patch` for OpenAI calls
- **`lib/tests/benchmarks/`** — gold-standard-case suites for `import_text`/`answer`/`validate`/`enrich` against real OpenAI responses (via VCR cassettes), with LLM-judge scoring; a heavier, opt-in tier, not part of the default `pytest` run
- **`platform/cli/tests/`** and **`platform/mcp/tests/`** — adapter-layer tests for the CLI and MCP server, each in their own package with their own fixtures

There is no Neo4j-backed integration test suite today — the Neo4j backend (via the `blue-data` dependency) is real and wired into both the CLI and MCP server, but nothing currently tests against a live Neo4j instance.

---

## 1. Tooling

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support (`asyncio_mode = "auto"`) |
| `pytest-recording` | Record and replay OpenAI HTTP interactions (VCR-based) — used by the **benchmark** suites only, not by `lib/tests/unit/` |
| `pytest-cov` | Coverage measurement, via `[tool.coverage.run]` (not wired into pytest's default `addopts`) |

Declared as `dev` extras in `lib/pyproject.toml`. `platform/cli/pyproject.toml` and `platform/mcp/pyproject.toml` each declare their own, smaller `dev` extras (`pytest`, `pytest-asyncio`) — neither uses `pytest-cov` or `pytest-recording`.

---

## 2. Test Layout

```
lib/tests/
  conftest.py                  # shared fixtures: kb (MemoryKBSource), populated_kb
  unit/
    schema/       test_manager.py       # declare, update, delete, hierarchy, search, consolidate, import/export
                  test_expansion.py     # expand_schema: tool dispatch, dedup_mode, CLASS-vs-entity/taxonomic-relation rejection, MCP wiring (OpenAI + MCP client mocked)
    knowledge/    test_manager.py       # add/update/delete entity/fact/property, get_metadata, export/import, answer/validate/enrich (QueryEngine mocked)
    provenance/   test_tracker.py       # add_source, add_extraction, link_extraction, update/delete_source
    notification/ test_notifier.py      # hook registration, event delivery, database scoping
    extraction/   test_engine.py        # import_text: tool dispatch (no schema-declaration tool), provenance wiring (OpenAI mocked)
  benchmarks/
    common/       capture.py, cases.py, query_cases.py, (query_)reporting.py, (query_)runner.py, (query_)scoring.py
    extraction/   cases/*.yaml, cassettes/, conftest.py, test_extraction_benchmark.py, run.py
    querying/     cases/*.yaml, cassettes/, conftest.py, test_querying_benchmark.py, run.py

platform/cli/tests/
  unit/
    conftest.py       # CliRunner fixture, isolated KBDB_DATA_DIR
    test_database.py  # database register/create/select/delete, config
    test_schema.py     # class/relation declaration, hierarchy, search
    test_data.py        # entity/fact/property CRUD, data search, reindex
    test_llm.py          # extract/answer/validate with mocked OpenAI

platform/mcp/tests/
  test_tools.py    # all 26 tools, called directly against an injected MemoryKBSource singleton

platform/wordnet-mcp/tests/
  test_tools.py    # all 10 tools, called directly against the real (downloaded) WordNet corpus — no mocking
```

Each `lib/tests/unit/` subdirectory carries an `__init__.py` so pytest doesn't confuse identically-named files (e.g. `schema/test_manager.py` vs. `knowledge/test_manager.py`) across directories.

---

## 3. Graph Store Test Double

KBDB's storage backend is abstracted by `GraphSource`. The unit-test double is `MemoryKBSource` — a concrete subclass that mixes `KBSource` with `MemoryGraphSource` from `blue-data`. It requires no setup, holds state in-process, and is discarded after each test.

No separate mock or stub is needed: the same code path that runs against Neo4j in production runs against `MemoryKBSource` in tests.

**Fixture:**

```python
# tests/conftest.py

@pytest.fixture
def kb() -> MemoryKBSource:
    inst = MemoryKBSource(name="test-kb")
    inst.create_database("test")   # inherited from GraphSource — synchronous, no await/asyncio.run
    return inst
```

---

## 4. LLM Test Doubles

Two different, deliberately separate mocking strategies exist depending on the tier:

**`lib/tests/unit/`** (fast, offline, no network) mocks `openai.AsyncOpenAI` directly via `unittest.mock.patch`, returning a scripted `MagicMock`/`AsyncMock` response sequence via a `_mock_openai_sequence` helper — the same small pattern independently redefined in each of `unit/extraction/test_engine.py`, `unit/schema/test_expansion.py`, and `unit/knowledge/test_manager.py`'s `answer`/`validate`/`enrich` tests, rather than imported from one shared module. `unit/schema/test_expansion.py`'s `reference="mcp"` test is the one exception to "no network/subprocess at all": only the model's decisions are mocked (`openai.AsyncOpenAI`, as above) — the MCP half (spawn, `list_tools`, `call_tool`) is real, spawning the actual `blue-data-wordnet-mcp` binary over stdio to prove the generic MCP bridge drives an external server's tools, not a scripted fake of the MCP protocol. It's skipped (`pytest.mark.skipif`) when `blue-data-wordnet-mcp` isn't installed on `PATH`, so it degrades gracefully rather than failing in an environment that only installed `lib/`'s `dev` extra. No cassettes, no `@pytest.mark.vcr` anywhere in this tier — this is what otherwise makes `lib/tests/unit/` safe to run with no `OPENAI_API_KEY` and no network access.

**`lib/tests/benchmarks/`** (opt-in, heavier) uses `pytest-recording` (VCR.py) against real OpenAI responses, scoped per suite — `lib/tests/benchmarks/extraction/cassettes/` and `lib/tests/benchmarks/querying/cassettes/`, each with its own `conftest.py` defining `vcr_config`/`vcr_cassette_dir`, and `pytestmark = pytest.mark.vcr` set at the module level in each suite's `test_*_benchmark.py`. This is where cassette-recording rules apply:

- Cassettes are committed to the repository.
- When a prompt or tool set changes in a way that would alter the response, the cassette must be re-recorded: delete the cassette file and run with `--record-mode=once`.
- API keys must be present when recording; they are stripped from cassette files before committing.

**Example — unit test (mocked, no cassette):**

```python
async def test_import_text_extracts_entity(kb):
    with patch("openai.AsyncOpenAI", return_value=_mock_openai_sequence([...])):
        src = await kb.add_source("test", source="doc1", name="Test Document")
        await kb.import_text(
            "test",
            "Alice joined IBM as a software engineer in June 1998.",
            queries=["Where did Alice work?"],
            source_id=src,
        )
    results = await kb.answer("test", "Where did Alice work?")
    assert any(r.get("value") == "IBM" for r in results)
```

See §12 below for the benchmark tier's real, live-LLM equivalent of this same scenario.

---

## 5. Provenance Verification

Tests that exercise any write path (entity, fact, property value) assert both the instance node and its provenance chain when `text` is provided. Each such test verifies:

1. The instance node exists and has the correct properties
2. An EXTRACTION node was created with the expected text
3. An EXTRACTED_FROM edge connects the instance to the EXTRACTION
4. If `source_id` was provided: a second EXTRACTED_FROM edge connects the EXTRACTION to the SOURCE

**Example:**

```python
async def test_add_entity_creates_provenance_chain(kb):
    src = await kb.add_source("test", source="doc1")
    entity_id = await kb.add_entity(
        "test", "Alice", class_id=None,
        text="Alice is a software engineer.", source_id=src
    )
    neighbors = await kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert len(neighbors) == 1
    extraction = neighbors[0]["node"]
    assert extraction["properties"]["text"] == "Alice is a software engineer."

    src_neighbors = await kb.get_neighbors("test", extraction["id"], edge_type="EXTRACTED_FROM", direction="out")
    assert src_neighbors[0]["node"]["id"] == src
```

---

## 6. Change Notification Verification

Tests for the Change Notifier assert that hooks fire with correctly enriched event payloads and that hooks scoped to one database do not fire for writes to another.

**Example:**

```python
async def test_hook_fires_on_entity_add(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)

    entity_id = await kb.add_entity("test", "Alice", class_id=None)

    assert len(events) == 1
    assert events[0]["type"] == "entity_added"
    assert events[0]["name"] == "Alice"

async def test_hook_does_not_fire_for_other_database(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)

    await kb.create_database("other")
    await kb.add_entity("other", "Bob")

    assert len(events) == 0
```

---

## 7. Fixtures and Test Data

Common fixtures are defined in `tests/conftest.py`. A `populated_kb` fixture provides a database with a standard schema and a small set of instances for tests that need existing data.

```python
@pytest.fixture
async def populated_kb(kb) -> MemoryKBSource:
    # Schema
    person   = await kb.declare_class("test", "PERSON",   "A human individual")
    company  = await kb.declare_class("test", "COMPANY",  "A business organization")
    employee = await kb.declare_class("test", "EMPLOYEE", "A person employed by an organization")
    await kb.add_is_subclass_of("test", employee, person)
    worked_at = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship", domain="EMPLOYEE", range="COMPANY"
    )
    start_date = await kb.declare_relation_property("test", "START_DATE", "Start of employment", relation_id=worked_at)

    # Instances
    src   = await kb.add_source("test", source="resume_alice", name="Alice's résumé")
    alice = await kb.add_entity("test", "Alice", class_id=employee, text="Alice is a software engineer.", source_id=src)
    ibm   = await kb.add_entity("test", "IBM",   class_id=company)
    fact  = await kb.add_fact("test", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm,
                              text="Alice joined IBM in June 1998.", source_id=src)
    await kb.add_fact_property_value("test", "1998-06-01", fact_id=fact, property_id=start_date,
                                     text="Alice joined IBM in June 1998.", source_id=src)
    return kb
```

---

## 8. Interface Tests

Interface tests live in each interface's **own package**, not under `lib/tests/` — each adapter is a separate deployable unit (see [07-repo-organization.md](07-repo-organization.md)) with its own test suite and fixtures rather than importing `lib/tests/conftest.py`.

- **CLI** (`platform/cli/tests/unit/`) — tested via Click's `CliRunner`, pointed at an isolated `tmp_path` per test via `KBDB_DATA_DIR` (`conftest.py`); `extract`/`answer`/`validate` tests mock `openai.AsyncOpenAI` the same way `lib/tests/unit/` does.
- **MCP Server** (`platform/mcp/tests/test_tools.py`) — an `autouse` fixture builds a `MemoryKBSource`, injects it as the module-level singleton via `config.reset_kb()`/monkeypatching `get_kb`, and tests call the `@mcp.tool()`-decorated async functions directly, asserting on the JSON string each returns.
- **REST API** — doesn't exist yet (`platform/api/` is empty); no tests exist for it.

---

## 9. Neo4j — not covered by an automated test today

A `Neo4jGraphSource`/`NEO4JSource` backend is real and wired into both the CLI (`kb database register --graph-backend neo4j`) and the MCP server (`BLUE_DATA_KB_SOURCE_GRAPH_BACKEND=neo4j`), but no automated test — unit or otherwise — currently runs against a live Neo4j instance. There is no `lib/tests/integration/` directory. Manual verification against Neo4j has been done ad hoc (e.g. `examples/backends/neo4j_backend_demo.py`); building a real Neo4j-backed test suite (structurally similar to the `MemoryKBSource`-backed unit tests, swapping the fixture backend) is open work, not yet started.

---

## 10. Coverage

Coverage is measured via `pytest-cov`, configured separately from pytest's own options — it does not run automatically on a bare `pytest` invocation, only when `--cov` flags are passed explicitly or via the `[tool.coverage.run]` section:

```toml
# lib/pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths    = ["tests/unit"]
addopts      = ""

[tool.coverage.run]
source = ["src"]
```

`testpaths = ["tests/unit"]` is also what keeps `lib/tests/benchmarks/` out of the default `pytest` run — the benchmark suites are invoked explicitly (§11). No minimum coverage threshold is enforced. `platform/cli/` and `platform/mcp/` don't declare `pytest-cov` at all.

---

## 11. Running Tests

```bash
# Library unit tests (fast, offline, no OPENAI_API_KEY needed)
cd lib && pytest tests/unit/

# With coverage
cd lib && pytest tests/unit/ --cov=src --cov-report=term-missing

# Library benchmark suites (opt-in, real OpenAI calls unless cassettes already exist)
cd lib && pytest tests/benchmarks/extraction/
cd lib && pytest tests/benchmarks/querying/

# Re-record a benchmark suite's cassettes
cd lib && pytest tests/benchmarks/extraction/ --record-mode=once

# CLI adapter tests
cd platform/cli && pytest tests/

# MCP server adapter tests
cd platform/mcp && pytest tests/
```

---

## 12. Benchmark Suites

`lib/tests/benchmarks/` is a first-class test tier, not a side project — gold-standard-case suites that score `import_text` (extraction) and `answer`/`validate`/`enrich` (querying) against real LLM output, not mocks. As of this writing: 11 extraction case files and 18 querying case files under `lib/tests/benchmarks/{extraction,querying}/cases/*.yaml`, spanning multiple domains (business, healthcare, finance, legal, supply-chain).

> **The extraction suite is currently known-broken**, not merely aspirational: it's keyed on `import_text(..., schema_discovery_mode=...)`, a parameter that no longer exists now that schema growth is the separate `expand_schema` pass. The harness needs reworking to score the two passes (schema expansion, then extraction) independently before it can run again. See the "Extraction benchmark suite" section of `lib/README.md` for the full caveat. The querying suite is unaffected — it seeds a fixed schema/data via `seed_schema`/`seed_data` rather than calling `import_text` at all.

Shared harness under `lib/tests/benchmarks/common/`:

| Module | Purpose |
|---|---|
| `capture.py` | Records/mocks OpenAI interactions for both cassette-based and scripted-mock testing |
| `cases.py` / `query_cases.py` | Load and parse gold-standard YAML case files |
| `runner.py` / `query_runner.py` | Drive `import_text`/`answer`/`validate`/`enrich` against each case |
| `scoring.py` / `query_scoring.py` | Score results — includes an LLM-judge comparison for cases where exact-match scoring isn't meaningful, plus deterministic checks (e.g. provenance/domain-range compliance) |
| `reporting.py` / `query_reporting.py` | Format scored results into a report |

Each suite also ships a live `run.py` CLI (see `lib/README.md` for usage) for ad hoc runs against real cases outside of pytest. Full documentation of the benchmark harness lives in `lib/README.md`, not duplicated here.
