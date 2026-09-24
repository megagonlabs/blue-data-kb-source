import itertools
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blue_data_kb_source.types import KBError, KnowledgeError


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
    person   = await kb.declare_class("test", "PERSON", "")
    company  = await kb.declare_class("test", "COMPANY", "")
    alice_id = await kb.add_entity("test", "Alice", class_id=person)
    ibm_id   = await kb.add_entity("test", "IBM", class_id=company)
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
    person   = await kb.declare_class("test", "PERSON", "")
    company  = await kb.declare_class("test", "COMPANY", "")
    alice_id = await kb.add_entity("test", "Alice", class_id=person)
    ibm_id   = await kb.add_entity("test", "IBM", class_id=company)
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
# add_fact — domain/range conformance                                  #
# ------------------------------------------------------------------ #

async def test_add_fact_rejects_head_outside_domain(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    ibm     = await kb.add_entity("test", "IBM", class_id=company)
    other   = await kb.add_entity("test", "Other Corp", class_id=company)
    rel_id  = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    with pytest.raises(KnowledgeError, match="DOMAIN_RANGE_MISMATCH"):
        await kb.add_fact("test", head_entity_id=ibm, tail_entity_id=other, relation_id=rel_id)
    assert not kb.match_nodes("test", label="FACT")


async def test_add_fact_rejects_tail_outside_range(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    alice   = await kb.add_entity("test", "Alice", class_id=person)
    bob     = await kb.add_entity("test", "Bob", class_id=person)
    rel_id  = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    with pytest.raises(KnowledgeError, match="DOMAIN_RANGE_MISMATCH"):
        await kb.add_fact("test", head_entity_id=alice, tail_entity_id=bob, relation_id=rel_id)


async def test_add_fact_rejects_untyped_head(kb):
    company = await kb.declare_class("test", "COMPANY", "")
    person  = await kb.declare_class("test", "PERSON", "")
    alice   = await kb.add_entity("test", "Alice")  # no class
    ibm     = await kb.add_entity("test", "IBM", class_id=company)
    rel_id  = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    with pytest.raises(KnowledgeError, match="UNTYPED_ENTITY"):
        await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=rel_id)


async def test_add_fact_allows_subclass_of_domain(kb):
    person   = await kb.declare_class("test", "PERSON", "")
    employee = await kb.declare_class("test", "EMPLOYEE", "")
    company  = await kb.declare_class("test", "COMPANY", "")
    await kb.add_is_subclass_of("test", employee, person)
    alice   = await kb.add_entity("test", "Alice", class_id=employee)
    ibm     = await kb.add_entity("test", "IBM", class_id=company)
    rel_id  = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    fact_id = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=rel_id)
    node = kb.get_node("test", fact_id)
    assert node is not None


async def test_add_fact_skips_check_when_head_tail_or_relation_missing(kb):
    person = await kb.declare_class("test", "PERSON", "")
    alice  = await kb.add_entity("test", "Alice", class_id=person)
    # No relation_id/tail given at all -- nothing to check against, must not raise.
    fact_id = await kb.add_fact("test", head_entity_id=alice)
    assert kb.get_node("test", fact_id) is not None


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
# literal value casting (explicit literal_type coerces string input)  #
# ------------------------------------------------------------------ #

async def test_number_string_is_cast_to_int(kb):
    lit_id = await kb._knowledge.add_literal("test", "42", literal_type="NUMBER")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == 42
    assert isinstance(node["properties"]["value"], int)


async def test_number_string_is_cast_to_float(kb):
    lit_id = await kb._knowledge.add_literal("test", "3.14", literal_type="NUMBER")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == 3.14
    assert isinstance(node["properties"]["value"], float)


async def test_boolean_string_is_cast_to_bool(kb):
    lit_id = await kb._knowledge.add_literal("test", "true", literal_type="BOOLEAN")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] is True


async def test_boolean_string_false_is_cast_to_bool(kb):
    lit_id = await kb._knowledge.add_literal("test", "false", literal_type="BOOLEAN")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] is False


async def test_already_typed_value_passes_through_cast(kb):
    lit_id = await kb._knowledge.add_literal("test", 42, literal_type="NUMBER")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == 42


async def test_string_value_is_cast_to_str_for_string_type(kb):
    lit_id = await kb._knowledge.add_literal("test", 42, literal_type="STRING")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == "42"
    assert isinstance(node["properties"]["value"], str)


async def test_invalid_number_string_raises(kb):
    with pytest.raises(KnowledgeError):
        await kb._knowledge.add_literal("test", "not-a-number", literal_type="NUMBER")


async def test_invalid_boolean_string_raises(kb):
    with pytest.raises(KnowledgeError):
        await kb._knowledge.add_literal("test", "maybe", literal_type="BOOLEAN")


async def test_date_textual_month_is_normalized_to_iso(kb):
    lit_id = await kb._knowledge.add_literal("test", "June 1998", literal_type="DATE")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == "1998-06"


async def test_date_full_textual_date_is_normalized_to_iso(kb):
    lit_id = await kb._knowledge.add_literal("test", "June 1, 1998", literal_type="DATE")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == "1998-06-01"


async def test_date_already_iso_passes_through_unchanged(kb):
    lit_id = await kb._knowledge.add_literal("test", "1998-06-01", literal_type="DATE")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == "1998-06-01"


async def test_date_ambiguous_numeric_format_is_not_guessed(kb):
    lit_id = await kb._knowledge.add_literal("test", "06/07/1998", literal_type="DATE")
    node = kb.get_node("test", lit_id)
    assert node["properties"]["value"] == "06/07/1998"


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
    person    = await kb.declare_class("test", "PERSON", "")
    prop_id   = await kb.declare_class_property("test", "AGE", "", class_id=person)
    entity_id = await kb.add_entity("test", "Alice", class_id=person)
    lit_id    = await kb.add_entity_property_value("test", 30,
                                                    entity_id=entity_id,
                                                    property_id=prop_id)
    has_prop = kb.get_neighbors("test", entity_id, edge_type="HAS_PROPERTY", direction="out")
    assert any(nb["node"]["id"] == lit_id for nb in has_prop)


async def test_add_entity_property_value_wires_is_value_of(kb):
    person    = await kb.declare_class("test", "PERSON", "")
    prop_id   = await kb.declare_class_property("test", "AGE", "", class_id=person)
    entity_id = await kb.add_entity("test", "Alice", class_id=person)
    lit_id    = await kb.add_entity_property_value("test", 30,
                                                    entity_id=entity_id,
                                                    property_id=prop_id)
    is_val_of = kb.get_neighbors("test", lit_id, edge_type="IS_VALUE_OF", direction="out")
    assert any(nb["node"]["id"] == prop_id for nb in is_val_of)


# ------------------------------------------------------------------ #
# add_*_property_value — property ownership conformance                #
# ------------------------------------------------------------------ #

async def test_add_entity_property_value_rejects_property_not_on_entitys_class(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    age_id  = await kb.declare_class_property("test", "AGE", "", class_id=person)
    ibm     = await kb.add_entity("test", "IBM", class_id=company)
    with pytest.raises(KnowledgeError, match="NOT_DEFINED_FOR_OWNER"):
        await kb.add_entity_property_value("test", 100, entity_id=ibm, property_id=age_id)


async def test_add_entity_property_value_rejects_untyped_entity(kb):
    person = await kb.declare_class("test", "PERSON", "")
    age_id = await kb.declare_class_property("test", "AGE", "", class_id=person)
    alice  = await kb.add_entity("test", "Alice")  # no class
    with pytest.raises(KnowledgeError, match="UNTYPED_ENTITY"):
        await kb.add_entity_property_value("test", 28, entity_id=alice, property_id=age_id)


async def test_add_entity_property_value_allows_property_via_subclass(kb):
    person   = await kb.declare_class("test", "PERSON", "")
    employee = await kb.declare_class("test", "EMPLOYEE", "")
    await kb.add_is_subclass_of("test", employee, person)
    age_id = await kb.declare_class_property("test", "AGE", "", class_id=person)
    alice  = await kb.add_entity("test", "Alice", class_id=employee)
    lit_id = await kb.add_entity_property_value("test", 28, entity_id=alice, property_id=age_id)
    assert kb.get_node("test", lit_id) is not None


async def test_add_fact_property_value_rejects_property_not_on_facts_relation(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked  = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    lives   = await kb.declare_relation_type("test", "LIVES_IN", "", domain="PERSON", range="COMPANY")
    since_id = await kb.declare_relation_property("test", "SINCE", "", relation_id=lives)
    fact_id = await kb.add_fact("test", relation_id=worked)
    with pytest.raises(KnowledgeError, match="NOT_DEFINED_FOR_OWNER"):
        await kb.add_fact_property_value("test", "2020", fact_id=fact_id, property_id=since_id)


async def test_add_fact_property_value_rejects_fact_with_no_relation(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    worked  = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="PERSON")
    start_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=worked)
    fact_id = await kb.add_fact("test")  # no relation at all
    with pytest.raises(KnowledgeError, match="FACT_HAS_NO_RELATION"):
        await kb.add_fact_property_value("test", "2020", fact_id=fact_id, property_id=start_id)


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
    person = await kb.declare_class("test", "PERSON", "")
    entity_id = await kb.add_entity("test", "Alice", class_id=person)
    prop_id = await kb.declare_class_property("test", "AGE", "", class_id=person)
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
# confidence / extraction_type threading                               #
# ------------------------------------------------------------------ #

def _extraction_props(kb, node_id: str) -> dict:
    """Properties of the single EXTRACTION attached to node_id."""
    nbs = kb.get_neighbors("test", node_id, edge_type="EXTRACTED_FROM", direction="out")
    assert len(nbs) == 1
    return nbs[0]["node"]["properties"]


async def test_add_entity_threads_confidence_and_type(kb):
    entity_id = await kb.add_entity(
        "test", "Alice", text="Alice joined IBM.", confidence=0.9, extraction_type="assertion"
    )
    props = _extraction_props(kb, entity_id)
    assert props["confidence"] == 0.9
    assert props["extraction_type"] == "assertion"


async def test_add_fact_threads_confidence_and_type(kb):
    alice = await kb.add_entity("test", "Alice")
    ibm   = await kb.add_entity("test", "IBM")
    fact_id = await kb.add_fact(
        "test", head_entity_id=alice, tail_entity_id=ibm,
        text="Alice joined IBM.", confidence=0.8, extraction_type="external_record",
    )
    props = _extraction_props(kb, fact_id)
    assert props["confidence"] == 0.8
    assert props["extraction_type"] == "external_record"


async def test_add_entity_property_value_threads_confidence_and_type(kb):
    entity_id = await kb.add_entity("test", "Alice")
    lit_id    = await kb.add_entity_property_value(
        "test", "28", entity_id=entity_id, text="Alice is 28.",
        confidence=0.6, extraction_type="external_record",
    )
    props = _extraction_props(kb, lit_id)
    assert props["confidence"] == 0.6
    assert props["extraction_type"] == "external_record"


async def test_add_fact_property_value_threads_confidence_and_type(kb):
    rel_id  = await _declare_worked_at(kb)
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    lit_id  = await kb.add_fact_property_value(
        "test", "1998-06-01", fact_id=fact_id, property_id=prop_id,
        text="Alice joined IBM in June 1998.", confidence=0.5, extraction_type="assertion",
    )
    props = _extraction_props(kb, lit_id)
    assert props["confidence"] == 0.5
    assert props["extraction_type"] == "assertion"


async def test_extraction_adders_thread_confidence_and_type(kb):
    entity_id = await kb.add_entity("test", "Alice")
    fact_id   = await kb.add_fact("test", head_entity_id=entity_id)
    ext_a = await kb.add_entity_extraction(
        "test", entity_id, text="Alice is a software engineer.", confidence=0.7
    )
    ext_f = await kb.add_fact_extraction(
        "test", fact_id, text="Alice joined IBM.", confidence=0.4, extraction_type="assertion"
    )
    assert kb.get_node("test", ext_a)["properties"]["confidence"] == 0.7
    assert kb.get_node("test", ext_a)["properties"]["extraction_type"] == "text_span"
    assert kb.get_node("test", ext_f)["properties"]["confidence"] == 0.4
    assert kb.get_node("test", ext_f)["properties"]["extraction_type"] == "assertion"


async def test_property_value_extraction_adders_thread_confidence_and_type(kb):
    person    = await kb.declare_class("test", "PERSON", "")
    entity_id = await kb.add_entity("test", "Alice", class_id=person)
    age_id    = await kb.declare_class_property("test", "AGE", "", class_id=person)
    await kb.add_entity_property_value("test", "28", entity_id=entity_id, property_id=age_id)
    ext_e = await kb.add_entity_property_value_extraction(
        "test", entity_id, age_id, text="Alice is 28.",
        confidence=0.3, extraction_type="external_record",
    )
    rel_id  = await _declare_worked_at(kb)
    start   = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    await kb.add_fact_property_value("test", "1998-06-01", fact_id=fact_id, property_id=start)
    ext_f = await kb.add_fact_property_value_extraction(
        "test", fact_id, start, text="Alice joined IBM in June 1998.", confidence=0.2
    )
    assert kb.get_node("test", ext_e)["properties"]["confidence"] == 0.3
    assert kb.get_node("test", ext_e)["properties"]["extraction_type"] == "external_record"
    assert kb.get_node("test", ext_f)["properties"]["confidence"] == 0.2
    assert kb.get_node("test", ext_f)["properties"]["extraction_type"] == "text_span"


async def test_creators_reject_confidence_without_text(kb):
    entity_id = await kb.add_entity("test", "Alice")
    fact_id   = await kb.add_fact("test", head_entity_id=entity_id)
    with pytest.raises(KBError, match="confidence requires text"):
        await kb.add_entity("test", "Bob", confidence=0.9)
    with pytest.raises(KBError, match="confidence requires text"):
        await kb.add_fact("test", head_entity_id=entity_id, confidence=0.9)
    with pytest.raises(KBError, match="confidence requires text"):
        await kb.add_entity_property_value("test", "28", entity_id=entity_id, confidence=0.9)
    with pytest.raises(KBError, match="confidence requires text"):
        await kb.add_fact_property_value("test", "1998", fact_id=fact_id, confidence=0.9)


async def test_creator_confidence_range_is_validated(kb):
    with pytest.raises(KBError, match=r"\[0\.0, 1\.0\]"):
        await kb.add_entity("test", "Alice", text="Alice joined IBM.", confidence=1.5)


# ------------------------------------------------------------------ #
# FACT confidence derivation                                           #
# ------------------------------------------------------------------ #

async def _fact(kb, text=None, confidence=None):
    """A fact between two entities, optionally created with scored evidence."""
    alice = await kb.add_entity("test", "Alice")
    ibm   = await kb.add_entity("test", "IBM")
    return await kb.add_fact(
        "test", head_entity_id=alice, tail_entity_id=ibm, text=text, confidence=confidence
    )


async def test_fact_confidence_is_the_max_of_its_extractions(kb):
    fact_id = await _fact(kb, text="Alice joined IBM.", confidence=0.6)
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.6
    await kb.add_fact_extraction("test", fact_id, text="A surer source.", confidence=0.9)
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.9
    await kb.add_fact_extraction("test", fact_id, text="A shakier source.", confidence=0.2)
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.9


async def test_fact_confidence_ignores_extractions_that_state_none(kb):
    fact_id = await _fact(kb, text="Alice joined IBM.", confidence=0.6)
    await kb.add_fact_extraction("test", fact_id, text="No score stated.")
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.6


async def test_fact_confidence_is_none_when_no_extraction_states_one(kb):
    fact_id = await _fact(kb)
    assert kb.get_node("test", fact_id)["properties"]["confidence"] is None
    await kb.add_fact_extraction("test", fact_id, text="Evidence with no score.")
    assert kb.get_node("test", fact_id)["properties"]["confidence"] is None


async def test_fact_confidence_of_zero_is_kept_not_treated_as_unstated(kb):
    fact_id = await _fact(kb, text="A source that denies it.", confidence=0.0)
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.0


async def test_fact_confidence_recomputes_on_the_facade_extraction_path(kb):
    fact_id = await _fact(kb)
    await kb.add_extraction("test", text="Attached directly.", element_id=fact_id, confidence=0.75)
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.75


async def test_recompute_leaves_non_fact_owners_alone(kb):
    entity_id = await kb.add_entity("test", "Bob", text="Bob exists.", confidence=0.5)
    assert "confidence" not in kb.get_node("test", entity_id)["properties"]


async def test_recompute_tolerates_an_element_that_does_not_exist(kb):
    await kb.add_extraction("test", text="Attached to nothing.", element_id="no-such-id",
                            confidence=0.5)
    assert kb.get_node("test", "no-such-id") is None


async def test_fact_confidence_change_refreshes_modified(kb):
    fact_id = await _fact(kb)
    before = kb.get_node("test", fact_id)["properties"]["modified"]
    await kb.add_fact_extraction("test", fact_id, text="A scored source.", confidence=0.4)
    assert kb.get_node("test", fact_id)["properties"]["modified"] > before


async def test_unchanged_confidence_writes_nothing(kb):
    fact_id = await _fact(kb, text="Alice joined IBM.", confidence=0.6)
    before = kb.get_node("test", fact_id)["properties"]["modified"]
    await kb.add_fact_extraction("test", fact_id, text="A shakier source.", confidence=0.2)
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.6
    assert kb.get_node("test", fact_id)["properties"]["modified"] == before


# ------------------------------------------------------------------ #
# get_metadata                                                         #
# ------------------------------------------------------------------ #

async def _fact_with_evidence(kb):
    src = await kb.add_source("test", source="resume.pdf", name="Alice's resume",
                              original="1998-06-01")
    fact_id = await _fact(kb, text="Alice joined IBM.", confidence=0.9)
    await kb.add_fact_extraction("test", fact_id, text="Per LinkedIn.", confidence=0.6,
                                 source_id=src)
    await kb.add_fact_extraction("test", fact_id, text="Unscored note.")
    return fact_id


async def test_metadata_carries_the_facts_derived_confidence(kb):
    fact_id = await _fact_with_evidence(kb)
    assert (await kb.get_metadata("test", fact_id))["confidence"] == 0.9


async def test_metadata_evidence_lists_each_extraction_with_its_own_score(kb):
    fact_id = await _fact_with_evidence(kb)
    evidence = (await kb.get_metadata("test", fact_id))["evidence"]
    assert [e["text"] for e in evidence] == [
        "Alice joined IBM.", "Per LinkedIn.", "Unscored note."
    ]
    assert [e["confidence"] for e in evidence] == [0.9, 0.6, None]


async def test_metadata_evidence_carries_each_extractions_own_source_and_date(kb):
    fact_id = await _fact_with_evidence(kb)
    evidence = (await kb.get_metadata("test", fact_id))["evidence"]
    assert [e["source"] for e in evidence] == [None, "Alice's resume", None]
    assert all(e["created"] for e in evidence)
    assert evidence[1]["source_original"] == "1998-06-01"


async def test_each_record_names_its_source_even_when_shared(kb):
    src = await kb.add_source("test", source="resume.pdf", name="Alice's resume")
    fact_id = await _fact(kb)
    await kb.add_fact_extraction("test", fact_id, text="First span.", source_id=src)
    await kb.add_fact_extraction("test", fact_id, text="Second span.", source_id=src)
    meta = await kb.get_metadata("test", fact_id)
    assert [e["source"] for e in meta["evidence"]] == ["Alice's resume", "Alice's resume"]


async def test_metadata_without_evidence_is_empty_list_not_none(kb):
    entity_id = await kb.add_entity("test", "Alice")
    meta = await kb.get_metadata("test", entity_id)
    assert meta["evidence"] == []
    assert meta["confidence"] is None


async def test_evidence_includes_an_extraction_with_empty_text(kb):
    entity_id = await kb.add_entity("test", "Alice")
    await kb.add_extraction("test", text="", element_id=entity_id, confidence=0.5)
    meta = await kb.get_metadata("test", entity_id)
    assert len(meta["evidence"]) == 1
    assert meta["evidence"][0]["text"] == ""
    assert meta["evidence"][0]["confidence"] == 0.5


# ------------------------------------------------------------------ #
# fetch_database_data                                                  #
# ------------------------------------------------------------------ #

async def test_detailed_rows_carry_the_facts_derived_confidence(kb):
    fact_id = await _fact(kb, text="Alice joined IBM.", confidence=0.9)
    rows = await kb.fetch_database_data("test", compact=True, detailed=True)
    fact_rows = [r for r in rows if r.get("relation_type") == "REPRESENTS"]
    assert fact_rows and all("confidence" in r for r in fact_rows)
    assert fact_rows[0]["confidence"] == 0.9
    assert kb.get_node("test", fact_id)["properties"]["confidence"] == 0.9


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
    texts = [e["text"] for e in entries[0]["evidence"]]
    assert texts == ["Alice is a software engineer.", "Alice was later promoted to team lead."]


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
    assert metadata[alice_id]["evidence"][0]["source"] == "Alice's résumé"
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
    assert metadata[alice_id]["evidence"][0]["source"] == "Alice's résumé"


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
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    age   = await kb.declare_class_property("test", "AGE", "", class_id=person)
    start = await kb.declare_relation_property("test", "START_DATE", "", relation_id=worked)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
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


async def test_import_database_data_is_all_or_nothing(kb):
    """A fact that fails domain/range mid-import removes the entities (and their
    provenance) already written for it, instead of leaving them half-imported (#108)."""
    person = await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "ROBOT", "")
    await kb.declare_class("test", "COMPANY", "")
    await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    kb.create_database("copy")
    await kb.import_database_schema("copy", await kb.export_database_schema("test"))
    existing = await kb.add_entity("copy", "Pre-existing", class_id=(await kb.find_class("copy", "PERSON"))["id"],
                                   text="Already here.")

    data = {
        "entities": [
            {"ref": "e1", "name": "R2", "class": "ROBOT", "extraction": "R2 worked at IBM."},
            {"ref": "e2", "name": "IBM", "class": "COMPANY", "extraction": "R2 worked at IBM."},
        ],
        "facts": [{"ref": "f1", "relation": "WORKED_AT", "head_ref": "e1", "tail_ref": "e2"}],
    }
    with pytest.raises(KnowledgeError, match="domain/range"):
        await kb.import_database_data("copy", data)

    assert [n["id"] for n in kb.match_nodes("copy", label="ENTITY")] == [existing]
    assert kb.match_nodes("copy", label="FACT") == []
    assert len(kb.match_nodes("copy", label="EXTRACTION")) == 1
    _ = person


async def test_import_database_data_missing_class_raises(kb):
    data = {"entities": [{"ref": "e1", "name": "Alice", "class": "GHOST"}]}
    with pytest.raises(KBError) as exc:
        await kb.import_database_data("test", data)
    assert "GHOST" in str(exc.value)


# ------------------------------------------------------------------ #
# Instance walks                                                       #
# ------------------------------------------------------------------ #

async def test_get_entity_classes_returns_all_classes(kb):
    person   = await kb.declare_class("test", "PERSON", "")
    employee = await kb.declare_class("test", "EMPLOYEE", "")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    kb.add_edge("test", alice, "IS_INSTANCE_OF", employee)
    names = {c["properties"]["name"] for c in await kb.get_entity_classes("test", alice)}
    assert names == {"PERSON", "EMPLOYEE"}


async def test_get_fact_parts_returns_nodes_and_none_when_unwired(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    parts = await kb.get_fact_parts("test", fact)
    assert parts["head"]["id"] == alice
    assert parts["relation"]["id"] == worked
    assert parts["tail"]["id"] == ibm
    bare = await kb.add_fact("test")
    assert await kb.get_fact_parts("test", bare) == {"head": None, "relation": None, "tail": None}


async def test_get_property_values_lists_literals_with_properties(kb):
    person = await kb.declare_class("test", "PERSON", "")
    age = await kb.declare_class_property("test", "AGE", "", class_id=person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    await kb.add_entity_property_value("test", 30, entity_id=alice, property_id=age)
    await kb.add_entity_property_value("test", "note", entity_id=alice)
    got = {}
    for v in await kb.get_property_values("test", alice):
        key = v["property"]["properties"]["name"] if v["property"] else None
        got[key] = v["literal"]["properties"]["value"]
    assert got == {"AGE": 30, None: "note"}


async def test_get_property_values_on_fact_owner(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    start = await kb.declare_relation_property("test", "START_DATE", "", relation_id=worked)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    await kb.add_fact_property_value("test", "1998-06-01", fact_id=fact, property_id=start)
    vals = await kb.get_property_values("test", fact)
    assert len(vals) == 1
    assert vals[0]["property"]["properties"]["name"] == "START_DATE"
    assert vals[0]["literal"]["properties"]["value"] == "1998-06-01"


async def test_instance_walk_guards_reject_wrong_labels(kb):
    person = await kb.declare_class("test", "PERSON", "")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    fact = await kb.add_fact("test")
    with pytest.raises(KBError):
        await kb.get_entity_classes("test", fact)
    with pytest.raises(KBError):
        await kb.get_fact_parts("test", alice)
    with pytest.raises(KBError):
        await kb.get_property_values("test", person)
    with pytest.raises(KBError):
        await kb.get_property_values("test", "no-such-id")


# ------------------------------------------------------------------ #
# Conformance checks                                                   #
# ------------------------------------------------------------------ #

async def test_is_fact_allowed_exact_match(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    verdict = await kb.is_fact_allowed("test", alice, worked, ibm)
    assert verdict["ok"] is True
    assert verdict["reason"] == "ALLOWED"
    assert verdict["details"]["head_in_domain"] is True
    assert verdict["details"]["tail_in_range"] is True


async def test_is_fact_allowed_via_ancestor_class(kb):
    person   = await kb.declare_class("test", "PERSON", "")
    employee = await kb.declare_class("test", "EMPLOYEE", "")
    company  = await kb.declare_class("test", "COMPANY", "")
    await kb.add_is_subclass_of("test", employee, person)
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    bob = await kb.add_entity("test", "Bob", class_id=employee)
    ibm = await kb.add_entity("test", "IBM", class_id=company)
    verdict = await kb.is_fact_allowed("test", bob, worked, ibm)
    assert verdict["ok"] is True
    assert verdict["reason"] == "ALLOWED"


async def test_is_fact_allowed_tail_via_ancestor_class(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    startup = await kb.declare_class("test", "STARTUP", "")
    await kb.add_is_subclass_of("test", startup, company)
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    acme  = await kb.add_entity("test", "Acme", class_id=startup)
    verdict = await kb.is_fact_allowed("test", alice, worked, acme)
    assert verdict["ok"] is True
    assert verdict["reason"] == "ALLOWED"


async def test_is_fact_allowed_matches_any_of_multiple_classes(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    bob = await kb.add_entity("test", "Bob", class_id=company)
    kb.add_edge("test", bob, "IS_INSTANCE_OF", person)
    ibm = await kb.add_entity("test", "IBM", class_id=company)
    verdict = await kb.is_fact_allowed("test", bob, worked, ibm)
    assert verdict["ok"] is True
    assert set(verdict["details"]["head_classes"]) == {"COMPANY", "PERSON"}


async def test_is_fact_allowed_max_hops_caps_ancestor_expansion(kb):
    company = await kb.declare_class("test", "COMPANY", "")
    chain = [await kb.declare_class("test", f"L{i}", "") for i in range(6)]
    top = await kb.declare_class("test", "TOP", "")
    lineage = chain + [top]
    for i in range(len(lineage) - 1):
        await kb.add_is_subclass_of("test", lineage[i], lineage[i + 1])
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="TOP", range="COMPANY"
    )
    bob = await kb.add_entity("test", "Bob", class_id=chain[0])
    ibm = await kb.add_entity("test", "IBM", class_id=company)
    capped = await kb.is_fact_allowed("test", bob, worked, ibm)
    assert capped["ok"] is False
    assert capped["reason"] == "DOMAIN_RANGE_MISMATCH"
    unlimited = await kb.is_fact_allowed("test", bob, worked, ibm, max_hops=None)
    assert unlimited["ok"] is True


async def test_is_fact_allowed_mismatch_reports_offending_side(kb):
    person = await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    carol = await kb.add_entity("test", "Carol", class_id=person)
    verdict = await kb.is_fact_allowed("test", alice, worked, carol)
    assert verdict["ok"] is False
    assert verdict["reason"] == "DOMAIN_RANGE_MISMATCH"
    assert verdict["details"]["head_in_domain"] is True
    assert verdict["details"]["tail_in_range"] is False
    assert verdict["details"]["tail_classes"] == ["PERSON"]
    assert verdict["details"]["range"] == ["COMPANY"]


async def test_is_fact_allowed_untyped_entity(kb):
    await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    ghost = await kb.add_entity("test", "Ghost")
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    verdict = await kb.is_fact_allowed("test", ghost, worked, ibm)
    assert verdict["ok"] is False
    assert verdict["reason"] == "UNTYPED_ENTITY"
    assert verdict["details"]["untyped"] == ["head"]
    tail_side = await kb.is_fact_allowed("test", ibm, worked, ghost)
    assert tail_side["reason"] == "UNTYPED_ENTITY"
    assert tail_side["details"]["untyped"] == ["tail"]
    ghost2 = await kb.add_entity("test", "Ghost2")
    both = await kb.is_fact_allowed("test", ghost, worked, ghost2)
    assert both["details"]["untyped"] == ["head", "tail"]


async def test_is_property_allowed_on_class_and_ancestor(kb):
    person   = await kb.declare_class("test", "PERSON", "")
    employee = await kb.declare_class("test", "EMPLOYEE", "")
    await kb.add_is_subclass_of("test", employee, person)
    age = await kb.declare_class_property("test", "AGE", "", class_id=person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    bob   = await kb.add_entity("test", "Bob", class_id=employee)
    direct = await kb.is_property_allowed("test", alice, age)
    assert direct["ok"] is True
    assert direct["reason"] == "ALLOWED"
    inherited = await kb.is_property_allowed("test", bob, age)
    assert inherited["ok"] is True
    assert inherited["details"]["defined_on"] == ["PERSON"]


async def test_is_property_allowed_matches_any_of_multiple_classes(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    age = await kb.declare_class_property("test", "AGE", "", class_id=person)
    bob = await kb.add_entity("test", "Bob", class_id=company)
    kb.add_edge("test", bob, "IS_INSTANCE_OF", person)
    verdict = await kb.is_property_allowed("test", bob, age)
    assert verdict["ok"] is True
    assert set(verdict["details"]["owner_classes"]) == {"COMPANY", "PERSON"}


async def test_is_property_allowed_rejects_unrelated_and_orphan(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    motto = await kb.declare_class_property("test", "MOTTO", "", class_id=company)
    note  = await kb.declare_class_property("test", "NOTE", "")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    unrelated = await kb.is_property_allowed("test", alice, motto)
    assert unrelated["ok"] is False
    assert unrelated["reason"] == "NOT_DEFINED_FOR_OWNER"
    assert unrelated["details"]["owner_classes"] == ["PERSON"]
    assert unrelated["details"]["defined_on"] == ["COMPANY"]
    orphan = await kb.is_property_allowed("test", alice, note)
    assert orphan["ok"] is False
    assert orphan["reason"] == "NOT_DEFINED_FOR_OWNER"
    assert orphan["details"]["defined_on"] == []


async def test_is_property_allowed_on_fact_owner(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    start = await kb.declare_relation_property("test", "START_DATE", "", relation_id=worked)
    age   = await kb.declare_class_property("test", "AGE", "", class_id=person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    verdict = await kb.is_property_allowed("test", fact, start)
    assert verdict["ok"] is True
    assert verdict["details"]["owner_relation"] == "WORKED_AT"
    wrong = await kb.is_property_allowed("test", fact, age)
    assert wrong["ok"] is False
    assert wrong["reason"] == "NOT_DEFINED_FOR_OWNER"
    bare = await kb.add_fact("test")
    unwired = await kb.is_property_allowed("test", bare, start)
    assert unwired["ok"] is False
    assert unwired["reason"] == "FACT_HAS_NO_RELATION"


async def test_is_property_allowed_via_relation_ancestor(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    affiliated = await kb.declare_relation_type(
        "test", "AFFILIATED_WITH", "", domain="PERSON", range="COMPANY"
    )
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    await kb.add_is_subclass_of("test", worked, affiliated)
    since = await kb.declare_relation_property("test", "SINCE", "", relation_id=affiliated)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    verdict = await kb.is_property_allowed("test", fact, since)
    assert verdict["ok"] is True
    assert verdict["details"]["defined_on"] == ["AFFILIATED_WITH"]


async def test_conformance_guards_reject_wrong_labels(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    age   = await kb.declare_class_property("test", "AGE", "", class_id=person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    with pytest.raises(KBError):
        await kb.is_fact_allowed("test", fact, worked, ibm)
    with pytest.raises(KBError):
        await kb.is_fact_allowed("test", alice, person, ibm)
    with pytest.raises(KBError):
        await kb.is_property_allowed("test", person, age)
    with pytest.raises(KBError):
        await kb.is_property_allowed("test", alice, alice)


# ------------------------------------------------------------------ #
# search_data fallback (find_data)                                     #
# ------------------------------------------------------------------ #

async def test_search_data_fallback_returns_fact_with_parts(kb):
    person  = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    worked = await kb.declare_relation_type(
        "test", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked)
    hits = await kb.search_data("test", "IBM", mode="keyword")
    by_type = {}
    for h in hits:
        by_type.setdefault(h.type, h)
    assert by_type["ENTITY"].name == "IBM"
    assert by_type["ENTITY"].class_name == "COMPANY"
    fact_hit = by_type["FACT"]
    assert fact_hit.head_name == "Alice"
    assert fact_hit.relation_name == "WORKED_AT"
    assert fact_hit.tail_name == "IBM"


# ------------------------------------------------------------------ #
# attach_entity_class                                                 #
# ------------------------------------------------------------------ #

def _collect_events(kb, database: str) -> list[dict]:
    events: list[dict] = []
    kb.add_change_hook(database, events.append)
    return events


async def test_attach_entity_class_adds_a_second_class(kb):
    person   = await kb.declare_class("test", "PERSON", "A human individual")
    employee = await kb.declare_class("test", "EMPLOYEE", "An employed person")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    await kb.attach_entity_class("test", alice, employee)
    names = {c["properties"]["name"] for c in await kb.get_entity_classes("test", alice)}
    assert names == {"PERSON", "EMPLOYEE"}


async def test_attach_entity_class_deduplicates(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    await kb.attach_entity_class("test", alice, person)
    assert len(await kb.get_entity_classes("test", alice)) == 1


async def test_attach_entity_class_fires_hook_once(kb):
    person   = await kb.declare_class("test", "PERSON", "A human individual")
    employee = await kb.declare_class("test", "EMPLOYEE", "An employed person")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    events = _collect_events(kb, "test")
    await kb.attach_entity_class("test", alice, employee)
    ev = next(e for e in events if e["type"] == "entity_class_attached")
    assert ev["entity_id"] == alice
    assert ev["entity_name"] == "Alice"
    assert ev["class_id"] == employee
    assert ev["class_name"] == "EMPLOYEE"
    await kb.attach_entity_class("test", alice, employee)
    assert len([e for e in events if e["type"] == "entity_class_attached"]) == 1


async def test_attach_entity_class_rejects_wrong_labels(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    with pytest.raises(KBError):
        await kb.attach_entity_class("test", person, person)   # entity_id is a CLASS
    with pytest.raises(KBError):
        await kb.attach_entity_class("test", alice, alice)     # class_id is an ENTITY
