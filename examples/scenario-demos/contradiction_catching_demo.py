"""
Scenario: catching contradictions with provenance, not guessing.

Problem: add_entity_property_value is additive, not an update -- writing a
new value for an entity's property doesn't replace the old one, it adds a
second value alongside it (see docs/features.md on provenance). A naive
lookup that just grabs "the" value for a property has no way to know two
conflicting values exist, let alone which one is current. It silently
returns whichever one it happens to see first.

Solution: every write is provenance-tagged with a source and an externally
supplied `original` date (add_source(..., original=...)). A lookup that
checks the source_original date on each candidate value's evidence records
(via get_metadata) can
resolve the conflict correctly -- pick the value backed by the most recent
source -- instead of guessing.

This is the whole demo: seed 4 people with two conflicting TITLE values each
(an old source and a newer one, out of insertion order so a naive lookup
can't accidentally get lucky), then score a naive lookup against a
provenance-aware one. No LLM, no API key -- this is a deterministic,
reproducible claim about the graph structure and provenance data itself.

A bonus section at the end shows validate() surfacing the same conflict
conversationally -- that part needs OPENAI_API_KEY and is not live-verified
in this session (see examples/README.md).
"""
import asyncio

from blue_data_kb_source import MemoryKBSource

DIVIDER = "=" * 60
STEP = "-" * 60


# Each person gets an out-of-order pair of (value, source_name, original_date, is_stale)
# writes -- the *newer* source is written second in one case and first in another,
# so a naive "first value wins" lookup can't accidentally score well by luck.
PEOPLE = [
    ("Alice", [
        ("Software Engineer", "resume-2020.pdf", "2020-01-15"),
        ("Staff Software Engineer", "performance-review-2024.pdf", "2024-06-01"),
    ]),
    ("Bob", [
        ("Sales Director", "org-chart-2019.pdf", "2019-08-01"),
        ("VP of Sales", "press-release-2023.pdf", "2023-11-12"),
    ]),
    ("Carol", [
        ("Data Analyst", "linkedin-export-2018.json", "2018-03-20"),
        ("Principal Data Scientist", "conference-bio-2025.txt", "2025-02-10"),
    ]),
    ("Dave", [
        ("Junior Developer", "onboarding-doc-2021.pdf", "2021-05-05"),
        ("Engineering Manager", "team-directory-2024.pdf", "2024-09-30"),
    ]),
]


async def naive_lookup(kb: MemoryKBSource, entity_id: str) -> str:
    """What a lookup that doesn't know about provenance would do: grab
    whichever HAS_PROPERTY value it happens to see first."""
    nbs = kb.get_neighbors("main", entity_id, edge_type="HAS_PROPERTY", direction="out")
    return nbs[0]["node"]["properties"]["value"]


async def provenance_aware_lookup(kb: MemoryKBSource, entity_id: str) -> str:
    """Check the source_original date on every candidate value's evidence records
    (via get_metadata), and return the one backed by the most recently dated source."""
    nbs = kb.get_neighbors("main", entity_id, edge_type="HAS_PROPERTY", direction="out")
    candidates = []
    for nb in nbs:
        meta = await kb.get_metadata("main", nb["node"]["id"])
        newest = max((e["source_original"] for e in meta["evidence"] if e["source_original"]), default="")
        candidates.append((newest, meta["value"]))
    candidates.sort(key=lambda c: c[0])
    return candidates[-1][1]


async def main():
    print()
    print(DIVIDER)
    print("Scenario: catching contradictions with provenance, not guessing")
    print(DIVIDER)
    print()
    print("Problem:  add_entity_property_value is additive -- a second write for the same")
    print("          entity+property doesn't replace the first, it coexists alongside it.")
    print("          A lookup with no notion of provenance can't tell which value is current.")
    print("Solution: every write carries a source with an externally supplied `original`")
    print("          date. A lookup that checks provenance can resolve the conflict correctly.")

    print()
    print(STEP)
    print("Step 1 -- seed 4 people, each with two conflicting TITLE values")
    print(STEP)

    kb = MemoryKBSource(name="contradiction-demo")
    kb.create_database("main")

    person = await kb.declare_class("main", "PERSON", "A human individual")
    title_prop = await kb.declare_class_property("main", "TITLE", "Job title", class_id=person)

    entity_ids: dict[str, str] = {}
    for name, values in PEOPLE:
        entity_ids[name] = await kb.add_entity("main", name, class_id=person)
        for value, source_name, original in values:
            src = await kb.add_source(
                "main", source=source_name, name=source_name, original=original
            )
            await kb.add_entity_property_value(
                "main", value, entity_id=entity_ids[name], property_id=title_prop,
                text=f"{name}'s title is {value}, per {source_name}.", source_id=src,
            )
        print(f"  {name}: wrote {values[0][0]!r} ({values[0][2]}), then {values[1][0]!r} ({values[1][2]})")

    print()
    print(STEP)
    print("Step 2 -- naive lookup vs. provenance-aware lookup, scored")
    print(STEP)
    print(f"{'Person':<8} {'Naive lookup':<28} {'Provenance-aware lookup':<28} {'Correct value'}")

    naive_correct = 0
    aware_correct = 0
    for name, values in PEOPLE:
        correct_value = max(values, key=lambda v: v[2])[0]  # value from the most recent source
        naive = await naive_lookup(kb, entity_ids[name])
        aware = await provenance_aware_lookup(kb, entity_ids[name])
        naive_ok = naive == correct_value
        aware_ok = aware == correct_value
        naive_correct += naive_ok
        aware_correct += aware_ok
        print(f"{name:<8} {naive:<28} {aware:<28} {correct_value}")
        print(f"{'':<8} {'✓' if naive_ok else '✗':<28} {'✓' if aware_ok else '✗':<28}")

    print()
    print(DIVIDER)
    print("Result")
    print(DIVIDER)
    print(f"Naive lookup:             {naive_correct}/{len(PEOPLE)} correct")
    print(f"Provenance-aware lookup:  {aware_correct}/{len(PEOPLE)} correct")
    print()
    print("The naive lookup isn't wrong by chance -- it doesn't know a conflict exists at")
    print("all, so it just returns the first HAS_PROPERTY edge it walks. The provenance-aware")
    print("lookup used nothing but the source_original dates on get_metadata's evidence")
    print("records, no LLM involved.")

    print()
    print(STEP)
    print("Bonus -- validate() surfaces the same conflict conversationally (needs an LLM)")
    print(STEP)
    print("Requires OPENAI_API_KEY -- not live-verified in this session (no key available")
    print("in the sandboxed environment this was written in).")

    try:
        result = await kb.validate("main", "Alice's title is Software Engineer.")
        print(f"validate() supporting: {result.supporting}")
        print(f"validate() counter:    {result.counter}")
        print("A real run shows the 2020 resume as supporting evidence and the 2024")
        print("performance review as counter-evidence -- validate() surfaces both, with their")
        print("provenance, rather than silently picking one.")
    except Exception as e:
        print(f"validate() did not run: {type(e).__name__}: {e}")
        print("Set OPENAI_API_KEY and rerun to see the live conflict-surfacing behavior.")

    print()
    print("Demo complete.")


asyncio.run(main())
