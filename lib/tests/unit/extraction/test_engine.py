import itertools
import json
from unittest.mock import AsyncMock, MagicMock, patch


def _tool_call_response(name, arguments):
    """Build a mock chat response whose assistant message calls tool *name*."""
    tc = MagicMock()
    tc.id = "call_0"
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
    """Build a mock chat response with no tool calls, ending the agent loop."""
    msg = MagicMock()
    msg.role = "assistant"
    msg.content = text
    msg.tool_calls = None

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _mock_openai_sequence(*responses):
    """Patch openai.AsyncOpenAI so every AsyncOpenAI() call shares one client
    whose chat.completions.create returns *responses* in order."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=list(responses))
    return MagicMock(return_value=mock_client)


def _mock_openai_endless(response):
    """Patch openai.AsyncOpenAI so every call returns the same tool-call response,
    forever — used to test the max_turns cutoff."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=itertools.repeat(response))
    return MagicMock(return_value=mock_client), mock_client


async def _seed_worked_at_with_start_date(kb):
    await kb.declare_class("test", "PERSON", "A human individual")
    await kb.declare_class("test", "COMPANY", "A business organization")
    rel_id = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    prop_id = await kb.declare_relation_property(
        "test", "START_DATE", "Start of employment", relation_id=rel_id
    )
    return rel_id, prop_id


# ------------------------------------------------------------------ #
# extraction is extraction-only: no schema-declaration tools          #
# ------------------------------------------------------------------ #

def test_extraction_tools_have_no_schema_declaration():
    from blue_data_kb_source.extraction.engine import _TOOLS

    tool_names = {t["function"]["name"] for t in _TOOLS}
    assert tool_names.isdisjoint({
        "declare_class", "declare_class_property",
        "declare_relation_type", "declare_relation_property",
    })
    # search_schema stays — extraction still resolves mentions against existing schema
    assert "search_schema" in tool_names


def test_system_prompt_instructs_skipping_unmatched_items():
    from blue_data_kb_source.extraction.engine import _SYSTEM_PROMPT_TEMPLATE

    assert "skip" in _SYSTEM_PROMPT_TEMPLATE.lower()


# ------------------------------------------------------------------ #
# max_turns                                                            #
# ------------------------------------------------------------------ #

async def test_import_text_stops_after_max_turns(kb):
    response = _tool_call_response("get_schema", {})
    factory, mock_client = _mock_openai_endless(response)
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text("test", "Some text.", expand_schema=False, max_turns=3)
    assert mock_client.chat.completions.create.call_count == 3


# ------------------------------------------------------------------ #
# search_schema resolves existing schema for extraction                #
# ------------------------------------------------------------------ #

async def test_search_schema_finds_existing_property_by_description(kb):
    _, prop_id = await _seed_worked_at_with_start_date(kb)

    calls = []
    responses = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "Start of employment", "type": "property"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text(
            "test", "Alice joined IBM in 1998.",
            expand_schema=False,
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert len(calls) == 1
    name, result = calls[0]
    assert name == "search_schema"
    ids = {el["id"] for el in result["schema_elements"]}
    assert prop_id in ids


async def test_search_schema_no_match_returns_empty(kb):
    await _seed_worked_at_with_start_date(kb)

    calls = []
    responses = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "zzz_unrelated_zzz", "type": "property"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text(
            "test", "Some text.",
            expand_schema=False,
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert calls[0][1]["schema_elements"] == []


# ------------------------------------------------------------------ #
# import_text(expand_schema=True) also runs SchemaManager.expand_schema #
# ------------------------------------------------------------------ #

async def test_import_text_runs_expand_schema_by_default(kb):
    calls = []
    with patch.object(kb._schema, "expand_schema", AsyncMock()) as mock_expand:
        responses = _mock_openai_sequence(_text_response("done"))
        with patch("openai.AsyncOpenAI", responses):
            await kb.import_text(
                "test", "Some text.",
                on_tool_result=lambda name, args, result: calls.append((name, result)),
            )
    mock_expand.assert_awaited_once()


async def test_import_text_skips_expand_schema_when_disabled(kb):
    with patch.object(kb._schema, "expand_schema", AsyncMock()) as mock_expand:
        responses = _mock_openai_sequence(_text_response("done"))
        with patch("openai.AsyncOpenAI", responses):
            await kb.import_text("test", "Some text.", expand_schema=False)
    mock_expand.assert_not_awaited()


# ------------------------------------------------------------------ #
# find_* tool dispatch (result contract)                              #
# ------------------------------------------------------------------ #

async def test_find_entity_tool_returns_id_name_description(kb):
    alice = await kb.add_entity("test", "Alice", description="An engineer")
    factory = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _text_response("done"),
    )
    results = []
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text(
            "test", "irrelevant",
            expand_schema=False,
            on_tool_result=lambda name, args, res: results.append((name, res)),
        )
    assert ("find_entity", {"id": alice, "name": "Alice", "description": "An engineer"}) in results


async def test_find_fact_tool_miss_returns_null_id(kb):
    factory = _mock_openai_sequence(
        _tool_call_response(
            "find_fact",
            {"head_entity_id": "x", "relation_id": "y", "tail_entity_id": "z"},
        ),
        _text_response("done"),
    )
    results = []
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text(
            "test", "irrelevant",
            expand_schema=False,
            on_tool_result=lambda name, args, res: results.append((name, res)),
        )
    assert ("find_fact", {"id": None}) in results


async def test_all_find_tools_honor_result_contract(kb):
    person = await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM")
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    factory = _mock_openai_sequence(
        _tool_call_response("find_class", {"name": "PERSON"}),
        _tool_call_response("find_relation_type", {"name": "MISSING"}),
        _tool_call_response(
            "find_fact",
            {"head_entity_id": alice, "relation_id": worked, "tail_entity_id": ibm},
        ),
        _tool_call_response("find_entity", {"name": "Nobody"}),
        _text_response("done"),
    )
    results = []
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text(
            "test", "irrelevant",
            expand_schema=False,
            on_tool_result=lambda name, args, res: results.append((name, res)),
        )
    assert ("find_class", {"id": person, "name": "PERSON", "description": ""}) in results
    assert ("find_relation_type", {"id": None}) in results
    assert ("find_fact", {"id": fact, "name": "Alice_WORKED_AT_IBM"}) in results
    assert ("find_entity", {"id": None}) in results
