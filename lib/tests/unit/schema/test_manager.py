import pytest

from blue_data_kb_source.types import SchemaError


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _collect_events(kb, database: str) -> list[dict]:
    events: list[dict] = []
    kb.add_change_hook(database, events.append)
    return events


# ------------------------------------------------------------------ #
# declare_class                                                        #
# ------------------------------------------------------------------ #

async def test_declare_class_creates_node(kb):
    class_id = await kb.declare_class("test", "PERSON", "A human individual")
    node = kb.get_node("test", class_id)
    assert node is not None
    assert node["label"] == "CLASS"
    assert node["properties"]["name"] == "PERSON"
    assert node["properties"]["description"] == "A human individual"


async def test_declare_class_is_idempotent(kb):
    id1 = await kb.declare_class("test", "PERSON", "A human individual")
    id2 = await kb.declare_class("test", "PERSON", "Different description")
    assert id1 == id2


async def test_declare_class_with_explicit_id(kb):
    class_id = await kb.declare_class("test", "PERSON", "A human individual", class_id="my-person")
    assert class_id == "my-person"


# ------------------------------------------------------------------ #
# declare_relation_type                                                #
# ------------------------------------------------------------------ #

async def test_declare_relation_type_creates_node(kb):
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    rel_id = await kb.declare_relation_type(
        "test", "WORKS_AT", "Employment relation", domain="PERSON", range="COMPANY"
    )
    node = kb.get_node("test", rel_id)
    assert node is not None
    assert node["label"] == "RELATION"
    assert node["properties"]["name"] == "WORKS_AT"


async def test_declare_relation_type_wires_domain_and_range(kb):
    person_id  = await kb.declare_class("test", "PERSON",  "")
    company_id = await kb.declare_class("test", "COMPANY", "")
    rel_id = await kb.declare_relation_type(
        "test", "WORKS_AT", "", domain="PERSON", range="COMPANY"
    )
    domain_nbs = kb.get_neighbors("test", rel_id, edge_type="DOMAIN", direction="out")
    range_nbs  = kb.get_neighbors("test", rel_id, edge_type="RANGE",  direction="out")
    assert any(nb["node"]["id"] == person_id  for nb in domain_nbs)
    assert any(nb["node"]["id"] == company_id for nb in range_nbs)


async def test_declare_relation_type_multiple_domains(kb):
    p1 = await kb.declare_class("test", "PERSON",   "")
    p2 = await kb.declare_class("test", "EMPLOYEE", "")
    await kb.declare_class("test", "COMPANY", "")
    rel_id = await kb.declare_relation_type(
        "test", "WORKS_AT", "", domain=["PERSON", "EMPLOYEE"], range="COMPANY"
    )
    domain_nbs = kb.get_neighbors("test", rel_id, edge_type="DOMAIN", direction="out")
    domain_ids = {nb["node"]["id"] for nb in domain_nbs}
    assert p1 in domain_ids
    assert p2 in domain_ids


async def test_declare_relation_type_raises_on_missing_domain(kb):
    await kb.declare_class("test", "COMPANY", "")
    with pytest.raises(SchemaError, match="domain class"):
        await kb.declare_relation_type("test", "WORKS_AT", "", domain="NONEXISTENT", range="COMPANY")


async def test_declare_relation_type_raises_on_missing_range(kb):
    await kb.declare_class("test", "PERSON", "")
    with pytest.raises(SchemaError, match="range class"):
        await kb.declare_relation_type("test", "WORKS_AT", "", domain="PERSON", range="NONEXISTENT")


async def test_declare_relation_type_raises_on_missing_domain_arg(kb):
    await kb.declare_class("test", "COMPANY", "")
    with pytest.raises(SchemaError, match="domain is required"):
        await kb.declare_relation_type("test", "WORKS_AT", "", domain=None, range="COMPANY")


async def test_declare_relation_type_raises_on_missing_range_arg(kb):
    await kb.declare_class("test", "PERSON", "")
    with pytest.raises(SchemaError, match="range is required"):
        await kb.declare_relation_type("test", "WORKS_AT", "", domain="PERSON", range=None)


async def test_declare_relation_type_is_idempotent(kb):
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    id1 = await kb.declare_relation_type("test", "WORKS_AT", "Employment", domain="PERSON", range="COMPANY")
    id2 = await kb.declare_relation_type("test", "WORKS_AT", "Changed description", domain="PERSON", range="COMPANY")
    assert id1 == id2


async def test_declare_relation_type_symmetric_flag(kb):
    await kb.declare_class("test", "PERSON", "")
    rel_id = await kb.declare_relation_type(
        "test", "KNOWS", "Knows relation", is_symmetric=True, domain="PERSON", range="PERSON"
    )
    node = kb.get_node("test", rel_id)
    assert node["properties"]["is_symmetric"] is True


# ------------------------------------------------------------------ #
# declare_class_property                                               #
# ------------------------------------------------------------------ #

async def test_declare_class_property_creates_node(kb):
    class_id = await kb.declare_class("test", "PERSON", "")
    prop_id = await kb.declare_class_property("test", "NAME", "Person's name", class_id=class_id)
    node = kb.get_node("test", prop_id)
    assert node is not None
    assert node["label"] == "PROPERTY"
    assert node["properties"]["name"] == "NAME"


async def test_declare_class_property_wires_define_property(kb):
    class_id = await kb.declare_class("test", "PERSON", "")
    prop_id = await kb.declare_class_property("test", "NAME", "Person's name", class_id=class_id)
    nbs = kb.get_neighbors("test", class_id, edge_type="DEFINE_PROPERTY", direction="out")
    assert any(nb["node"]["id"] == prop_id for nb in nbs)


async def test_declare_class_property_is_idempotent(kb):
    class_id = await kb.declare_class("test", "PERSON", "")
    id1 = await kb.declare_class_property("test", "NAME", "name", class_id=class_id)
    id2 = await kb.declare_class_property("test", "NAME", "name", class_id=class_id)
    assert id1 == id2


async def test_declare_class_property_without_class_id(kb):
    prop_id = await kb.declare_class_property("test", "NAME", "A name property")
    node = kb.get_node("test", prop_id)
    assert node["label"] == "PROPERTY"


# ------------------------------------------------------------------ #
# declare_relation_property                                            #
# ------------------------------------------------------------------ #

async def _declare_works_at(kb):
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    return await kb.declare_relation_type("test", "WORKS_AT", "", domain="PERSON", range="COMPANY")


async def test_declare_relation_property_creates_node(kb):
    rel_id = await _declare_works_at(kb)
    prop_id = await kb.declare_relation_property("test", "START_DATE", "Start date", relation_id=rel_id)
    node = kb.get_node("test", prop_id)
    assert node is not None
    assert node["label"] == "PROPERTY"
    assert node["properties"]["name"] == "START_DATE"


async def test_declare_relation_property_wires_define_property(kb):
    rel_id = await _declare_works_at(kb)
    prop_id = await kb.declare_relation_property("test", "START_DATE", "Start date", relation_id=rel_id)
    nbs = kb.get_neighbors("test", rel_id, edge_type="DEFINE_PROPERTY", direction="out")
    assert any(nb["node"]["id"] == prop_id for nb in nbs)


async def test_declare_relation_property_is_idempotent(kb):
    rel_id = await _declare_works_at(kb)
    id1 = await kb.declare_relation_property("test", "START_DATE", "date", relation_id=rel_id)
    id2 = await kb.declare_relation_property("test", "START_DATE", "date", relation_id=rel_id)
    assert id1 == id2


# ------------------------------------------------------------------ #
# add_is_subclass_of                                                   #
# ------------------------------------------------------------------ #

async def test_add_is_subclass_of_wires_edge(kb):
    parent_id = await kb.declare_class("test", "PERSON",   "")
    child_id  = await kb.declare_class("test", "EMPLOYEE", "")
    await kb.add_is_subclass_of("test", child_id, parent_id)
    nbs = kb.get_neighbors("test", child_id, edge_type="IS_SUBCLASS_OF", direction="out")
    assert any(nb["node"]["id"] == parent_id for nb in nbs)


# ------------------------------------------------------------------ #
# fetch_database_schema                                                #
# ------------------------------------------------------------------ #

async def test_fetch_database_schema_includes_classes(populated_kb):
    schema = await populated_kb.fetch_database_schema("test")
    names = [e["name"] for e in schema.entities.values()]
    assert "PERSON"   in names
    assert "COMPANY"  in names
    assert "EMPLOYEE" in names


async def test_fetch_database_schema_includes_relations(populated_kb):
    schema = await populated_kb.fetch_database_schema("test")
    rel_names = [r["name"] for r in schema.relations.values()]
    assert "WORKED_AT" in rel_names


async def test_fetch_database_schema_empty_database(kb):
    schema = await kb.fetch_database_schema("test")
    assert len(list(schema.entities.values())) == 0
    assert len(list(schema.relations.values())) == 0


# ------------------------------------------------------------------ #
# import_database_schema (round-trip)                                  #
# ------------------------------------------------------------------ #

async def test_import_database_schema_roundtrip(kb):
    await kb.declare_class("test", "PERSON",  "A human individual")
    await kb.declare_class("test", "COMPANY", "A business organization")
    await kb.declare_relation_type("test", "WORKS_AT", "Employment",
                                   domain="PERSON", range="COMPANY")

    schema_json = (await kb.fetch_database_schema("test")).to_json()
    kb.create_database("copy")
    await kb.import_database_schema("copy", schema_json)

    copy_schema = await kb.fetch_database_schema("copy")
    copy_names = [e["name"] for e in copy_schema.entities.values()]
    assert "PERSON"  in copy_names
    assert "COMPANY" in copy_names
    copy_rel_names = [r["name"] for r in copy_schema.relations.values()]
    assert "WORKS_AT" in copy_rel_names


async def test_import_database_schema_returns_none(kb):
    schema_json = (await kb.fetch_database_schema("test")).to_json()
    kb.create_database("copy")
    result = await kb.import_database_schema("copy", schema_json)
    assert result is None


# ------------------------------------------------------------------ #
# search_schema                                                        #
# ------------------------------------------------------------------ #

async def test_search_schema_matches_by_name(populated_kb):
    results = await populated_kb.search_schema("test", "PERSON")
    assert any(r.name == "PERSON" for r in results)


async def test_search_schema_matches_by_description(populated_kb):
    results = await populated_kb.search_schema("test", "Employment")
    assert any(r.name == "WORKED_AT" for r in results)


async def test_search_schema_is_case_insensitive(populated_kb):
    results = await populated_kb.search_schema("test", "person")
    assert any(r.name == "PERSON" for r in results)


async def test_search_schema_returns_type(populated_kb):
    results = await populated_kb.search_schema("test", "PERSON")
    hit = next(r for r in results if r.name == "PERSON")
    assert hit.type == "CLASS"


async def test_search_schema_no_false_positives(populated_kb):
    results = await populated_kb.search_schema("test", "xyz_not_found_xyz")
    assert results == []


async def test_search_schema_matches_property(kb):
    class_id = await kb.declare_class("test", "PERSON", "")
    await kb.declare_class_property("test", "BIRTHDATE", "Date of birth", class_id=class_id)
    results = await kb.search_schema("test", "birth")
    assert any(r.name == "BIRTHDATE" for r in results)


# ------------------------------------------------------------------ #
# consolidate_schema                                                   #
# ------------------------------------------------------------------ #

async def test_consolidate_schema_merge_rewires_instances(kb):
    worker_id   = await kb.declare_class("test", "WORKER",   "A worker")
    employee_id = await kb.declare_class("test", "EMPLOYEE", "An employee")
    alice = await kb.add_entity("test", "Alice", class_id=worker_id)
    bob   = await kb.add_entity("test", "Bob",   class_id=employee_id)

    await kb.consolidate_schema("test", "merge", ["WORKER", "EMPLOYEE"], "EMPLOYEE")

    target = kb.match_nodes("test", label="CLASS", properties={"name": "EMPLOYEE"})
    assert target
    target_id = target[0]["id"]

    # Alice (was under WORKER) should now have IS_INSTANCE_OF -> EMPLOYEE
    alice_nbs = kb.get_neighbors("test", alice, edge_type="IS_INSTANCE_OF", direction="out")
    assert any(nb["node"]["id"] == target_id for nb in alice_nbs)

    # Bob (already under EMPLOYEE) still has the edge
    bob_nbs = kb.get_neighbors("test", bob, edge_type="IS_INSTANCE_OF", direction="out")
    assert any(nb["node"]["id"] == target_id for nb in bob_nbs)


async def test_consolidate_schema_unsupported_op_raises(kb):
    await kb.declare_class("test", "PERSON", "")
    with pytest.raises(SchemaError):
        await kb.consolidate_schema("test", "split", ["PERSON"], "PERSON")


async def test_consolidate_schema_missing_source_raises(kb):
    with pytest.raises(SchemaError):
        await kb.consolidate_schema("test", "merge", ["DOES_NOT_EXIST"], "TARGET")


# ------------------------------------------------------------------ #
# Schema change hooks                                                  #
# ------------------------------------------------------------------ #

async def test_declare_class_fires_hook(kb):
    events = _collect_events(kb, "test")
    await kb.declare_class("test", "PERSON", "A human individual")
    ev = next(e for e in events if e["type"] == "class_declared")
    assert ev["name"] == "PERSON"
    assert ev["description"] == "A human individual"
    assert ev["database"] == "test"
    assert "id" in ev


async def test_declare_class_idempotent_does_not_fire_hook(kb):
    events = _collect_events(kb, "test")
    await kb.declare_class("test", "PERSON", "A human individual")
    count_before = len(events)
    await kb.declare_class("test", "PERSON", "Different description")
    assert len(events) == count_before


async def test_declare_relation_fires_hook(kb):
    events = _collect_events(kb, "test")
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    await kb.declare_relation_type("test", "WORKED_AT", "Employment", domain="PERSON", range="COMPANY")
    ev = next(e for e in events if e["type"] == "relation_declared")
    assert ev["name"] == "WORKED_AT"
    assert ev["domain"] == ["PERSON"]
    assert ev["range"] == ["COMPANY"]
    assert ev["database"] == "test"


async def test_declare_relation_idempotent_does_not_fire_hook(kb):
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    events = _collect_events(kb, "test")
    await kb.declare_relation_type("test", "WORKED_AT", "Employment", domain="PERSON", range="COMPANY")
    count_before = len(events)
    await kb.declare_relation_type("test", "WORKED_AT", "Employment again", domain="PERSON", range="COMPANY")
    assert len(events) == count_before


async def test_declare_class_property_fires_hook(kb):
    events = _collect_events(kb, "test")
    class_id = await kb.declare_class("test", "PERSON", "")
    await kb.declare_class_property("test", "AGE", "Age of person", class_id=class_id)
    ev = next(e for e in events if e["type"] == "class_property_declared")
    assert ev["name"] == "AGE"
    assert ev["class_id"] == class_id
    assert ev["class_name"] == "PERSON"
    assert ev["database"] == "test"


async def test_declare_relation_property_fires_hook(kb):
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    events = _collect_events(kb, "test")
    rel_id = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    await kb.declare_relation_property("test", "START_DATE", "Start date", relation_id=rel_id)
    ev = next(e for e in events if e["type"] == "relation_property_declared")
    assert ev["name"] == "START_DATE"
    assert ev["relation_id"] == rel_id
    assert ev["relation_name"] == "WORKED_AT"
    assert ev["database"] == "test"


async def test_add_is_subclass_of_class_fires_hook(kb):
    events = _collect_events(kb, "test")
    person_id   = await kb.declare_class("test", "PERSON", "")
    employee_id = await kb.declare_class("test", "EMPLOYEE", "")
    await kb.add_is_subclass_of("test", employee_id, person_id)
    ev = next(e for e in events if e["type"] == "class_subclass")
    assert ev["child_id"]   == employee_id
    assert ev["child_name"] == "EMPLOYEE"
    assert ev["parent_id"]  == person_id
    assert ev["parent_name"] == "PERSON"
    assert ev["database"] == "test"


async def test_add_is_subclass_of_relation_fires_hook(kb):
    await kb.declare_class("test", "PERSON", "")
    await kb.declare_class("test", "COMPANY", "")
    events = _collect_events(kb, "test")
    parent_id = await kb.declare_relation_type("test", "WORKS_FOR", "", domain="PERSON", range="COMPANY")
    child_id  = await kb.declare_relation_type("test", "CONTRACTED_BY", "", domain="PERSON", range="COMPANY")
    await kb.add_is_subclass_of("test", child_id, parent_id)
    ev = next(e for e in events if e["type"] == "relation_subclass")
    assert ev["child_id"]   == child_id
    assert ev["child_name"] == "CONTRACTED_BY"
    assert ev["parent_id"]  == parent_id
    assert ev["parent_name"] == "WORKS_FOR"


async def test_schema_hooks_scoped_to_database(kb):
    kb.create_database("other")
    events = _collect_events(kb, "test")
    await kb.declare_class("other", "THING", "")
    assert not any(e["type"] == "class_declared" for e in events)
