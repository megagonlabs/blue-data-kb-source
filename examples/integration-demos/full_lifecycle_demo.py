"""
Full lifecycle -- declare, populate, observe, search, migrate, in one flow.

The direct kb-source analog of blue-data's own integration-demos hero script
(register -> sync -> enrich -> search -> query on one realistic dataset):
this chains stages that backends/ and feature-demos/ each show in isolation
into one continuous narrative on one small, realistic org dataset --

    declare schema -> populate with provenance -> observe via change hooks
    -> keyword + semantic search -> migrate to a second backend

Deliberately needs no API key, the same choice blue-data made for its own
hero integration demo (llm_fn=None there; a deterministic fake embedding
here) -- this is meant to be the one script in examples/ you can always run,
with nothing gated behind external credentials.
"""
import asyncio
import hashlib
import math
import shutil
import tempfile

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_data_kb_source import FileKBSource, MemoryKBSource

DIVIDER = "-" * 60
DIM = 64


def fake_embedding(text: str) -> list[float]:
    seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
    return [math.sin(seed * (i + 1) * 0.1) for i in range(DIM)]


async def main():
    print(DIVIDER)
    print("1. Declare schema")
    print(DIVIDER)

    # BM25 keyword search and semantic search are both wired at construction time,
    # and change hooks are registered before any writes so nothing is missed.
    kb = MemoryKBSource(
        name="lifecycle-demo",
        enable_search=True,
        vector_source=MemoryVectorSource("kb_vectors"),
        embedding_fn=fake_embedding,
        vector_dim=DIM,
    )
    kb.create_database("main")

    events: list[dict] = []
    kb.add_change_hook("main", lambda e: events.append(e))

    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    acquired = await kb.declare_relation_type(
        "main", "ACQUIRED", "One company acquiring another", domain="COMPANY", range="COMPANY"
    )
    title_prop = await kb.declare_class_property("main", "TITLE", "Job title", class_id=person)
    print("Declared PERSON, COMPANY, WORKED_AT, ACQUIRED, TITLE")

    print()
    print(DIVIDER)
    print("2. Populate with provenance -- a small org, from two source documents")
    print(DIVIDER)

    directory_src = await kb.add_source("main", source="org_directory.csv", name="Org directory")
    press_src = await kb.add_source(
        "main", source="press_release.txt", name="Acquisition press release", original="2019-10-28"
    )

    people = {}
    for name, title, company_name in [
        ("Alice", "Senior Software Engineer", "IBM"),
        ("Bob", "Sales Director", "Red Hat"),
        ("Carol", "Data Scientist", "Nimbus Analytics"),
    ]:
        people[name] = await kb.add_entity(
            "main", name, class_id=person, description=title,
            text=f"{name} works at {company_name}.", source_id=directory_src,
        )

    companies = {}
    for name, description in [
        ("IBM", "Technology and consulting company"),
        ("Red Hat", "Enterprise open-source software company"),
        ("Nimbus Analytics", "Data analytics startup"),
    ]:
        companies[name] = await kb.add_entity(
            "main", name, class_id=company, description=description,
            text=f"{name} is listed in the org directory.", source_id=directory_src,
        )

    for person_name, title, company_name in [
        ("Alice", "Senior Software Engineer", "IBM"),
        ("Bob", "Sales Director", "Red Hat"),
        ("Carol", "Data Scientist", "Nimbus Analytics"),
    ]:
        await kb.add_entity_property_value(
            "main", title, entity_id=people[person_name], property_id=title_prop,
            text=f"{person_name}'s title is {title}.", source_id=directory_src,
        )
        await kb.add_fact(
            "main", head_entity_id=people[person_name], tail_entity_id=companies[company_name],
            relation_id=worked_at, text=f"{person_name} works at {company_name}.", source_id=directory_src,
        )

    await kb.add_fact(
        "main", head_entity_id=companies["IBM"], tail_entity_id=companies["Red Hat"], relation_id=acquired,
        text="IBM acquired Red Hat.", source_id=press_src,
    )
    print("Added 3 people, 3 companies, 3 titles, 3 WORKED_AT facts, and 1 ACQUIRED fact")

    print()
    print(DIVIDER)
    print("3. Observe -- what the change hook saw across the whole populate step")
    print(DIVIDER)

    counts: dict[str, int] = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print("Event counts:")
    for event_type, count in sorted(counts.items()):
        print(f"  {event_type:<28} {count}")

    print()
    print(DIVIDER)
    print("4. Search -- BM25 keyword vs semantic, over the same populated KB")
    print(DIVIDER)

    kw_hits = await kb.search_data("main", "acquired open-source company", mode="keyword")
    print("search_data('acquired open-source company', mode='keyword'):")
    for h in kw_hits:
        print(f"  {h.type:<8} {h.name}")

    sem_hits = await kb.search_data("main", "acquired open-source company", mode="semantic")
    print("search_data('acquired open-source company', mode='semantic'):")
    for h in sem_hits:
        print(f"  {h.type:<8} {h.name:<20} score={round(h.score, 3) if h.score is not None else None}")
    print("(the embedding is a deterministic hash, not a real model -- like semantic_search_demo.py,")
    print("this proves the wiring works, not that the ranking is meaningful; a real embedding model")
    print("would rank Red Hat/IBM/the ACQUIRED fact above Carol for this query)")

    print()
    print(DIVIDER)
    print("5. Migrate -- export/import the graph into a second, file-backed KB")
    print(DIVIDER)

    tmp = tempfile.mkdtemp(prefix="blue-data-kb-lifecycle-")
    migrated = FileKBSource(
        name="lifecycle-demo-migrated",
        directory=tmp,
        enable_search=True,
        vector_source=MemoryVectorSource("kb_vectors"),
        embedding_fn=fake_embedding,
        vector_dim=DIM,
    )
    migrated.create_database("main")

    schema_json = await kb.export_database_schema("main")
    await migrated.import_database_schema("main", schema_json)
    data_json = await kb.export_database_data("main", include_provenance=True)
    await migrated.import_database_data("main", data_json)

    rows = await migrated.fetch_database_data("main", compact=True)
    print(f"Migrated KB (FileGraphSource, {tmp}) now has {len(rows)} graph rows")

    migrated_hits = await migrated.search_data("main", "acquired open-source company", mode="semantic")
    print("Semantic search still works after migration (import re-triggers the target's own indexer):")
    for h in migrated_hits:
        print(f"  {h.type:<8} {h.name:<20} score={round(h.score, 3) if h.score is not None else None}")

    ibm_id = next(hit.id for hit in migrated_hits if hit.name == "IBM")
    meta = await migrated.get_metadata("main", ibm_id)
    print(f"IBM's extraction text survived the migration: {meta['extraction']!r}")
    print(f"But its SOURCE-node link did not: source={meta['source']!r}, "
          f"source_original={meta['source_original']!r}")
    print("Gotcha: export_database_data/import_database_data carry over extraction *text*")
    print("(re-linked via a fresh EXTRACTED_FROM edge on import) but SOURCE nodes themselves")
    print("are never exported -- only entities/facts/property values are. If you need the")
    print("SOURCE name/original date to survive a migration too, add_source() them again in")
    print("the target database and re-link with add_*_extraction(..., source_id=...).")

    shutil.rmtree(tmp)
    print()
    print(f"Cleaned up {tmp}")
    print("Demo complete.")


asyncio.run(main())
