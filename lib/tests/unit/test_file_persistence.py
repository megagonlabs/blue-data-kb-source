"""File backend: a failed write must never damage what is already on disk (#118)."""
import datetime
import json
import os

import pytest

from blue_data_kb_source import FileKBSource, MemoryKBSource
from blue_data_kb_source._json_io import write_json_atomic


def _kb(directory, **search):
    props = {"connection": {"directory": str(directory)}}
    if search:
        props["search"] = search
    return FileKBSource(name="kb", properties=props)


async def _person_with_birth_date(kb, db="main"):
    kb.create_database(db)
    person = await kb.declare_class(db, "PERSON", "A person")
    born = await kb.declare_class_property(db, "BIRTH_DATE", "Date of birth", class_id=person)
    alice = await kb.add_entity(db, "Alice", class_id=person)
    return alice, born


@pytest.mark.parametrize("value, stored", [
    (datetime.date(1998, 6, 1), "1998-06-01"),
    (datetime.datetime(1998, 6, 1, 9, 30), "1998-06-01T09:30:00"),
])
async def test_untyped_date_value_is_stored_as_iso_date(tmp_path, value, stored):
    kb = _kb(tmp_path)
    alice, born = await _person_with_birth_date(kb)
    lit = await kb.add_entity_property_value("main", value, entity_id=alice, property_id=born)
    props = kb.get_node("main", lit)["properties"]
    assert (props["value"], props["type"]) == (stored, "DATE")
    reread = _kb(tmp_path)
    assert reread.get_node("main", lit)["properties"]["value"] == stored


async def test_untyped_non_scalar_value_is_stored_as_string():
    kb = MemoryKBSource(name="kb")
    alice, born = await _person_with_birth_date(kb)
    lit = await kb.add_entity_property_value("main", {"y": 1998}, entity_id=alice, property_id=born)
    props = kb.get_node("main", lit)["properties"]
    assert (props["value"], props["type"]) == ("{'y': 1998}", "STRING")


async def test_failed_save_leaves_the_database_file_intact(tmp_path):
    kb = _kb(tmp_path)
    await _person_with_birth_date(kb)
    path = tmp_path / "main.json"
    before = path.read_text()
    with pytest.raises(TypeError):
        kb.add_node("main", "LITERAL", {"value": object()})  # bypasses the cast on purpose
    assert path.read_text() == before
    assert len(_kb(tmp_path).match_nodes("main", label="ENTITY")) == 1
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


async def test_bm25_corpus_is_written_atomically(tmp_path, monkeypatch):
    kb = _kb(tmp_path, mode="bm25", bm25_index_dir=str(tmp_path))
    await _person_with_birth_date(kb)
    corpus = tmp_path / "main_schema_bm25.json"
    before = corpus.read_text()

    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(OSError):
        await kb.declare_class("main", "COMPANY", "A business")
    assert corpus.read_text() == before


def test_write_json_atomic_replaces_whole_file(tmp_path):
    path = tmp_path / "x.json"
    write_json_atomic(path, {"a": 1})
    write_json_atomic(path, {"b": 2})
    assert json.loads(path.read_text()) == {"b": 2}
    assert os.listdir(tmp_path) == ["x.json"]
