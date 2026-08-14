"""
Minimal in-memory quickstart: declare a schema, add data, query it.

No persistence, no search, no LLM — the smallest useful KBSource example.
"""
import asyncio

from blue_data_kb_source import MemoryKBSource


async def main():
    kb = MemoryKBSource(name="quickstart")
    kb.create_database("main")

    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )

    alice = await kb.add_entity("main", "Alice", class_id=person)
    ibm = await kb.add_entity("main", "IBM", class_id=company)
    await kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)

    rows = await kb.fetch_database_data("main", compact=True)
    print("Query result:", rows)


asyncio.run(main())
