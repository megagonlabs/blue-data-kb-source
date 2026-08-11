"""
Change hooks -- add_change_hook/remove_change_hook, event catalog, per-database scoping.

file_backend_demo.py registers one hook and prints event types as a smoke test.
This demo is the deeper look: the event catalog across schema/instance writes,
updates, and deletes; that hooks are scoped per database (a hook on "main"
never fires for events on "other"); that idempotent declare_* calls don't
re-fire; and that removal requires the exact function object you registered.
"""
import asyncio

from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_kb_source import KBSource

DIVIDER = "-" * 60


class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


async def main():
    kb = MemoryKBSource(name="hooks-demo")
    kb.create_database("main")
    kb.create_database("other")

    print(DIVIDER)
    print("1. add_change_hook -- a plain callback receiving one event dict per call")
    print(DIVIDER)

    main_events: list[dict] = []

    def on_main_change(event: dict) -> None:
        main_events.append(event)

    kb.add_change_hook("main", on_main_change)

    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    title_prop = await kb.declare_class_property("main", "TITLE", "Job title", class_id=person)
    employee = await kb.declare_class("main", "EMPLOYEE", "A person employed by a company")
    await kb.add_is_subclass_of("main", child_id=employee, parent_id=person)

    print("Schema-write event types fired so far:")
    for e in main_events:
        print(f"  {e['type']:<28} {sorted(k for k in e if k not in ('type', 'database'))}")

    print()
    print(DIVIDER)
    print("2. Idempotent declare_* calls do not re-fire")
    print(DIVIDER)

    before = len(main_events)
    await kb.declare_class("main", "PERSON", "A human individual (redeclared)")
    print(f"Redeclaring PERSON: {len(main_events) - before} new event(s) fired (expected 0)")

    print()
    print(DIVIDER)
    print("3. Instance-write events")
    print(DIVIDER)

    main_events.clear()
    alice = await kb.add_entity("main", "Alice", class_id=person, description="Software engineer")
    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology company")
    fact = await kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)
    await kb.add_entity_property_value(
        "main", "Senior Engineer", entity_id=alice, property_id=title_prop
    )
    print("Instance-write event types fired:")
    for e in main_events:
        print(f"  {e['type']:<28} {sorted(k for k in e if k not in ('type', 'database'))}")

    print()
    print(DIVIDER)
    print("4. Update and delete events")
    print(DIVIDER)

    main_events.clear()
    await kb.update_entity("main", alice, description="Staff software engineer")
    await kb.delete_entity_property_value("main", alice, title_prop)
    print("Update/delete event types fired:")
    for e in main_events:
        print(f"  {e['type']:<28} {sorted(k for k in e if k not in ('type', 'database'))}")

    print()
    print(DIVIDER)
    print("5. Hooks are scoped per database")
    print(DIVIDER)

    other_events: list[dict] = []
    kb.add_change_hook("other", lambda e: other_events.append(e))

    main_events.clear()
    await kb.declare_class("main", "PRODUCT", "A thing a company sells")
    print(f"After a write on 'main': main_events={len(main_events)}, other_events={len(other_events)}")
    print("The 'other' hook never fires for events on 'main' -- hooks are keyed by database.")

    print()
    print(DIVIDER)
    print("6. remove_change_hook requires the exact function object")
    print(DIVIDER)

    kb.remove_change_hook("main", on_main_change)
    main_events.clear()
    await kb.declare_class("main", "VENDOR", "A supplier company")
    print(f"After remove_change_hook + a write: main_events={len(main_events)} (expected 0)")

    print()
    print("Demo complete.")


asyncio.run(main())
