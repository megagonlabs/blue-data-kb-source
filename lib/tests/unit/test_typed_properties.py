"""A property may declare the type of value it holds, and a value that doesn't fit
it is refused (#95): a job title in AGE used to be written because AGE was
declared on PERSON."""
import json
from unittest.mock import patch

import pytest

from blue_data.core.schema import DataSchema
from blue_data_kb_source import KnowledgeError, SchemaError


async def _person_with(kb, **types):
    person = await kb.declare_class("test", "PERSON", "A person")
    props = {name: await kb.declare_class_property("test", name, f"{name} of a person", class_id=person,
                                                   literal_type=t)
             for name, t in types.items()}
    alice = await kb.add_entity("test", "Alice", class_id=person)
    return person, alice, props


def _prop(kb, pid):
    return kb.get_node("test", pid)["properties"]


async def test_declared_type_is_stored_and_checked(kb):
    person, _, props = await _person_with(kb, AGE="number")
    assert _prop(kb, props["AGE"])["literal_type"] == "NUMBER"
    with pytest.raises(SchemaError, match="literal_type must be one of"):
        await kb.declare_class_property("test", "HEIGHT", "Height", class_id=person, literal_type="float")


async def test_redeclaring_types_an_untyped_property_but_never_retypes(kb):
    person = await kb.declare_class("test", "PERSON", "A person")
    age = await kb.declare_class_property("test", "AGE", "Age", class_id=person)
    assert await kb.declare_class_property("test", "AGE", "Age", class_id=person, literal_type="NUMBER") == age
    assert _prop(kb, age)["literal_type"] == "NUMBER"
    await kb.declare_class_property("test", "AGE", "Age", class_id=person, literal_type="STRING")
    assert _prop(kb, age)["literal_type"] == "NUMBER"


@pytest.mark.parametrize("prop, value", [
    ("AGE", "Senior Project Manager"),   # the #95 case
    ("AGE", "twelve hundred"),
    ("BORN", "Q3 2026"),
    ("BORN", "Senior Project Manager"),
    ("ACTIVE", "maybe"),
])
async def test_a_value_that_does_not_fit_is_refused(kb, prop, value):
    _, alice, props = await _person_with(kb, AGE="NUMBER", BORN="DATE", ACTIVE="BOOLEAN")
    with pytest.raises(KnowledgeError, match=f"does not fit {prop}"):
        await kb.add_entity_property_value("test", value, entity_id=alice, property_id=props[prop])
    assert kb.match_nodes("test", label="LITERAL") == []


@pytest.mark.parametrize("prop, value, literal_type, stored", [
    ("AGE", "41", None, 41),
    ("AGE", "41", "STRING", 41),          # the declared type wins over the caller's
    ("BORN", "March 2019", None, "2019-03"),
    ("ACTIVE", "true", None, True),
    ("NOTE", 7, None, "7"),
])
async def test_a_value_that_fits_is_stored_as_the_declared_type(kb, prop, value, literal_type, stored):
    _, alice, props = await _person_with(kb, AGE="NUMBER", BORN="DATE", ACTIVE="BOOLEAN", NOTE="STRING")
    lid = await kb.add_entity_property_value("test", value, literal_type=literal_type,
                                             entity_id=alice, property_id=props[prop])
    lit = kb.get_node("test", lid)["properties"]
    assert (lit["value"], lit["type"]) == (stored, _prop(kb, props[prop])["literal_type"])


async def test_untyped_properties_behave_as_before(kb):
    _, alice, props = await _person_with(kb, TITLE=None)
    lid = await kb.add_entity_property_value("test", "Senior Project Manager", entity_id=alice,
                                             property_id=props["TITLE"])
    assert kb.get_node("test", lid)["properties"]["type"] == "STRING"


async def test_schema_shows_types_and_they_survive_export_and_import(kb):
    await _person_with(kb, AGE="NUMBER", BORN="DATE", TITLE=None)
    rendered = DataSchema.from_json(kb.fetch_database_schema("test")).to_str(simplified=True)
    assert "AGE: number" in rendered and "BORN: date" in rendered and "TITLE: str" in rendered
    kb.create_database("copy")
    await kb.import_database_schema("copy", await kb.export_database_schema("test"))
    types = {n["properties"]["name"]: n["properties"].get("literal_type")
             for n in kb.match_nodes("copy", label="PROPERTY")}
    assert types == {"AGE": "NUMBER", "BORN": "DATE", "TITLE": None}


async def test_update_property_sets_a_type(kb):
    _, _, props = await _person_with(kb, AGE=None)
    await kb.update_property("test", props["AGE"], literal_type="number")
    assert _prop(kb, props["AGE"])["literal_type"] == "NUMBER"


# ------------------------------------------------------------------ #
# The agents see and declare types                                     #
# ------------------------------------------------------------------ #

from tests.unit.test_agent_tool_errors import ScriptedModel  # noqa: E402


async def test_expand_schema_declares_the_type(kb):
    person = await kb.declare_class("test", "PERSON", "A person")
    model = ScriptedModel(
        [("declare_class_property", {"class_id": person, "name": "AGE",
                                     "description": "Age in years", "literal_type": "NUMBER"})],
        [("return_property_check", {"describes_owner": True, "same_as_id": None, "reason": "fits"})],
        "done",
    )
    with patch("openai.AsyncOpenAI", return_value=model):
        await kb.expand_schema("test", "Alice is 41.", dedup_mode="none")
    age = next(n for n in kb.match_nodes("test", label="PROPERTY") if n["properties"]["name"] == "AGE")
    assert age["properties"]["literal_type"] == "NUMBER"


async def test_extraction_sees_the_type_and_a_misfit_goes_back_to_the_model(kb):
    _, alice, props = await _person_with(kb, AGE="NUMBER")
    text = "Alice Chen — Senior Project Manager at Contoso since March 2019."
    model = ScriptedModel(
        "done",   # entities
        "done",   # relations
        "done",   # fact properties
        [("search_schema", {"query": "AGE", "type": "property"}),
         ("add_entity_property_value", {"entity_id": alice, "property_id": props["AGE"],
                                        "value": "Senior Project Manager", "text": text})],
        "done",
    )
    with patch("openai.AsyncOpenAI", return_value=model):
        await kb.import_text("test", text, expand_schema=False, related_data_k=0)
    search, add = model.tool_replies()
    assert {"name": "AGE", "literal_type": "NUMBER"}.items() <= json.loads(search)["schema_elements"][0].items()
    assert "does not fit AGE, which holds a NUMBER" in json.loads(add)["error"]
    assert kb.match_nodes("test", label="LITERAL") == []


async def test_updating_a_value_checks_the_declared_type_too(kb):
    _, alice, props = await _person_with(kb, AGE="NUMBER")
    lid = await kb.add_entity_property_value("test", 41, entity_id=alice, property_id=props["AGE"])
    with pytest.raises(KnowledgeError, match="does not fit AGE"):
        await kb.update_entity_property_value("test", alice, props["AGE"], value="Senior Project Manager")
    await kb.update_entity_property_value("test", alice, props["AGE"], value="42")
    assert kb.get_node("test", lid)["properties"]["value"] == 42
