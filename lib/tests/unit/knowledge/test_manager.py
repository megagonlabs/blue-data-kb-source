import itertools
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blue_data_kb_source.types import KBError


def _agent_response(result_payload):
    """Build a mock chat response that immediately calls return_result."""
    tc = MagicMock()
    tc.id = "call_0"
    tc.type = "function"
    tc.function.name = "return_result"
    tc.function.arguments = json.dumps({"result": result_payload})

    msg = MagicMock()
    msg.role = "assistant"
    msg.content = None
    msg.tool_calls = [tc]

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _text_response(text):
    """Build a mock chat response with plain text content (no tool calls)."""
    msg = MagicMock()
    msg.role = "assistant"
    msg.content = text
    msg.tool_calls = None

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _mock_openai(result_payload):
    """Patch openai.AsyncOpenAI for a single agent call (return_result)."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=_agent_response(result_payload)
    )
    return MagicMock(return_value=mock_client)


def _mock_openai_nl(result_payload, nl_text):
    """Patch openai.AsyncOpenAI for agent call followed by NL completion call."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[_agent_response(result_payload), _text_response(nl_text)]
    )
    return MagicMock(return_value=mock_client)


def _tool_call_response(fn, args, call_id="call_0"):
    """Build a mock chat response that calls an arbitrary tool (not return_result)."""
    tc = MagicMock()
    tc.id = call_id
    tc.type = "function"
    tc.function.name = fn
    tc.function.arguments = json.dumps(args)

    msg = MagicMock()
    msg.role = "assistant"
    msg.content = None
    msg.tool_calls = [tc]

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = msg
    return response


def _mock_openai_with_metadata(node_id, result_payload):
    """Patch openai.AsyncOpenAI: calls get_metadata(node_id) once, then return_result."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[_tool_call_response("get_metadata", {"id": node_id}), _agent_response(result_payload)]
    )
    return MagicMock(return_value=mock_client)


def _mock_openai_repeating_tool_call(fn, args=None):
    """Patch openai.AsyncOpenAI: every call returns the same tool call, forever
    (never return_result) — used to test the max_turns cutoff."""
    response = _tool_call_response(fn, args or {}, call_id="call_repeat")
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=itertools.repeat(response))
    return MagicMock(return_value=mock_client), mock_client


def _mock_openai_with_metadata_nl(node_id, result_payload, nl_text, metadata_nl_text):
    """Patch openai.AsyncOpenAI: get_metadata tool call, return_result, then two NL completions
    (main answer/validate text, then metadata text)."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_call_response("get_metadata", {"id": node_id}),
            _agent_response(result_payload),
            _text_response(nl_text),
            _text_response(metadata_nl_text),
        ]
    )
    return MagicMock(return_value=mock_client)


# ------------------------------------------------------------------ #
# add_entity                                                           #
# ------------------------------------------------------------------ #

async def test_add_entity_creates_node(kb):
    entity_id = await kb.add_entity("test", "Alice")
    node = kb.get_node("test", entity_id)
    assert node is not None
    assert node["label"] == "ENTITY"
    assert node["properties"]["name"] == "Alice"


async def test_add_entity_with_explicit_id(kb):
    entity_id = await kb.add_entity("test", "Alice", entity_id="alice")
    assert entity_id == "alice"


async def test_add_entity_wires_is_instance_of(kb):
    class_id  = await kb.declare_class("test", "PERSON", "")
    entity_id = await kb.add_entity("test", "Alice", class_id=class_id)
    nbs = kb.get_neighbors("test", entity_id, edge_type="IS_INSTANCE_OF", direction="out")
    assert any(nb["node"]["id"] == class_id for nb in nbs)


async def test_add_entity_no_is_instance_of_without_class(kb):
    entity_id = await kb.add_entity("test", "Alice")
    nbs = kb.get_neighbors("test", entity_id, edge_type="IS_INSTANCE_OF", direction="out")
    assert nbs == []


# ------------------------------------------------------------------ #
# add_fact                                                             #
# ------------------------------------------------------------------ #

async def test_add_fact_creates_node(kb):
    fact_id = await kb.add_fact("test")
    node = kb.get_node("test", fact_id)
    assert node is not None
    assert node["label"] == "FACT"


async def test_add_fact_auto_names_from_head_tail_relation(kb):
    alice_id = await kb.add_entity("test", "Alice")
    ibm_id   = await kb.add_entity("test", "IBM")
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    rel_id   = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    fact_id  = await kb.add_fact("test",
                                  head_entity_id=alice_id,
                                  tail_entity_id=ibm_id,
                                  relation_id=rel_id)
    node = kb.get_node("test", fact_id)
    name = node["properties"]["name"]
    assert "Alice"    in name
    assert "IBM"      in name
    assert "WORKED_AT" in name


async def test_add_fact_uses_explicit_name(kb):
    fact_id = await kb.add_fact("test", name="my-fact")
    node = kb.get_node("test", fact_id)
    assert node["properties"]["name"] == "my-fact"


async def test_add_fact_wires_head_tail_represents(kb):
    alice_id = await kb.add_entity("test", "Alice")
    ibm_id   = await kb.add_entity("test", "IBM")
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    rel_id   = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    fact_id  = await kb.add_fact("test",
                                  head_entity_id=alice_id,
                                  tail_entity_id=ibm_id,
                                  relation_id=rel_id)
    heads      = kb.get_neighbors("test", fact_id, edge_type="HEAD",      direction="out")
    tails      = kb.get_neighbors("test", fact_id, edge_type="TAIL",      direction="out")
    represents = kb.get_neighbors("test", fact_id, edge_type="REPRESENTS", direction="out")
    assert heads[0]["node"]["id"]      == alice_id
    assert tails[0]["node"]["id"]      == ibm_id
    assert represents[0]["node"]["id"] == rel_id


# ------------------------------------------------------------------ #
# literal type inference (bool before int)                            #
# ------------------------------------------------------------------ #

async def test_bool_value_typed_as_boolean(kb):
    lit_id = await kb._knowledge.add_literal("test", True)
    node = kb.get_node("test", lit_id)
    assert node["properties"]["type"] == "BOOLEAN"
    assert node["properties"]["value"] is True


async def test_false_value_typed_as_boolean(kb):
    lit_id = await kb._knowledge.add_literal("test", False)
    node = kb.get_node("test", lit_id)
    assert node["properties"]["type"] == "BOOLEAN"


async def test_int_value_typed_as_number(kb):
    lit_id = await kb._knowledge.add_literal("test", 42)
    node = kb.get_node("test", lit_id)
    assert node["properties"]["type"] == "NUMBER"


async def test_float_value_typed_as_number(kb):
    lit_id = await kb._knowledge.add_literal("test", 3.14)
    node = kb.get_node("test", lit_id)
    assert node["properties"]["type"] == "NUMBER"


async def test_str_value_typed_as_string(kb):
    lit_id = await kb._knowledge.add_literal("test", "hello")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["type"] == "STRING"


async def test_explicit_literal_type_overrides_inference(kb):
    lit_id = await kb._knowledge.add_literal("test", "2024-01-01", literal_type="DATE")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["type"] == "DATE"


# ------------------------------------------------------------------ #
# add_entity_property_value                                            #
# ------------------------------------------------------------------ #

async def test_add_entity_property_value_creates_literal(kb):
    class_id  = await kb.declare_class("test", "PERSON", "")
    prop_id   = await kb.declare_class_property("test", "AGE", "", class_id=class_id)
    entity_id = await kb.add_entity("test", "Alice", class_id=class_id)
    lit_id    = await kb.add_entity_property_value("test", 30,
                                                    entity_id=entity_id,
                                                    property_id=prop_id)
    node = kb.get_node("test", lit_id)
    assert node["label"] == "LITERAL"
    assert node["properties"]["value"] == 30
    assert node["properties"]["type"] == "NUMBER"


async def test_add_entity_property_value_wires_has_property(kb):
    prop_id   = await kb.declare_class_property("test", "AGE", "")
    entity_id = await kb.add_entity("test", "Alice")
    lit_id    = await kb.add_entity_property_value("test", 30,
                                                    entity_id=entity_id,
                                                    property_id=prop_id)
    has_prop = kb.get_neighbors("test", entity_id, edge_type="HAS_PROPERTY", direction="out")
    assert any(nb["node"]["id"] == lit_id for nb in has_prop)


async def test_add_entity_property_value_wires_is_value_of(kb):
    prop_id   = await kb.declare_class_property("test", "AGE", "")
    entity_id = await kb.add_entity("test", "Alice")
    lit_id    = await kb.add_entity_property_value("test", 30,
                                                    entity_id=entity_id,
                                                    property_id=prop_id)
    is_val_of = kb.get_neighbors("test", lit_id, edge_type="IS_VALUE_OF", direction="out")
    assert any(nb["node"]["id"] == prop_id for nb in is_val_of)


async def test_add_entity_property_bool_typed_correctly(kb):
    entity_id = await kb.add_entity("test", "Alice")
    lit_id    = await kb.add_entity_property_value("test", True, entity_id=entity_id)
    node      = kb.get_node("test", lit_id)
    assert node["properties"]["type"] == "BOOLEAN"


# ------------------------------------------------------------------ #
# add_fact_property_value                                              #
# ------------------------------------------------------------------ #

async def _declare_worked_at(kb):
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    return await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")


async def test_add_fact_property_value_creates_literal(kb):
    rel_id  = await _declare_worked_at(kb)
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    lit_id  = await kb.add_fact_property_value("test", "1998-06-01",
                                                fact_id=fact_id,
                                                property_id=prop_id)
    node = kb.get_node("test", lit_id)
    assert node["label"] == "LITERAL"
    assert node["properties"]["value"] == "1998-06-01"


async def test_add_fact_property_value_wires_has_property(kb):
    rel_id  = await _declare_worked_at(kb)
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    lit_id  = await kb.add_fact_property_value("test", "1998-06-01",
                                                fact_id=fact_id,
                                                property_id=prop_id)
    has_prop = kb.get_neighbors("test", fact_id, edge_type="HAS_PROPERTY", direction="out")
    assert any(nb["node"]["id"] == lit_id for nb in has_prop)


# ------------------------------------------------------------------ #
# add_entity_extraction / add_fact_extraction                          #
# add_entity_property_value_extraction / add_fact_property_value_extraction #
# ------------------------------------------------------------------ #

async def test_add_entity_extraction_wires_edge(kb):
    entity_id = await kb.add_entity("test", "Alice")
    extraction_id = await kb.add_entity_extraction("test", entity_id, text="Alice is a software engineer.")
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == extraction_id for nb in nbs)


async def test_add_entity_extraction_raises_for_missing_entity(kb):
    with pytest.raises(KBError, match="does not exist"):
        await kb.add_entity_extraction("test", "not-a-real-id", text="Some text.")


async def test_add_entity_extraction_raises_for_non_entity_id(kb):
    fact_id = await kb.add_fact("test")
    with pytest.raises(KBError, match="expected"):
        await kb.add_entity_extraction("test", fact_id, text="Some text.")


async def test_add_fact_extraction_wires_edge(kb):
    fact_id = await kb.add_fact("test")
    extraction_id = await kb.add_fact_extraction("test", fact_id, text="Alice joined IBM.")
    nbs = kb.get_neighbors("test", fact_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == extraction_id for nb in nbs)


async def test_add_fact_extraction_raises_for_missing_fact(kb):
    with pytest.raises(KBError, match="does not exist"):
        await kb.add_fact_extraction("test", "not-a-real-id", text="Some text.")


async def test_add_entity_property_value_extraction_wires_edge(kb):
    entity_id = await kb.add_entity("test", "Alice")
    prop_id = await kb.declare_class_property("test", "AGE", "")
    lit_id = await kb.add_entity_property_value("test", 28, entity_id=entity_id, property_id=prop_id)
    extraction_id = await kb.add_entity_property_value_extraction(
        "test", entity_id, prop_id, text="Alice is 28."
    )
    nbs = kb.get_neighbors("test", lit_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == extraction_id for nb in nbs)


async def test_add_entity_property_value_extraction_raises_when_no_value_set(kb):
    entity_id = await kb.add_entity("test", "Alice")
    prop_id = await kb.declare_class_property("test", "AGE", "")
    with pytest.raises(KBError, match="No property value found"):
        await kb.add_entity_property_value_extraction("test", entity_id, prop_id, text="Alice is 28.")


async def test_add_fact_property_value_extraction_wires_edge(kb):
    rel_id  = await _declare_worked_at(kb)
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    lit_id  = await kb.add_fact_property_value("test", "1998-06-01", fact_id=fact_id, property_id=prop_id)
    extraction_id = await kb.add_fact_property_value_extraction(
        "test", fact_id, prop_id, text="Alice joined IBM in June 1998."
    )
    nbs = kb.get_neighbors("test", lit_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == extraction_id for nb in nbs)


async def test_add_fact_property_value_extraction_raises_when_no_value_set(kb):
    rel_id  = await _declare_worked_at(kb)
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    with pytest.raises(KBError, match="No property value found"):
        await kb.add_fact_property_value_extraction("test", fact_id, prop_id, text="Some text.")


# ------------------------------------------------------------------ #
# fetch_database_data                                                  #
# ------------------------------------------------------------------ #

async def test_fetch_database_data_returns_nodes_and_edges(populated_kb):
    data = await populated_kb.fetch_database_data("test")
    assert "nodes" in data
    assert "edges" in data
    entity_labels = {n["label"] for n in data["nodes"]}
    assert "ENTITY" in entity_labels
    assert "FACT"   in entity_labels
    assert "LITERAL" in entity_labels


async def test_fetch_database_data_excludes_provenance_by_default(populated_kb):
    data = await populated_kb.fetch_database_data("test")
    labels = {n["label"] for n in data["nodes"]}
    assert "EXTRACTION" not in labels
    assert "SOURCE"     not in labels


async def test_fetch_database_data_includes_provenance(populated_kb):
    data = await populated_kb.fetch_database_data("test", include_provenance=True)
    labels = {n["label"] for n in data["nodes"]}
    assert "EXTRACTION" in labels
    assert "SOURCE"     in labels


async def test_fetch_database_data_compact_returns_list(populated_kb):
    data = await populated_kb.fetch_database_data("test", compact=True)
    assert isinstance(data, list)
    assert len(data) > 0


async def test_fetch_database_data_compact_has_relation_entries(populated_kb):
    data = await populated_kb.fetch_database_data("test", compact=True)
    types = {entry["relation_type"] for entry in data}
    assert "IS_INSTANCE_OF" in types or "REPRESENTS" in types


async def test_fetch_database_data_compact_shows_all_evidence(populated_kb):
    kb = populated_kb
    entity_nodes = kb.match_nodes("test", label="ENTITY", properties={"name": "Alice"})
    alice_id = entity_nodes[0]["id"]
    await kb.add_entity_extraction("test", alice_id, text="Alice was later promoted to team lead.")

    data = await kb.fetch_database_data("test", include_provenance=True, compact=True)
    entries = [
        e for e in data
        if e["relation_type"] == "IS_INSTANCE_OF" and e["entity_name"] == "Alice"
    ]
    assert len(entries) == 1
    assert "Alice is a software engineer." in entries[0]["extraction"]
    assert "Alice was later promoted to team lead." in entries[0]["extraction"]


# ------------------------------------------------------------------ #
# answer  (QueryEngine — openai patched)                              #
# ------------------------------------------------------------------ #

_ANSWER_PAYLOAD = [
    {"entity": "Alice",        "relation": None,       "value": None, "provenance": "Alice is a software engineer."},
    {"entity": "Alice → IBM",  "relation": "WORKED_AT","value": None, "provenance": "Alice joined IBM in June 1998."},
]

async def test_answer_finds_entity_by_name(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_ANSWER_PAYLOAD)):
        results = await populated_kb.answer("test", "Where did Alice work?")
    entities = [r["entity"] for r in results if r.get("entity")]
    assert any("Alice" in e for e in entities)


async def test_answer_returns_provenance(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_ANSWER_PAYLOAD)):
        results = await populated_kb.answer("test", "Alice")
    alice_result = next((r for r in results if r.get("entity") and "Alice" in r["entity"]), None)
    assert alice_result is not None
    assert alice_result.get("provenance") is not None


async def test_answer_returns_empty_for_unknown_term(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai([])):
        results = await populated_kb.answer("test", "zzz_unknown_zzz")
    assert results == []


async def test_answer_stops_after_max_turns(populated_kb):
    factory, mock_client = _mock_openai_repeating_tool_call("get_schema")
    with patch("openai.AsyncOpenAI", factory):
        results = await populated_kb.answer("test", "Where did Alice work?", max_turns=3)
    assert results == []
    assert mock_client.chat.completions.create.call_count == 3


# ------------------------------------------------------------------ #
# validate  (QueryEngine — openai patched)                            #
# ------------------------------------------------------------------ #

_VALIDATE_PAYLOAD = {
    "supporting": [{"fact": "Alice_WORKED_AT_IBM", "head": "Alice", "tail": "IBM", "relation": "WORKED_AT"}],
    "counter": [],
}

async def test_validate_returns_supporting_facts(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_VALIDATE_PAYLOAD)):
        result = await populated_kb.validate("test", "Alice worked at IBM")
    assert len(result.supporting) > 0


async def test_validate_supporting_fact_has_expected_fields(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_VALIDATE_PAYLOAD)):
        result = await populated_kb.validate("test", "Alice worked at IBM")
    fact = result.supporting[0]
    assert "fact" in fact
    assert "head" in fact
    assert "tail" in fact
    assert "relation" in fact


async def test_validate_counter_empty_by_default(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_VALIDATE_PAYLOAD)):
        result = await populated_kb.validate("test", "Alice worked at IBM")
    assert result.counter == []


# ------------------------------------------------------------------ #
# enrich  (QueryEngine — openai patched)                              #
# ------------------------------------------------------------------ #

_ENRICH_PAYLOAD = [
    {"entity": "Alice → IBM", "relation": "WORKED_AT", "value": None, "distance": 1},
]

async def test_enrich_finds_connected_facts(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_ENRICH_PAYLOAD)):
        results = await populated_kb.enrich("test", "Alice")
    assert len(results) > 0
    assert all(r.distance >= 1 for r in results)
    assert all(r.distance <= 2 for r in results)


async def test_enrich_result_includes_relation_name(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_ENRICH_PAYLOAD)):
        results = await populated_kb.enrich("test", "Alice")
    assert any(r.relation == "WORKED_AT" for r in results)


async def test_enrich_no_results_for_unknown_entity(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai([])):
        results = await populated_kb.enrich("test", "zzz_not_a_person_zzz")
    assert results == []


# ------------------------------------------------------------------ #
# answer natural_language=True                                         #
# ------------------------------------------------------------------ #

_ANSWER_NL_TEXT = "Alice worked at IBM starting in June 1998."

async def test_answer_natural_language_returns_string(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai_nl(_ANSWER_PAYLOAD, _ANSWER_NL_TEXT)):
        result = await populated_kb.answer("test", "Where did Alice work?", natural_language=True)
    assert isinstance(result, str)
    assert len(result) > 0


async def test_answer_natural_language_contains_expected_content(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai_nl(_ANSWER_PAYLOAD, _ANSWER_NL_TEXT)):
        result = await populated_kb.answer("test", "Where did Alice work?", natural_language=True)
    assert result == _ANSWER_NL_TEXT


async def test_answer_default_returns_structured(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_ANSWER_PAYLOAD)):
        result = await populated_kb.answer("test", "Where did Alice work?")
    assert isinstance(result, list)


# ------------------------------------------------------------------ #
# answer / validate include_metadata=True                             #
# ------------------------------------------------------------------ #

def _alice_id(populated_kb):
    return next(
        n["id"] for n in populated_kb.match_nodes("test", label="ENTITY")
        if n["properties"]["name"] == "Alice"
    )


async def test_answer_include_metadata_returns_tuple(populated_kb):
    alice_id = _alice_id(populated_kb)
    with patch("openai.AsyncOpenAI", _mock_openai_with_metadata(alice_id, _ANSWER_PAYLOAD)):
        results, metadata = await populated_kb.answer("test", "Where did Alice work?", include_metadata=True)
    assert isinstance(results, list)
    assert alice_id in metadata
    assert metadata[alice_id]["type"] == "ENTITY"
    assert metadata[alice_id]["name"] == "Alice"
    assert metadata[alice_id]["source"] == "Alice's résumé"
    assert metadata[alice_id]["created"] is not None


async def test_answer_without_include_metadata_returns_bare_list(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_ANSWER_PAYLOAD)):
        result = await populated_kb.answer("test", "Where did Alice work?")
    assert isinstance(result, list)


async def test_validate_include_metadata_returns_tuple(populated_kb):
    alice_id = _alice_id(populated_kb)
    with patch("openai.AsyncOpenAI", _mock_openai_with_metadata(alice_id, _VALIDATE_PAYLOAD)):
        result, metadata = await populated_kb.validate("test", "Alice worked at IBM", include_metadata=True)
    assert len(result.supporting) > 0
    assert alice_id in metadata
    assert metadata[alice_id]["source"] == "Alice's résumé"


_METADATA_NL_TEXT = "This was extracted from Alice's résumé, added on 2026-08-07."

async def test_answer_include_metadata_natural_language_appends_metadata_section(populated_kb):
    alice_id = _alice_id(populated_kb)
    mock = _mock_openai_with_metadata_nl(alice_id, _ANSWER_PAYLOAD, _ANSWER_NL_TEXT, _METADATA_NL_TEXT)
    with patch("openai.AsyncOpenAI", mock):
        text, metadata = await populated_kb.answer(
            "test", "Where did Alice work?", natural_language=True, include_metadata=True
        )
    assert _ANSWER_NL_TEXT in text
    assert _METADATA_NL_TEXT in text
    assert text.index(_ANSWER_NL_TEXT) < text.index(_METADATA_NL_TEXT)
    assert alice_id in metadata


async def test_validate_include_metadata_natural_language_appends_metadata_section(populated_kb):
    alice_id = _alice_id(populated_kb)
    mock = _mock_openai_with_metadata_nl(alice_id, _VALIDATE_PAYLOAD, _VALIDATE_NL_TEXT, _METADATA_NL_TEXT)
    with patch("openai.AsyncOpenAI", mock):
        text, metadata = await populated_kb.validate(
            "test", "Alice worked at IBM", natural_language=True, include_metadata=True
        )
    assert _VALIDATE_NL_TEXT in text
    assert _METADATA_NL_TEXT in text
    assert text.index(_VALIDATE_NL_TEXT) < text.index(_METADATA_NL_TEXT)
    assert alice_id in metadata


# ------------------------------------------------------------------ #
# validate natural_language=True                                       #
# ------------------------------------------------------------------ #

_VALIDATE_NL_TEXT = "The statement is supported: Alice did work at IBM according to the knowledge base."

async def test_validate_natural_language_returns_string(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai_nl(_VALIDATE_PAYLOAD, _VALIDATE_NL_TEXT)):
        result = await populated_kb.validate("test", "Alice worked at IBM", natural_language=True)
    assert isinstance(result, str)
    assert len(result) > 0


async def test_validate_natural_language_contains_expected_content(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai_nl(_VALIDATE_PAYLOAD, _VALIDATE_NL_TEXT)):
        result = await populated_kb.validate("test", "Alice worked at IBM", natural_language=True)
    assert result == _VALIDATE_NL_TEXT


async def test_validate_default_returns_structured(populated_kb):
    with patch("openai.AsyncOpenAI", _mock_openai(_VALIDATE_PAYLOAD)):
        result = await populated_kb.validate("test", "Alice worked at IBM")
    assert hasattr(result, "supporting")


# ------------------------------------------------------------------ #
# find_entity / find_fact                                              #
# ------------------------------------------------------------------ #

async def test_find_entity_exact_name(kb):
    alice = await kb.add_entity("test", "Alice")
    await kb.add_entity("test", "Alicia")
    node = await kb.find_entity("test", "Alice")
    assert node is not None and node["id"] == alice
    assert await kb.find_entity("test", "alice") is None
    assert await kb.find_entity("test", "Missing") is None


async def test_find_entity_duplicate_names_returns_one(kb):
    await kb.add_entity("test", "IBM")
    await kb.add_entity("test", "IBM")
    node = await kb.find_entity("test", "IBM")
    assert node is not None and node["properties"]["name"] == "IBM"


async def test_find_fact_matches_exact_triple(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    hired  = await kb.declare_relation_type(
        "test", "HIRED_BY", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    hit = await kb.find_fact("test", alice, worked, ibm)
    assert hit is not None and hit["id"] == fact
    assert await kb.find_fact("test", ibm, worked, alice) is None
    assert await kb.find_fact("test", alice, hired, ibm) is None


# ------------------------------------------------------------------ #
# import_database_data (schema-ref resolution)                         #
# ------------------------------------------------------------------ #

async def test_import_database_data_round_trip(kb):
    person = await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    age   = await kb.declare_class_property("test", "AGE", "", class_id=person)
    start = await kb.declare_relation_property("test", "START_DATE", "", relation_id=worked)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM")
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    await kb.add_entity_property_value("test", 30, entity_id=alice, property_id=age)
    await kb.add_fact_property_value("test", "1998-06-01", fact_id=fact, property_id=start)
    data = await kb.export_database_data("test")

    kb.create_database("copy")
    await kb.import_database_schema("copy", await kb.export_database_schema("test"))
    await kb.import_database_data("copy", data)
    ents = {n["properties"]["name"] for n in kb.match_nodes("copy", label="ENTITY")}
    assert ents == {"Alice", "IBM"}
    assert len(kb.match_nodes("copy", label="FACT")) == 1
    assert len(kb.match_nodes("copy", label="LITERAL")) == 2


async def test_import_database_data_missing_class_raises(kb):
    data = {"entities": [{"ref": "e1", "name": "Alice", "class": "GHOST"}]}
    with pytest.raises(KBError) as exc:
        await kb.import_database_data("test", data)
    assert "GHOST" in str(exc.value)
