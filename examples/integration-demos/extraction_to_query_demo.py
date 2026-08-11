"""
Extraction feeds querying -- import_text() populates the KB that answer()/
validate()/enrich() then query.

llm_extraction_demo.py only shows text-in, extraction-out. answer_demo.py/
validate_demo.py/enrich_demo.py each hand-build their own schema/data rather
than querying a KB that was itself extracted from text. This demo chains the
two: write-side LLM extraction and read-side LLM querying, against the same
KBSource instance, so the KB that gets queried is exactly what a real
"paste in a document, then ask questions about it" workflow would produce.

Requires OPENAI_API_KEY -- not live-verified in this session (no key
available in the sandboxed environment this was written in). Structurally
correct against import_text/answer/validate/enrich's signatures; run it
yourself with a real key to see live output.

    export OPENAI_API_KEY=sk-...
    python3 extraction_to_query_demo.py
"""
import asyncio

from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_kb_source import KBSource

DIVIDER = "-" * 60

TEXT = """\
Alice Johnson is a senior software engineer at IBM, where she has worked
since 2019. Before IBM, she was a backend developer at a startup called
Nimbus Analytics. IBM is a technology and consulting company headquartered
in Armonk, New York. In October 2019, IBM acquired Red Hat, an
enterprise open-source software company. Alice specializes in distributed
systems and has led IBM's internal knowledge graph initiative since 2022.
"""


class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


def on_tool_call(name: str, args: dict) -> None:
    print(f"  [tool call]   {name}({args})")


async def main():
    kb = MemoryKBSource(name="extraction-to-query-demo")
    kb.create_database("main")

    print(DIVIDER)
    print("1. Seed a minimal starting schema -- import_text extends this as needed")
    print(DIVIDER)

    await kb.declare_class("main", "PERSON", "A human individual")
    await kb.declare_class("main", "COMPANY", "A business organization")
    print("Declared PERSON, COMPANY")

    print()
    print(DIVIDER)
    print("2. import_text -- extract entities, facts, and provenance from the bio")
    print(DIVIDER)

    source_id = await kb.add_source("main", source="alice-bio.txt", name="Alice's bio")
    await kb.import_text(
        "main",
        TEXT,
        queries=["Extract every person, organization, and employment or acquisition relationship."],
        source_id=source_id,
        schema_discovery_mode="search",
        on_tool_call=on_tool_call,
    )

    rows = await kb.fetch_database_data("main", compact=True)
    print(f"\nfetch_database_data(compact=True) -> {len(rows)} rows extracted from one paragraph")

    print()
    print(DIVIDER)
    print("3. answer() -- query the KB that import_text just built")
    print(DIVIDER)

    answer = await kb.answer("main", "What company did IBM acquire, and when?", natural_language=True)
    print(f"answer(): {answer}")

    print()
    print(DIVIDER)
    print("4. validate() -- check a statement against the extracted facts")
    print(DIVIDER)

    result = await kb.validate("main", "Alice has worked at IBM since 2019.")
    print(f"validate() supporting: {result.supporting}")
    print(f"validate() counter:    {result.counter}")

    print()
    print(DIVIDER)
    print("5. enrich() -- pull additional context around Alice from the extracted graph")
    print(DIVIDER)

    results = await kb.enrich("main", "Alice")
    print(f"enrich('Alice') -> {len(results)} result(s):")
    for r in results:
        print(f"  entity={r.entity!r} relation={r.relation!r} value={r.value!r} distance={r.distance}")

    print()
    print("Demo complete.")


asyncio.run(main())
