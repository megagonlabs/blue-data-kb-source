"""Uniqueness the store enforces (#82): one CLASS / RELATION per name and one node
per id within a logical KB database. The Neo4j tests run with
KB_TEST_BACKEND=neo4j against a throwaway server, like the rest of the suite."""
import os
from unittest.mock import patch

import pytest

from blue_data_kb_source import DuplicateNodeError, KBError

on_neo4j = pytest.mark.skipif(os.environ.get("KB_TEST_BACKEND") != "neo4j", reason="needs a Neo4j server")


# ------------------------------------------------------------------ #
# Any backend: a declare that loses a race returns the winner          #
# ------------------------------------------------------------------ #

async def test_declare_class_returns_the_class_another_writer_just_created(kb):
    """Between declare_class's lookup and its write, another writer created the class;
    the store refuses the duplicate and the declare returns the existing one."""
    winner = await kb.declare_class("test", "PERSON", "A person")
    real_find = kb._schema.find_class
    calls = {"n": 0}

    async def stale_then_fresh(database, name):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real_find(database, name)

    def refuse(*args, **kwargs):
        raise DuplicateNodeError("CLASS 'PERSON' already exists")
    with patch.object(kb._schema, "find_class", stale_then_fresh), patch.object(kb, "add_node", refuse):
        assert await kb.declare_class("test", "PERSON", "A person") == winner
    assert len(kb.match_nodes("test", label="CLASS")) == 1


async def test_declare_relation_type_returns_the_relation_another_writer_just_created(kb):
    await kb.declare_class("test", "PERSON", "A person")
    winner = await kb.declare_relation_type("test", "KNOWS", "Acquaintance", domain="PERSON", range="PERSON")
    real_find = kb._schema.find_relation_type
    calls = {"n": 0}

    async def stale_then_fresh(database, name):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real_find(database, name)

    def refuse(*args, **kwargs):
        raise DuplicateNodeError("RELATION 'KNOWS' already exists")
    with patch.object(kb._schema, "find_relation_type", stale_then_fresh), patch.object(kb, "add_node", refuse):
        assert await kb.declare_relation_type("test", "KNOWS", "x", domain="PERSON", range="PERSON") == winner


# ------------------------------------------------------------------ #
# Neo4j: the constraints themselves                                   #
# ------------------------------------------------------------------ #

def _constraints(kb):
    return {r["name"] for r in kb._run_query("SHOW CONSTRAINTS YIELD name RETURN name", database="neo4j")}


@on_neo4j
async def test_setup_schema_creates_the_constraints_and_is_repeatable(kb):
    kb.setup_schema()
    kb.setup_schema()
    assert {"kb_class_name", "kb_relation_name", "kb_class_id", "kb_entity_id", "kb_fact_id"} <= _constraints(kb)


@on_neo4j
async def test_the_same_name_is_allowed_in_two_logical_databases(kb):
    kb.create_database("other")
    a = await kb.declare_class("test", "PERSON", "A person")
    b = await kb.declare_class("other", "PERSON", "A person")
    assert a != b


@on_neo4j
async def test_a_second_class_with_the_same_name_is_refused_by_the_store(kb):
    await kb.declare_class("test", "PERSON", "A person")
    with pytest.raises(DuplicateNodeError):
        kb.add_node("test", "CLASS", {"name": "PERSON", "description": "again"})
    # entity names are not unique on purpose
    kb.add_node("test", "ENTITY", {"name": "Apple"})
    kb.add_node("test", "ENTITY", {"name": "Apple"})


@on_neo4j
async def test_setup_schema_names_existing_duplicates_instead_of_deleting_them(kb):
    kb._run_query("DROP CONSTRAINT kb_class_name IF EXISTS", database="neo4j", read=False)
    try:
        for i in (1, 2):
            kb._run_query("CREATE (:CLASS {_kb_db: 'test', name: 'DUP', _id: $i})", database="neo4j",
                          params={"i": f"dup-{i}"}, read=False)
        kb._schema_ready = False
        with pytest.raises(KBError, match="DUP"):
            kb.setup_schema()
        assert len([n for n in kb.match_nodes("test", label="CLASS") if n["properties"]["name"] == "DUP"]) == 2
    finally:
        kb._run_query("MATCH (n:CLASS {name: 'DUP'}) DETACH DELETE n", database="neo4j", read=False)
        kb._schema_ready = False
        kb.setup_schema()
