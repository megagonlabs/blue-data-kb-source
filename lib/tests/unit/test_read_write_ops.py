"""Every KB.* and GRAPH.* op is declared a read or a write, and blue-data's registry
refuses the writes without commit (#167)."""

import inspect
import re

import pytest
from blue_data.registry.data_registry import DataRegistry
from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source import FileKBSource, KBSource, MemoryKBSource
from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource

_OP_LITERAL = re.compile(r'op == "((?:KB|GRAPH)\.[A-Z_]+)"')


def _served_ops() -> set[str]:
    return set(_OP_LITERAL.findall(inspect.getsource(KBSource._execute_op))) | set(
        _OP_LITERAL.findall(inspect.getsource(GraphSource.execute_query))
    )


def test_every_served_op_is_declared_in_exactly_one_set():
    served = _served_ops()
    assert any(op.startswith("KB.") for op in served)
    assert any(op.startswith("GRAPH.") for op in served)
    assert not (KBSource._READ_OPS & KBSource._WRITE_OPS)
    declared = KBSource._READ_OPS | KBSource._WRITE_OPS
    assert served <= declared, sorted(served - declared)
    # The only declared op not served is one GraphSource itself lists as a write
    # (GRAPH.MERGE), kept so the inherited set stays whole.
    assert declared - served <= GraphSource._WRITE_OPS, sorted(declared - served)


@pytest.mark.parametrize("cls", [MemoryKBSource, FileKBSource, NEO4JKBSource])
def test_backends_inherit_the_kb_declarations(cls):
    assert cls._READ_OPS == KBSource._READ_OPS
    assert cls._WRITE_OPS == KBSource._WRITE_OPS
    assert GraphSource._WRITE_OPS <= cls._WRITE_OPS


def _query(op: str) -> dict:
    # Placeholder fields: the guard refuses before any field is read.
    return {"op": op, "name": "Placeholder", "entity_id": "e", "fact_id": "f"}


@pytest.fixture
def registry(tmp_path):
    src = FileKBSource("kb", properties={"connection": {"directory": str(tmp_path)}})
    src.create_database("demo")
    reg = DataRegistry()
    reg.register_source(src)
    return src, reg


@pytest.mark.parametrize("op", sorted(KBSource._WRITE_OPS))
def test_write_op_without_commit_is_refused_by_the_registry(registry, op):
    src, reg = registry
    before = src.execute_query({"op": "KB.GET_DATA"}, database="demo")
    with pytest.raises(ValueError, match="commit"):
        reg.execute_query("kb", _query(op), database="demo")
    assert src.execute_query({"op": "KB.GET_DATA"}, database="demo") == before


def test_add_entity_with_commit_still_writes(registry):
    src, reg = registry
    reg.execute_query(
        "kb",
        {"op": "KB.ADD_ENTITY", "name": "Placeholder Entity", "description": "test"},
        database="demo",
        optional_properties={"commit": True},
    )
    hits = src.execute_query({"op": "KB.SEARCH_DATA", "query": "Placeholder", "k": 1}, database="demo")
    assert [h.get("name") for h in hits] == ["Placeholder Entity"]


@pytest.mark.parametrize(
    "query",
    [
        {"op": "KB.GET_SCHEMA"},
        {"op": "KB.GET_DATA"},
        {"op": "KB.SEARCH_SCHEMA", "query": "x"},
        {"op": "KB.SEARCH_DATA", "query": "x"},
        {"op": "GRAPH.MATCH_NODE"},
    ],
)
def test_read_op_without_commit_runs(registry, query):
    _, reg = registry
    assert isinstance(reg.execute_query("kb", query, database="demo"), list)
