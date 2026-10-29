"""
Natural language querying -- answer().

answer() runs its own tool-calling agent loop against the KB (search_schema,
search_entities, search_facts, get_neighbors, get_metadata, ...) and returns
either structured rows or LLM-composed prose, optionally paired with
per-node metadata -- see the table below for how `natural_language` and
`include_metadata` combine.

    natural_language | include_metadata | return
    -----------------|------------------|----------------------------------
    False            | False            | list[dict]
    False            | True             | (list[dict], metadata: dict)
    True             | False            | str
    True             | True             | (str, metadata: dict)  -- the str
                                           has the metadata narrated as an
                                           appended section

Requires OPENAI_API_KEY.

    export OPENAI_API_KEY=sk-...
    python3 answer_demo.py
"""
import asyncio

from blue_data_kb_source import MemoryKBSource

DIVIDER = "-" * 60


def on_tool_call(name: str, args: dict) -> None:
    print(f"  [tool call]   {name}({args})")


async def seed(kb: MemoryKBSource) -> str:
    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    title_prop = await kb.declare_class_property("main", "TITLE", "Job title", class_id=person)

    src = await kb.add_source("main", source="resume.pdf", name="Alice's resume", original="2020-01-15")
    alice = await kb.add_entity(
        "main", "Alice", class_id=person, description="Software engineer",
        text="Alice is a software engineer.", source_id=src,
    )
    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology company")
    await kb.add_entity_property_value(
        "main", "Senior Software Engineer", entity_id=alice, property_id=title_prop,
        text="Alice's title is Senior Software Engineer.", source_id=src,
    )
    await kb.add_fact(
        "main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at,
        text="Alice has worked at IBM since 2019.", source_id=src,
    )
    return alice


async def main():
    kb = MemoryKBSource(name="answer-demo")
    kb.create_database("main")
    await seed(kb)

    print(DIVIDER)
    print("1. Structured answer -- natural_language=False, include_metadata=False")
    print(DIVIDER)

    rows = await kb.answer("main", "Where does Alice work and what is her title?", on_tool_call=on_tool_call)
    print(f"answer() -> list[dict]: {rows}")

    print()
    print(DIVIDER)
    print("2. Structured answer + metadata -- include_metadata=True")
    print(DIVIDER)

    rows, metadata = await kb.answer(
        "main", "Where does Alice work and what is her title?", include_metadata=True
    )
    print(f"answer() -> (list[dict], metadata): rows={rows}")
    print(f"metadata is keyed by node id, one get_metadata()-shaped dict per id: "
          f"{len(metadata)} entr(y/ies)")

    print()
    print(DIVIDER)
    print("3. Prose answer -- natural_language=True")
    print(DIVIDER)

    prose = await kb.answer(
        "main", "Where does Alice work and what is her title?", natural_language=True
    )
    print(f"answer() -> str:\n{prose}")

    print()
    print(DIVIDER)
    print("4. Prose answer + metadata -- both flags set")
    print(DIVIDER)

    prose_with_metadata, metadata = await kb.answer(
        "main", "Where does Alice work and what is her title?",
        natural_language=True, include_metadata=True,
    )
    print("answer() -> (str, metadata): the str has a metadata section appended, e.g.")
    print("provenance/source/original-date facts narrated in prose, not just raw dicts:")
    print(prose_with_metadata)

    print()
    print("Demo complete.")


asyncio.run(main())
