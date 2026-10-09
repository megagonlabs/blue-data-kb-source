"""Caller-supplied values that address storage: database names (#116) and node ids (#117).

Both reach the library unchecked from MCP tool arguments, i.e. from text an agent chose.
"""
import os

import pytest

from blue_data_kb_source import FileKBSource, MemoryKBSource
from blue_data_kb_source.types import KBError, KnowledgeError, ProvenanceError, SchemaError

# ------------------------------------------------------------------ #
# database names (#116)                                                #
# ------------------------------------------------------------------ #

BAD_NAMES = ["../escaped", "a/b", "/abs/path/kb", "..", "", "x#y", "a b", "x" * 65]


@pytest.fixture
def file_kb(tmp_path):
    data = tmp_path / "data"
    return FileKBSource(
        name="kb",
        properties={"connection": {"directory": str(data)},
                    "search": {"mode": "bm25", "bm25_index_dir": str(data)}},
    )


@pytest.mark.parametrize("name", BAD_NAMES)
async def test_file_backend_rejects_database_names_that_are_paths(file_kb, tmp_path, name):
    with pytest.raises(KBError, match="invalid database name"):
        await file_kb.declare_class(name, "PERSON", "A person")
    assert sorted(os.listdir(tmp_path)) == ["data"]
    assert os.listdir(tmp_path / "data") == []


async def test_file_backend_cannot_read_a_kb_file_by_absolute_path(file_kb, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "kb.json").write_text('{"nodes": {}, "edges": {}}')
    with pytest.raises(KBError, match="invalid database name"):
        await file_kb.fetch_database_schema(str(other / "kb"))


@pytest.mark.parametrize("name", ["main", "default", "acme_2026", "my-kb", "A1"])
async def test_file_backend_accepts_plain_database_names(file_kb, tmp_path, name):
    await file_kb.declare_class(name, "PERSON", "A person")
    assert f"{name}.json" in os.listdir(tmp_path / "data")


def test_bm25_corpus_path_rejects_database_names_that_are_paths(file_kb):
    with pytest.raises(KBError, match="invalid database name"):
        file_kb._search_indexer._corpus_file_path("../escaped", "schema")


# ------------------------------------------------------------------ #
# caller-supplied node ids (#117)                                      #
# ------------------------------------------------------------------ #

@pytest.fixture
async def kb():
    kb = MemoryKBSource(name="kb")
    kb.create_database("t")
    kb._ids = {
        "person": await kb.declare_class("t", "PERSON", ""),
        "company": await kb.declare_class("t", "COMPANY", ""),
    }
    kb._ids["worked_at"] = await kb.declare_relation_type(
        "t", "WORKED_AT", "", domain="PERSON", range="COMPANY"
    )
    kb._ids["age"] = await kb.declare_class_property("t", "AGE", "", class_id=kb._ids["person"])
    kb._ids["alice"] = await kb.add_entity("t", "Alice", class_id=kb._ids["person"])
    kb._ids["ibm"] = await kb.add_entity("t", "IBM", class_id=kb._ids["company"])
    return kb


def _creators(kb):
    i = kb._ids
    return {
        "declare_class": (SchemaError, lambda nid: kb.declare_class("t", "OTHER", "", class_id=nid)),
        "declare_relation_type": (SchemaError, lambda nid: kb.declare_relation_type(
            "t", "OWNS", "", relation_id=nid, domain="PERSON", range="COMPANY")),
        "declare_class_property": (SchemaError, lambda nid: kb.declare_class_property(
            "t", "HEIGHT", "", property_id=nid, class_id=i["person"])),
        "declare_relation_property": (SchemaError, lambda nid: kb.declare_relation_property(
            "t", "START", "", property_id=nid, relation_id=i["worked_at"])),
        "add_entity": (KnowledgeError, lambda nid: kb.add_entity("t", "Bob", entity_id=nid, class_id=i["person"])),
        "add_fact": (KnowledgeError, lambda nid: kb.add_fact(
            "t", fact_id=nid, head_entity_id=i["alice"], tail_entity_id=i["ibm"], relation_id=i["worked_at"])),
        "add_source": (ProvenanceError, lambda nid: kb.add_source("t", "cv", node_id=nid)),
        "add_extraction": (ProvenanceError, lambda nid: kb.add_extraction("t", "some text", node_id=nid)),
    }


@pytest.mark.parametrize("creator", [
    "declare_class", "declare_relation_type", "declare_class_property", "declare_relation_property",
    "add_entity", "add_fact", "add_source", "add_extraction",
])
async def test_taken_id_is_refused_and_existing_node_is_untouched(kb, creator):
    error_cls, create = _creators(kb)[creator]
    alice = kb._ids["alice"]
    before = kb.get_node("t", alice)
    with pytest.raises(error_cls, match="already the id of an existing node"):
        await create(alice)
    assert kb.get_node("t", alice) == before


@pytest.mark.parametrize("creator", ["declare_class", "add_entity", "add_source"])
async def test_unused_explicit_id_is_still_honoured(kb, creator):
    _, create = _creators(kb)[creator]
    assert await create("caller-chosen-id") == "caller-chosen-id"


async def test_redeclaring_an_existing_class_by_name_stays_idempotent(kb):
    person = kb._ids["person"]
    assert await kb.declare_class("t", "PERSON", "", class_id=person) == person
