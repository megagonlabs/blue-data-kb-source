"""Tests for SchemaManager.expand_schema / SchemaExpansionEngine.

reference="llm" cases mock openai.AsyncOpenAI entirely (same pattern as
extraction/test_engine.py). The reference="mcp" test connects to the real,
already-installed blue-data-wordnet-mcp package over stdio — only the model's
decisions are scripted (via a mocked openai.AsyncOpenAI), so the MCP half of
the round trip (spawn, list_tools, call_tool) is real, proving the generic
bridge actually drives an external server's tools.
"""
import itertools
import json
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
    return MagicMock(return_value=mock_client), mock_client


def _mock_openai_endless(response):
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=itertools.repeat(response))
    return MagicMock(return_value=mock_client), mock_client


# ------------------------------------------------------------------ #
# reference="llm"                                                      #
# ------------------------------------------------------------------ #

async def test_expand_schema_declares_class(kb):
    factory, _ = _mock_openai_sequence(
        _tool_call_response("declare_class", {"name": "PERSON", "description": "A human individual"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Alice is a person.", dedup_mode="none")

    schema = await kb.fetch_database_schema("test")
    assert "PERSON" in schema.entities


async def test_expand_schema_dedup_mode_none_omits_search_schema_tool():
    from blue_data_kb_source import MemoryKBSource

    kb = MemoryKBSource(name="t")
    kb.create_database("test")
    factory, mock_client = _mock_openai_sequence(_text_response("done"))
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Some text.", dedup_mode="none")
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    tool_names = {t["function"]["name"] for t in call_kwargs["tools"]}
    assert "search_schema" not in tool_names
    assert "declare_class" in tool_names


async def test_expand_schema_prompt_warns_against_entity_classes(kb):
    """Regression guard for declaring a CLASS per specific named individual (e.g. ALICE,
    UNITED_NATIONS) instead of the general kind it belongs to (PERSON, ORGANIZATION) — a
    prompt/tool-description-only fix, so this just pins the guidance stays present."""
    factory, mock_client = _mock_openai_sequence(_text_response("done"))
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Alice also worked at United Nations.", dedup_mode="none")

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    system_prompt = call_kwargs["messages"][0]["content"]
    assert "ALICE" in system_prompt and "UNITED_NATIONS" in system_prompt
    assert "never a CLASS of its own" in system_prompt

    tools = {t["function"]["name"]: t["function"]["description"] for t in call_kwargs["tools"]}
    assert "NEVER call this for a specific named individual" in tools["declare_class"]
    assert "never a specific fact between two named individuals" in tools["declare_relation_type"]


async def test_expand_schema_dedup_mode_search_finds_existing_property(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    rel_id = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    prop_id = await kb.declare_relation_property(
        "test", "START_DATE", "Start of employment", relation_id=rel_id
    )

    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "Start of employment", "type": "property"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice joined IBM in 1998.", dedup_mode="search",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert len(calls) == 1
    ids = {el["id"] for el in calls[0][1]["schema_elements"]}
    assert prop_id in ids


async def test_expand_schema_dedup_mode_llm_judges_synonym(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    rel_id = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY",
    )
    prop_id = await kb.declare_relation_property(
        "test", "START_DATE", "Start of employment", relation_id=rel_id
    )

    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "date the person joined the company", "type": "property"}),
        _tool_call_response("return_matches", {"matching_ids": [prop_id]}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice joined IBM in 1998.", dedup_mode="llm",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert len(calls) == 1
    ids = {el["id"] for el in calls[0][1]["schema_elements"]}
    assert ids == {prop_id}


async def test_expand_schema_stops_after_max_turns(kb):
    response = _tool_call_response("get_schema", {})
    factory, mock_client = _mock_openai_endless(response)
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Some text.", max_turns=3)
    assert mock_client.chat.completions.create.call_count == 3


async def test_expand_schema_mcp_reference_requires_servers(kb):
    from blue_data_kb_source.types import SchemaError

    with pytest.raises(SchemaError):
        await kb.expand_schema("test", "Some text.", reference="mcp", mcp_servers=None)


# ------------------------------------------------------------------ #
# hierarchy / property reuse / auto-dedup                              #
# ------------------------------------------------------------------ #

async def test_expand_schema_add_subclass_of_links_hierarchy(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    employee = await kb.declare_class("test", "EMPLOYEE", "A person employed by an organization")

    factory, _ = _mock_openai_sequence(
        _tool_call_response("add_subclass_of", {"child_id": employee, "parent_id": person}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Bob is an employee.", dedup_mode="none")

    schema = await kb.fetch_database_schema("test")
    assert any(
        r["name"] == "IS_SUBCLASS_OF" and r["source"] == "EMPLOYEE" and r["target"] == "PERSON"
        for r in schema.relations.values()
    )


async def test_expand_schema_attach_property_reuses_existing_property(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    name_prop = await kb.declare_class_property("test", "NAME", "Name of the individual", class_id=person)

    factory, _ = _mock_openai_sequence(
        _tool_call_response("attach_property", {"target_id": company, "property_id": name_prop}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "IBM is a company.", dedup_mode="none")

    schema = await kb.fetch_database_schema("test")
    assert "NAME" in schema.entities["COMPANY"]["contents"]["attributes"]
    # reused, not duplicated: still the same single PROPERTY node
    props = await kb.search_schema("test", "NAME", type_filter="property")
    assert [p.id for p in props if p.name == "NAME"] == [name_prop]


async def test_expand_schema_rejects_taxonomic_relation_type(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    animal = await kb.declare_class("test", "ANIMAL", "A living organism")

    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response(
            "declare_relation_type",
            {"name": "IS_A", "description": "taxonomy", "domain": "PERSON", "range": "ANIMAL"},
        ),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "A person is an animal.", dedup_mode="none",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert "error" in calls[0][1]
    schema = await kb.fetch_database_schema("test")
    assert not any(r["name"] == "IS_A" for r in schema.relations.values())
    _ = person, animal


async def test_expand_schema_auto_dedup_catches_relation_synonym_without_search(kb):
    """declare_relation_type is auto-checked for near-duplicates even when the model skips
    search_schema and calls it directly — closing the gap where only exact-name matches were
    caught (e.g. WORKED_AT vs EMPLOYED_AT)."""
    person = await kb.declare_class("test", "PERSON", "A human individual")
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )

    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response(
            "declare_relation_type",
            {"name": "EMPLOYED_AT", "description": "Employment relationship", "domain": "PERSON", "range": "COMPANY"},
        ),
        _tool_call_response("return_matches", {"matching_ids": [worked_at]}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice is employed at IBM.", dedup_mode="llm",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert calls[0] == ("declare_relation_type", {"id": worked_at})
    schema = await kb.fetch_database_schema("test")
    assert not any(r["name"] == "EMPLOYED_AT" for r in schema.relations.values())
    _ = person, company


# ------------------------------------------------------------------ #
# reference="mcp" — live bridge to the real blue-data-wordnet-mcp server #
# ------------------------------------------------------------------ #

requires_wordnet_mcp = pytest.mark.skipif(
    shutil.which("blue-data-wordnet-mcp") is None,
    reason="blue-data-wordnet-mcp not installed (platform/wordnet-mcp)",
)


@requires_wordnet_mcp
async def test_expand_schema_mcp_reference_bridges_external_tools(kb):
    """The model (mocked) calls a real WordNet tool over a live stdio MCP
    connection, then declares a class — proving the generic bridge actually
    lists and calls an external server's tools, not just wires in stub names."""
    seen_tools: dict[str, list[str]] = {}

    async def create_side_effect(*args, **kwargs):
        if "first" not in seen_tools:
            seen_tools["first"] = [t["function"]["name"] for t in kwargs["tools"]]
            return _tool_call_response(
                "mcp__server0__lookup_synsets", {"word": "dog", "pos": "n"}, call_id="c1"
            )
        if "second" not in seen_tools:
            seen_tools["second"] = True
            return _tool_call_response(
                "declare_class", {"name": "DOG", "description": "A domestic dog, canine"}, call_id="c2"
            )
        return _text_response("done")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=create_side_effect)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        await kb.expand_schema(
            "test",
            "The dog barked all night.",
            reference="mcp",
            mcp_servers=[{"transport": "stdio", "command": "blue-data-wordnet-mcp"}],
            dedup_mode="none",
        )

    assert any(name.startswith("mcp__server0__lookup_synsets") for name in seen_tools["first"])
    schema = await kb.fetch_database_schema("test")
    assert "DOG" in schema.entities
