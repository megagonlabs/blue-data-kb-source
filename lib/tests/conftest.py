import pytest
from blue_data.source.graph.memory_graph_source import MemoryGraphSource

from blue_data_kb_source import KBSource


class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


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
        "test", "WORKED_AT", "Employment relationship", domain="EMPLOYEE", range_="COMPANY"
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
