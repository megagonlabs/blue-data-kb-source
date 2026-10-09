import os

import pytest

import blue_data_kb_source

# Opt-in: run the suite against Neo4j instead of the in-memory backend (#120, #81).
#   KB_TEST_BACKEND=neo4j KB_NEO4J_PORT=17691 KB_NEO4J_PASSWORD=... pytest tests/unit
# Every MemoryKBSource the tests build (via the fixtures below or directly) becomes a
# NEO4JKBSource on that server, wiped when it is created — so use a throwaway server.
if os.environ.get("KB_TEST_BACKEND") == "neo4j":
    from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource

    class _Neo4jTestKB(NEO4JKBSource):
        def __init__(self, name, properties=None):
            props = dict(properties or {})
            props["connection"] = {
                "protocol": "bolt", "host": os.environ.get("KB_NEO4J_HOST", "127.0.0.1"),
                "port": int(os.environ.get("KB_NEO4J_PORT", "7687")),
                "user": os.environ.get("KB_NEO4J_USER", "neo4j"),
                "password": os.environ["KB_NEO4J_PASSWORD"],
            }
            super().__init__(name, properties=props)
            self._run_query("MATCH (n) DETACH DELETE n", database="neo4j", read=False)

    blue_data_kb_source.MemoryKBSource = _Neo4jTestKB

from blue_data_kb_source import MemoryKBSource  # noqa: E402  (after the optional swap)


@pytest.fixture
def kb() -> MemoryKBSource:
    inst = MemoryKBSource(name="test-kb")
    inst.create_database("test")
    return inst


@pytest.fixture
async def populated_kb(kb: MemoryKBSource) -> MemoryKBSource:
    person   = await kb.declare_class("test", "PERSON",   "A human individual")
    company  = await kb.declare_class("test", "COMPANY",  "A business organization")
    employee = await kb.declare_class("test", "EMPLOYEE", "A person employed by an organization")
    await kb.add_is_subclass_of("test", employee, person)
    worked_at = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment relationship", domain="EMPLOYEE", range="COMPANY"
    )
    start_date = await kb.declare_relation_property(
        "test", "START_DATE", "Start of employment", relation_id=worked_at
    )

    src   = await kb.add_source("test", source="resume_alice", name="Alice's résumé")
    alice = await kb.add_entity("test", "Alice", class_id=employee,
                                text="Alice is a software engineer.", source_id=src)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", relation_id=worked_at,
                              head_entity_id=alice, tail_entity_id=ibm,
                              text="Alice joined IBM in June 1998.", source_id=src)
    await kb.add_fact_property_value("test", "1998-06-01",
                                     fact_id=fact, property_id=start_date,
                                     text="Alice joined IBM in June 1998.", source_id=src)
    return kb
