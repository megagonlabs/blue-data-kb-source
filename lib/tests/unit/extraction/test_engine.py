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
    whose chat.completions.create returns *responses* in order across all calls
    (the outer extraction agent and any nested judge call share this queue)."""
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
# schema_discovery_mode controls tool availability                    #
# ------------------------------------------------------------------ #

async def test_none_mode_omits_search_schema_tool(kb):
    factory = _mock_openai_sequence(_text_response("done"))
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text("test", "Some text.", schema_discovery_mode="none")
    call_kwargs = factory.return_value.chat.completions.create.call_args.kwargs
    tool_names = {t["function"]["name"] for t in call_kwargs["tools"]}
    assert "search_schema" not in tool_names


async def test_default_mode_includes_search_schema_tool(kb):
    factory = _mock_openai_sequence(_text_response("done"))
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text("test", "Some text.")
    call_kwargs = factory.return_value.chat.completions.create.call_args.kwargs
    tool_names = {t["function"]["name"] for t in call_kwargs["tools"]}
    assert "search_schema" in tool_names


# ------------------------------------------------------------------ #
# max_turns                                                            #
# ------------------------------------------------------------------ #

async def test_import_text_stops_after_max_turns(kb):
    response = _tool_call_response("get_schema", {})
    factory, mock_client = _mock_openai_endless(response)
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text("test", "Some text.", max_turns=3)
    assert mock_client.chat.completions.create.call_count == 3


# ------------------------------------------------------------------ #
# schema_discovery_mode="search"                                       #
# ------------------------------------------------------------------ #

async def test_search_mode_finds_similar_property_by_description(kb):
    _, prop_id = await _seed_worked_at_with_start_date(kb)

    calls = []
    responses = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "Start of employment", "type": "property"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text(
            "test", "Alice joined IBM in 1998.",
            schema_discovery_mode="search",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert len(calls) == 1
    name, result = calls[0]
    assert name == "search_schema"
    ids = {el["id"] for el in result["schema_elements"]}
    assert prop_id in ids


async def test_search_mode_no_match_returns_empty(kb):
    await _seed_worked_at_with_start_date(kb)

    calls = []
    responses = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "zzz_unrelated_zzz", "type": "property"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text(
            "test", "Some text.",
            schema_discovery_mode="search",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert calls[0][1]["schema_elements"] == []


# ------------------------------------------------------------------ #
# schema_discovery_mode="llm"                                          #
# ------------------------------------------------------------------ #

async def test_llm_mode_finds_synonym_property(kb):
    _, prop_id = await _seed_worked_at_with_start_date(kb)

    calls = []
    responses = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "date the person joined the company", "type": "property"}),
        _tool_call_response("return_matches", {"matching_ids": [prop_id]}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text(
            "test", "Alice joined IBM in 1998.",
            schema_discovery_mode="llm",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert len(calls) == 1
    name, result = calls[0]
    assert name == "search_schema"
    ids = {el["id"] for el in result["schema_elements"]}
    assert ids == {prop_id}


async def test_llm_mode_no_match_returns_empty(kb):
    await _seed_worked_at_with_start_date(kb)

    calls = []
    responses = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "favorite color", "type": "property"}),
        _tool_call_response("return_matches", {"matching_ids": []}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text(
            "test", "Some text.",
            schema_discovery_mode="llm",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert calls[0][1]["schema_elements"] == []
