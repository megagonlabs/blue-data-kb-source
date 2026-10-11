from unittest.mock import AsyncMock, patch

import pytest
from blue_data_kb_source import IngestionError, IngestReport, KBError
from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    LITERAL,
    PROPERTY,
    RELATION,
)


def _classes(kb):
    return list(kb.match_nodes("test", label=CLASS))


def _evidence_of(kb, node_id):
    nbs = kb.get_neighbors("test", node_id, edge_type=EXTRACTED_FROM, direction="out")
    return [nb["node"].get("properties", {}) for nb in nbs]


# ------------------------------------------------------------------ #
# assert_class                                                         #
# ------------------------------------------------------------------ #

async def test_assert_class_creates_with_evidence(kb):
    result = await kb.assert_class("test", "PERSON", schema_creation_mode="none")
    assert result.created is True
    assert result.match_type is None
    assert [r.label for r in result.created_nodes] == [CLASS]
    props = _evidence_of(kb, result.node_id)
    assert [(p["text"], p["extraction_type"], p["confidence"]) for p in props] == [
        ("PERSON", "assertion", None)
    ]


async def test_assert_class_reuses_exact(kb):
    class_id = await kb.declare_class("test", "PERSON", "A human individual")
    result = await kb.assert_class("test", "PERSON", schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (class_id, False, "exact")
    assert result.created_nodes == []
    assert _evidence_of(kb, class_id) == []


async def test_assert_class_reuses_normalized(kb):
    class_id = await kb.declare_class("test", "PERSON", "A human individual")
    result = await kb.assert_class("test", "person", schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (class_id, False, "normalized")


async def test_single_search_hit_auto_resolves(kb):
    # The sharp edge of decision 2: one identity-uncertain hit auto-resolves.
    class_id = await kb.declare_class("test", "GADGET", "A maker of widgets")
    result = await kb.assert_class("test", "widgets", schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (class_id, False, "search")


async def test_two_candidates_raise_with_candidates(kb):
    await kb.declare_class("test", "ALPHA_THING", "carries the beta token")
    await kb.declare_class("test", "GAMMA_THING", "also carries the beta token")
    before = len(_classes(kb))
    with pytest.raises(IngestionError) as err:
        await kb.assert_class("test", "beta", schema_creation_mode="none")
    assert len(err.value.candidates) == 2
    assert len(_classes(kb)) == before


# ------------------------------------------------------------------ #
# Judges in phase 1                                                    #
# ------------------------------------------------------------------ #

async def test_llm_mode_fills_description_via_judge(kb):
    judge = AsyncMock(return_value="A judged sentence.")
    with patch("blue_data_kb_source.ingestion.manager.judge_description", judge):
        result = await kb.assert_class("test", "PERSON")
    node = kb.get_node("test", result.node_id)
    assert node["properties"]["description"] == "A judged sentence."


async def test_none_mode_never_calls_a_judge(kb):
    judge = AsyncMock(return_value="A judged sentence.")
    with patch("blue_data_kb_source.ingestion.manager.judge_description", judge):
        result = await kb.assert_class("test", "PERSON", schema_creation_mode="none")
    judge.assert_not_called()
    assert kb.get_node("test", result.node_id)["properties"]["description"] == ""


async def test_judge_crash_becomes_ingestion_error_and_writes_nothing(kb):
    judge = AsyncMock(side_effect=ValueError("model returned garbage"))
    with patch("blue_data_kb_source.ingestion.manager.judge_description", judge):
        with pytest.raises(IngestionError, match="judge_description"):
            await kb.assert_class("test", "PERSON")
    assert _classes(kb) == []


async def test_missing_api_key_error_passes_through(kb, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(KBError, match="OPENAI_API_KEY") as err:
        await kb.assert_class("test", "PERSON")
    # The judge's own KBError must pass through unwrapped: IngestionError is a
    # KBError too, so the type check is what actually pins the passthrough.
    assert not isinstance(err.value, IngestionError)
    assert _classes(kb) == []


async def test_explicit_description_is_used_verbatim(kb):
    judge = AsyncMock(return_value="A judged sentence.")
    with patch("blue_data_kb_source.ingestion.manager.judge_description", judge):
        result = await kb.assert_class("test", "PERSON", description="A human individual")
    judge.assert_not_called()
    assert kb.get_node("test", result.node_id)["properties"]["description"] == "A human individual"


async def test_judge_receives_the_assertion_context(kb):
    judge = AsyncMock(return_value="A judged sentence.")
    with patch("blue_data_kb_source.ingestion.manager.judge_description", judge):
        await kb.assert_relation_type("test", "WORKED_AT", domain="PERSON", range="COMPANY")
    assert judge.call_count > 0
    for call in judge.call_args_list:
        assert "asserting: PERSON WORKED_AT COMPANY" in call.args[1]


# ------------------------------------------------------------------ #
# assert_relation_type                                                 #
# ------------------------------------------------------------------ #

async def test_assert_relation_creates_the_full_cascade(kb):
    result = await kb.assert_relation_type(
        "test", "WORKED_AT", domain="PERSON", range="COMPANY", schema_creation_mode="none"
    )
    assert result.created is True
    assert sorted((r.label, r.name) for r in result.created_nodes) == [
        (CLASS, "COMPANY"), (CLASS, "PERSON"), (RELATION, "WORKED_AT"),
    ]
    dr = await kb.get_relation_domain_range("test", result.node_id)
    assert [n["properties"]["name"] for n in dr["domain"]] == ["PERSON"]
    assert [n["properties"]["name"] for n in dr["range"]] == ["COMPANY"]
    for ref in result.created_nodes:
        assert [p["text"] for p in _evidence_of(kb, ref.node_id)] == ["PERSON WORKED_AT COMPANY"]


async def test_assert_relation_widens_an_existing_relation(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    await kb.declare_class("test", "NONPROFIT", "A nonprofit organization")
    rel_id = await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                            domain="PERSON", range="COMPANY")
    result = await kb.assert_relation_type(
        "test", "WORKED_AT", domain="PERSON", range="NONPROFIT", schema_creation_mode="none"
    )
    assert (result.node_id, result.created) == (rel_id, False)
    assert [w.node_id for w in result.widened_relations] == [rel_id]
    dr = await kb.get_relation_domain_range("test", rel_id)
    assert sorted(n["properties"]["name"] for n in dr["range"]) == ["COMPANY", "NONPROFIT"]


async def test_widening_creates_a_missing_class(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    rel_id = await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                            domain="PERSON", range="COMPANY")
    result = await kb.assert_relation_type(
        "test", "WORKED_AT", domain="GOVERNMENT_AGENCY", range="COMPANY",
        confidence=0.6, schema_creation_mode="none",
    )
    assert [(r.label, r.name) for r in result.created_nodes] == [(CLASS, "GOVERNMENT_AGENCY")]
    assert [w.node_id for w in result.widened_relations] == [rel_id]
    # The run's confidence must reach evidence on the cascade-created class too.
    agency = result.created_nodes[0].node_id
    assert [p["confidence"] for p in _evidence_of(kb, agency)] == [0.6]


async def test_list_valued_domain_and_range_all_wire(kb):
    result = await kb.assert_relation_type(
        "test", "WORKED_AT", domain=["PERSON", "AGENCY"], range=["COMPANY"],
        schema_creation_mode="none",
    )
    dr = await kb.get_relation_domain_range("test", result.node_id)
    assert sorted(n["properties"]["name"] for n in dr["domain"]) == ["AGENCY", "PERSON"]


async def test_is_symmetric_is_stored(kb):
    result = await kb.assert_relation_type(
        "test", "MARRIED_TO", domain="PERSON", range="PERSON",
        is_symmetric=True, schema_creation_mode="none",
    )
    assert kb.get_node("test", result.node_id)["properties"]["is_symmetric"] is True


async def test_ambiguous_operand_leaves_the_graph_untouched(kb):
    await kb.declare_class("test", "ALPHA_ORG", "an org token org")
    await kb.declare_class("test", "BETA_ORG", "another org token org")
    before = len(_classes(kb))
    with pytest.raises(IngestionError):
        await kb.assert_relation_type(
            "test", "WORKED_AT", domain="PERSON", range="org", schema_creation_mode="none"
        )
    assert len(_classes(kb)) == before
    assert list(kb.match_nodes("test", label=RELATION)) == []


# ------------------------------------------------------------------ #
# Property asserts                                                     #
# ------------------------------------------------------------------ #

async def test_assert_class_property_creates_and_wires(kb):
    await kb.declare_class("test", "PERSON", "A human")
    result = await kb.assert_class_property("test", "AGE", "PERSON", schema_creation_mode="none")
    assert result.created is True
    assert [r.label for r in result.created_nodes] == [PROPERTY]
    assert [p["text"] for p in _evidence_of(kb, result.node_id)] == ["PERSON has AGE"]


async def test_assert_class_property_reuses_the_owners_property(kb):
    class_id = await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class_property("test", "HEIGHT", "Height", class_id=class_id)
    prop_id = await kb.declare_class_property("test", "AGE", "Age", class_id=class_id)
    result = await kb.assert_class_property("test", "AGE", "PERSON", schema_creation_mode="none")
    # Two properties on the owner: the reuse must be by name, not first-found.
    assert (result.node_id, result.created, result.match_type) == (prop_id, False, "exact")


async def test_assert_class_property_cascades_a_missing_class(kb):
    result = await kb.assert_class_property("test", "AGE", "PERSON", schema_creation_mode="none")
    assert sorted(r.label for r in result.created_nodes) == [CLASS, PROPERTY]


async def test_assert_relation_property_requires_the_relation(kb):
    with pytest.raises(IngestionError, match="assert_relation_type"):
        await kb.assert_relation_property("test", "SINCE", "WORKED_AT",
                                          schema_creation_mode="none")


async def test_assert_relation_property_reuses_the_relations_property(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    rel_id = await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                            domain="PERSON", range="COMPANY")
    prop_id = await kb.declare_relation_property("test", "SINCE", "Start", relation_id=rel_id)
    result = await kb.assert_relation_property("test", "SINCE", "WORKED_AT",
                                               schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (prop_id, False, "exact")


async def test_assert_relation_property_creates_on_existing_relation(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                   domain="PERSON", range="COMPANY")
    result = await kb.assert_relation_property("test", "SINCE", "WORKED_AT",
                                               schema_creation_mode="none")
    assert result.created is True
    assert [p["text"] for p in _evidence_of(kb, result.node_id)] == ["WORKED_AT has SINCE"]


# ------------------------------------------------------------------ #
# Edge asserts                                                         #
# ------------------------------------------------------------------ #

async def test_assert_subclass_wires_the_edge(kb):
    await kb.declare_class("test", "EMPLOYEE", "An employed person")
    await kb.declare_class("test", "PERSON", "A human")
    result = await kb.assert_subclass("test", "EMPLOYEE", "PERSON", schema_creation_mode="none")
    assert (result.created, result.match_type, result.created_nodes) == (True, None, [])
    ancestors = await kb.get_class_ancestors("test", result.node_id)
    assert [a["properties"]["name"] for a in ancestors] == ["PERSON"]


async def test_assert_subclass_is_edge_idempotent(kb):
    await kb.declare_class("test", "EMPLOYEE", "An employed person")
    await kb.declare_class("test", "PERSON", "A human")
    await kb.assert_subclass("test", "EMPLOYEE", "PERSON", schema_creation_mode="none")
    result = await kb.assert_subclass("test", "EMPLOYEE", "PERSON", schema_creation_mode="none")
    assert result.created is False
    ancestors = await kb.get_class_ancestors("test", result.node_id)
    assert len(ancestors) == 1


async def test_assert_subclass_creates_a_missing_parent(kb):
    await kb.declare_class("test", "EMPLOYEE", "A staff member on a payroll")
    result = await kb.assert_subclass("test", "EMPLOYEE", "PERSON", schema_creation_mode="none")
    assert [(r.label, r.name) for r in result.created_nodes] == [(CLASS, "PERSON")]
    parent_id = result.created_nodes[0].node_id
    assert [p["text"] for p in _evidence_of(kb, parent_id)] == ["EMPLOYEE IS_SUBCLASS_OF PERSON"]


async def test_child_and_parent_collapsing_to_one_node_raises(kb):
    # "person" in EMPLOYEE's description makes the parent resolve to the child via
    # the search tier; a self-subclass edge must be refused, not written.
    await kb.declare_class("test", "EMPLOYEE", "An employed person")
    with pytest.raises(IngestionError, match="same node"):
        await kb.assert_subclass("test", "EMPLOYEE", "PERSON", schema_creation_mode="none")


async def test_assert_subrelation_wires_and_requires_relations(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                   domain="PERSON", range="COMPANY")
    await kb.declare_relation_type("test", "AFFILIATED_WITH", "Affiliation",
                                   domain="PERSON", range="COMPANY")
    result = await kb.assert_subrelation("test", "WORKED_AT", "AFFILIATED_WITH",
                                         schema_creation_mode="none")
    assert result.created is True
    with pytest.raises(IngestionError, match="assert_relation_type"):
        await kb.assert_subrelation("test", "WORKED_AT", "MISSING_REL",
                                    schema_creation_mode="none")


# ------------------------------------------------------------------ #
# Provenance threading                                                 #
# ------------------------------------------------------------------ #

async def test_confidence_and_source_reach_the_evidence(kb):
    src = await kb.add_source("test", source="wikidata", name="Wikidata",
                              original="2026-08-01 dump")
    result = await kb.assert_class("test", "PERSON", source_id=src,
                                   confidence=0.7, schema_creation_mode="none")
    props = _evidence_of(kb, result.node_id)
    assert [(p["confidence"], p["extraction_type"]) for p in props] == [(0.7, "assertion")]
    ext_id = result.extraction_id
    src_nbs = kb.get_neighbors("test", ext_id, edge_type=EXTRACTED_FROM, direction="out")
    assert [nb["node"]["properties"]["name"] for nb in src_nbs] == ["Wikidata"]


async def test_extraction_type_can_mark_external_records(kb):
    result = await kb.assert_class("test", "PERSON", extraction_type="external_record",
                                   schema_creation_mode="none")
    assert [p["extraction_type"] for p in _evidence_of(kb, result.node_id)] == ["external_record"]


# ------------------------------------------------------------------ #
# assert_entity                                                        #
# ------------------------------------------------------------------ #

async def test_assert_entity_untyped_gets_everything_under_none(kb):
    result = await kb.assert_entity("test", "Alice", schema_creation_mode="none")
    assert result.created is True
    assert sorted((r.label, r.name) for r in result.created_nodes) == [
        (CLASS, "EVERYTHING"), (ENTITY, "Alice"),
    ]
    classes = await kb.get_entity_classes("test", result.node_id)
    assert [c["properties"]["name"] for c in classes] == ["EVERYTHING"]
    assert [p["text"] for p in _evidence_of(kb, result.node_id)] == ["Alice"]


async def test_assert_entity_with_class_serializes_is_a(kb):
    result = await kb.assert_entity("test", "Alice", class_name="PERSON",
                                    schema_creation_mode="none")
    assert [p["text"] for p in _evidence_of(kb, result.node_id)] == ["Alice is_a PERSON"]


async def test_assert_entity_reuses_and_attaches_second_class(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    entity_id = await kb.add_entity("test", "Alice", class_id=person)
    result = await kb.assert_entity("test", "Alice", class_name="EMPLOYEE",
                                    schema_creation_mode="none")
    # Decision 20: a certain match with a class mismatch widens membership.
    assert (result.node_id, result.created) == (entity_id, False)
    names = sorted(c["properties"]["name"]
                   for c in await kb.get_entity_classes("test", entity_id))
    assert names == ["EMPLOYEE", "PERSON"]


async def test_classless_existing_entity_gets_a_class_on_resolve(kb):
    entity_id = await kb.add_entity("test", "Alice")
    result = await kb.assert_entity("test", "Alice", schema_creation_mode="none")
    assert (result.node_id, result.created) == (entity_id, False)
    classes = await kb.get_entity_classes("test", entity_id)
    assert [c["properties"]["name"] for c in classes] == ["EVERYTHING"]


async def test_classless_fact_operand_gets_a_membership_class(kb):
    alice = await kb.add_entity("test", "Alice")
    await kb.assert_fact("test", "Alice", "WORKED_AT", "IBM", schema_creation_mode="none")
    classes = await kb.get_entity_classes("test", alice)
    assert [c["properties"]["name"] for c in classes] == ["EVERYTHING"]


async def test_uncertain_entity_hit_with_class_conflict_raises(kb):
    await kb.declare_class("test", "PERSON", "A human")
    company = await kb.declare_class("test", "COMPANY", "A business")
    await kb.add_entity("test", "Alice Smith", class_id=company)
    with pytest.raises(IngestionError, match="conflict"):
        await kb.assert_entity("test", "Alice", class_name="PERSON",
                               schema_creation_mode="none")


async def test_judge_2_reuses_an_existing_class(kb):
    await kb.declare_class("test", "PERSON", "A human")
    judge = AsyncMock(return_value="PERSON")
    with patch("blue_data_kb_source.ingestion.manager.judge_class_name", judge), \
         patch("blue_data_kb_source.ingestion.manager.judge_description",
               AsyncMock(return_value="d")):
        result = await kb.assert_entity("test", "Alice")
    classes = await kb.get_entity_classes("test", result.node_id)
    assert [c["properties"]["name"] for c in classes] == ["PERSON"]
    # The existing class list is load-bearing judge input.
    assert any(c["name"] == "PERSON" for c in judge.call_args.args[2])


async def test_judge_2_can_coin_a_new_class(kb):
    judge = AsyncMock(return_value="ROBOT")
    with patch("blue_data_kb_source.ingestion.manager.judge_class_name", judge), \
         patch("blue_data_kb_source.ingestion.manager.judge_description",
               AsyncMock(return_value="A machine.")):
        result = await kb.assert_entity("test", "R2D2")
    classes = await kb.get_entity_classes("test", result.node_id)
    assert [c["properties"]["name"] for c in classes] == ["ROBOT"]


async def test_judge_2_crash_becomes_ingestion_error_and_writes_nothing(kb):
    judge = AsyncMock(side_effect=ValueError("garbage"))
    with patch("blue_data_kb_source.ingestion.manager.judge_class_name", judge):
        with pytest.raises(IngestionError, match="judge_class_name"):
            await kb.assert_entity("test", "Alice")
    assert list(kb.match_nodes("test", label=ENTITY)) == []



# ------------------------------------------------------------------ #
# assert_fact                                                          #
# ------------------------------------------------------------------ #

async def test_assert_fact_full_cascade_under_none(kb):
    result = await kb.assert_fact("test", "Alice", "WORKED_AT", "IBM",
                                  schema_creation_mode="none")
    assert result.created is True
    labels = sorted(r.label for r in result.created_nodes)
    assert labels == [CLASS, ENTITY, ENTITY, FACT, RELATION]
    fact_evidence = _evidence_of(kb, result.node_id)
    assert [p["text"] for p in fact_evidence] == ["Alice WORKED_AT IBM"]


async def test_assert_fact_dedupes_structurally_and_recomputes(kb):
    first = await kb.assert_fact("test", "Alice", "WORKED_AT", "IBM",
                                 schema_creation_mode="none")
    result = await kb.assert_fact("test", "Alice", "WORKED_AT", "IBM",
                                  confidence=0.9, schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (
        first.node_id, False, "structural"
    )
    assert result.extraction_id is not None
    # Decision 14: the new evidence recomputes the fact's derived confidence.
    meta = await kb.get_metadata("test", first.node_id)
    assert meta["confidence"] == 0.9


async def test_assert_fact_widens_on_domain_mismatch(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    agency = await kb.declare_class("test", "AGENCY", "A public body")
    rel_id = await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                            domain="PERSON", range="COMPANY")
    await kb.add_entity("test", "Bob", class_id=agency)
    company = await kb.declare_class("test", "COMPANY", "A business")
    await kb.add_entity("test", "IBM", class_id=company)
    result = await kb.assert_fact("test", "Bob", "WORKED_AT", "IBM",
                                  schema_creation_mode="none")
    assert [w.node_id for w in result.widened_relations] == [rel_id]
    dr = await kb.get_relation_domain_range("test", rel_id)
    assert sorted(n["properties"]["name"] for n in dr["domain"]) == ["AGENCY", "PERSON"]


# ------------------------------------------------------------------ #
# Property-value asserts                                               #
# ------------------------------------------------------------------ #

async def test_assert_entity_property_creates_value_with_evidence(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    await kb.add_entity("test", "Alice", class_id=person)
    result = await kb.assert_entity_property("test", "Alice", "AGE", "30",
                                             schema_creation_mode="none")
    assert result.created is True
    assert [p["text"] for p in _evidence_of(kb, result.node_id)] == ["Alice AGE 30"]


async def test_assert_entity_property_dedupes_equal_value(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    await kb.add_entity("test", "Alice", class_id=person)
    first = await kb.assert_entity_property("test", "Alice", "AGE", "30",
                                            schema_creation_mode="none")
    result = await kb.assert_entity_property("test", "Alice", "AGE", "30",
                                             schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (
        first.node_id, False, "structural"
    )
    assert len(_evidence_of(kb, first.node_id)) == 2


async def test_dedupe_matches_the_right_property_among_several(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    await kb.add_entity("test", "Alice", class_id=person)
    # NICKNAME first, so its literal is the owner's first value; the AGE dedupe
    # must still find the AGE literal, not the first one.
    await kb.assert_entity_property("test", "Alice", "NICKNAME", "Al",
                                    schema_creation_mode="none")
    first = await kb.assert_entity_property("test", "Alice", "AGE", "30",
                                            schema_creation_mode="none")
    result = await kb.assert_entity_property("test", "Alice", "AGE", "30",
                                             schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (
        first.node_id, False, "structural"
    )


async def test_assert_entity_property_new_value_is_a_new_literal(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    await kb.add_entity("test", "Alice", class_id=person)
    first = await kb.assert_entity_property("test", "Alice", "AGE", "30",
                                            schema_creation_mode="none")
    result = await kb.assert_entity_property("test", "Alice", "AGE", "31",
                                             schema_creation_mode="none")
    assert result.created is True
    assert result.node_id != first.node_id


async def test_inherited_property_is_reused_not_redeclared(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    employee = await kb.declare_class("test", "EMPLOYEE", "A staff member")
    await kb.add_is_subclass_of("test", employee, person)
    prop_id = await kb.declare_class_property("test", "FULL_NAME", "Name", class_id=person)
    await kb.add_entity("test", "Alice", class_id=employee)
    result = await kb.assert_entity_property("test", "Alice", "FULL_NAME", "Alice S",
                                             schema_creation_mode="none")
    props = list(kb.match_nodes("test", label=PROPERTY))
    assert [n["id"] for n in props] == [prop_id]
    assert result.created is True


async def test_judge_1_places_the_property_on_the_chosen_ancestor(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    employee = await kb.declare_class("test", "EMPLOYEE", "A staff member")
    await kb.add_is_subclass_of("test", employee, person)
    await kb.add_entity("test", "Alice", class_id=employee)
    placement = AsyncMock(return_value=[person])
    with patch("blue_data_kb_source.ingestion.manager.judge_property_placement", placement), \
         patch("blue_data_kb_source.ingestion.manager.judge_description",
               AsyncMock(return_value="d")):
        await kb.assert_entity_property("test", "Alice", "FULL_NAME", "Alice S")
    nbs = kb.get_neighbors("test", person, edge_type=DEFINE_PROPERTY, direction="out")
    assert [nb["node"]["properties"]["name"] for nb in nbs] == ["FULL_NAME"]


async def test_placement_on_several_classes_shares_one_property_node(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    employee = await kb.declare_class("test", "EMPLOYEE", "A staff member")
    await kb.add_is_subclass_of("test", employee, person)
    await kb.add_entity("test", "Alice", class_id=employee)
    placement = AsyncMock(return_value=[person, employee])
    with patch("blue_data_kb_source.ingestion.manager.judge_property_placement", placement), \
         patch("blue_data_kb_source.ingestion.manager.judge_description",
               AsyncMock(return_value="d")):
        await kb.assert_entity_property("test", "Alice", "FULL_NAME", "Alice S")
    owners = [
        [nb["node"]["id"] for nb in kb.get_neighbors("test", cid, edge_type=DEFINE_PROPERTY,
                                                     direction="out")]
        for cid in (person, employee)
    ]
    assert owners[0] == owners[1] and len(owners[0]) == 1
    assert len(list(kb.match_nodes("test", label=PROPERTY))) == 1


async def test_assert_fact_property_creates_on_the_relation(kb):
    await kb.assert_fact("test", "Alice", "WORKED_AT", "IBM", schema_creation_mode="none")
    result = await kb.assert_fact_property("test", "Alice", "WORKED_AT", "IBM",
                                           "SINCE", "1998", schema_creation_mode="none")
    assert result.created is True
    assert [p["text"] for p in _evidence_of(kb, result.node_id)] == [
        "Alice WORKED_AT IBM: SINCE 1998"
    ]


async def test_assert_fact_property_cascades_a_missing_fact(kb):
    result = await kb.assert_fact_property("test", "Alice", "WORKED_AT", "IBM",
                                           "SINCE", "1998", schema_creation_mode="none")
    assert result.created is True
    labels = sorted(r.label for r in result.created_nodes)
    assert labels == [CLASS, ENTITY, ENTITY, FACT, LITERAL, PROPERTY, RELATION]


async def test_empty_placement_pick_falls_back_to_direct_classes(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    await kb.add_entity("test", "Alice", class_id=person)
    placement = AsyncMock(return_value=[])
    with patch("blue_data_kb_source.ingestion.manager.judge_property_placement", placement), \
         patch("blue_data_kb_source.ingestion.manager.judge_description",
               AsyncMock(return_value="d")):
        result = await kb.assert_entity_property("test", "Alice", "AGE", "30")
    assert result.created is True
    nbs = kb.get_neighbors("test", person, edge_type=DEFINE_PROPERTY, direction="out")
    assert [nb["node"]["properties"]["name"] for nb in nbs] == ["AGE"]


async def test_assert_fact_property_dedupes_equal_value(kb):
    first = await kb.assert_fact_property("test", "Alice", "WORKED_AT", "IBM",
                                          "SINCE", "1998", schema_creation_mode="none")
    result = await kb.assert_fact_property("test", "Alice", "WORKED_AT", "IBM",
                                           "SINCE", "1998", schema_creation_mode="none")
    assert (result.node_id, result.created, result.match_type) == (
        first.node_id, False, "structural"
    )


async def test_assert_fact_property_new_value_is_a_new_literal(kb):
    first = await kb.assert_fact_property("test", "Alice", "WORKED_AT", "IBM",
                                          "SINCE", "1998", schema_creation_mode="none")
    result = await kb.assert_fact_property("test", "Alice", "WORKED_AT", "IBM",
                                           "SINCE", "2001", schema_creation_mode="none")
    assert result.created is True
    assert result.node_id != first.node_id


# ------------------------------------------------------------------ #
# Bulk ingest                                                          #
# ------------------------------------------------------------------ #

_PAYLOAD = {
    "classes": [{"name": "PERSON", "description": "A human"}],
    "relations": [{"name": "WORKED_AT", "domain": "PERSON", "range": "COMPANY"}],
    "entities": [{"name": "Alice", "class_name": "PERSON"}],
    "facts": [{"head": "Alice", "relation": "WORKED_AT", "tail": "IBM"}],
    "entity_properties": [{"entity": "Alice", "property": "AGE", "value": "30"}],
}


async def test_ingest_mixed_payload_counts(kb):
    report = await kb.ingest("test", _PAYLOAD, schema_creation_mode="none")
    assert isinstance(report, IngestReport)
    assert (report.written, report.deduped, report.skipped, report.failures) == (5, 0, 0, [])
    # classes: PERSON + COMPANY (relation cascade) + EVERYTHING (IBM untyped)
    assert report.created[CLASS] == 3
    assert report.created[ENTITY] == 2
    assert report.created[FACT] == 1


async def test_reingest_converges(kb):
    await kb.ingest("test", _PAYLOAD, schema_creation_mode="none")
    report = await kb.ingest("test", _PAYLOAD, schema_creation_mode="none")
    # class + relation + entity reuse without evidence -> skipped;
    # fact and property value dedupe structurally -> deduped.
    assert (report.written, report.deduped, report.skipped) == (0, 2, 3)
    assert report.created == {}


async def test_row_failure_is_collected_and_later_rows_run(kb):
    await kb.declare_class("test", "ALPHA_THING", "carries the beta token")
    await kb.declare_class("test", "GAMMA_THING", "also carries the beta token")
    payload = {
        "classes": [{"name": "beta"}, {"name": "SOLID"}],
    }
    report = await kb.ingest("test", payload, schema_creation_mode="none")
    assert report.written == 1
    assert len(report.failures) == 1
    failure = report.failures[0]
    assert (failure.arm, failure.index) == ("classes", 0)
    assert len(failure.candidates) == 2
    names = [n["properties"]["name"] for n in kb.match_nodes("test", label=CLASS)]
    assert "SOLID" in names


async def test_row_confidence_overrides_the_run_default(kb):
    payload = {
        "facts": [
            {"head": "Alice", "relation": "WORKED_AT", "tail": "IBM", "confidence": 0.9},
        ],
    }
    report = await kb.ingest("test", payload, confidence=0.4, schema_creation_mode="none")
    assert report.written == 1
    fact_id = next(n["id"] for n in kb.match_nodes("test", label=FACT))
    meta = await kb.get_metadata("test", fact_id)
    assert meta["confidence"] == 0.9


async def test_run_confidence_applies_when_row_has_none(kb):
    payload = {"facts": [{"head": "Alice", "relation": "WORKED_AT", "tail": "IBM"}]}
    await kb.ingest("test", payload, confidence=0.4, schema_creation_mode="none")
    fact_id = next(n["id"] for n in kb.match_nodes("test", label=FACT))
    meta = await kb.get_metadata("test", fact_id)
    assert meta["confidence"] == 0.4


async def test_unknown_arm_raises_before_any_write(kb):
    payload = {"classes": [{"name": "PERSON"}], "factz": []}
    with pytest.raises(IngestionError, match="factz"):
        await kb.ingest("test", payload, schema_creation_mode="none")
    assert list(kb.match_nodes("test", label=CLASS)) == []


async def test_widened_relations_reach_the_report(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    agency = await kb.declare_class("test", "AGENCY", "A public body")
    await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                   domain="PERSON", range="COMPANY")
    await kb.add_entity("test", "Bob", class_id=agency)
    company = await kb.declare_class("test", "COMPANY", "A business")
    await kb.add_entity("test", "IBM", class_id=company)
    payload = {"facts": [{"head": "Bob", "relation": "WORKED_AT", "tail": "IBM"}]}
    report = await kb.ingest("test", payload, schema_creation_mode="none")
    assert report.widened == ["WORKED_AT"]


async def test_row_extraction_type_overrides_the_run_default(kb):
    payload = {"classes": [
        {"name": "PERSON"},
        {"name": "COMPANY", "extraction_type": "assertion"},
    ]}
    report = await kb.ingest("test", payload, extraction_type="external_record",
                             schema_creation_mode="none")
    assert report.written == 2
    by_name = {n["properties"]["name"]: n["id"] for n in kb.match_nodes("test", label=CLASS)}
    person_types = [p["extraction_type"] for p in _evidence_of(kb, by_name["PERSON"])]
    assert person_types == ["external_record"]
    assert [p["extraction_type"] for p in _evidence_of(kb, by_name["COMPANY"])] == ["assertion"]


async def test_malformed_row_is_collected_and_later_rows_run(kb):
    payload = {"classes": [
        {"name": "ALPHA"},
        {"name": "BETA", "bogus": 1},
        {"name": "GAMMA"},
    ]}
    report = await kb.ingest("test", payload, schema_creation_mode="none")
    assert report.written == 2
    assert [(f.arm, f.index) for f in report.failures] == [("classes", 1)]
    assert "bogus" in report.failures[0].reason
    names = {n["properties"]["name"] for n in kb.match_nodes("test", label=CLASS)}
    assert names == {"ALPHA", "GAMMA"}


async def test_arms_run_in_payload_order_not_a_fixed_order(kb):
    # facts before relations: the fact row creates WORKED_AT itself, so the later
    # relations row finds it and is skipped (widening aside). A fixed schema-first
    # order would write both rows — the report is the order-sensitive signal,
    # since widening makes the final graph converge either way.
    payload = {
        "facts": [{"head": "Alice", "relation": "WORKED_AT", "tail": "IBM"}],
        "relations": [{"name": "WORKED_AT", "domain": "PERSON", "range": "COMPANY"}],
    }
    report = await kb.ingest("test", payload, schema_creation_mode="none")
    assert (report.written, report.skipped) == (1, 1)


async def test_run_source_reaches_every_rows_evidence(kb):
    src = await kb.add_source("test", source="wikidata", name="Wikidata")
    report = await kb.ingest("test", {"classes": [{"name": "PERSON"}]},
                             source_id=src, schema_creation_mode="none")
    assert report.written == 1
    class_id = next(n["id"] for n in kb.match_nodes("test", label=CLASS))
    ext_id = _evidence_of(kb, class_id) and kb.get_neighbors(
        "test", class_id, edge_type=EXTRACTED_FROM, direction="out")[0]["node"]["id"]
    src_nbs = kb.get_neighbors("test", ext_id, edge_type=EXTRACTED_FROM, direction="out")
    assert [nb["node"]["properties"]["name"] for nb in src_nbs] == ["Wikidata"]


async def test_ingest_passes_mode_and_model_to_the_judges(kb):
    judge = AsyncMock(return_value="A judged sentence.")
    with patch("blue_data_kb_source.ingestion.manager.judge_description", judge):
        await kb.ingest("test", {"classes": [{"name": "PERSON"}]}, model="gpt-4o-mini")
    judge.assert_called_once()
    assert judge.call_args.kwargs["model"] == "gpt-4o-mini"


async def test_empty_payload_reports_zeros(kb):
    report = await kb.ingest("test", {}, schema_creation_mode="none")
    assert (report.written, report.deduped, report.skipped, report.failures,
            report.created, report.widened) == (0, 0, 0, [], {}, [])


async def test_thirty_row_bulk_counts_and_converges(kb):
    # Same-width names cannot substring-match each other, so resolution stays
    # clean at any count; this pins the report arithmetic beyond hand-counted
    # fixtures and doubles as a performance canary for the O(rows x graph) scans.
    payload = {
        "classes": [{"name": f"TYPE_{i:02d}"} for i in range(10)],
        "entities": [{"name": f"ENT_{i:02d}", "class_name": f"TYPE_{i:02d}"}
                     for i in range(10)],
        "entity_properties": [{"entity": f"ENT_{i:02d}", "property": f"AGE_{i:02d}",
                               "value": str(20 + i)} for i in range(10)],
    }
    report = await kb.ingest("test", payload, schema_creation_mode="none")
    assert (report.written, report.deduped, report.skipped, report.failures) == (30, 0, 0, [])
    assert report.created == {CLASS: 10, ENTITY: 10, PROPERTY: 10, LITERAL: 10}
    again = await kb.ingest("test", payload, schema_creation_mode="none")
    assert (again.written, again.deduped, again.skipped) == (0, 10, 20)
    assert again.created == {}


async def test_similar_names_at_scale_fail_into_the_report_not_the_graph(kb):
    # Bulk ingest grows the graph it resolves against: once many OUTPOST-A** rows
    # exist, a bare "OUTPOST" row search-matches several of them and must fail
    # into the report with candidates, never corrupt the graph or stop the run.
    rows = ([{"name": f"OUTPOST A{i:02d}"} for i in range(1, 21)]
            + [{"name": "OUTPOST"} for _ in range(4)])
    report = await kb.ingest("test", {"entities": rows}, schema_creation_mode="none")
    assert report.written + len(report.failures) == len(rows)
    assert report.written == 20
    assert len(report.failures) == 4
    for failure in report.failures:
        assert len(failure.candidates) >= 2
    names = {n["properties"]["name"] for n in kb.match_nodes("test", label=ENTITY)}
    assert "OUTPOST" not in names
    assert len(names) == 20


# ------------------------------------------------------------------ #
# Every judge runs before the first write (#126)                       #
# ------------------------------------------------------------------ #

_M = "blue_data_kb_source.ingestion.manager"


def _graph(kb):
    return {label: sorted(n["properties"].get("name", "") for n in kb.match_nodes("test", label=label))
            for label in (CLASS, ENTITY, RELATION, PROPERTY, FACT, LITERAL)}


def _fails_for(kind):
    """judge_description that answers for everything except one kind of element."""
    async def judge(name, context, model="gpt-4o"):
        if f"a {kind} " in context:
            raise RuntimeError("judge unavailable")
        return f"description of {name}"
    return judge


async def _class_for(entity_name, text, listing, model="gpt-4o"):
    return "PERSON" if entity_name in ("Alice", "Bob") else "COMPANY"


async def test_assert_fact_writes_nothing_when_the_relation_judge_fails(kb):
    with patch(f"{_M}.judge_class_name", _class_for), patch(f"{_M}.judge_description", _fails_for("relation")):
        with pytest.raises(IngestionError, match="relation 'WORKED_AT'"):
            await kb.assert_fact("test", "Alice", "WORKED_AT", "IBM")
    assert _graph(kb) == {CLASS: [], ENTITY: [], RELATION: [], PROPERTY: [], FACT: [], LITERAL: []}


async def test_assert_fact_writes_nothing_when_a_class_judge_fails(kb):
    async def class_for(entity_name, text, listing, model="gpt-4o"):
        if entity_name == "IBM":
            raise RuntimeError("judge unavailable")
        return "PERSON"
    with patch(f"{_M}.judge_class_name", class_for), patch(f"{_M}.judge_description", _fails_for("nothing")):
        with pytest.raises(IngestionError, match="judge_class_name"):
            await kb.assert_fact("test", "Alice", "WORKED_AT", "IBM")
    assert _graph(kb)[CLASS] == [] and _graph(kb)[ENTITY] == []


async def test_assert_fact_creates_a_class_two_new_entities_share_once(kb):
    with patch(f"{_M}.judge_class_name", _class_for), patch(f"{_M}.judge_description", _fails_for("nothing")):
        await kb.assert_fact("test", "Alice", "KNOWS", "Bob")
    assert _graph(kb)[CLASS] == ["PERSON"]
    assert _graph(kb)[ENTITY] == ["Alice", "Bob"]


async def test_assert_entity_property_writes_nothing_when_the_property_judge_fails(kb):
    with patch(f"{_M}.judge_class_name", _class_for), patch(f"{_M}.judge_description", _fails_for("property")):
        with pytest.raises(IngestionError, match="property 'AGE'"):
            await kb.assert_entity_property("test", "Alice", "AGE", 30)
    assert _graph(kb)[CLASS] == [] and _graph(kb)[ENTITY] == []


async def test_assert_entity_property_writes_nothing_when_placement_fails(kb):
    await kb.assert_entity("test", "Alice", "PERSON", schema_creation_mode="none")
    await kb.assert_class("test", "EMPLOYEE", schema_creation_mode="none")
    await kb.assert_subclass("test", "PERSON", "EMPLOYEE", schema_creation_mode="none")
    before = _graph(kb)
    placement = AsyncMock(side_effect=RuntimeError("judge unavailable"))
    with patch(f"{_M}.judge_property_placement", placement), \
         patch(f"{_M}.judge_description", _fails_for("nothing")):
        with pytest.raises(IngestionError, match="judge_property_placement"):
            await kb.assert_entity_property("test", "Alice", "AGE", 30)
    assert _graph(kb) == before


async def test_assert_entity_property_on_a_new_entity_places_the_property_on_its_new_class(kb):
    with patch(f"{_M}.judge_class_name", _class_for), patch(f"{_M}.judge_description", _fails_for("nothing")):
        result = await kb.assert_entity_property("test", "Alice", "AGE", 30)
    person = next(c for c in _classes(kb) if c["properties"]["name"] == "PERSON")
    owned = [nb["node"]["properties"]["name"]
             for nb in kb.get_neighbors("test", person["id"], edge_type=DEFINE_PROPERTY, direction="out")]
    assert owned == ["AGE"] and result.created


async def test_assert_fact_property_writes_nothing_when_the_property_judge_fails(kb):
    with patch(f"{_M}.judge_class_name", _class_for), patch(f"{_M}.judge_description", _fails_for("property")):
        with pytest.raises(IngestionError, match="property 'START_DATE'"):
            await kb.assert_fact_property("test", "Alice", "WORKED_AT", "IBM", "START_DATE", "1998")
    assert _graph(kb) == {CLASS: [], ENTITY: [], RELATION: [], PROPERTY: [], FACT: [], LITERAL: []}
