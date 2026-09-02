from unittest.mock import AsyncMock, patch

import pytest
from blue_data_kb_source import IngestionError, KBError
from blue_data_kb_source._constants import CLASS, EXTRACTED_FROM, PROPERTY, RELATION


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
