"""
Natural language querying -- validate().

validate() checks a free-text statement against the KB and returns a
ValidationResult(supporting: list[dict], counter: list[dict]) -- facts that
support the statement and facts that contradict it. Like answer(), it also
takes natural_language and include_metadata (see answer_demo.py for how
those two flags combine); validate() has no separate "confidence score",
the supporting/counter fact lists are the evidence.

Requires OPENAI_API_KEY -- not live-verified in this session (no key
available in the sandboxed environment this was written in). The calls
below are structurally correct against KnowledgeManager.validate's
signature; run it yourself with a real key to see live output.

    export OPENAI_API_KEY=sk-...
    python3 validate_demo.py
"""
import asyncio

from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_kb_source import KBSource

DIVIDER = "-" * 60


class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


async def seed(kb: MemoryKBSource) -> None:
    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    src = await kb.add_source("main", source="resume.pdf", name="Alice's resume")
    alice = await kb.add_entity(
        "main", "Alice", class_id=person, description="Software engineer",
        text="Alice is a software engineer.", source_id=src,
    )
    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology company")
    await kb.add_fact(
        "main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at,
        text="Alice has worked at IBM since 2019.", source_id=src,
    )


async def main():
    kb = MemoryKBSource(name="validate-demo")
    kb.create_database("main")
    await seed(kb)

    print(DIVIDER)
    print("1. A statement the KB supports")
    print(DIVIDER)

    result = await kb.validate("main", "Alice works at IBM.")
    print(f"supporting: {result.supporting}")
    print(f"counter:    {result.counter}")

    print()
    print(DIVIDER)
    print("2. A statement the KB contradicts")
    print(DIVIDER)

    result = await kb.validate("main", "Alice works at Microsoft.")
    print(f"supporting: {result.supporting}")
    print(f"counter:    {result.counter}")

    print()
    print(DIVIDER)
    print("3. natural_language=True -- prose verdict instead of a ValidationResult")
    print(DIVIDER)

    prose = await kb.validate("main", "Alice works at IBM.", natural_language=True)
    print(f"validate(..., natural_language=True) -> str:\n{prose}")

    print()
    print(DIVIDER)
    print("4. include_metadata=True -- provenance alongside the verdict")
    print(DIVIDER)

    result, metadata = await kb.validate("main", "Alice works at IBM.", include_metadata=True)
    print(f"supporting: {result.supporting}")
    print(f"metadata keyed by node id: {len(metadata)} entr(y/ies)")

    print()
    print("Demo complete.")


asyncio.run(main())
