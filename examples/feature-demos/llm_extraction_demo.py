"""
LLM knowledge extraction -- import_text.

Give the KB free text and a model does the schema expansion, entity/fact
extraction, and provenance linking that the other feature-demos do by hand
(declare_class, add_entity(text=...), add_entity_extraction, ...). By default
import_text runs schema expansion (SchemaManager.expand_schema) first, then
extracts against the resulting schema — every write it makes is
provenance-tagged with the source text and source_id automatically, and it
reuses existing schema/entities by name instead of creating duplicates.

Requires OPENAI_API_KEY -- not live-verified in this session (no key
available in the sandboxed environment this was written in). The calls
below are structurally correct against KBSource.import_text's signature;
run it yourself with a real key to see live output.

    export OPENAI_API_KEY=sk-...
    python3 llm_extraction_demo.py
"""
import asyncio

from blue_data_kb_source import MemoryKBSource

DIVIDER = "-" * 60


TEXT = """\
Alice Johnson is a senior software engineer at IBM, where she has worked
since 2019. Before IBM, she was a backend developer at a startup called
Nimbus Analytics. IBM is a technology and consulting company headquartered
in Armonk, New York. Alice specializes in distributed systems and has led
IBM's internal knowledge graph initiative since 2022.
"""


def on_tool_call(name: str, args: dict) -> None:
    print(f"  [tool call]   {name}({args})")


def on_tool_result(name: str, args: dict, result) -> None:
    print(f"  [tool result] {name} -> {result}")


async def main():
    kb = MemoryKBSource(name="extraction-demo")
    kb.create_database("main")

    print(DIVIDER)
    print("1. Seed a minimal starting schema -- import_text reuses these by name")
    print(DIVIDER)

    await kb.declare_class("main", "PERSON", "A human individual")
    await kb.declare_class("main", "COMPANY", "A business organization")
    print("Declared PERSON, COMPANY -- import_text's schema-expansion pass can extend")
    print("this schema as needed (e.g. declaring WORKED_AT itself) before extracting")

    print()
    print(DIVIDER)
    print("2. import_text -- one call, two passes (expand schema, then extract)")
    print(DIVIDER)

    source_id = await kb.add_source("main", source="alice-bio.txt", name="Alice's bio")

    await kb.import_text(
        "main",
        TEXT,
        queries=["Extract every person, organization, and employment relationship mentioned."],
        model="gpt-4o",
        source_id=source_id,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )
    print("import_text returns None -- its effects are the tool calls printed above:")
    print("first declare_class/declare_relation_type (schema expansion), then")
    print("add_entity/add_fact (extraction), each tagged with text=<the sentence it")
    print("came from>, source_id=<alice-bio.txt>.")

    print()
    print(DIVIDER)
    print("3. Inspect what got extracted")
    print(DIVIDER)

    rows = await kb.fetch_database_data("main", compact=True)
    print(f"fetch_database_data(compact=True) -> {len(rows)} rows:")
    for r in rows:
        print(" ", r)

    hits = await kb.search_data("main", "Alice")
    if hits:
        meta = await kb.get_metadata("main", hits[0].id)
        print(f"get_metadata on the extracted Alice entity: {meta}")

    print()
    print("Demo complete.")


asyncio.run(main())
