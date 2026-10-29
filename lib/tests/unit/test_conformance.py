"""KBSource meets blue-data's DataSource contract (design/14), checked with its
conformance kit, plus what the kit can't see (#147)."""
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from blue_data.core.schema import DataSchema
from blue_data.testing.conformance import DataSourceConformance

from blue_data_kb_source import FileKBSource, MemoryKBSource


def _seeded(kb):
    kb.create_database("main")
    kb.execute_query({"op": "KB.DECLARE_CLASS", "name": "PERSON", "description": "A person"}, database="main")
    return kb


class TestMemoryKBSourceConformance(DataSourceConformance):
    @pytest.fixture
    def data_source(self):
        return _seeded(MemoryKBSource("t", properties={"search": {"mode": "bm25"}}))

    @pytest.fixture
    def sample_query(self):
        return ({"op": "KB.GET_DATA"}, {"database": "main"})


class TestFileKBSourceConformance(DataSourceConformance):
    @pytest.fixture
    def data_source(self, tmp_path):
        return _seeded(FileKBSource("t", properties={"connection": {"directory": str(tmp_path)}}))

    @pytest.fixture
    def sample_query(self):
        return ({"op": "KB.GET_SCHEMA"}, {"database": "main"})


class TestMemoryKBSourceWriteConformance(DataSourceConformance):
    @pytest.fixture
    def data_source(self):
        return _seeded(MemoryKBSource("t"))

    @pytest.fixture
    def sample_query(self):
        return ({"op": "KB.SEARCH_SCHEMA", "query": "person"}, {"database": "main"})


class TestNeo4jKBSourceConformance(DataSourceConformance):
    """Structural checks only: the behavioural ones need a server."""

    @pytest.fixture
    def data_source(self):
        pytest.importorskip("neo4j")
        import blue_data.source.graph.neo4j.neo4j_graph_source as ngs
        from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource

        with patch.object(ngs.GraphDatabase, "driver", return_value=MagicMock()):
            yield NEO4JKBSource("t", properties={"connection": {"password": "unused"}})


# ------------------------------------------------------------------ #
# What the kit doesn't cover                                           #
# ------------------------------------------------------------------ #

def test_every_execute_query_operation_returns_rows():
    # No search index: KB.SEARCH_SCHEMA answers through the index-free fallback, so
    # this runs without the optional rank-bm25 package (#151).
    kb = MemoryKBSource("t")
    kb.create_database("m")

    def q(**op):
        rows = kb.execute_query(op, database="m")
        assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows), (op["op"], rows)
        return rows

    person = q(op="KB.DECLARE_CLASS", name="PERSON", description="p")[0]["id"]
    q(op="KB.DECLARE_RELATION", name="KNOWS", description="k", domain="PERSON", range="PERSON")
    alice = q(op="KB.ADD_ENTITY", name="Alice", class_id=person)[0]["id"]
    assert q(op="KB.UPDATE_ENTITY", entity_id=alice, description="x") == []
    assert q(op="KB.GET_SCHEMA")[0]["entities"]["PERSON"]["name"] == "PERSON"
    assert "PERSON" in [r["name"] for r in q(op="KB.SEARCH_SCHEMA", query="person")]
    assert any("nodes" in row for row in q(op="KB.GET_DATA"))
    assert q(op="KB.GET_DATA", compact=True)
    assert q(op="GRAPH.GET_NODE", node_id=alice)[0]["id"] == alice
    assert q(op="GRAPH.ADD_NODE", label="NOTE", properties={})[0]["id"]


def test_fetch_database_schema_is_sync_and_round_trips_through_data_schema():
    kb = _seeded(MemoryKBSource("t"))
    schema = kb.fetch_database_schema("main")
    assert isinstance(schema, dict)
    assert DataSchema.from_json(schema).has_entity("PERSON")


def test_sync_entry_points_work_inside_a_running_event_loop():
    """An async host (a web server, the registry used from async code) calls them
    from inside its loop; asyncio.run used to raise RuntimeError there."""
    kb = _seeded(MemoryKBSource("t"))

    async def host():
        return (kb.fetch_database_schema("main"),
                kb.execute_query({"op": "KB.GET_DATA", "compact": True}, database="main"))

    schema, rows = asyncio.run(host())
    assert "PERSON" in schema["entities"] and isinstance(rows, list)


@pytest.mark.parametrize("env, props, expected", [
    ({}, None, "gpt-4o"),
    ({"BLUE_DATA_LLM_MODEL": "family"}, None, "family"),
    ({"BLUE_DATA_LLM_MODEL": "family", "BLUE_DATA_KB_SOURCE_LLM_MODEL": "kb"}, None, "kb"),
    ({"BLUE_DATA_KB_SOURCE_LLM_MODEL": "kb", "BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL": "query"}, None, "query"),
    ({"BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL": "query"}, {"model": "explicit"}, "explicit"),
])
def test_execute_query_nl_model_comes_from_properties_then_configuration(monkeypatch, env, props, expected):
    for name in ("BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL", "BLUE_DATA_KB_SOURCE_LLM_MODEL", "BLUE_DATA_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    kb = _seeded(MemoryKBSource("t"))
    seen = {}

    async def fake_answer(database, question, model, natural_language, max_turns):
        seen["model"] = model
        return []
    with patch.object(kb, "answer", fake_answer):
        assert kb.execute_query_nl("who?", database="main", llm_fn=lambda p: "ignored",
                                   optional_properties=props) == []
    assert seen["model"] == expected
