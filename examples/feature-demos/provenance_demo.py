"""
Provenance tracking — sources, per-write evidence, and accumulating extractions.

file_backend_demo.py touches this briefly (one add_source, one add_entity with
text=/source_id=, one get_metadata call). This demo goes deeper: multiple
sources with an `original` date, provenance on entity/fact/property writes,
and the *_extraction methods that let you attach additional evidence to an
element that already exists -- each call adds a record, none of them replace
a prior one.
"""
import asyncio

from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_kb_source import KBSource

DIVIDER = "-" * 60


class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


async def main():
    kb = MemoryKBSource(name="provenance-demo")
    kb.create_database("main")

    print(DIVIDER)
    print("1. Sources -- add_source(..., original=...) for externally known dates")
    print(DIVIDER)

    resume = await kb.add_source(
        "main", source="resume.pdf", name="Alice's resume", original="2020-01-15"
    )
    linkedin = await kb.add_source(
        "main", source="linkedin.json", name="Alice's LinkedIn profile", original="2023-06-01"
    )
    print(f"Two SOURCE nodes: resume={resume[:8]}..., linkedin={linkedin[:8]}...")
    print("`original` is a free-text externally supplied date -- distinct from the node's")
    print("own `created`/`modified` timestamps, which the library stamps automatically.")

    print()
    print(DIVIDER)
    print("2. Provenance on a write -- add_entity(text=..., source_id=...)")
    print(DIVIDER)

    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    title_prop = await kb.declare_class_property("main", "TITLE", "Job title", class_id=person)

    alice = await kb.add_entity(
        "main", "Alice", class_id=person, description="Software engineer",
        text="Alice is a software engineer.", source_id=resume,
    )
    meta = await kb.get_metadata("main", alice)
    print(f"get_metadata after one write: extraction={meta['extraction']!r}")
    print(f"                              source={meta['source']!r}")
    print(f"                              source_original={meta['source_original']!r}")

    print()
    print(DIVIDER)
    print("3. Accumulating more evidence with add_entity_extraction")
    print(DIVIDER)

    print("add_entity_extraction does NOT touch the entity's name/description -- it only")
    print("requires the entity to already exist, and appends one more EXTRACTED_FROM record.")
    await kb.add_entity_extraction(
        "main", alice, text="Alice currently works as a senior engineer.", source_id=linkedin
    )
    meta = await kb.get_metadata("main", alice)
    print(f"get_metadata after two extractions: extraction={meta['extraction']!r}")
    print(f"                                    source={meta['source']!r}")
    print(f"                                    source_original={meta['source_original']!r}")
    print("Both texts and both sources are pipe-joined -- neither call replaced the other.")

    print()
    print(DIVIDER)
    print("4. Same pattern for property values (add_entity_property_value_extraction)")
    print(DIVIDER)

    title_literal = await kb.add_entity_property_value(
        "main", "Senior Software Engineer", entity_id=alice, property_id=title_prop,
        text="Alice's title is Senior Software Engineer.", source_id=linkedin,
    )
    await kb.add_entity_property_value_extraction(
        "main", alice, title_prop, text="Confirmed via a phone call on 2024-03-01.", source_id=None
    )
    title_meta = await kb.get_metadata("main", title_literal)
    print(f"TITLE literal's accumulated extraction: {title_meta['extraction']!r}")

    print()
    print(DIVIDER)
    print("5. Same pattern for facts (add_fact_extraction)")
    print(DIVIDER)

    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology company")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    fact = await kb.add_fact(
        "main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at,
        text="Alice worked at IBM.", source_id=resume,
    )
    await kb.add_fact_extraction(
        "main", fact, text="LinkedIn confirms Alice's employment at IBM.", source_id=linkedin
    )
    fact_meta = await kb.get_metadata("main", fact)
    print(f"WORKED_AT fact's accumulated extraction: {fact_meta['extraction']!r}")
    print(f"WORKED_AT fact's accumulated source:      {fact_meta['source']!r}")

    print()
    print("Demo complete.")


asyncio.run(main())
