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


# ------------------------------------------------------------------ #
# Several processes on one database (#127)                            #
# ------------------------------------------------------------------ #

def _write_entities(directory, tag, n, class_id):
    import asyncio

    kb = _kb(directory)

    async def run():
        for i in range(n):
            await kb.add_entity("main", f"{tag}-{i}", class_id=class_id)

    asyncio.run(run())


async def test_concurrent_processes_lose_no_writes(tmp_path):
    import multiprocessing as mp

    kb = _kb(tmp_path)
    kb.create_database("main")
    person = await kb.declare_class("main", "PERSON", "A person")
    ctx = mp.get_context("spawn")  # no inherited event loop or locks
    procs = [ctx.Process(target=_write_entities, args=(str(tmp_path), tag, 40, person)) for tag in "ABC"]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert [p.exitcode for p in procs] == [0, 0, 0]
    names = {n["properties"]["name"] for n in _kb(tmp_path).match_nodes("main", label="ENTITY")}
    assert len(names) == 120


async def test_lock_file_is_not_listed_as_a_database(tmp_path):
    kb = _kb(tmp_path)
    await _person_with_birth_date(kb)
    assert any(f.endswith(".lock") for f in os.listdir(tmp_path))
    assert kb.fetch_databases() == ["main"]


def _kb_with_sidecars(directory):
    """A file KB with its BM25 corpus and vector store beside the graph, and a registry file."""
    kb = _kb(
        directory, mode="semantic", bm25_index_dir=str(directory),
        embedding_fn=lambda text: [0.1] * 8, vector_dim=8,
        vector={"_class": "blue_data.source.vector.file_vector_source.FileVectorSource",
                "name": "kb_vectors", "connection": {"directory": str(directory)}},
    )
    (directory / "registry.json").write_text('{"main": {"graph": "file"}}')
    return kb


async def test_only_files_holding_a_graph_are_databases(tmp_path):
    """#175: the BM25 corpora, vector stores and CLI registry are *.json too."""
    kb = _kb_with_sidecars(tmp_path)
    await _person_with_birth_date(kb)
    assert len([f for f in os.listdir(tmp_path) if f.endswith(".json")]) > 2
    assert kb.fetch_databases() == ["main"]


async def test_a_directory_with_sidecars_syncs_into_a_registry(tmp_path):
    from blue_data.registry.data_registry import DataRegistry

    await _person_with_birth_date(_kb_with_sidecars(tmp_path))
    registry = DataRegistry()
    registry.register_source(_kb(tmp_path))
    registry.sync_source("kb", recursive=True)
    assert [r["name"] for r in registry.list_source_databases("kb")] == ["main"]


def test_an_empty_database_is_listed(tmp_path):
    kb = _kb(tmp_path)
    kb.create_database("main")
    assert kb.fetch_databases() == ["main"]


def test_file_lock_is_reentrant_within_a_thread(tmp_path):
    from blue_data_kb_source._file_lock import FileLocks

    locks, path = FileLocks(), str(tmp_path / ".x.lock")
    with locks.hold(path):
        with locks.hold(path):  # a locked call making another locked call
            pass
    with locks.hold(path):  # released, so it can be taken again
        pass


def _write_indexed_entities(directory, tag, n, class_id):
    import asyncio

    kb = _kb(directory, mode="bm25", bm25_index_dir=str(directory))

    async def run():
        for i in range(n):
            await kb.add_entity("main", f"{tag}person{i}", class_id=class_id)

    asyncio.run(run())


async def test_search_corpus_keeps_other_processes_entries(tmp_path):
    """A long-lived process (the MCP server) used to save its stale in-memory corpus
    over the file, dropping everything another process (the CLI) had indexed."""
    pytest.importorskip("rank_bm25")
    import multiprocessing as mp

    kb = _kb(tmp_path, mode="bm25", bm25_index_dir=str(tmp_path))
    kb.create_database("main")
    person = await kb.declare_class("main", "PERSON", "A person")
    await kb.add_entity("main", "seedling", class_id=person)
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_write_indexed_entities, args=(str(tmp_path), tag, 20, person)) for tag in "AB"]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert [p.exitcode for p in procs] == [0, 0]

    await kb.add_entity("main", "latecomer", class_id=person)
    for name in ("Aperson7", "Bperson19", "seedling", "latecomer"):
        hits = await kb.search_data("main", name, k=1)
        assert hits and hits[0].name == name, name
    corpus = json.loads((tmp_path / "main_data_bm25.json").read_text())["corpus"]
    assert len(corpus) == 42


def _write_vectors(directory, tag, n, class_id):
    import asyncio

    kb = _kb_with_sidecars(directory)

    async def run():
        for i in range(n):
            await kb.add_entity("main", f"{tag}person{i}", class_id=class_id)

    asyncio.run(run())


def _search_vectors(directory, stop):
    import asyncio

    kb = _kb_with_sidecars(directory)

    async def run():
        while not stop.is_set():
            await kb.search_data("main", "person", k=5, mode="semantic")
        assert await kb.search_data("main", "person", k=5, mode="semantic")

    asyncio.run(run())


async def test_two_processes_lose_no_vectors_and_a_reader_never_fails(tmp_path):
    """FileVectorSource is the default store for semantic search; blue-data locks and
    replaces its files (blue-data#272). Writers used to lose vectors and fail after
    the graph write had gone through, and a reader could meet a half-written file."""
    import multiprocessing as mp

    kb = _kb_with_sidecars(tmp_path)
    kb.create_database("main")
    person = await kb.declare_class("main", "PERSON", "A person")
    ctx = mp.get_context("spawn")
    stop = ctx.Event()
    reader = ctx.Process(target=_search_vectors, args=(tmp_path, stop))
    writers = [ctx.Process(target=_write_vectors, args=(tmp_path, tag, 20, person)) for tag in "AB"]
    reader.start()
    for p in writers:
        p.start()
    for p in writers:
        p.join(timeout=120)
    stop.set()
    reader.join(timeout=120)
    assert [p.exitcode for p in writers] == [0, 0]
    assert reader.exitcode == 0

    assert len(json.loads((tmp_path / "kb_data_main.json").read_text())) == 40
    assert len(await kb.search_data("main", "person", k=50, mode="semantic")) == 40


# ------------------------------------------------------------------ #
# Saves keep the file mode (#135)                                      #
# ------------------------------------------------------------------ #

@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
async def test_a_new_database_file_gets_the_umask_mode(tmp_path):
    old = os.umask(0o002)
    try:
        await _person_with_birth_date(_kb(tmp_path))
    finally:
        os.umask(old)
    assert (tmp_path / "main.json").stat().st_mode & 0o777 == 0o664


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
async def test_a_save_keeps_the_existing_file_mode(tmp_path):
    kb = _kb(tmp_path)
    await _person_with_birth_date(kb)
    path = tmp_path / "main.json"
    os.chmod(path, 0o640)
    await kb.declare_class("main", "COMPANY", "A business")
    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_write_json_atomic_keeps_modes_for_the_search_corpus_too(tmp_path):
    path = tmp_path / "corpus.json"
    write_json_atomic(path, {"a": 1})
    os.chmod(path, 0o660)
    write_json_atomic(path, {"a": 2})
    assert path.stat().st_mode & 0o777 == 0o660
