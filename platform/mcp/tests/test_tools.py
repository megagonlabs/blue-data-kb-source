"""Tests for blue-data-kb-mcp tool functions.

Each test injects a pre-built in-memory KBSource as the server singleton so no
real graph backend, search backend, or (for most tests) OPENAI_API_KEY is needed.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_kb_source import KBSource

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


class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


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
    assert result["source"] == "Alice's résumé"
    assert result["source_original"] == "2020-01-15"


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
    assert result["metadata"][alice_id]["source"] == "Alice's résumé"


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
# Extraction (mocked OpenAI)
# ---------------------------------------------------------------------------

async def test_import_text_completes_with_no_tool_calls():
    factory = _mock_openai_sequence(_text_response("done"))
    with patch("openai.AsyncOpenAI", factory):
        result = json.loads(await import_text("Some text.", database=DB))
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

async def test_add_source_returns_id():
    result = json.loads(await add_source("doc2", name="Doc 2", original="2021-05-01", database=DB))
    assert "id" in result
