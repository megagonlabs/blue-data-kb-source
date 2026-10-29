"""Tests for blue-data-kb-mcp tool functions.

Each test injects a pre-built in-memory KBSource as the server singleton so no
real graph backend, search backend, or (for most tests) OPENAI_API_KEY is needed.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from blue_data_kb_source import MemoryKBSource

import blue_data_kb_mcp.config as config_module
from blue_data_kb_mcp.server import (
    add_entity,
    add_entity_extraction,
    add_entity_property,
    add_entity_property_extraction,
    add_fact,
    add_fact_extraction,
    add_fact_property,
    add_fact_property_extraction,
    add_is_subclass_of,
    add_source,
    answer,
    declare_class,
    declare_class_property,
    declare_relation,
    declare_relation_property,
    enrich,
    expand_schema,
    fetch_data,
    fetch_meta_schema,
    fetch_schema,
    get_metadata,
    import_text,
    search_data,
    search_schema,
    validate,
)

DB = "test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def kb():
    """Pre-populated in-memory KB: PERSON/COMPANY/WORKED_AT + Alice/IBM."""
    inst = MemoryKBSource(name="test-kb")
    inst.create_database(DB)
    person = await inst.declare_class(DB, "PERSON", "A human individual")
    company = await inst.declare_class(DB, "COMPANY", "A business organization")
    worked_at = await inst.declare_relation_type(
        DB, "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    age = await inst.declare_class_property(DB, "AGE", "Age in years", class_id=person)
    src = await inst.add_source(DB, "resume_alice", name="Alice's résumé", original="2020-01-15")
    alice = await inst.add_entity(
        DB, "Alice", class_id=person, text="Alice is a software engineer.", source_id=src
    )
    ibm = await inst.add_entity(DB, "IBM", class_id=company)
    fact = await inst.add_fact(
        DB, relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm,
        text="Alice joined IBM in June 1998.", source_id=src,
    )
    inst._test_ids = {  # type: ignore[attr-defined]
        "person": person, "company": company, "worked_at": worked_at, "age": age,
        "source": src, "alice": alice, "ibm": ibm, "fact": fact,
    }
    return inst


@pytest.fixture(autouse=True)
def inject_kb(kb):
    config_module._kb = kb
    yield
    config_module.reset_kb()


# ---------------------------------------------------------------------------
# Mock OpenAI helpers (mirroring lib/tests/unit's patterns)
# ---------------------------------------------------------------------------

def _tool_call_response(name, arguments, call_id="call_0"):
    tc = MagicMock()
    tc.id = call_id
    tc.type = "function"
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    msg = MagicMock()
    msg.role = "assistant"
    msg.content = None
    msg.tool_calls = [tc]
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _text_response(text=None):
    msg = MagicMock()
    msg.role = "assistant"
    msg.content = text
    msg.tool_calls = None
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _mock_openai_sequence(*responses):
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=list(responses))
    return MagicMock(return_value=mock_client)


# ---------------------------------------------------------------------------
# Schema tools (write)
# ---------------------------------------------------------------------------

async def test_declare_class_returns_id():
    result = json.loads(await declare_class("EMPLOYEE", "A person employed by an organization", database=DB))
    assert "id" in result


async def test_declare_class_idempotent(kb):
    person_id = kb._test_ids["person"]
    result = json.loads(await declare_class("PERSON", "A human individual", database=DB))
    assert result["id"] == person_id


async def test_declare_relation_requires_valid_domain():
    result = json.loads(await declare_relation("LIKES", "desc", "NO_SUCH_CLASS", "PERSON", database=DB))
    assert "error" in result


async def test_declare_relation_returns_id():
    await declare_class("THING", "A thing", database=DB)
    result = json.loads(await declare_relation("RELATES_TO", "desc", "THING", "THING", database=DB))
    assert "id" in result


async def test_declare_class_property_returns_id(kb):
    result = json.loads(
        await declare_class_property("NICKNAME", "desc", class_id=kb._test_ids["person"], database=DB)
    )
    assert "id" in result


async def test_declare_relation_property_returns_id(kb):
    result = json.loads(
        await declare_relation_property("END_DATE", "desc", relation_id=kb._test_ids["worked_at"], database=DB)
    )
    assert "id" in result


async def test_add_is_subclass_of(kb):
    employee = json.loads(await declare_class("EMPLOYEE", "desc", database=DB))["id"]
    result = json.loads(await add_is_subclass_of(employee, kb._test_ids["person"], database=DB))
    assert result["status"] == "ok"


async def test_add_is_subclass_of_mismatched_kinds_returns_error(kb):
    result = json.loads(await add_is_subclass_of(kb._test_ids["person"], kb._test_ids["worked_at"], database=DB))
    assert "error" in result


# ---------------------------------------------------------------------------
# Schema tools (read)
# ---------------------------------------------------------------------------

async def test_search_schema_finds_class():
    result = json.loads(await search_schema("PERSON", database=DB))
    assert any(r["name"] == "PERSON" for r in result)


async def test_search_schema_type_filter():
    result = json.loads(await search_schema("employment", type="relation", database=DB))
    assert all(r["type"].upper() == "RELATION" for r in result)


async def test_fetch_schema_contains_declared_classes():
    result = json.loads(await fetch_schema(database=DB))
    names = {e["name"] for e in result.get("entities", {}).values()}
    assert "PERSON" in names
    assert "COMPANY" in names


async def test_fetch_meta_schema_contains_source():
    result = json.loads(await fetch_meta_schema())
    assert "SOURCE" in result.get("entities", {})


# ---------------------------------------------------------------------------
# Knowledge tools (write)
# ---------------------------------------------------------------------------

async def test_add_entity_returns_id(kb):
    result = json.loads(await add_entity("Bob", class_id=kb._test_ids["person"], database=DB))
    assert "id" in result


async def test_add_fact_returns_id(kb):
    result = json.loads(await add_fact(
        head_entity_id=kb._test_ids["alice"],
        relation_id=kb._test_ids["worked_at"],
        tail_entity_id=kb._test_ids["ibm"],
        database=DB,
    ))
    assert "id" in result


async def test_add_entity_property_returns_id(kb):
    result = json.loads(await add_entity_property(
        kb._test_ids["alice"], kb._test_ids["age"], 30, database=DB
    ))
    assert "id" in result


async def test_add_fact_property_returns_id(kb):
    prop = json.loads(await declare_relation_property(
        "START_DATE", "desc", relation_id=kb._test_ids["worked_at"], database=DB
    ))["id"]
    result = json.loads(await add_fact_property(kb._test_ids["fact"], prop, "1998-06-01", database=DB))
    assert "id" in result


async def test_add_entity_extraction_returns_id(kb):
    result = json.loads(await add_entity_extraction(kb._test_ids["alice"], "More text about Alice.", database=DB))
    assert "id" in result


async def test_add_fact_extraction_returns_id(kb):
    result = json.loads(await add_fact_extraction(kb._test_ids["fact"], "More text about the fact.", database=DB))
    assert "id" in result


async def test_add_entity_property_extraction_requires_existing_value(kb):
    result = json.loads(
        await add_entity_property_extraction(kb._test_ids["alice"], kb._test_ids["age"], "text", database=DB)
    )
    assert "error" in result  # no AGE value set on Alice yet


async def test_add_fact_property_extraction_requires_existing_value(kb):
    prop = json.loads(await declare_relation_property(
        "END_DATE", "desc", relation_id=kb._test_ids["worked_at"], database=DB
    ))["id"]
    result = json.loads(await add_fact_property_extraction(kb._test_ids["fact"], prop, "text", database=DB))
    assert "error" in result


async def test_add_entity_unknown_class_returns_error():
    result = json.loads(await add_entity("Ghost", class_id="no-such-id", database=DB))
    assert "error" in result


# ---------------------------------------------------------------------------
# Knowledge tools (read)
# ---------------------------------------------------------------------------

async def test_search_data_finds_alice():
    result = json.loads(await search_data("Alice", database=DB))
    assert any(r["name"] == "Alice" for r in result)


async def test_fetch_data_compact_default():
    result = json.loads(await fetch_data(database=DB))
    assert isinstance(result, list)
    assert any(r.get("entity_name") == "Alice" for r in result)


async def test_fetch_data_raw_form():
    result = json.loads(await fetch_data(database=DB, compact=False))
    assert "nodes" in result and "edges" in result


async def test_fetch_data_detailed_includes_created(kb):
    result = json.loads(await fetch_data(database=DB, detailed=True))
    alice_row = next(r for r in result if r.get("entity_name") == "Alice")
    assert alice_row.get("created") is not None


async def test_get_metadata_entity(kb):
    result = json.loads(await get_metadata(kb._test_ids["alice"], database=DB))
    assert result["type"] == "ENTITY"
    assert result["name"] == "Alice"
    record = result["evidence"][0]
    assert record["source"] == "Alice's résumé"
    assert record["source_original"] == "2020-01-15"


async def test_get_metadata_wrong_type_returns_error(kb):
    result = json.loads(await get_metadata(kb._test_ids["person"], database=DB))
    assert "error" in result


# ---------------------------------------------------------------------------
# Query tools (mocked OpenAI)
# ---------------------------------------------------------------------------

_ANSWER_PAYLOAD = [{"entity": "Alice", "relation": "WORKED_AT", "value": "IBM", "provenance": None}]
_VALIDATE_PAYLOAD = {
    "supporting": [{"fact": "Alice_WORKED_AT_IBM", "head": "Alice", "tail": "IBM", "relation": "WORKED_AT"}],
    "counter": [],
}


def _return_result_response(payload):
    return _tool_call_response("return_result", {"result": payload})


async def test_answer_returns_structured_result():
    factory = _mock_openai_sequence(_return_result_response(_ANSWER_PAYLOAD))
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await answer("Where did Alice work?", database=DB))
    assert result["result"] == _ANSWER_PAYLOAD


async def test_answer_include_metadata(kb):
    alice_id = kb._test_ids["alice"]
    factory = _mock_openai_sequence(
        _tool_call_response("get_metadata", {"id": alice_id}),
        _return_result_response(_ANSWER_PAYLOAD),
    )
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await answer("Where did Alice work?", include_metadata=True, database=DB))
    assert alice_id in result["metadata"]
    assert result["metadata"][alice_id]["evidence"][0]["source"] == "Alice's résumé"


async def test_validate_returns_supporting():
    factory = _mock_openai_sequence(_return_result_response(_VALIDATE_PAYLOAD))
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await validate("Alice worked at IBM.", database=DB))
    assert len(result["supporting"]) == 1


async def test_enrich_returns_list():
    payload = [{"entity": "Alice → IBM", "relation": "WORKED_AT", "value": None, "distance": 1}]
    factory = _mock_openai_sequence(_return_result_response(payload))
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await enrich("Alice", database=DB))
    assert result[0]["relation"] == "WORKED_AT"


# ---------------------------------------------------------------------------
# Schema expansion (mocked OpenAI)
# ---------------------------------------------------------------------------

async def test_expand_schema_declares_class():
    factory = _mock_openai_sequence(
        _tool_call_response("declare_class", {"name": "PERSON", "description": "A human individual"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await expand_schema("Alice is a person.", dedup_mode="none", database=DB))
    assert result["status"] == "ok"
    schema = json.loads(await fetch_schema(database=DB))
    assert any(e["name"] == "PERSON" for e in schema["entities"].values())


# ---------------------------------------------------------------------------
# Extraction (mocked OpenAI)
# ---------------------------------------------------------------------------

def _mock_openai_endless_done():
    """A client that answers every call with a no-tool-calls turn.

    Extraction is a multi-stage pipeline whose stage count is free to change, so
    a fixed-length response sequence pins the test to today's number and breaks
    the next time a stage is added or removed.
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=lambda *a, **k: _text_response("done")
    )
    return MagicMock(return_value=mock_client)


def _schema_expansion_prompts(factory):
    """System prompts belonging to the schema-expansion agent."""
    return [
        call.kwargs["messages"][0]["content"]
        for call in factory.return_value.chat.completions.create.call_args_list
        if "schema expansion agent" in call.kwargs["messages"][0]["content"]
    ]


async def test_import_text_completes_with_no_tool_calls():
    factory = _mock_openai_endless_done()
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await import_text("Some text.", expand_schema=False, database=DB))
    assert result["status"] == "ok"
    # expand_schema=False, so the expansion agent must not have run.
    assert not _schema_expansion_prompts(factory)


async def test_import_text_default_also_runs_schema_expansion():
    factory = _mock_openai_endless_done()
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await import_text("Some text.", database=DB))
    assert result["status"] == "ok"
    assert _schema_expansion_prompts(factory)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

async def test_add_source_returns_id():
    result = json.loads(await add_source("doc2", name="Doc 2", original="2021-05-01", database=DB))
    assert "id" in result


# ---------------------------------------------------------------------------
# Configuration naming — shared with the `blue-kb` CLI
# ---------------------------------------------------------------------------

def test_model_resolution_matches_the_cli_precedence(monkeypatch):
    """Function-specific > KB-wide > family-wide > built-in default, the same
    chain the CLI resolves from its profile."""
    from blue_data_kb_mcp.config import model_for

    for var in ("BLUE_DATA_LLM_MODEL", "BLUE_DATA_KB_SOURCE_LLM_MODEL",
                "BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert model_for("EXTRACTION") == "gpt-4o"

    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "family")
    assert model_for("EXTRACTION") == "family"

    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_LLM_MODEL", "kb-wide")
    assert model_for("EXTRACTION") == "kb-wide"
    assert model_for("QUERY") == "kb-wide"

    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL", "extraction-only")
    assert model_for("EXTRACTION") == "extraction-only"
    assert model_for("QUERY") == "kb-wide"


def test_every_variable_this_server_reads_is_a_name_the_cli_also_uses():
    """The two are configured independently — this server never reads a profile —
    but a variable must mean the same thing in both. Anything here that the CLI
    does not also know is a name that has drifted."""
    import json
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "blue_data_kb_mcp"
    used = set()
    for f in src.glob("*.py"):
        used |= set(re.findall(r"BLUE_DATA_KB_SOURCE_[A-Z0-9_]+", f.read_text()))

    spec_path = (
        Path(__file__).resolve().parents[3]
        / "platform" / "cli" / "src" / "blue_data_kb_cli" / "profile_spec.json"
    )
    known = set(json.loads(spec_path.read_text()))
    # Per-database backend settings are configured by `blue-kb database register`
    # flags rather than by a profile parameter, so they have no spec entry —
    # the flag name is the CLI's counterpart and is checked by review, not here.
    backend_only = {
        "BLUE_DATA_KB_SOURCE_GRAPH_BACKEND",
        "BLUE_DATA_KB_SOURCE_SEARCH",
        "BLUE_DATA_KB_SOURCE_BM25_INDEX_DIR",
        "BLUE_DATA_KB_SOURCE_VECTOR_BACKEND",
        "BLUE_DATA_KB_SOURCE_EMBEDDING_MODEL",
        "BLUE_DATA_KB_SOURCE_NEO4J_URI",
        "BLUE_DATA_KB_SOURCE_NEO4J_USER",
        "BLUE_DATA_KB_SOURCE_NEO4J_DATABASE",
        "BLUE_DATA_KB_SOURCE_REDIS_HOST",
        "BLUE_DATA_KB_SOURCE_REDIS_PORT",
        "BLUE_DATA_KB_SOURCE_REDIS_DB",
        "BLUE_DATA_KB_SOURCE_REDIS_SCAN_PREFIX",
        # The CLI takes reference servers per command (`--mcp-server`); only this
        # server needs them as operator config, since its callers mustn't supply them.
        "BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS",
    }
    drifted = used - known - backend_only
    assert not drifted, f"MCP reads variables the CLI does not know: {sorted(drifted)}"


# ---------------------------------------------------------------------------
# Reference MCP servers are operator config, never tool arguments (#111)
# ---------------------------------------------------------------------------

_WORDNET = [{"transport": "stdio", "command": "blue-data-wordnet-mcp"}]


async def test_expansion_tools_do_not_take_server_definitions():
    from blue_data_kb_mcp.server import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    assert "mcp_servers" not in tools["expand_schema"].inputSchema["properties"]
    assert "schema_expansion_mcp_servers" not in tools["import_text"].inputSchema["properties"]


async def test_caller_supplied_server_is_never_spawned(tmp_path, monkeypatch):
    """The #111 PoC: a stdio "server" that is really a shell command, passed as a tool
    argument through the MCP protocol. It must never reach the spawn path."""
    from blue_data_kb_mcp.server import mcp

    monkeypatch.delenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS", raising=False)
    marker = tmp_path / "pwned"
    evil = [{"transport": "stdio", "command": "/bin/sh", "args": ["-c", f"touch {marker}"]}]
    with patch("blue_data_kb_source.schema.expansion._connect_mcp_servers") as connect:
        for tool, args in [
            ("expand_schema", {"text": "x", "reference": "mcp", "mcp_servers": evil, "database": DB}),
            ("import_text", {"text": "x", "schema_expansion_mode": "mcp",
                             "schema_expansion_mcp_servers": evil, "database": DB}),
        ]:
            try:
                await mcp.call_tool(tool, args)
            except Exception:
                pass  # rejecting the unknown argument outright is fine too
    connect.assert_not_called()
    assert not marker.exists()


async def test_reference_mcp_without_configured_servers_errors(monkeypatch, kb):
    monkeypatch.delenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS", raising=False)
    with patch.object(kb, "expand_schema", new=AsyncMock()) as expand:
        result = json.loads(await expand_schema("x", reference="mcp", database=DB))
    assert "BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS" in result["error"]
    expand.assert_not_called()


async def test_expand_schema_uses_operator_configured_servers(monkeypatch, kb):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS", json.dumps(_WORDNET))
    with patch.object(kb, "expand_schema", new=AsyncMock()) as expand:
        result = json.loads(await expand_schema("x", reference="mcp", database=DB))
    assert result == {"status": "ok"}
    assert expand.call_args.kwargs["mcp_servers"] == _WORDNET


async def test_expand_schema_llm_reference_connects_nothing(monkeypatch, kb):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS", json.dumps(_WORDNET))
    with patch.object(kb, "expand_schema", new=AsyncMock()) as expand:
        await expand_schema("x", database=DB)
    assert expand.call_args.kwargs["mcp_servers"] is None


async def test_import_text_uses_operator_configured_servers(monkeypatch, kb):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS", json.dumps(_WORDNET))
    with patch.object(kb, "import_text", new=AsyncMock()) as imp:
        result = json.loads(await import_text("x", schema_expansion_mode="mcp", database=DB))
    assert result == {"status": "ok"}
    assert imp.call_args.kwargs["schema_expansion_mcp_servers"] == _WORDNET


async def test_malformed_server_config_is_reported(monkeypatch, kb):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS", '{"command": "x"}')
    result = json.loads(await expand_schema("x", reference="mcp", database=DB))
    assert "must be a JSON list" in result["error"]


def test_neo4j_physical_database_is_operator_config(monkeypatch):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_NEO4J_DATABASE", "kb_store")
    assert config_module._neo4j_connection()["database"] == "kb_store"
    monkeypatch.delenv("BLUE_DATA_KB_SOURCE_NEO4J_DATABASE")
    assert "database" not in config_module._neo4j_connection()  # library default: neo4j


def test_redis_vector_connection_comes_from_the_environment(monkeypatch, tmp_path):
    """#148: every non-file vector backend used to get `connection: {}`, so Redis had
    no host/port and no scan_prefix — the index was never created."""
    for var in ("HOST", "PORT", "DB", "SCAN_PREFIX"):
        monkeypatch.delenv(f"BLUE_DATA_KB_SOURCE_REDIS_{var}", raising=False)
    assert config_module._redis_connection() == {"scan_prefix": "blue_kb:"}

    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_GRAPH_BACKEND", "file")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SEARCH", "semantic")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_VECTOR_BACKEND", "redis")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_REDIS_HOST", "redis.internal")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_REDIS_PORT", "6380")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_REDIS_DB", "2")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_REDIS_SCAN_PREFIX", "team_a:")
    pytest.importorskip("redis")
    vector = config_module._build_kb().properties["search"]["vector"]
    assert vector["_class"].endswith("RedisVectorSource")
    assert {k: vector["connection"][k] for k in ("host", "port", "db", "scan_prefix")} == {
        "host": "redis.internal", "port": 6380, "db": 2, "scan_prefix": "team_a:",
    }


def test_a_non_integer_redis_port_is_refused(monkeypatch):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_REDIS_PORT", "six")
    with pytest.raises(ValueError, match="BLUE_DATA_KB_SOURCE_REDIS_PORT must be an integer"):
        config_module._redis_connection()


async def test_add_fact_requires_head_relation_and_tail(kb):
    """#139: the library allows a partial fact; an agent's null id is a mistake."""
    from blue_data_kb_mcp.server import mcp

    person = await kb.declare_class(DB, "PERSON", "A person")
    alice = await kb.add_entity(DB, "Alice", class_id=person)
    bob = await kb.add_entity(DB, "Bob", class_id=person)
    tools = {t.name: t for t in await mcp.list_tools()}
    assert {"head_entity_id", "relation_id", "tail_entity_id"} <= set(tools["add_fact"].inputSchema["required"])
    facts_before = len(kb.match_nodes(DB, label="FACT"))
    result = json.loads(await add_fact(head_entity_id=alice, relation_id="", tail_entity_id=bob, database=DB))
    assert "add_fact requires relation_id" in result["error"]
    assert len(kb.match_nodes(DB, label="FACT")) == facts_before


def test_neo4j_tls_uri_is_dialled_over_tls(monkeypatch):
    """#121: the scheme went under `protocol`, which the driver never reads, so a
    `neo4j+s://` URI was dialled as plain `bolt://`."""
    pytest.importorskip("neo4j")
    import blue_data.source.graph.neo4j.neo4j_graph_source as ngs

    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_GRAPH_BACKEND", "neo4j")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SEARCH", "none")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_NEO4J_URI", "neo4j+s://kb.example.com:7687")
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD", "placeholder")
    with patch.object(ngs.GraphDatabase, "driver") as driver:
        config_module._build_kb()
    assert driver.call_args.args[0] == "neo4j+s://kb.example.com:7687"


def test_neo4j_uri_with_an_unknown_scheme_is_refused(monkeypatch):
    monkeypatch.setenv("BLUE_DATA_KB_SOURCE_NEO4J_URI", "https://kb.example.com:7687")
    with pytest.raises(ValueError, match="BLUE_DATA_KB_SOURCE_NEO4J_URI: unsupported Neo4j URI scheme"):
        config_module._neo4j_connection()


async def test_expansion_tools_apply_the_overall_timeout(monkeypatch, kb):
    monkeypatch.delenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_TIMEOUT", raising=False)
    with patch.object(kb, "expand_schema", new=AsyncMock()) as expand, \
         patch.object(kb, "import_text", new=AsyncMock()) as imp:
        await expand_schema("x", database=DB)
        await import_text("x", database=DB)
        assert expand.call_args.kwargs["timeout"] == 1800
        assert imp.call_args.kwargs["schema_expansion_timeout"] == 1800
        monkeypatch.setenv("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_TIMEOUT", "0")
        await expand_schema("x", database=DB)
        assert expand.call_args.kwargs["timeout"] is None
