"""
BM25 keyword search — enable_search=True, no vector backend.

Compares the ranked BM25 path against the unranked substring fallback used
when search isn't enabled at all, then shows search_schema/search_data with
a type_filter. No external services, no API key.

Note: `score` on SchemaSearchResult/DataSearchResult is only populated for
mode="semantic" — keyword mode (ranked or not) always reports score=None;
BM25 only changes *result order*, which is what this demo compares.
"""
import asyncio

from blue_data_kb_source import MemoryKBSource

DIVIDER = "-" * 60


async def seed(kb: MemoryKBSource) -> None:
    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship between a person and a company",
        domain="PERSON", range="COMPANY",
    )
    await kb.declare_relation_type(
        "main", "ACQUIRED", "One company acquiring another", domain="COMPANY", range="COMPANY",
    )

    alice = await kb.add_entity("main", "Alice", class_id=person, description="Software engineer")
    bob = await kb.add_entity("main", "Bob", class_id=person, description="Sales director")
    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology and consulting company")
    redhat = await kb.add_entity(
        "main", "Red Hat", class_id=company, description="Enterprise open-source software company"
    )

    await kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)
    await kb.add_fact("main", head_entity_id=bob, tail_entity_id=redhat, relation_id=worked_at)


async def main():
    # ---------------------------------------------------------------- #
    # 1. Without search enabled — unranked substring fallback
    # ---------------------------------------------------------------- #
    print(DIVIDER)
    print("1. No search enabled — unranked substring match")
    print(DIVIDER)

    plain_kb = MemoryKBSource(name="plain")
    plain_kb.create_database("main")
    await seed(plain_kb)

    hits = await plain_kb.search_data("main", "software")
    print("search_data('software') without enable_search (graph iteration order):")
    for h in hits:
        print(f"  {h.type:<8} {h.name:<10} {h.description}")

    # ---------------------------------------------------------------- #
    # 2. With BM25 enabled — ranked results
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("2. enable_search=True — ranked by BM25 relevance")
    print(DIVIDER)

    kb = MemoryKBSource(name="bm25", enable_search=True)
    kb.create_database("main")
    await seed(kb)

    hits = await kb.search_data("main", "software")
    print("search_data('software') with BM25 (most relevant first):")
    for h in hits:
        print(f"  {h.type:<8} {h.name:<10} {h.description}")

    schema_hits = await kb.search_schema("main", "employment")
    print("search_schema('employment'):", [h.name for h in schema_hits])

    # ---------------------------------------------------------------- #
    # 3. type_filter — restrict to one node type
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("3. type_filter")
    print(DIVIDER)

    rel_hits = await kb.search_schema("main", "company", type_filter="relation")
    print("search_schema('company', type_filter='relation'):", [h.name for h in rel_hits])

    fact_hits = await kb.search_data("main", "Alice", type_filter="fact")
    print("search_data('Alice', type_filter='fact'):", [h.name for h in fact_hits])

    print()
    print("Demo complete.")


asyncio.run(main())
