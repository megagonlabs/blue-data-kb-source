# KBDB — Testing Strategy

## Overview

KBDB uses two test tiers:

- **Unit tests** — run on every push in CI; all I/O boundaries are doubled via `MemoryKBSource` (the in-process graph backend) and recorded OpenAI responses
- **Integration tests** — run on demand or in a dedicated CI job; exercise the Neo4j backend against a live instance

---

## 1. Tooling

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support (`asyncio_mode = "auto"`) |
| `pytest-recording` | Record and replay OpenAI HTTP interactions (VCR-based) |
| `pytest-cov` | Coverage measurement (reported, not enforced) |

All tools are declared as `dev` dependencies in `pyproject.toml`.

---

## 2. Test Layout

Tests mirror the source layout under `lib/tests/`.

```
lib/tests/
  conftest.py                  # shared fixtures: MemoryKBSource, pre-populated schema and instances
  cassettes/                   # recorded OpenAI responses (VCR cassettes)
    extraction/
  unit/
    schema/
      test_manager.py          # declare, search, consolidate, import/export
    knowledge/
      test_manager.py          # add_entity, add_fact, add_property_value, answer, validate, enrich
    provenance/
      test_tracker.py          # add_source, add_extraction, track
    notification/
      test_notifier.py         # hook registration, event delivery, database scoping
    extraction/
      test_engine.py           # import_text: tool dispatch, schema extension, provenance wiring
    interfaces/
      test_rest_api.py
      test_mcp_server.py
      test_cli.py
  integration/
    conftest.py                # Neo4j connection fixture
    test_neo4j_backend.py      # verifies full KBDB operation set against a live Neo4j instance
```

---

## 3. Graph Store Test Double

KBDB's storage backend is abstracted by `GraphSource`. The unit-test double is `MemoryKBSource` — a concrete subclass that mixes `KBSource` with `MemoryGraphSource` from `blue-data`. It requires no setup, holds state in-process, and is discarded after each test.

No separate mock or stub is needed: the same code path that runs against Neo4j in production runs against `MemoryKBSource` in tests.

**Fixture:**

```python
# tests/conftest.py

@pytest.fixture
def kb() -> MemoryKBSource:
    kb = MemoryKBSource(name="test-kb")
    asyncio.run(kb.create_database("test"))
    return kb
```

---

## 4. LLM Test Doubles

OpenAI calls within `ExtractionEngine.import_text` are tested using recorded HTTP responses via `pytest-recording` (VCR.py). On first run with `--record-mode=once`, real API responses are captured and saved as cassettes under `tests/cassettes/extraction/`. Subsequent runs replay the cassettes without hitting the network.

**Recording rules:**

- Cassettes are committed to the repository.
- When a prompt or tool set changes in a way that would alter the response, the cassette must be re-recorded: delete the cassette file and run with `--record-mode=once`.
- Tests fail if a cassette is missing and `--record-mode` is not set (default CI behavior).
- API keys must be present when recording; they are stripped from cassette files before committing.

**Example:**

```python
@pytest.mark.vcr
async def test_import_text_extracts_entity(kb):
    src = await kb.add_source("test", source="doc1", name="Test Document")
    await kb.import_text(
        "test",
        "Alice joined IBM as a software engineer in June 1998.",
        queries=["Where did Alice work?"],
        source_id=src,
    )
    results = await kb.answer("test", "Where did Alice work?")
    assert any(r.value == "IBM" for r in results)
```

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
        "test", "WORKED_AT", "Employment relationship", domain="EMPLOYEE", range_="COMPANY"
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

Each external interface has its own test module under `tests/unit/interfaces/`. These are still unit tests — they use `MemoryKBSource` and recorded cassettes. They exercise the translation layer (request parsing, response serialization, error mapping) rather than end-to-end behavior.

- **REST API** — tested via FastAPI's `TestClient` (sync wrapper, compatible with `pytest-asyncio`)
- **MCP Server** — tested by calling tool handler functions directly with a mock MCP context
- **CLI** — tested via Click's `CliRunner`

---

## 9. Integration Tests

Integration tests run the full operation set against a live Neo4j instance. They are structurally identical to unit tests but swap the fixture backend from `MemoryKBSource` to `Neo4jKBSource` pointed at a local or CI-managed Neo4j container.

Integration tests are excluded from the default `pytest` run (`testpaths = ["tests/unit"]` in `pyproject.toml`) and must be invoked explicitly:

```bash
pytest lib/tests/integration/ --neo4j-uri bolt://localhost:7687
```

---

## 10. Coverage

Coverage is measured via `pytest-cov` and reported in CI output. No minimum threshold is enforced.

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths    = ["tests/unit"]
addopts      = "--cov=src --cov-report=term-missing"
```

---

## 11. Running Tests

```bash
# Run all unit tests
pytest lib/tests/unit/

# Run with coverage
pytest lib/tests/unit/ --cov=src --cov-report=term-missing

# Re-record OpenAI cassettes for extraction tests
pytest lib/tests/unit/extraction/ --record-mode=once

# Run integration tests against a local Neo4j instance
pytest lib/tests/integration/ --neo4j-uri bolt://localhost:7687
```
