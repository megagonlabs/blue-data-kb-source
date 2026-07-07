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
    rel_id   = await kb.declare_relation_type("test", "WORKED_AT", "")
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
    rel_id   = await kb.declare_relation_type("test", "WORKED_AT", "")
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

async def test_add_fact_property_value_creates_literal(kb):
    rel_id  = await kb.declare_relation_type("test", "WORKED_AT", "")
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    lit_id  = await kb.add_fact_property_value("test", "1998-06-01",
                                                fact_id=fact_id,
                                                property_id=prop_id)
    node = kb.get_node("test", lit_id)
    assert node["label"] == "LITERAL"
    assert node["properties"]["value"] == "1998-06-01"


async def test_add_fact_property_value_wires_has_property(kb):
    rel_id  = await kb.declare_relation_type("test", "WORKED_AT", "")
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", relation_id=rel_id)
    lit_id  = await kb.add_fact_property_value("test", "1998-06-01",
                                                fact_id=fact_id,
                                                property_id=prop_id)
    has_prop = kb.get_neighbors("test", fact_id, edge_type="HAS_PROPERTY", direction="out")
    assert any(nb["node"]["id"] == lit_id for nb in has_prop)


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


# ------------------------------------------------------------------ #
# answer                                                               #
# ------------------------------------------------------------------ #

async def test_answer_finds_entity_by_name(populated_kb):
    results = await populated_kb.answer("test", "Where did Alice work?")
    entities = [r.entity for r in results if r.entity]
    assert any("Alice" in e for e in entities)


async def test_answer_returns_provenance(populated_kb):
    results = await populated_kb.answer("test", "Alice")
    alice_result = next((r for r in results if r.entity and "Alice" in r.entity), None)
    assert alice_result is not None
    assert alice_result.provenance is not None


async def test_answer_returns_empty_for_unknown_term(populated_kb):
    results = await populated_kb.answer("test", "zzz_unknown_zzz")
    assert results == []


# ------------------------------------------------------------------ #
# validate                                                             #
# ------------------------------------------------------------------ #

async def test_validate_returns_supporting_facts(populated_kb):
    result = await populated_kb.validate("test", "Alice worked at IBM")
    assert len(result.supporting) > 0


async def test_validate_supporting_fact_has_expected_fields(populated_kb):
    result = await populated_kb.validate("test", "Alice worked at IBM")
    fact = result.supporting[0]
    assert "fact" in fact
    assert "head" in fact
    assert "tail" in fact
    assert "relation" in fact


async def test_validate_counter_empty_by_default(populated_kb):
    result = await populated_kb.validate("test", "Alice worked at IBM")
    assert result.counter == []


# ------------------------------------------------------------------ #
# enrich                                                               #
# ------------------------------------------------------------------ #

async def test_enrich_finds_connected_facts(populated_kb):
    results = await populated_kb.enrich("test", "Alice")
    assert len(results) > 0
    assert all(r.distance >= 1 for r in results)
    assert all(r.distance <= 2 for r in results)


async def test_enrich_result_includes_relation_name(populated_kb):
    results = await populated_kb.enrich("test", "Alice")
    assert any(r.relation == "WORKED_AT" for r in results)


async def test_enrich_no_results_for_unknown_entity(populated_kb):
    results = await populated_kb.enrich("test", "zzz_not_a_person_zzz")
    assert results == []
