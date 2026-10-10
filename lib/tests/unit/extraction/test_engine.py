import asyncio
import itertools
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


def _tool_replies(factory):
    """Every tool-result message the agent sent back, across all stages.

    `call_args_list` holds a *reference* to the message list `_run_stage`
    keeps appending to, so positional indexing into it sees the end state of
    the stage, not the state at that call. Collect by role instead.
    """
    seen: list[dict] = []
    for call in factory.return_value.chat.completions.create.call_args_list:
        for m in call.kwargs["messages"]:
            if m.get("role") == "tool" and m not in seen:
                seen.append(m)
    return seen


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


def _mock_openai_with_delay(delay):
    """Patch openai.AsyncOpenAI so every call sleeps `delay` seconds before
    returning a 'done' response — used to prove concurrent chunks actually
    overlap in wall-clock time rather than running one after another."""
    async def _delayed(*args, **kwargs):
        await asyncio.sleep(delay)
        return _text_response("done")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=_delayed)
    return MagicMock(return_value=mock_client)


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
# related-data context injection                                      #
# ------------------------------------------------------------------ #

def _four_stage_done():
    """One immediate 'done' per extraction stage (entities/relations/
    entity-properties/fact-properties) — nothing to do in any of them."""
    return _mock_openai_sequence(
        _text_response("done"), _text_response("done"),
        _text_response("done"), _text_response("done"),
    )


async def test_related_data_absent_without_search_indexer(kb):
    """No search_indexer configured (the default `blue-kb` fixture): the system
    prompt says so plainly rather than silently omitting the section."""
    responses = _four_stage_done()
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)
    # Every stage's prompt carries the same related-data section; check stage 1's.
    system_prompt = factory.return_value.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert "search not configured" in system_prompt


async def test_related_data_injects_matching_existing_entities():
    from blue_data_kb_source import MemoryKBSource

    kb = MemoryKBSource(name="related-data-test", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    await kb.add_entity("test", "IBM", class_id=company)

    responses = _four_stage_done()
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "IBM reported strong earnings.", expand_schema=False)
    system_prompt = factory.return_value.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert "IBM (COMPANY)" in system_prompt


async def test_related_data_k_zero_skips_the_search_call():
    from blue_data_kb_source import MemoryKBSource

    kb = MemoryKBSource(name="related-data-k0-test", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    await kb.add_entity("test", "IBM", class_id=company)

    responses = _four_stage_done()
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "IBM reported strong earnings.", expand_schema=False, related_data_k=0)
    system_prompt = factory.return_value.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert "search not configured" in system_prompt


# ------------------------------------------------------------------ #
# max_turns                                                            #
# ------------------------------------------------------------------ #

async def test_import_text_stops_after_max_turns(kb):
    # get_schema is offered in every stage, so an endless get_schema loop hits
    # max_turns once per stage: 4 stages x max_turns=3 = 12 calls total.
    response = _tool_call_response("get_schema", {})
    factory, mock_client = _mock_openai_endless(response)
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text("test", "Some text.", expand_schema=False, max_turns=3)
    assert mock_client.chat.completions.create.call_count == 12


# ------------------------------------------------------------------ #
# search_schema resolves existing schema for extraction                #
# ------------------------------------------------------------------ #

async def test_search_schema_finds_existing_property_by_description(kb):
    _, prop_id = await _seed_worked_at_with_start_date(kb)

    calls = []
    # search_schema is offered in every stage; call it once in stage 1, then
    # let the remaining 3 stages end immediately (no tool calls).
    responses = _mock_openai_sequence(
        _tool_call_response("search_schema", {"query": "Start of employment", "type": "property"}),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
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
        _text_response("done"), _text_response("done"), _text_response("done"),
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
        # One immediate "done" per stage (4 stages), each ending with no tool calls.
        responses = _mock_openai_sequence(
            _text_response("done"), _text_response("done"),
            _text_response("done"), _text_response("done"),
        )
        with patch("openai.AsyncOpenAI", responses):
            await kb.import_text(
                "test", "Some text.",
                on_tool_result=lambda name, args, result: calls.append((name, result)),
            )
    mock_expand.assert_awaited_once()


async def test_import_text_skips_expand_schema_when_disabled(kb):
    with patch.object(kb._schema, "expand_schema", AsyncMock()) as mock_expand:
        responses = _mock_openai_sequence(
            _text_response("done"), _text_response("done"),
            _text_response("done"), _text_response("done"),
        )
        with patch("openai.AsyncOpenAI", responses):
            await kb.import_text("test", "Some text.", expand_schema=False)
    mock_expand.assert_not_awaited()


# ------------------------------------------------------------------ #
# find_* tool dispatch (result contract)                              #
# ------------------------------------------------------------------ #

async def test_find_entity_tool_returns_id_name_description(kb):
    alice = await kb.add_entity("test", "Alice", description="An engineer")
    # find_entity is stage-1-only; let the remaining 3 stages end immediately.
    factory = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    results = []
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text(
            "test", "irrelevant",
            expand_schema=False,
            on_tool_result=lambda name, args, res: results.append((name, res)),
        )
    assert ("find_entity", {"id": alice, "name": "Alice", "description": "An engineer", "classes": []}) in results


async def test_find_entity_tool_falls_back_to_normalized_match(kb):
    ibm = await kb.add_entity("test", "IBM", description="A company")
    factory = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "ibm"}),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    results = []
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text(
            "test", "irrelevant",
            expand_schema=False,
            on_tool_result=lambda name, args, res: results.append((name, res)),
        )
    assert ("find_entity", {"id": ibm, "name": "IBM", "description": "A company", "classes": []}) in results


async def test_find_entity_tool_unrelated_name_still_returns_null_id(kb):
    await kb.add_entity("test", "IBM")
    factory = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Nobody"}),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    results = []
    with patch("openai.AsyncOpenAI", factory):
        await kb.import_text(
            "test", "irrelevant",
            expand_schema=False,
            on_tool_result=lambda name, args, res: results.append((name, res)),
        )
    assert ("find_entity", {"id": None}) in results


async def test_find_fact_tool_miss_returns_null_id(kb):
    """A genuine miss — every argument is a real id, the fact just isn't there."""
    person = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm = await kb.add_entity("test", "IBM", class_id=company)

    # find_fact is a relation-stage tool: the entity stage ends immediately,
    # then the call happens in stage 2, then the remaining stages end.
    factory = _mock_openai_sequence(
        _text_response("done"),
        _tool_call_response(
            "find_fact",
            {"head_entity_id": alice, "relation_id": worked, "tail_entity_id": ibm},
        ),
        _text_response("done"),
        _text_response("done"), _text_response("done"),
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
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    # find_class/find_entity are stage-1 tools; find_relation_type/find_fact are
    # stage-2 tools — each stage gets its pair of calls, then ends.
    factory = _mock_openai_sequence(
        _tool_call_response("find_class", {"name": "PERSON"}),
        _tool_call_response("find_entity", {"name": "Nobody"}),
        _text_response("done"),
        _tool_call_response("find_relation_type", {"name": "MISSING"}),
        _tool_call_response(
            "find_fact",
            {"head_entity_id": alice, "relation_id": worked, "tail_entity_id": ibm},
        ),
        _text_response("done"),
        _text_response("done"),
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


# ------------------------------------------------------------------ #
# staged extraction: entities -> relations -> properties               #
# ------------------------------------------------------------------ #

def test_stage_tool_sets_are_scoped_to_their_purpose():
    from blue_data_kb_source.extraction.engine import (
        STAGE_ENTITIES, STAGE_ENTITY_PROPERTIES, STAGE_FACT_PROPERTIES, STAGE_RELATIONS,
        _STAGE_TOOL_NAMES,
    )

    entity_tools = set(_STAGE_TOOL_NAMES[STAGE_ENTITIES])
    relation_tools = set(_STAGE_TOOL_NAMES[STAGE_RELATIONS])
    assert {"find_entity", "add_entity"} <= entity_tools
    assert "add_fact" not in entity_tools

    assert {"find_fact", "add_fact", "find_relation_type"} <= relation_tools
    # Relations are handed already-resolved entities by id in the prompt —
    # this stage must never rediscover or invent one.
    assert "find_entity" not in relation_tools
    assert "add_entity" not in relation_tools

    # find_literal is the property stages' check-before-write, the counterpart
    # of find_entity/find_fact in the stages above.
    assert set(_STAGE_TOOL_NAMES[STAGE_ENTITY_PROPERTIES]) == {
        "get_schema", "search_schema", "find_literal",
        "add_entity_property_value", "link_extraction",
    }
    assert set(_STAGE_TOOL_NAMES[STAGE_FACT_PROPERTIES]) == {
        "get_schema", "search_schema", "find_literal",
        "add_fact_property_value", "link_extraction",
    }
    # …and neither property stage may create or rediscover entities or facts.
    for stage in (STAGE_ENTITY_PROPERTIES, STAGE_FACT_PROPERTIES):
        assert set(_STAGE_TOOL_NAMES[stage]).isdisjoint(
            {"find_entity", "add_entity", "find_fact", "add_fact"}
        )


def test_every_stage_offers_get_schema_and_search_schema():
    from blue_data_kb_source.extraction.engine import STAGES, _STAGE_TOOL_NAMES

    for stage in STAGES:
        assert "get_schema" in _STAGE_TOOL_NAMES[stage]
        assert "search_schema" in _STAGE_TOOL_NAMES[stage]


async def test_relation_stage_prompt_lists_entities_resolved_by_entity_stage(kb):
    person = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice", "class_id": person}),
        _tool_call_response("add_entity", {"name": "IBM", "class_id": company}),
        _text_response("done"),   # ends stage 1 (entities)
        _text_response("done"),   # stage 2 (relations) — prompt captured below
        _text_response("done"),   # stage 3 (entity properties)
        _text_response("done"),   # stage 4 (fact properties)
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    stage2_prompt = factory.return_value.chat.completions.create.call_args_list[3].kwargs["messages"][0]["content"]
    assert "Alice (PERSON)" in stage2_prompt
    assert "IBM (COMPANY)" in stage2_prompt


async def test_staged_pipeline_creates_fact_from_stage1_resolved_entity_ids(kb):
    """End-to-end: the relation stage never calls find_entity/add_entity — it
    uses the ids the entity stage already resolved, exactly as the prompt
    hands them over."""
    person = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm = await kb.add_entity("test", "IBM", class_id=company)

    responses = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _tool_call_response("find_entity", {"name": "IBM"}),
        _text_response("done"),
        _tool_call_response("find_relation_type", {"name": "WORKED_AT"}),
        _tool_call_response(
            "add_fact", {"head_entity_id": alice, "tail_entity_id": ibm, "relation_id": worked}
        ),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    facts = kb.match_nodes("test", label="FACT")
    assert len(facts) == 1
    heads = kb.get_neighbors("test", facts[0]["id"], edge_type="HEAD", direction="out")
    tails = kb.get_neighbors("test", facts[0]["id"], edge_type="TAIL", direction="out")
    assert heads[0]["node"]["id"] == alice
    assert tails[0]["node"]["id"] == ibm


async def test_fact_property_stage_prompt_lists_facts_resolved_by_relation_stage(kb):
    person = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm = await kb.add_entity("test", "IBM", class_id=company)

    responses = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _tool_call_response("find_entity", {"name": "IBM"}),
        _text_response("done"),
        _tool_call_response("find_relation_type", {"name": "WORKED_AT"}),
        _tool_call_response(
            "add_fact", {"head_entity_id": alice, "tail_entity_id": ibm, "relation_id": worked}
        ),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    # Fact properties now run *before* entity properties, so this is call 6.
    fact_prop_prompt = factory.return_value.chat.completions.create.call_args_list[6].kwargs["messages"][0]["content"]
    assert f"Alice (PERSON, id={alice}) —WORKED_AT→ IBM (COMPANY, id={ibm})" in fact_prop_prompt
    # …and it is given the entities too, not just the facts.
    assert f"id={alice}  Alice (PERSON)" in fact_prop_prompt


# ------------------------------------------------------------------ #
# effort: repeats the whole 4-stage pipeline low=1/medium=2/high=3    #
# ------------------------------------------------------------------ #

async def test_effort_low_runs_pipeline_once(kb):
    # 4 stages x 1 pass, each ending immediately.
    responses = _mock_openai_sequence(*[_text_response("done")] * 4)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, effort="low")
    assert factory.return_value.chat.completions.create.call_count == 4


async def test_effort_medium_runs_pipeline_twice(kb):
    responses = _mock_openai_sequence(*[_text_response("done")] * 8)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, effort="medium")
    assert factory.return_value.chat.completions.create.call_count == 8


async def test_effort_high_runs_pipeline_three_times(kb):
    responses = _mock_openai_sequence(*[_text_response("done")] * 12)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, effort="high")
    assert factory.return_value.chat.completions.create.call_count == 12


async def test_effort_invalid_value_raises(kb):
    from blue_data_kb_source.types import ExtractionError

    with pytest.raises(ExtractionError):
        await kb.import_text("test", "Some text.", expand_schema=False, effort="extreme")


async def test_effort_low_prompt_carries_no_pass_note(kb):
    responses = _mock_openai_sequence(*[_text_response("done")] * 4)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, effort="low")
    prompt = factory.return_value.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert "extraction pass" not in prompt.lower()


async def test_effort_medium_first_pass_announces_more_passes_without_claiming_a_prior_one(kb):
    responses = _mock_openai_sequence(*[_text_response("done")] * 8)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, effort="medium")
    first_pass_prompt = factory.return_value.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert "pass 1 of 2" in first_pass_prompt
    assert "prior pass" not in first_pass_prompt.lower()


async def test_effort_medium_second_pass_references_the_prior_pass(kb):
    responses = _mock_openai_sequence(*[_text_response("done")] * 8)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, effort="medium")
    second_pass_prompt = factory.return_value.chat.completions.create.call_args_list[4].kwargs["messages"][0]["content"]
    assert "pass 2 of 2" in second_pass_prompt
    assert "prior pass" in second_pass_prompt.lower()


async def test_effort_second_pass_related_data_sees_entities_from_first_pass():
    from blue_data_kb_source import MemoryKBSource

    kb = MemoryKBSource(name="effort-related-data-test", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    person = await kb.declare_class("test", "PERSON", "")

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice", "class_id": person}),
        _text_response("done"),   # ends pass-1 entity stage
        _text_response("done"),   # pass-1 relations
        _text_response("done"),   # pass-1 entity properties
        _text_response("done"),   # pass-1 fact properties
        _text_response("done"),   # pass-2 entity stage — prompt captured below
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice works somewhere.", expand_schema=False, effort="medium")

    pass2_entity_prompt = factory.return_value.chat.completions.create.call_args_list[5].kwargs["messages"][0]["content"]
    assert "Alice (PERSON)" in pass2_entity_prompt


# ------------------------------------------------------------------ #
# max_concurrency: chunk a long text and run chunks concurrently      #
# ------------------------------------------------------------------ #

def test_split_into_chunks_single_block_is_never_split():
    from blue_data_kb_source.extraction.engine import _split_into_chunks

    text = "Alice joined IBM in 1998."
    assert _split_into_chunks(text, 1) == [text]
    assert _split_into_chunks(text, 5) == [text]  # no paragraph breaks to split on


def test_split_into_chunks_respects_max_chunks_one():
    from blue_data_kb_source.extraction.engine import _split_into_chunks

    text = "Para one.\n\nPara two.\n\nPara three.\n\nPara four."
    assert _split_into_chunks(text, 1) == [text]


def test_split_into_chunks_splits_contiguous_paragraphs_evenly():
    from blue_data_kb_source.extraction.engine import _split_into_chunks

    text = "Para one.\n\nPara two.\n\nPara three.\n\nPara four."
    chunks = _split_into_chunks(text, 2)
    assert chunks == ["Para one.\n\nPara two.", "Para three.\n\nPara four."]


def test_split_into_chunks_caps_at_paragraph_count():
    from blue_data_kb_source.extraction.engine import _split_into_chunks

    text = "Para one.\n\nPara two."
    chunks = _split_into_chunks(text, 10)
    assert chunks == ["Para one.", "Para two."]


async def test_max_concurrency_one_is_unchanged_from_default(kb):
    responses = _mock_openai_sequence(*[_text_response("done")] * 4)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, max_concurrency=1)
    assert factory.return_value.chat.completions.create.call_count == 4


async def test_max_concurrency_runs_chunks_concurrently_not_sequentially(kb):
    # 3 paragraphs -> 3 chunks x 4 stages x 1 pass = 12 calls if fully
    # sequential, but each chunk's 4 stages run one after another within that
    # chunk while chunks themselves overlap. If sequential end-to-end, 12
    # calls x delay would take ~0.6s; concurrent chunks should land close to
    # one chunk's own 4-call critical path (~0.2s).
    text = "Para one.\n\nPara two.\n\nPara three."
    delay = 0.05
    responses = _mock_openai_with_delay(delay)
    with patch("openai.AsyncOpenAI", responses):
        start = time.monotonic()
        await kb.import_text("test", text, expand_schema=False, max_concurrency=3)
        elapsed = time.monotonic() - start
    assert elapsed < 8 * delay  # well under the 12x-delay fully-sequential bound


# ------------------------------------------------------------------ #
# entity stage sweeps the schema class by class, not the text name    #
# by name — the framing that made non-proper-noun classes (SKILL)     #
# get skipped wholesale. See issue #91.                               #
# ------------------------------------------------------------------ #

async def _resume_schema(kb):
    """PERSON + SKILL: one class with proper-noun instances, one without."""
    person = await kb.declare_class("test", "PERSON", "A human individual")
    skill = await kb.declare_class("test", "SKILL", "User skill or capability")
    return person, skill


def test_entity_workflow_is_class_driven_and_names_non_proper_nouns():
    from blue_data_kb_source.extraction.engine import STAGE_ENTITIES, _STAGE_WORKFLOWS

    workflow = _STAGE_WORKFLOWS[STAGE_ENTITIES]
    assert "{classes}" in workflow, "entity stage must render the class checklist"
    assert "class by class" in workflow
    assert "proper noun" in workflow


def test_system_prompt_glossary_shows_a_common_noun_instance():
    from blue_data_kb_source.extraction.engine import _SYSTEM_PROMPT_TEMPLATE

    # Every example used to be a proper noun (Alice, IBM), which is what
    # pushed the model toward a named-entity reading of "entity".
    assert "SKILL" in _SYSTEM_PROMPT_TEMPLATE
    assert "Python" in _SYSTEM_PROMPT_TEMPLATE


async def test_entity_stage_prompt_lists_every_class_with_id_and_description(kb):
    person, skill = await _resume_schema(kb)

    responses = _mock_openai_sequence(*[_text_response("done")] * 4)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False)

    stage1_prompt = factory.return_value.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert f"id={person}  PERSON — A human individual" in stage1_prompt
    assert f"id={skill}  SKILL — User skill or capability" in stage1_prompt


async def test_entity_stage_checklist_is_name_sorted_for_a_stable_prompt(kb):
    await kb.declare_class("test", "ZEBRA", "")
    await kb.declare_class("test", "APPLE", "")

    responses = _mock_openai_sequence(*[_text_response("done")] * 4)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False)

    stage1_prompt = factory.return_value.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert stage1_prompt.index("APPLE") < stage1_prompt.index("ZEBRA")


# ------------------------------------------------------------------ #
# gap recovery: one extra sweep over classes that got no instances    #
# ------------------------------------------------------------------ #

async def test_gap_stage_runs_for_a_class_the_entity_stage_left_empty(kb):
    person, skill = await _resume_schema(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice Chen", "class_id": person}),
        _text_response("done"),   # ends the entity stage — SKILL got nothing
        _text_response("done"),   # gap stage: prompt captured below
        _text_response("done"),   # relations
        _text_response("done"),   # entity properties
        _text_response("done"),   # fact properties
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen knows SQL.", expand_schema=False)

    assert factory.return_value.chat.completions.create.call_count == 6
    gap_prompt = factory.return_value.chat.completions.create.call_args_list[2].kwargs["messages"][0]["content"]
    assert "gap recovery" in gap_prompt
    assert f"id={skill}  SKILL" in gap_prompt
    # Only the empty classes are re-swept; PERSON is listed as already resolved.
    assert f"id={person}  PERSON" not in gap_prompt
    assert "Alice Chen (PERSON)" in gap_prompt


async def test_gap_stage_entities_reach_the_relation_stage(kb):
    """An entity only the gap stage found must still be available to later
    stages — otherwise recovering it is pointless, since no fact can use it."""
    person, skill = await _resume_schema(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice Chen", "class_id": person}),
        _text_response("done"),
        _tool_call_response("add_entity", {"name": "SQL", "class_id": skill}),
        _text_response("done"),   # ends the gap stage
        _text_response("done"),   # relations — prompt captured below
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen knows SQL.", expand_schema=False)

    relation_prompt = factory.return_value.chat.completions.create.call_args_list[4].kwargs["messages"][0]["content"]
    assert "Alice Chen (PERSON)" in relation_prompt
    assert "SQL (SKILL)" in relation_prompt


async def test_gap_stage_skipped_when_every_class_got_an_instance(kb):
    person, skill = await _resume_schema(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice Chen", "class_id": person}),
        _tool_call_response("add_entity", {"name": "SQL", "class_id": skill}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen knows SQL.", expand_schema=False)

    assert factory.return_value.chat.completions.create.call_count == 6


async def test_gap_stage_skipped_when_the_entity_stage_resolved_nothing(kb):
    """No partial coverage to explain — re-listing every class would just
    repeat the entity prompt. Extra passes are `effort`'s job, not this one's."""
    await _resume_schema(kb)

    responses = _mock_openai_sequence(*[_text_response("done")] * 4)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some unrelated text.", expand_schema=False)

    assert factory.return_value.chat.completions.create.call_count == 4


async def test_attach_entity_class_adds_a_second_class_instead_of_an_entity(kb):
    """A descriptive phrase for an entity already found ("Alice ... as a staff
    engineer") types that entity, rather than becoming a phrase-named entity."""
    person = await kb.declare_class("test", "PERSON", "A human individual")
    engineer = await kb.declare_class("test", "ENGINEER", "A person who builds software")

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice", "class_id": person}),
        _text_response("done"),   # ends the entity stage — ENGINEER got nothing
        _tool_call_response("attach_entity_class", {"entity_id": "Alice", "class_id": engineer}),
        _text_response("done"),   # ends the gap stage
        _text_response("done"),   # relations — prompt captured below
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined Google as a staff engineer.", expand_schema=False)

    entities = kb.match_nodes("test", label="ENTITY")
    assert [e["properties"]["name"] for e in entities] == ["Alice"]
    classes = await kb.get_entity_classes("test", entities[0]["id"])
    assert {c["properties"]["name"] for c in classes} == {"PERSON", "ENGINEER"}
    relation_prompt = factory.return_value.chat.completions.create.call_args_list[4].kwargs["messages"][0]["content"]
    assert "Alice (PERSON, ENGINEER)" in relation_prompt or "Alice (ENGINEER, PERSON)" in relation_prompt


async def test_gap_stage_counts_every_class_of_an_entity(kb):
    """An entity carrying a second class covers that class too — otherwise the
    gap stage re-hunts it and invents a phrase-named instance."""
    person = await kb.declare_class("test", "PERSON", "A human individual")
    engineer = await kb.declare_class("test", "ENGINEER", "A person who builds software")

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice", "class_id": person}),
        _tool_call_response("attach_entity_class", {"entity_id": "Alice", "class_id": engineer}),
        _text_response("done"),   # ends the entity stage — both classes covered
        _text_response("done"),   # relations
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice is a staff engineer.", expand_schema=False)

    prompts = [c.kwargs["messages"][0]["content"] for c in factory.return_value.chat.completions.create.call_args_list]
    assert not any("gap recovery" in p for p in prompts)


def test_extraction_prompts_forbid_coercing_into_the_nearest_element():
    from blue_data_kb_source.extraction.engine import (
        STAGE_ENTITIES, STAGE_RELATIONS, _STAGE_WORKFLOWS, _SYSTEM_PROMPT_TEMPLATE,
    )

    assert "Fit, not nearest" in _SYSTEM_PROMPT_TEMPLATE
    assert "not WORKED_AT" in _SYSTEM_PROMPT_TEMPLATE
    assert "skipped because" in _STAGE_WORKFLOWS[STAGE_ENTITIES]
    assert "never reuse the nearest" in _STAGE_WORKFLOWS[STAGE_RELATIONS]


async def test_gap_stage_cannot_add_facts_or_property_values(kb):
    from blue_data_kb_source.extraction.engine import STAGE_ENTITY_GAPS, _STAGE_TOOL_NAMES

    tools = set(_STAGE_TOOL_NAMES[STAGE_ENTITY_GAPS])
    assert {"find_entity", "add_entity", "link_extraction"} <= tools
    assert tools.isdisjoint({"add_fact", "add_entity_property_value", "add_fact_property_value"})


# ------------------------------------------------------------------ #
# verbatim-span grounding: a write whose `text` isn't a real quote    #
# from the source is refused (the AGE = 28 hallucination in #91).     #
# ------------------------------------------------------------------ #

async def test_ungrounded_span_is_refused_and_creates_nothing(kb):
    person, _ = await _resume_schema(kb)
    prop = await kb.declare_class_property("test", "AGE", "Age in years", class_id=person)
    alice = await kb.add_entity("test", "Alice Chen", class_id=person)

    responses = _mock_openai_sequence(
        _text_response("done"),   # entities
        _text_response("done"),   # relations
        _tool_call_response("add_entity_property_value", {
            "entity_id": alice, "property_id": prop, "value": "28",
            "literal_type": "NUMBER",
            "text": "Alice Chen is 28 years old.",   # nowhere in the source
        }),
        _text_response("done"),
        _text_response("done"),   # fact properties
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen is a project manager.", expand_schema=False)

    assert kb.match_nodes("test", label="LITERAL") == []
    # The refusal goes back to the model as the tool result, so it can correct
    # itself rather than silently losing the turn.
    replies = _tool_replies(factory)
    assert len(replies) == 1
    assert "not a quote from the source text" in replies[0]["content"]


async def test_grounded_span_survives_case_and_whitespace_differences(kb):
    person, _ = await _resume_schema(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {
            "name": "Alice Chen", "class_id": person,
            "text": "alice chen is a   PROJECT manager.",
        }),
        _text_response("done"),
        _text_response("done"),   # gap stage (SKILL empty)
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Alice Chen is a project\nmanager.", expand_schema=False)

    assert [n["properties"]["name"] for n in kb.match_nodes("test", label="ENTITY")] == ["Alice Chen"]


async def test_span_spanning_two_sentences_is_grounded(kb):
    person, _ = await _resume_schema(kb)
    text = "Alice Chen is a project manager. She joined IBM in 1998."

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {
            "name": "Alice Chen", "class_id": person, "text": text,
        }),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", text, expand_schema=False)

    assert len(kb.match_nodes("test", label="ENTITY")) == 1


async def test_grounding_check_leaves_lookup_tools_alone(kb):
    """find_entity has no `text` argument and must never be refused."""
    person, _ = await _resume_schema(kb)
    await kb.add_entity("test", "Alice Chen", class_id=person)

    responses = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice Chen"}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Unrelated source text.", expand_schema=False)

    replies = _tool_replies(factory)
    assert len(replies) == 1
    assert json.loads(replies[0]["content"])["name"] == "Alice Chen"


# ------------------------------------------------------------------ #
# forward-carried context: every stage is shown what prior stages     #
# resolved, and the whole thing carries across effort passes.         #
# ------------------------------------------------------------------ #

async def _alice_at_ibm(kb):
    person = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    start = await kb.declare_relation_property("test", "START_DATE", "", relation_id=worked)
    age = await kb.declare_class_property("test", "AGE", "", class_id=person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm = await kb.add_entity("test", "IBM", class_id=company)
    return {
        "person": person, "company": company, "worked": worked,
        "start": start, "age": age, "alice": alice, "ibm": ibm,
    }


def _through_relations(ids):
    """Tool calls that resolve Alice + IBM and the WORKED_AT fact between them,
    ending the entity and relation stages."""
    return [
        _tool_call_response("find_entity", {"name": "Alice"}),
        _tool_call_response("find_entity", {"name": "IBM"}),
        _text_response("done"),
        _tool_call_response("find_relation_type", {"name": "WORKED_AT"}),
        _tool_call_response("add_fact", {
            "head_entity_id": ids["alice"], "tail_entity_id": ids["ibm"],
            "relation_id": ids["worked"],
        }),
        _text_response("done"),
    ]


def test_fact_properties_stage_runs_before_entity_properties():
    from blue_data_kb_source.extraction.engine import (
        STAGE_ENTITY_PROPERTIES,
        STAGE_FACT_PROPERTIES,
        STAGES,
    )

    assert STAGES.index(STAGE_FACT_PROPERTIES) < STAGES.index(STAGE_ENTITY_PROPERTIES)


async def test_entity_property_stage_sees_facts_and_what_the_fact_stage_took(kb):
    """The ownership hand-off: the entity-property stage runs second and is
    shown the values the fact stage already claimed, so it doesn't re-claim
    them onto the entity."""
    ids = await _alice_at_ibm(kb)
    text = "Alice joined IBM in June 1998."

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        # fact properties: START_DATE goes on the fact
        _tool_call_response("add_fact_property_value", {
            "fact_id": None, "property_id": ids["start"], "value": "1998-06",
            "literal_type": "DATE", "text": text,
        }),
        _text_response("done"),
        _text_response("done"),   # entity properties — prompt captured below
    )
    # The fact id isn't known until add_fact runs, so patch it in at dispatch.
    with patch("openai.AsyncOpenAI", responses) as factory:
        engine = kb._extraction
        original = engine._dispatch_tool

        async def _fill_fact_id(database, fn, args, source_id, source_text):
            if fn == "add_fact_property_value" and args.get("fact_id") is None:
                args["fact_id"] = kb.match_nodes("test", label="FACT")[0]["id"]
            return await original(database, fn, args, source_id, source_text)

        with patch.object(engine, "_dispatch_tool", _fill_fact_id):
            await kb.import_text("test", text, expand_schema=False)

    entity_prop_prompt = factory.return_value.chat.completions.create.call_args_list[8].kwargs["messages"][0]["content"]
    assert "Stage: entity property extraction" in entity_prop_prompt
    # the facts it did not have before
    assert f"Alice (PERSON, id={ids['alice']}) —WORKED_AT→ IBM" in entity_prop_prompt
    # and what the fact stage already claimed
    assert "(Alice —WORKED_AT→ IBM).START_DATE = 1998-06" in entity_prop_prompt


async def test_fact_property_stage_prompt_carries_entities_and_facts(kb):
    ids = await _alice_at_ibm(kb)

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),   # fact properties
        _text_response("done"),   # entity properties
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    prompt = factory.return_value.chat.completions.create.call_args_list[6].kwargs["messages"][0]["content"]
    assert "Stage: fact property extraction" in prompt
    assert f"id={ids['alice']}  Alice (PERSON)" in prompt
    assert f"id={ids['ibm']}  IBM (COMPANY)" in prompt
    assert f"Alice (PERSON, id={ids['alice']}) —WORKED_AT→ IBM (COMPANY, id={ids['ibm']})" in prompt


async def test_single_pass_prompt_carries_no_prior_extraction_block(kb):
    """effort="low" must read exactly as it did before the carry existed."""
    await _alice_at_ibm(kb)

    responses = _mock_openai_sequence(*[_text_response("done")] * 4)
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Some text.", expand_schema=False, effort="low")

    for call in factory.return_value.chat.completions.create.call_args_list:
        assert "Already extracted from this text by earlier passes" not in call.kwargs["messages"][0]["content"]


async def test_second_pass_inherits_what_the_first_pass_resolved(kb):
    ids = await _alice_at_ibm(kb)

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),   # pass 1 fact properties
        _text_response("done"),   # pass 1 entity properties
        _text_response("done"),   # pass 2 entities — prompt captured below
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False, effort="medium")

    pass2_first = factory.return_value.chat.completions.create.call_args_list[8].kwargs["messages"][0]["content"]
    assert "Already extracted from this text by earlier passes" in pass2_first
    assert f"id={ids['alice']}  Alice (PERSON)" in pass2_first
    assert "Alice (PERSON" in pass2_first and "—WORKED_AT→ IBM" in pass2_first


async def test_carried_context_is_per_chunk_not_shared_across_chunks(kb):
    """Concurrent chunks must not be told an entity exists because another
    chunk is midway through creating it — the same reason the dispatch lock
    can't make cross-chunk extraction race-free."""
    await _alice_at_ibm(kb)
    text = "Alice joined IBM.\n\nBob joined ACME."

    responses = _mock_openai_endless(_text_response("done"))[0]
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", text, expand_schema=False, max_concurrency=2)

    for call in factory.return_value.chat.completions.create.call_args_list:
        assert "Already extracted from this text by earlier passes" not in call.kwargs["messages"][0]["content"]


def test_property_value_rendering_labels_entity_and_fact_owners():
    from blue_data_kb_source.extraction.engine import _render_property_values

    rendered = _render_property_values([
        {"id": "l1", "owner_label": "Alice", "property_name": "AGE", "value": "28"},
        {"id": "l2", "owner_label": "(Alice —WORKED_AT→ IBM)", "property_name": "START_DATE", "value": "1998-06"},
    ])
    assert "- Alice.AGE = 28" in rendered
    assert "- (Alice —WORKED_AT→ IBM).START_DATE = 1998-06" in rendered


def test_extraction_context_extend_dedupes_by_id():
    from blue_data_kb_source.extraction.engine import _ExtractionContext

    ctx = _ExtractionContext()
    assert ctx.is_empty()
    ctx.extend("entities", [{"id": "e1", "name": "Alice"}, {"id": "e2", "name": "IBM"}])
    ctx.extend("entities", [{"id": "e2", "name": "IBM"}, {"id": "e3", "name": "ACME"}])
    assert [e["id"] for e in ctx.entities] == ["e1", "e2", "e3"]
    assert not ctx.is_empty()


def test_extraction_context_snapshot_does_not_alias_the_live_context():
    from blue_data_kb_source.extraction.engine import _ExtractionContext

    ctx = _ExtractionContext()
    ctx.extend("entities", [{"id": "e1"}])
    snap = ctx.snapshot()
    ctx.extend("entities", [{"id": "e2"}])
    assert [e["id"] for e in snap.entities] == ["e1"]


# ------------------------------------------------------------------ #
# fixes found by a live run (see the staged-extraction walkthrough):  #
# gap coverage vs. the carry, span grounding on list-structured text, #
# and property-value dedupe.                                          #
# ------------------------------------------------------------------ #

_RESUME = """Alice Chen — Senior Project Manager

EXPERIENCE
Alice joined Contoso Ltd in March 2019 as a Senior Project Manager.

SKILLS
* Project Management: Agile, Scrum, Kanban * Tools: JIRA, Trello
* Technical: SQL, Basic Python * Languages: English (Native), Spanish (Fluent)
"""


@pytest.mark.parametrize("span", [
    # Verbatim prose — the fast path.
    "Alice joined Contoso Ltd in March 2019 as a Senior Project Manager.",
    # A heading spliced onto a genuine fragment: what a model actually sends
    # when asked to quote a bullet list.
    "Skills: Tools: JIRA, Trello",
    "Skills: * Technical: SQL, Basic Python * Languages: English (Native), Spanish (Fluent)",
    "Languages: Spanish (Fluent)",
    # A genuine fragment with an elided middle ("SQL, " dropped).
    "* Technical: Basic Python",
    # Case and whitespace differences.
    "alice joined contoso   ltd in march 2019",
])
def test_real_quotes_are_grounded_even_when_not_contiguous(span):
    from blue_data_kb_source.extraction.engine import _span_is_grounded

    assert _span_is_grounded(span, _RESUME)


@pytest.mark.parametrize("span", [
    "Alice Chen is 28 years old.",            # outright fabrication
    "Alice Chen, aged 28, joined Contoso.",   # the value is not in the text
    "Alice joined Trello",                    # stitched from scattered words
    "Alice Chen worked at JIRA in 2019",      # plausible, but assembled
    "Kanban Alice",                           # right words, wrong order
    "",
    "   ",
])
def test_fabricated_spans_are_still_refused(span):
    from blue_data_kb_source.extraction.engine import _span_is_grounded

    assert not _span_is_grounded(span, _RESUME)


def test_grounding_window_rejects_a_span_stretched_across_the_whole_source():
    """The words are all present and in order; only the window bound
    distinguishes this from a quote."""
    from blue_data_kb_source.extraction.engine import _span_is_grounded

    source = "Alice " + ("filler " * 60) + "joined " + ("filler " * 60) + "Contoso"
    assert not _span_is_grounded("Alice joined Contoso", source)
    assert _span_is_grounded("Alice joined Contoso", "Alice joined Contoso Ltd.")


async def test_gap_stage_does_not_rerun_for_classes_an_earlier_pass_filled(kb):
    """Pass 2's entity stage only re-touches what it re-reads, so PERSON can
    look empty there even though pass 1 filled it. Coverage is judged against
    the carried context, not just the current pass."""
    person, skill = await _resume_schema(kb)
    alice = await kb.add_entity("test", "Alice Chen", class_id=person)
    sql = await kb.add_entity("test", "SQL", class_id=skill)

    responses = _mock_openai_sequence(
        # pass 1 — both classes covered, so no gap stage
        _tool_call_response("find_entity", {"name": "Alice Chen"}),
        _tool_call_response("find_entity", {"name": "SQL"}),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
        # pass 2 — only SQL re-touched; PERSON must NOT look missing
        _tool_call_response("find_entity", {"name": "SQL"}),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen knows SQL.", expand_schema=False, effort="medium")

    prompts = [c.kwargs["messages"][0]["content"]
               for c in factory.return_value.chat.completions.create.call_args_list]
    assert not any("Stage: entity gap recovery" in p for p in prompts)
    assert len(prompts) == 11
    assert alice and sql


async def test_gap_stage_prompt_lists_entities_from_earlier_passes_too(kb):
    """When the gap stage does run on a later pass, the 'already resolved'
    list it shows must include what earlier passes found."""
    person, skill = await _resume_schema(kb)
    alice = await kb.add_entity("test", "Alice Chen", class_id=person)

    responses = _mock_openai_sequence(
        # pass 1 — PERSON only; SKILL empty, so the gap stage runs and finds nothing
        _tool_call_response("find_entity", {"name": "Alice Chen"}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
        # pass 2 — re-links Alice again; SKILL still empty, gap stage runs again
        _tool_call_response("find_entity", {"name": "Alice Chen"}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen is a manager.", expand_schema=False, effort="medium")

    prompts = [c.kwargs["messages"][0]["content"]
               for c in factory.return_value.chat.completions.create.call_args_list]
    gap_prompts = [p for p in prompts if "Stage: entity gap recovery" in p]
    assert len(gap_prompts) == 2
    for p in gap_prompts:
        assert f"id={alice}  Alice Chen (PERSON)" in p
        assert f"id={skill}  SKILL" in p


async def test_property_stages_can_find_an_existing_value_before_writing(kb):
    ids = await _alice_at_ibm(kb)
    text = "Alice joined IBM."
    existing = await kb.add_entity_property_value(
        "test", value="28", literal_type="NUMBER",
        entity_id=ids["alice"], property_id=ids["age"], text=text,
    )

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),   # fact properties
        _tool_call_response("find_literal", {
            "owner_id": ids["alice"], "property_id": ids["age"],
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", text, expand_schema=False)

    replies = _tool_replies(factory)
    found = json.loads(replies[-1]["content"])
    assert found["id"] == existing
    # NUMBER literals are stored cast, so the tool surfaces 28, not "28".
    assert found["value"] == 28
    # and no second literal was created
    assert len(kb.match_nodes("test", label="LITERAL")) == 1


async def test_find_literal_reports_no_existing_value(kb):
    ids = await _alice_at_ibm(kb)

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),
        _tool_call_response("find_literal", {
            "owner_id": ids["alice"], "property_id": ids["age"],
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    assert json.loads(_tool_replies(factory)[-1]["content"]) == {"id": None}


def test_property_workflows_tell_the_model_to_check_before_writing():
    from blue_data_kb_source.extraction.engine import (
        STAGE_ENTITY_PROPERTIES,
        STAGE_FACT_PROPERTIES,
        _STAGE_WORKFLOWS,
    )

    for stage in (STAGE_ENTITY_PROPERTIES, STAGE_FACT_PROPERTIES):
        workflow = _STAGE_WORKFLOWS[stage]
        assert "find_literal" in workflow
        assert "link_extraction" in workflow


# ------------------------------------------------------------------ #
# dispatch-level dedupe: looking before writing is advisory for the   #
# model, so the engine enforces it where every write passes through.  #
# ------------------------------------------------------------------ #

async def test_add_entity_returns_the_existing_entity_instead_of_forking_it(kb):
    person, _ = await _resume_schema(kb)
    alice = await kb.add_entity("test", "Alice Chen", class_id=person)

    responses = _mock_openai_sequence(
        # No find_entity first — exactly what the model does on a later pass.
        _tool_call_response("add_entity", {
            "name": "Alice Chen", "class_id": person, "text": "Alice Chen is a manager.",
        }),
        _text_response("done"),
        _text_response("done"),   # gap stage (SKILL empty)
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen is a manager.", expand_schema=False)

    assert len(kb.match_nodes("test", label="ENTITY")) == 1
    reply = json.loads(_tool_replies(factory)[0]["content"])
    assert reply == {"id": alice, "deduped": True}


async def test_deduped_entity_still_records_the_new_text_span(kb):
    """Collapsing onto the existing entity must not lose the evidence — that is
    the whole point of link_extraction, and add_entity now does the same."""
    person, _ = await _resume_schema(kb)
    alice = await kb.add_entity("test", "Alice Chen", class_id=person, text="Alice Chen is a manager.")
    before = len(kb.get_neighbors("test", alice, edge_type="EXTRACTED_FROM", direction="out"))

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {
            "name": "Alice Chen", "class_id": person, "text": "Alice Chen joined Contoso.",
        }),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Alice Chen joined Contoso.", expand_schema=False)

    after = kb.get_neighbors("test", alice, edge_type="EXTRACTED_FROM", direction="out")
    assert len(after) == before + 1


async def test_entity_dedupe_is_case_and_whitespace_insensitive(kb):
    person, _ = await _resume_schema(kb)
    await kb.add_entity("test", "IBM", class_id=person)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "ibm", "class_id": person}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "ibm", expand_schema=False)

    assert [n["properties"]["name"] for n in kb.match_nodes("test", label="ENTITY")] == ["IBM"]


async def test_add_fact_collapses_onto_an_existing_fact(kb):
    ids = await _alice_at_ibm(kb)
    existing = await kb.add_fact(
        "test", head_entity_id=ids["alice"], tail_entity_id=ids["ibm"], relation_id=ids["worked"],
    )

    responses = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _tool_call_response("find_entity", {"name": "IBM"}),
        _text_response("done"),
        _tool_call_response("add_fact", {
            "head_entity_id": ids["alice"], "tail_entity_id": ids["ibm"],
            "relation_id": ids["worked"], "text": "Alice joined IBM.",
        }),
        _text_response("done"),
        _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    assert len(kb.match_nodes("test", label="FACT")) == 1
    assert json.loads(_tool_replies(factory)[-1]["content"]) == {"id": existing, "deduped": True}


async def test_identical_property_value_is_deduped(kb):
    ids = await _alice_at_ibm(kb)
    text = "Alice joined IBM."
    existing = await kb.add_entity_property_value(
        "test", value="28", literal_type="NUMBER",
        entity_id=ids["alice"], property_id=ids["age"], text=text,
    )

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),   # fact properties
        _tool_call_response("add_entity_property_value", {
            "entity_id": ids["alice"], "property_id": ids["age"],
            "value": "28", "literal_type": "NUMBER", "text": text,
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", text, expand_schema=False)

    assert len(kb.match_nodes("test", label="LITERAL")) == 1
    assert json.loads(_tool_replies(factory)[-1]["content"]) == {"id": existing, "deduped": True}


async def test_a_second_distinct_value_for_the_same_property_is_kept(kb):
    """Multi-valued attributes are legitimate (two phone numbers) — dedupe keys
    on the value, not just the property."""
    ids = await _alice_at_ibm(kb)
    text = "Alice joined IBM."
    await kb.add_entity_property_value(
        "test", value="28", literal_type="NUMBER",
        entity_id=ids["alice"], property_id=ids["age"], text=text,
    )

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),
        _tool_call_response("add_entity_property_value", {
            "entity_id": ids["alice"], "property_id": ids["age"],
            "value": "29", "literal_type": "NUMBER", "text": text,
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", text, expand_schema=False)

    values = sorted(str(n["properties"]["value"]) for n in kb.match_nodes("test", label="LITERAL"))
    assert values == ["28", "29"]


# ------------------------------------------------------------------ #
# id-argument coercion (issue #92): the agent routinely passes a name #
# where a node id belongs. Resolve it before letting the call fail.   #
# ------------------------------------------------------------------ #

def test_every_id_argument_documents_that_it_takes_an_id_not_a_name():
    from blue_data_kb_source.extraction.engine import _ID_ARGS, _TOOLS

    id_args = {arg for arg, _ in _ID_ARGS} | {"owner_id"}
    for tool in _TOOLS:
        fn = tool["function"]
        for pname, spec in fn["parameters"].get("properties", {}).items():
            if pname in id_args:
                assert "never a name" in spec.get("description", ""), f"{fn['name']}.{pname}"


async def test_class_id_given_as_a_name_is_resolved(kb):
    person, _ = await _resume_schema(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice Chen", "class_id": "PERSON"}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen is a manager.", expand_schema=False)

    entities = kb.match_nodes("test", label="ENTITY")
    assert len(entities) == 1
    classes = kb.get_neighbors("test", entities[0]["id"], edge_type="IS_INSTANCE_OF", direction="out")
    assert classes[0]["node"]["id"] == person
    reply = json.loads(_tool_replies(factory)[0]["content"])
    assert reply["coerced"] == {"class_id": f"PERSON -> {person}"}


async def test_relation_id_given_as_a_name_is_resolved(kb):
    ids = await _alice_at_ibm(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _tool_call_response("find_entity", {"name": "IBM"}),
        _text_response("done"),
        _tool_call_response("add_fact", {
            "head_entity_id": ids["alice"], "tail_entity_id": ids["ibm"],
            "relation_id": "WORKED_AT",
        }),
        _text_response("done"),
        _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    facts = kb.match_nodes("test", label="FACT")
    assert len(facts) == 1
    rel = kb.get_neighbors("test", facts[0]["id"], edge_type="REPRESENTS", direction="out")
    assert rel[0]["node"]["id"] == ids["worked"]


async def test_entity_id_given_as_a_name_is_resolved(kb):
    ids = await _alice_at_ibm(kb)

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),   # fact properties
        _tool_call_response("add_entity_property_value", {
            "entity_id": "Alice", "property_id": ids["age"],
            "value": "28", "literal_type": "NUMBER",
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    literals = kb.match_nodes("test", label="LITERAL")
    assert len(literals) == 1
    owner = kb.get_neighbors("test", literals[0]["id"], edge_type="HAS_PROPERTY", direction="in")
    assert owner[0]["node"]["id"] == ids["alice"]


async def test_property_id_given_as_a_name_is_resolved(kb):
    ids = await _alice_at_ibm(kb)

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),
        _tool_call_response("add_entity_property_value", {
            "entity_id": ids["alice"], "property_id": "AGE",
            "value": "28", "literal_type": "NUMBER",
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    assert len(kb.match_nodes("test", label="LITERAL")) == 1
    reply = json.loads(_tool_replies(factory)[-1]["content"])
    assert reply["coerced"] == {"property_id": f"AGE -> {ids['age']}"}


async def test_property_id_prefers_the_property_declared_for_this_owner(kb):
    """Property names are not unique. The call already names its owner, so the
    owner's own class wins over a same-named property on another class."""
    person = await kb.declare_class("test", "PERSON", "")
    animal = await kb.declare_class("test", "ANIMAL", "")
    animal_age = await kb.declare_class_property("test", "AGE", "Age of the animal", class_id=animal)
    person_age = await kb.declare_class_property("test", "AGE", "Age of the person", class_id=person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    assert animal_age != person_age

    responses = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _text_response("done"),
        _text_response("done"),   # gap stage (ANIMAL empty)
        _text_response("done"),   # relations
        _text_response("done"),   # fact properties
        _tool_call_response("add_entity_property_value", {
            "entity_id": alice, "property_id": "AGE",
            "value": "28", "literal_type": "NUMBER",
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Alice is 28.", expand_schema=False)

    literals = kb.match_nodes("test", label="LITERAL")
    assert len(literals) == 1
    prop = kb.get_neighbors("test", literals[0]["id"], edge_type="IS_VALUE_OF", direction="out")
    assert prop[0]["node"]["id"] == person_age


async def test_find_fact_with_a_relation_name_finds_the_fact(kb):
    """The quiet failure this change exists for: before coercion this returned
    {id: null}, the model concluded the fact was new, and duplicated it."""
    ids = await _alice_at_ibm(kb)
    existing = await kb.add_fact(
        "test", head_entity_id=ids["alice"], tail_entity_id=ids["ibm"], relation_id=ids["worked"],
    )

    responses = _mock_openai_sequence(
        _tool_call_response("find_entity", {"name": "Alice"}),
        _tool_call_response("find_entity", {"name": "IBM"}),
        _text_response("done"),
        _tool_call_response("find_fact", {
            "head_entity_id": "Alice", "relation_id": "WORKED_AT", "tail_entity_id": "IBM",
        }),
        _text_response("done"),
        _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice joined IBM.", expand_schema=False)

    reply = json.loads(_tool_replies(factory)[-1]["content"])
    assert reply["id"] == existing
    assert set(reply["coerced"]) == {"head_entity_id", "relation_id", "tail_entity_id"}


async def test_a_valid_id_is_left_untouched_and_reports_no_coercion(kb):
    person, _ = await _resume_schema(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice Chen", "class_id": person}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen is a manager.", expand_schema=False)

    assert "coerced" not in json.loads(_tool_replies(factory)[0]["content"])


async def test_a_name_that_resolves_to_nothing_still_errors(kb):
    """Coercion must not become a way to invent schema — the pipeline extracts
    against the schema as it stands."""
    person, _ = await _resume_schema(kb)

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice Chen", "class_id": "NO_SUCH_CLASS"}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", "Alice Chen is a manager.", expand_schema=False)

    assert kb.match_nodes("test", label="ENTITY") == []
    reply = json.loads(_tool_replies(factory)[0]["content"])
    assert "could not resolve" in reply["error"]
    assert "NO_SUCH_CLASS" in reply["error"]


async def test_coercion_never_falls_back_to_a_fuzzy_match(kb):
    """Exact names only: 'COMPANIES' must not resolve to 'COMPANY'."""
    await kb.declare_class("test", "COMPANY", "A business organization")

    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Contoso", "class_id": "COMPANIES"}),
        _text_response("done"),
        _text_response("done"),
        _text_response("done"), _text_response("done"), _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Contoso Ltd.", expand_schema=False)

    assert kb.match_nodes("test", label="ENTITY") == []


async def test_a_date_re_extracted_in_another_wording_is_still_deduped(kb):
    """Writes normalize ("March 2019" -> "2019-03"), so dedupe has to compare
    the incoming value in its stored form or every re-extraction looks new."""
    ids = await _alice_at_ibm(kb)
    text = "Alice joined IBM in March 2019."
    existing = await kb.add_entity_property_value(
        "test", value="March 2019", literal_type="DATE",
        entity_id=ids["alice"], property_id=ids["age"], text=text,
    )
    stored = kb.get_node("test", existing)["properties"]["value"]
    assert stored == "2019-03"

    responses = _mock_openai_sequence(
        *_through_relations(ids),
        _text_response("done"),
        _tool_call_response("add_entity_property_value", {
            "entity_id": ids["alice"], "property_id": ids["age"],
            "value": "March 2019", "literal_type": "DATE", "text": text,
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", responses) as factory:
        await kb.import_text("test", text, expand_schema=False)

    assert len(kb.match_nodes("test", label="LITERAL")) == 1
    assert json.loads(_tool_replies(factory)[-1]["content"])["deduped"] is True


def test_comparable_value_matches_the_write_paths_casting():
    from blue_data_kb_source.extraction.engine import _comparable_value

    assert _comparable_value("March 2019", "DATE") == "2019-03"
    assert _comparable_value("2019-03", "DATE") == "2019-03"
    assert _comparable_value("28", "NUMBER") == "28"
    assert _comparable_value(28, None) == "28"
    # An un-castable value falls back to its raw text rather than blowing up.
    assert _comparable_value("not a date", "DATE") == "not a date"


def test_same_value_matches_one_date_written_under_two_literal_types():
    from blue_data_kb_source.extraction.engine import _same_value

    # Only the DATE one is normalized on write, so the stored values differ.
    assert _same_value(("March 2019", "STRING"), ("2019-03", "DATE"))
    assert _same_value(("March 2019", None), ("2019-03", "DATE"))
    # Genuinely different values stay different — normalize_date is an identity
    # on anything that isn't a date, so nothing is collapsed by accident.
    assert not _same_value(("March 2019", "DATE"), ("April 2019", "DATE"))
    assert not _same_value(("Senior Project Manager", None), ("Project Management", None))
    assert not _same_value(("28", "NUMBER"), ("29", "NUMBER"))


# ------------------------------------------------------------------ #
# same-named entities of different classes stay distinct (#115)        #
# ------------------------------------------------------------------ #

async def _apples(kb):
    company = await kb.declare_class("test", "COMPANY", "A business")
    fruit = await kb.declare_class("test", "FRUIT", "An edible fruit")
    apple_inc = await kb.add_entity("test", "Apple", class_id=company)
    return company, fruit, apple_inc


async def _dispatch(kb, fn, args, text="Apple makes phones. An apple a day."):
    return await kb._extraction._dispatch_tool("test", fn, args, None, text)


async def test_add_entity_does_not_collapse_onto_a_same_named_entity_of_another_class(kb):
    company, fruit, apple_inc = await _apples(kb)
    result = await _dispatch(kb, "add_entity", {"name": "Apple", "class_id": fruit, "text": "An apple a day."})
    assert result["id"] != apple_inc and not result.get("deduped")
    classes = {c["properties"]["name"] for c in await kb.get_entity_classes("test", apple_inc)}
    assert classes == {"COMPANY"}


async def test_add_entity_picks_the_same_named_entity_of_its_own_class(kb):
    company, fruit, apple_inc = await _apples(kb)
    apple_fruit = (await _dispatch(kb, "add_entity", {"name": "Apple", "class_id": fruit, "text": "An apple a day."}))["id"]
    again = await _dispatch(kb, "add_entity", {"name": "apple", "class_id": fruit, "text": "An apple a day."})
    assert again["id"] == apple_fruit
    company_again = await _dispatch(kb, "add_entity", {"name": "Apple", "class_id": company, "text": "Apple makes phones."})
    assert company_again["id"] == apple_inc


async def test_add_entity_still_dedupes_across_a_subclass(kb):
    person = await kb.declare_class("test", "PERSON", "")
    engineer = await kb.declare_class("test", "ENGINEER", "")
    await kb.add_is_subclass_of("test", engineer, person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    result = await _dispatch(kb, "add_entity", {"name": "Alice", "class_id": engineer, "text": "Alice is an engineer."},
                             text="Alice is an engineer.")
    assert result["id"] == alice


async def test_find_entity_reports_classes_and_other_same_named_entities(kb):
    company, fruit, apple_inc = await _apples(kb)
    result = await _dispatch(kb, "find_entity", {"name": "Apple", "class_id": fruit})
    assert result["id"] is None
    assert result["other_matches"] == [{"id": apple_inc, "classes": ["COMPANY"]}]
    by_company = await _dispatch(kb, "find_entity", {"name": "Apple", "class_id": company})
    assert (by_company["id"], by_company["classes"]) == (apple_inc, ["COMPANY"])
    assert "other_matches" not in by_company


async def test_on_stage_reports_each_stage_in_order(kb):
    person, skill = await _resume_schema(kb)
    seen = []
    responses = _mock_openai_sequence(
        _tool_call_response("add_entity", {"name": "Alice Chen", "class_id": person}),
        _text_response("done"),   # entities — SKILL left empty, so the gap stage runs
        _text_response("done"),   # entity gaps
        _text_response("done"),   # relations
        _text_response("done"),   # fact properties
        _text_response("done"),   # entity properties
    )
    with patch("openai.AsyncOpenAI", responses):
        await kb.import_text("test", "Alice Chen knows SQL.", expand_schema=False,
                             on_stage=lambda stage, pass_num: seen.append((stage, pass_num)))
    assert seen == [("entities", 1), ("entity_gaps", 1), ("relations", 1),
                    ("fact_properties", 1), ("entity_properties", 1)]
