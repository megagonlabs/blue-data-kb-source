import pytest

# ------------------------------------------------------------------ #
# entity_added events                                                  #
# ------------------------------------------------------------------ #

async def test_hook_fires_on_entity_add(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    await kb.add_entity("test", "Alice")
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "entity_added"
    assert ev["name"] == "Alice"
    assert ev["database"] == "test"


async def test_hook_entity_event_has_id(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    entity_id = await kb.add_entity("test", "Alice")
    assert events[0]["id"] == entity_id


async def test_hook_entity_event_has_class_name(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    class_id = await kb.declare_class("test", "PERSON", "")
    await kb.add_entity("test", "Alice", class_id=class_id)
    entity_events = [e for e in events if e["type"] == "entity_added"]
    assert entity_events[0]["class_name"] == "PERSON"
    assert entity_events[0]["class_id"]   == class_id


async def test_hook_entity_event_class_name_none_without_class(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    await kb.add_entity("test", "Alice")
    assert events[0]["class_name"] is None
    assert events[0]["class_id"]   is None


# ------------------------------------------------------------------ #
# fact_added events                                                    #
# ------------------------------------------------------------------ #

async def test_hook_fires_on_fact_add(kb):
    person = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    rel   = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=rel)
    fact_events = [e for e in events if e["type"] == "fact_added"]
    assert len(fact_events) == 1
    ev = fact_events[0]
    assert ev["head_entity_name"] == "Alice"
    assert ev["tail_entity_name"] == "IBM"
    assert ev["relation_name"]    == "WORKED_AT"
    assert ev["head_entity_id"]   == alice
    assert ev["tail_entity_id"]   == ibm
    assert ev["relation_id"]      == rel


async def test_hook_fact_event_names_none_without_ids(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    await kb.add_fact("test")
    fact_events = [e for e in events if e["type"] == "fact_added"]
    assert len(fact_events) == 1
    ev = fact_events[0]
    assert ev["head_entity_name"] is None
    assert ev["tail_entity_name"] is None
    assert ev["relation_name"]    is None


# ------------------------------------------------------------------ #
# entity_property_added events                                         #
# ------------------------------------------------------------------ #

async def test_hook_fires_on_entity_property_add(kb):
    person = await kb.declare_class("test", "PERSON", "")
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    prop_id   = await kb.declare_class_property("test", "AGE", "", class_id=person)
    entity_id = await kb.add_entity("test", "Alice", class_id=person)
    await kb.add_entity_property_value("test", 30, entity_id=entity_id, property_id=prop_id)
    prop_events = [e for e in events if e["type"] == "entity_property_added"]
    assert len(prop_events) == 1
    ev = prop_events[0]
    assert ev["value"]         == 30
    assert ev["property_name"] == "AGE"
    assert ev["property_id"]   == prop_id
    assert ev["entity_id"]     == entity_id


# ------------------------------------------------------------------ #
# fact_property_added events                                           #
# ------------------------------------------------------------------ #

async def test_hook_fires_on_fact_property_add(kb):
    person = await kb.declare_class("test", "PERSON", "")
    company = await kb.declare_class("test", "COMPANY", "")
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    alice   = await kb.add_entity("test", "Alice", class_id=person)
    ibm     = await kb.add_entity("test", "IBM", class_id=company)
    rel_id  = await kb.declare_relation_type("test", "WORKED_AT", "", domain="PERSON", range="COMPANY")
    prop_id = await kb.declare_relation_property("test", "START_DATE", "", relation_id=rel_id)
    fact_id = await kb.add_fact("test", head_entity_id=alice, tail_entity_id=ibm, relation_id=rel_id)
    await kb.add_fact_property_value("test", "1998-06-01", fact_id=fact_id, property_id=prop_id)
    prop_events = [e for e in events if e["type"] == "fact_property_added"]
    assert len(prop_events) == 1
    ev = prop_events[0]
    assert ev["value"]             == "1998-06-01"
    assert ev["property_name"]     == "START_DATE"
    assert ev["head_entity_name"]  == "Alice"
    assert ev["tail_entity_name"]  == "IBM"
    assert ev["relation_name"]     == "WORKED_AT"


# ------------------------------------------------------------------ #
# database scoping                                                     #
# ------------------------------------------------------------------ #

async def test_hook_does_not_fire_for_other_database(kb):
    events: list[dict] = []
    kb.add_change_hook("test", events.append)
    kb.create_database("other")
    await kb.add_entity("other", "Bob")
    assert len(events) == 0


async def test_hooks_scoped_independently_per_database(kb):
    test_events:  list[dict] = []
    other_events: list[dict] = []
    kb.add_change_hook("test",  test_events.append)
    kb.add_change_hook("other", other_events.append)
    kb.create_database("other")
    await kb.add_entity("test",  "Alice")
    await kb.add_entity("other", "Bob")
    assert len(test_events)  == 1
    assert len(other_events) == 1
    assert test_events[0]["name"]  == "Alice"
    assert other_events[0]["name"] == "Bob"


# ------------------------------------------------------------------ #
# multiple hooks                                                       #
# ------------------------------------------------------------------ #

async def test_multiple_hooks_all_fire_in_order(kb):
    order: list[str] = []
    kb.add_change_hook("test", lambda e: order.append("a"))
    kb.add_change_hook("test", lambda e: order.append("b"))
    await kb.add_entity("test", "Alice")
    assert order == ["a", "b"]


# ------------------------------------------------------------------ #
# remove_hook                                                          #
# ------------------------------------------------------------------ #

async def test_remove_hook_stops_firing(kb):
    events: list[dict] = []
    hook = events.append
    kb.add_change_hook("test", hook)
    await kb.add_entity("test", "Alice")
    assert len(events) == 1

    kb.remove_change_hook("test", hook)
    await kb.add_entity("test", "Bob")
    assert len(events) == 1  # no new events after removal


async def test_remove_hook_noop_if_not_registered(kb):
    kb.remove_change_hook("test", lambda e: None)  # must not raise


async def test_remove_one_of_two_hooks(kb):
    calls_a: list[dict] = []
    calls_b: list[dict] = []
    hook_a = calls_a.append
    hook_b = calls_b.append
    kb.add_change_hook("test", hook_a)
    kb.add_change_hook("test", hook_b)
    kb.remove_change_hook("test", hook_a)
    await kb.add_entity("test", "Alice")
    assert len(calls_a) == 0
    assert len(calls_b) == 1


# ------------------------------------------------------------------ #
# One hook can't stop another, or the search index (#123)              #
# ------------------------------------------------------------------ #

async def test_a_failing_hook_is_logged_and_the_rest_still_run(caplog):
    from blue_data_kb_source import MemoryKBSource

    kb = MemoryKBSource(name="kb", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    person = await kb.declare_class("test", "PERSON", "A person")
    seen = []

    def failing(event):
        raise RuntimeError("downstream consumer unavailable")
    kb.add_change_hook("test", failing)
    kb.add_change_hook("test", lambda e: seen.append(e["type"]))

    await kb.add_entity("test", "Zanzibar Quill", class_id=person)  # does not raise
    assert seen == ["entity_added"]
    assert len(await kb.search_data("test", "Zanzibar Quill")) == 1
    assert "downstream consumer unavailable" in caplog.text


async def test_a_hook_may_remove_itself_while_firing(kb):
    person = await kb.declare_class("test", "PERSON", "A person")
    seen = []

    def one_shot(event):
        kb.remove_change_hook("test", one_shot)
        seen.append("one_shot")
    kb.add_change_hook("test", one_shot)
    kb.add_change_hook("test", lambda e: seen.append("next"))

    await kb.add_entity("test", "Alice", class_id=person)
    await kb.add_entity("test", "Bob", class_id=person)
    assert seen == ["one_shot", "next", "next"]


def test_an_internal_hook_error_is_raised_after_every_hook_runs():
    from blue_data_kb_source.notification.notifier import ChangeNotifier

    notifier, seen = ChangeNotifier(), []

    def broken_indexer(event):
        raise ValueError("index unavailable")
    notifier.add_global_hook(broken_indexer)
    notifier.add_hook("db", lambda e: seen.append(e["type"]))
    with pytest.raises(ValueError, match="index unavailable"):
        notifier.fire({"type": "entity_added", "database": "db", "id": "x"})
    assert seen == ["entity_added"]
