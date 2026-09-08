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
    """No search_indexer configured (the default `kb` fixture): the system
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
    assert ("find_entity", {"id": alice, "name": "Alice", "description": "An engineer"}) in results


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
    assert ("find_entity", {"id": ibm, "name": "IBM", "description": "A company"}) in results


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
    # find_fact is stage-2-only: stage 1 ends immediately, then the find_fact
    # call happens in stage 2, then stages 3-4 end immediately.
    factory = _mock_openai_sequence(
        _text_response("done"),
        _tool_call_response(
            "find_fact",
            {"head_entity_id": "x", "relation_id": "y", "tail_entity_id": "z"},
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

    assert set(_STAGE_TOOL_NAMES[STAGE_ENTITY_PROPERTIES]) == {
        "get_schema", "search_schema", "add_entity_property_value", "link_extraction",
    }
    assert set(_STAGE_TOOL_NAMES[STAGE_FACT_PROPERTIES]) == {
        "get_schema", "search_schema", "add_fact_property_value", "link_extraction",
    }


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

    stage4_prompt = factory.return_value.chat.completions.create.call_args_list[7].kwargs["messages"][0]["content"]
    assert "Alice —WORKED_AT→ IBM" in stage4_prompt


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
