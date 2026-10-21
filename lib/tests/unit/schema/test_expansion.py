"""Tests for SchemaManager.expand_schema / SchemaExpansionEngine.

reference="llm" cases mock openai.AsyncOpenAI entirely (same pattern as
extraction/test_engine.py). The reference="mcp" test connects to the real,
already-installed blue-data-wordnet-mcp package over stdio — only the model's
decisions are scripted (via a mocked openai.AsyncOpenAI), so the MCP half of
the round trip (spawn, list_tools, call_tool) is real, proving the generic
bridge actually drives an external server's tools.
"""
import importlib.util
import itertools
import json
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from blue_data.core.schema import DataSchema


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

    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
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


async def test_expand_schema_prompt_pushes_relation_coverage_for_new_classes(kb):
    """Regression guard for declaring new CLASSes (e.g. SKILL, HOBBY) with no RELATION
    connecting them to the rest of the schema (e.g. HAS_SKILL/HAS_HOBBY from PERSON) — a
    prompt/tool-description-only fix, so this just pins the guidance stays present."""
    factory, mock_client = _mock_openai_sequence(_text_response("done"))
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice has programming skills, and hobbies such as wood working.", dedup_mode="none"
        )

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    system_prompt = call_kwargs["messages"][0]["content"]
    assert "HAS_SKILL" in system_prompt and "HAS_HOBBY" in system_prompt
    assert "a class with no relation" in system_prompt and "is usually a sign a relation was missed" in system_prompt

    tools = {t["function"]["name"]: t["function"]["description"] for t in call_kwargs["tools"]}
    assert "a class left with no relation to anything is often a sign one was missed" in tools["declare_class"]


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
        _tool_call_response("return_verdict", {"is_subtype": True, "reason": "An employee is a person."}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Bob is an employee.", dedup_mode="none")

    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
    assert any(
        r["name"] == "IS_SUBCLASS_OF" and r["source"] == "EMPLOYEE" and r["target"] == "PERSON"
        for r in schema.relations.values()
    )


async def test_expand_schema_add_subclass_of_rejects_implausible_link(kb):
    """The judge vetoes an is-a link between merely related classes (issue #103:
    COMPANY under ENGINEER), and the agent is told why."""
    engineer = await kb.declare_class("test", "ENGINEER", "A person who designs and builds software")
    company = await kb.declare_class("test", "COMPANY", "A business organization")

    calls = []
    factory, client = _mock_openai_sequence(
        _tool_call_response("add_subclass_of", {"child_id": company, "parent_id": engineer}),
        _tool_call_response("return_verdict", {"is_subtype": False, "reason": "A company employs engineers; it is not one."}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice is an engineer at Google.", dedup_mode="none",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert kb.get_neighbors("test", company, edge_type="IS_SUBCLASS_OF", direction="out") == []
    assert "not a subtype" in calls[0][1]["error"]
    judge_prompt = client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
    assert "COMPANY — A business organization" in judge_prompt
    assert "ENGINEER — A person who designs" in judge_prompt


async def test_expand_schema_add_subclass_of_skips_judge_for_malformed_pairs(kb):
    """Self-loops, mixed kinds and repeats are settled by add_is_subclass_of itself —
    no judge call is spent on them."""
    person = await kb.declare_class("test", "PERSON", "A human individual")
    engineer = await kb.declare_class("test", "ENGINEER", "")
    await kb.add_is_subclass_of("test", engineer, person)

    calls = []
    factory, client = _mock_openai_sequence(
        _tool_call_response("add_subclass_of", {"child_id": person, "parent_id": person}),
        _tool_call_response("add_subclass_of", {"child_id": engineer, "parent_id": person}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice is an engineer.", dedup_mode="none",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert client.chat.completions.create.call_count == 3
    assert "itself" in calls[0][1]["error"]
    assert calls[1][1] == {}


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

    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
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
    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
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

    name, result = calls[0]
    assert name == "declare_relation_type"
    assert result["id"] == worked_at
    assert result["reused_existing"]["name"] == "WORKED_AT"
    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
    assert not any(r["name"] == "EMPLOYED_AT" for r in schema.relations.values())
    _ = person, company


async def _seed_employment(kb):
    await kb.declare_class("test", "PERSON", "A human individual")
    await kb.declare_class("test", "COMPANY", "A business organization")
    await kb.declare_class("test", "UNIVERSITY", "An institution of higher education")
    return await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship between a person and a company",
        domain="PERSON", range="COMPANY",
    )


async def test_expand_schema_search_dedup_ignores_keyword_overlap():
    """A BM25 hit sharing only words like 'person'/'relationship' must not swallow a new
    concept into an existing one (issue #101: STUDIED_AT/MANAGED_BY -> WORKED_AT)."""
    from blue_data_kb_source import MemoryKBSource
    kb = MemoryKBSource(name="dedup-bm25", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    worked_at = await _seed_employment(kb)

    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response("declare_class", {"name": "ROLE", "description": "A job title held by a person at a company"}),
        _tool_call_response(
            "declare_relation_type",
            {"name": "STUDIED_AT", "description": "Education relationship between a person and a university",
             "domain": "PERSON", "range": "UNIVERSITY"},
        ),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice studied at CMU and is a staff engineer.", dedup_mode="search",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
    assert "ROLE" in schema.entities
    assert any(r["name"] == "STUDIED_AT" for r in schema.relations.values())
    assert all("reused_existing" not in result for _, result in calls)
    assert calls[1][1]["id"] != worked_at


async def test_expand_schema_llm_dedup_rejects_relation_with_incompatible_ends(kb):
    """Even when the judge calls two relations synonyms, a candidate whose domain/range
    don't line up with the requested classes is not reused."""
    worked_at = await _seed_employment(kb)
    factory, _ = _mock_openai_sequence(
        _tool_call_response(
            "declare_relation_type",
            {"name": "ATTENDED", "description": "A person was at an institution",
             "domain": "PERSON", "range": "UNIVERSITY"},
        ),
        _tool_call_response("return_matches", {"matching_ids": [worked_at]}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Alice attended CMU.", dedup_mode="llm")

    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
    assert any(r["name"] == "ATTENDED" for r in schema.relations.values())


async def test_expand_schema_llm_dedup_reuses_relation_on_subclass_domain(kb):
    """A requested domain that is a subclass of the existing relation's domain still counts
    as compatible, so a genuine synonym is reused."""
    worked_at = await _seed_employment(kb)
    engineer = await kb.declare_class("test", "ENGINEER", "A software engineer")
    await kb.add_is_subclass_of("test", engineer, (await kb.find_class("test", "PERSON"))["id"])
    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response(
            "declare_relation_type",
            {"name": "EMPLOYED_BY", "description": "Employment", "domain": "ENGINEER", "range": "COMPANY"},
        ),
        _tool_call_response("return_matches", {"matching_ids": [worked_at]}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice is employed by Google.", dedup_mode="llm",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert calls[0][1]["id"] == worked_at


@pytest.mark.parametrize("score, reused", [(0.95, True), (0.7, False)])
async def test_expand_schema_semantic_dedup_applies_score_cutoff(kb, score, reused):
    from blue_data_kb_source.types import SchemaSearchResult
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    stub = MagicMock()
    stub.has_semantic = True
    stub.search_schema.return_value = [SchemaSearchResult(
        id=company, type="CLASS", name="COMPANY", description="A business organization", score=score,
    )]
    kb._schema._expansion._search_indexer = stub
    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response("declare_class", {"name": "FIRM", "description": "A business"}),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Acme is a firm.", dedup_mode="search",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert (calls[0][1]["id"] == company) is reused
    assert ("reused_existing" in calls[0][1]) is reused


# ------------------------------------------------------------------ #
# reference="mcp" — live bridge to the real blue-data-wordnet-mcp server #
# ------------------------------------------------------------------ #

requires_wordnet_mcp = pytest.mark.skipif(
    # A pyenv shim satisfies `which` even when the package lives in another environment.
    shutil.which("blue-data-wordnet-mcp") is None or importlib.util.find_spec("blue_data_wordnet_mcp") is None,
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
    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
    assert "DOG" in schema.entities


# ------------------------------------------------------------------ #
# owner-scoped property check (#104)                                   #
# ------------------------------------------------------------------ #

async def _employment(kb):
    await kb.declare_class("test", "PERSON", "A human individual")
    await kb.declare_class("test", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    start = await kb.declare_relation_property("test", "START_DATE", "Start of employment", relation_id=worked_at)
    return worked_at, start


def _relation_property_names(kb, relation_id):
    return sorted(
        nb["node"]["properties"]["name"]
        for nb in kb.get_neighbors("test", relation_id, edge_type="DEFINE_PROPERTY", direction="out")
    )


async def test_expand_schema_property_check_reuses_same_owner_synonym(kb):
    worked_at, start = await _employment(kb)
    calls = []
    factory, client = _mock_openai_sequence(
        _tool_call_response("declare_relation_property", {
            "relation_id": worked_at, "name": "JOIN_DATE", "description": "The date a person joined a company.",
        }),
        _tool_call_response("return_property_check", {
            "describes_owner": True, "same_as_id": start, "reason": "Both are when the employment began.",
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice joined Google in 2005.",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert calls[0][1]["id"] == start
    assert calls[0][1]["reused_existing"]["name"] == "START_DATE"
    assert _relation_property_names(kb, worked_at) == ["START_DATE"]
    check_prompt = client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
    assert "RELATION WORKED_AT — Employment relationship (from PERSON to COMPANY)" in check_prompt
    assert f"{start}: START_DATE — Start of employment" in check_prompt


async def test_expand_schema_property_check_rejects_misplaced_property(kb):
    worked_at, _ = await _employment(kb)
    calls = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response("declare_relation_property", {
            "relation_id": worked_at, "name": "DEGREE", "description": "The degree received from the educational institution.",
        }),
        _tool_call_response("return_property_check", {
            "describes_owner": False, "same_as_id": "", "reason": "A degree describes an education, not an employment.",
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice holds a master's degree from CMU.",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert "does not describe WORKED_AT" in calls[0][1]["error"]
    assert _relation_property_names(kb, worked_at) == ["START_DATE"]


async def test_expand_schema_property_check_with_dedup_none_still_checks_placement(kb):
    worked_at, start = await _employment(kb)
    factory, client = _mock_openai_sequence(
        _tool_call_response("declare_relation_property", {
            "relation_id": worked_at, "name": "END_DATE", "description": "End of employment",
        }),
        _tool_call_response("return_property_check", {
            "describes_owner": True, "same_as_id": start, "reason": "(ignored: no candidates offered)",
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema("test", "Alice left IBM in 2005.", dedup_mode="none")

    check_prompt = client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
    assert "Owner's existing properties:\n(none)" in check_prompt
    assert _relation_property_names(kb, worked_at) == ["END_DATE", "START_DATE"]


async def test_expand_schema_property_check_skipped_for_exact_name_redeclaration(kb):
    worked_at, start = await _employment(kb)
    calls = []
    factory, client = _mock_openai_sequence(
        _tool_call_response("declare_relation_property", {
            "relation_id": worked_at, "name": "start date", "description": "When the job began",
        }),
        _text_response("done"),
    )
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "Alice joined Google in 2005.",
            on_tool_result=lambda name, args, result: calls.append((name, result)),
        )

    assert client.chat.completions.create.call_count == 2
    assert calls[0][1] == {"id": start}



# ------------------------------------------------------------------ #
# timeout (#106)                                                       #
# ------------------------------------------------------------------ #

async def test_expand_schema_timeout_raises_and_keeps_earlier_declarations(kb):
    """A stalled model call must not hang the run: the overall timeout fires with a
    clear error, and what was declared before it stays declared."""
    import asyncio
    from blue_data_kb_source.types import SchemaError

    first = _tool_call_response("declare_class", {"name": "PERSON", "description": "A human individual"})

    async def create(*args, **kwargs):
        if create.calls == 0:
            create.calls += 1
            return first
        await asyncio.sleep(3600)
    create.calls = 0

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=create)
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        with pytest.raises(SchemaError, match=r"timed out after 0.2s, 2 of at most 25 model turn"):
            await kb.expand_schema("test", "Alice is a person.", dedup_mode="none", timeout=0.2)

    schema = DataSchema.from_json(kb.fetch_database_schema("test"))
    assert "PERSON" in schema.entities


# ------------------------------------------------------------------ #
# reference MCP servers can't hang the run (#113)                      #
# ------------------------------------------------------------------ #

import sys as _sys
import time as _time

_SLOW_SERVER = '''
import time
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("slow")

@mcp.tool()
def slow(word: str) -> str:
    time.sleep(60)
    return word

mcp.run()
'''


async def test_dead_reference_server_fails_its_startup_handshake_clearly(kb):
    pytest.importorskip("mcp")
    from blue_data_kb_source.types import SchemaError

    start = _time.monotonic()
    with pytest.raises(SchemaError, match="did not complete its startup handshake within 1s"):
        await kb.expand_schema("test", "x", reference="mcp", mcp_servers=[
            {"name": "dead", "transport": "stdio", "command": "sleep", "args": ["3600"], "startup_timeout": 1}
        ])
    assert _time.monotonic() - start < 20


async def test_overall_timeout_while_connecting_blames_the_servers_not_the_model(kb):
    pytest.importorskip("mcp")
    from blue_data_kb_source.types import SchemaError

    with pytest.raises(SchemaError, match="while connecting to its reference MCP servers"):
        await kb.expand_schema("test", "x", reference="mcp", timeout=1, mcp_servers=[
            {"name": "dead", "transport": "stdio", "command": "sleep", "args": ["3600"]}
        ])


async def test_hanging_reference_tool_call_times_out_and_returns_an_error(kb, tmp_path):
    pytest.importorskip("mcp")
    server = tmp_path / "slow_server.py"
    server.write_text(_SLOW_SERVER)
    results = []
    factory, _ = _mock_openai_sequence(
        _tool_call_response("mcp__slow__slow", {"word": "dog"}),
        _text_response("done"),
    )
    start = _time.monotonic()
    with patch("openai.AsyncOpenAI", factory):
        await kb.expand_schema(
            "test", "x", reference="mcp", dedup_mode="none",
            mcp_servers=[{"name": "slow", "transport": "stdio", "command": _sys.executable,
                          "args": [str(server)], "call_timeout": 1}],
            on_tool_result=lambda name, args, result: results.append((name, result)),
        )
    assert _time.monotonic() - start < 30
    assert results and "error" in results[0][1]


async def test_import_text_forwards_its_schema_expansion_timeout(kb):
    with patch.object(kb._schema, "expand_schema", new=AsyncMock()) as expand, \
         patch.object(kb._extraction, "import_text", new=AsyncMock()):
        await kb.import_text("test", "x", schema_expansion_timeout=42)
    assert expand.call_args.kwargs["timeout"] == 42
