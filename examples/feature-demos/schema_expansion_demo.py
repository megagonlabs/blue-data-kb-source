"""
LLM schema expansion -- expand_schema.

Growing the schema (CLASS/RELATION/PROPERTY) is a separate LLM pass from
extracting entities/facts (import_text) -- see llm_extraction_demo.py for
that half. expand_schema never creates entities, facts, or property values;
it only ever declares schema, grounded by a text passage, and is what
import_text runs first by default (expand_schema=True) before its own
extraction pass. Called directly here, standalone, the way a developer would
use it to bootstrap or grow a schema ahead of any document ingestion --
e.g. from a glossary, a spec document, or a batch of sample text.

This demo walks through the model's two most important disciplines:

  1. CLASS vs. entity -- a mention of a specific named individual ("Alice",
     "IBM") must never become a CLASS of its own (no CLASS ALICE); only the
     general kind it belongs to (PERSON, COMPANY) gets declared here. The
     individual itself is created later, as an entity, by import_text.
  2. Hierarchy vs. relation -- an is-a/type-of/kind-of link is wired with
     add_subclass_of, never declared as a RELATION like IS_A or TYPE_OF.
     declare_relation_type rejects a taxonomic name outright.

It also shows dedup_mode (avoiding near-duplicate schema elements) and
attach_property (reusing an existing property on another class/relation
instead of declaring a same-meaning duplicate).

Requires OPENAI_API_KEY -- not live-verified in this session (no key
available in the sandboxed environment this was written in). The calls
below are structurally correct against KBSource.expand_schema's signature;
run it yourself with a real key to see live output.

    export OPENAI_API_KEY=sk-...
    python3 schema_expansion_demo.py
"""
import asyncio

from blue_data_kb_source import MemoryKBSource

DIVIDER = "-" * 60


def on_tool_call(name: str, args: dict) -> None:
    print(f"  [tool call]   {name}({args})")


def on_tool_result(name: str, args: dict, result) -> None:
    print(f"  [tool result] {name} -> {result}")


async def main():
    kb = MemoryKBSource(name="schema-expansion-demo")
    kb.create_database("main")

    print(DIVIDER)
    print("1. expand_schema -- grow the schema from a text passage, nothing")
    print("   entered by hand first")
    print(DIVIDER)

    text = (
        "Alice Johnson is a senior software engineer at IBM. She has "
        "programming skills in distributed systems and enjoys woodworking "
        "as a hobby outside of work."
    )
    print(f"Text: {text!r}\n")
    print("Expect: general classes like PERSON/COMPANY plus relations connecting")
    print("them (WORKED_AT and similar) -- but never a CLASS named ALICE or IBM;")
    print("those are entities, created later by import_text. Exact names and how")
    print("finely the model subdivides SKILL/HOBBY are the model's own choice,")
    print("not scripted -- that's the point of an LLM-driven pass.")
    print()

    await kb.expand_schema(
        "main",
        text,
        depth=1,           # also propose one hierarchy level beyond what's evidenced
        dedup_mode="search",
        model="gpt-4o",
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )

    schema = await kb.fetch_database_schema("main")
    print(f"\nSchema after expansion: {len(schema.entities)} classes, "
          f"{len(schema.relations)} relations")
    for name in schema.entities:
        print(f"  CLASS    {name}")
    for rel in schema.relations.values():
        print(f"  RELATION {rel['name']}  ({rel['source']} -> {rel['target']})")

    print()
    print(DIVIDER)
    print("2. expand_schema again -- a second, related passage. dedup_mode")
    print("   keeps this convergent instead of piling up near-duplicates")
    print(DIVIDER)

    text2 = (
        "IBM later acquired Nimbus Analytics, a smaller technology "
        "consulting firm."
    )
    print(f"Text: {text2!r}")
    print("Expect: still no CLASS named IBM or NIMBUS_ANALYTICS -- IBM and Nimbus")
    print("Analytics are entities of whatever general class fits (COMPANY, or a")
    print("more specific subclass of it the model chooses to declare). A new")
    print("ACQUIRED relation is likely, unless dedup_mode judges an existing one")
    print("already covers 'acquired'.")
    print()

    await kb.expand_schema(
        "main", text2, dedup_mode="search",
        on_tool_call=on_tool_call, on_tool_result=on_tool_result,
    )

    print()
    print(DIVIDER)
    print("3. attach_property -- reuse an existing property on another class")
    print("   instead of declaring a same-meaning duplicate under a new name")
    print(DIVIDER)

    # Look the PERSON/COMPANY class ids up via search_schema (the same tool
    # expand_schema's own agent uses internally) rather than assuming shape:
    person_hits = await kb.search_schema("main", "PERSON", type_filter="CLASS")
    company_hits = await kb.search_schema("main", "COMPANY", type_filter="CLASS")
    if person_hits and company_hits:
        name_prop = await kb.declare_class_property(
            "main", name="NAME", description="Name of the individual",
            class_id=person_hits[0].id,
        )
        await kb.attach_property("main", target_id=company_hits[0].id, property_id=name_prop)
        print(f"Declared NAME on PERSON ({name_prop}), then attached the same")
        print("property to COMPANY -- one PROPERTY node, two DEFINE_PROPERTY edges,")
        print("instead of a second same-meaning property under a new name.")

    print()
    print(DIVIDER)
    print("4. Hierarchy is add_subclass_of, never a declared relation")
    print(DIVIDER)

    employee_id = await kb.declare_class("main", "EMPLOYEE", "A person employed by an organization")
    if person_hits:
        await kb.add_is_subclass_of("main", employee_id, person_hits[0].id)
        print("EMPLOYEE IS_SUBCLASS_OF PERSON -- wired directly, not via a")
        print("declared IS_A/TYPE_OF relation (declare_relation_type rejects those names).")

    print()
    print("Demo complete. Next: llm_extraction_demo.py runs import_text against")
    print("a schema like this one (expand_schema=True by default, or pass")
    print("expand_schema=False to extract only, reusing what's already here).")


asyncio.run(main())
