"""
Natural language querying -- enrich().

enrich() walks outward from whatever the query best matches in the KB,
returning a ranked list of EnrichmentResult(entity, relation, value,
distance) -- candidate facts within `depth` hops that the query didn't
directly ask for but that a caller building a fuller picture might want.
Unlike answer()/validate(), it has no natural_language/include_metadata
flags -- it always returns structured results.

Requires OPENAI_API_KEY -- not live-verified in this session (no key
available in the sandboxed environment this was written in). The calls
below are structurally correct against KnowledgeManager.enrich's signature;
run it yourself with a real key to see live output.

    export OPENAI_API_KEY=sk-...
    python3 enrich_demo.py
"""
import asyncio

from blue_data_kb_source import MemoryKBSource

DIVIDER = "-" * 60


async def seed(kb: MemoryKBSource) -> None:
    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    acquired = await kb.declare_relation_type(
        "main", "ACQUIRED", "One company acquiring another", domain="COMPANY", range="COMPANY"
    )
    title_prop = await kb.declare_class_property("main", "TITLE", "Job title", class_id=person)

    alice = await kb.add_entity("main", "Alice", class_id=person, description="Software engineer")
    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology company")
    redhat = await kb.add_entity("main", "Red Hat", class_id=company, description="Open-source software company")

    await kb.add_entity_property_value("main", "Senior Software Engineer", entity_id=alice, property_id=title_prop)
    await kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)
    await kb.add_fact("main", head_entity_id=ibm, tail_entity_id=redhat, relation_id=acquired)


async def main():
    kb = MemoryKBSource(name="enrich-demo")
    kb.create_database("main")
    await seed(kb)

    print(DIVIDER)
    print("1. enrich with the default depth=2 -- 1-2 hop context around the best match")
    print(DIVIDER)

    results = await kb.enrich("main", "Alice")
    print(f"enrich('Alice') -> {len(results)} EnrichmentResult(s):")
    for r in results:
        print(f"  entity={r.entity!r} relation={r.relation!r} value={r.value!r} distance={r.distance}")
    print("distance=1 results are direct facts/properties on Alice (title, WORKED_AT IBM);")
    print("distance=2 results are one hop further out (e.g. IBM's ACQUIRED Red Hat fact),")
    print("reached by walking past Alice's WORKED_AT edge into IBM's own neighborhood.")

    print()
    print(DIVIDER)
    print("2. depth=1 -- only the immediate neighborhood")
    print(DIVIDER)

    shallow = await kb.enrich("main", "Alice", depth=1)
    print(f"enrich('Alice', depth=1) -> {len(shallow)} result(s), all distance=1")

    print()
    print("Demo complete.")


asyncio.run(main())
