"""
FileGraphSource end-to-end demo.

Covers the full operational shape of the file-backed KBSource:
  1. Declare a schema and add data with provenance
  2. Persistence — a second KBSource instance pointed at the same directory
     sees everything the first one wrote, with no explicit save step
  3. Change hooks
  4. get_metadata — created/modified/provenance for one item
  5. Schema export / import — into a second database on the same backend
  6. Data export / import — same, after the schema

Run from examples/backends/:
    python3 file_backend_demo.py
"""
import asyncio
import shutil
import tempfile

from blue_data_kb_source import FileKBSource

DIVIDER = "-" * 60


async def main():
    tmp = tempfile.mkdtemp(prefix="blue-data-kb-demo-")
    print(f"Working directory: {tmp}")

    # ---------------------------------------------------------------- #
    # 1. Declare a schema and add data with provenance
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("1. Declare schema, add data with provenance")
    print(DIVIDER)

    kb = FileKBSource(name="kb", properties={"connection": {"directory": tmp}})
    kb.create_database("main")

    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    age_prop = await kb.declare_class_property("main", "AGE", "Age in years", class_id=person)

    src = await kb.add_source("main", "resume_alice", name="Alice's résumé", original="2020-01-15")
    alice = await kb.add_entity(
        "main", "Alice", class_id=person, text="Alice is a software engineer.", source_id=src
    )
    ibm = await kb.add_entity("main", "IBM", class_id=company)
    fact = await kb.add_fact(
        "main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at,
        text="Alice joined IBM in June 1998.", source_id=src,
    )
    await kb.add_entity_property_value("main", 30, entity_id=alice, property_id=age_prop)

    rows = await kb.fetch_database_data("main", compact=True)
    print(f"Wrote {len(rows)} compact rows; entities:",
          [r["entity_name"] for r in rows if r.get("relation_type") == "IS_INSTANCE_OF"])

    # ---------------------------------------------------------------- #
    # 2. Persistence — a fresh instance sees the same data, no save step
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("2. Persistence — reopen the same directory")
    print(DIVIDER)

    kb2 = FileKBSource(name="kb", properties={"connection": {"directory": tmp}})
    rows2 = await kb2.fetch_database_data("main", compact=True)
    print(f"Reopened KB sees {len(rows2)} rows without any explicit save/load call")
    entities = sorted(r["entity_name"] for r in rows2 if r.get("relation_type") == "IS_INSTANCE_OF")
    print("Entities visible after reopen:", entities)

    # ---------------------------------------------------------------- #
    # 3. Change hooks
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("3. Change hooks")
    print(DIVIDER)

    seen = []

    def on_change(event: dict) -> None:
        seen.append(event["type"])
        if event["type"] == "entity_added":
            print(f"  hook: entity {event['name']!r} (class: {event['class_name']})")

    kb2.add_change_hook("main", on_change)
    await kb2.add_entity("main", "Google", class_id=company)
    await kb2.add_entity("main", "Bob", class_id=person)
    kb2.remove_change_hook("main", on_change)
    print("Hook fired for event types:", seen)

    # ---------------------------------------------------------------- #
    # 4. get_metadata — created/modified/provenance for one item
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("4. get_metadata")
    print(DIVIDER)

    meta = await kb2.get_metadata("main", alice)
    print("Alice's metadata:")
    for k in ("type", "name", "class_name", "created", "modified", "evidence"):
        print(f"  {k}: {meta.get(k)}")

    # ---------------------------------------------------------------- #
    # 5. Schema export / import — into a second database
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("5. Schema export / import")
    print(DIVIDER)

    schema_json = await kb2.export_database_schema("main")
    print(f"Exported schema: {len(schema_json.get('entities', {}))} classes, "
          f"{len(schema_json.get('relations', {}))} relation entries")

    kb2.create_database("copy")
    await kb2.import_database_schema("copy", schema_json)
    copy_schema = await kb2.fetch_database_schema("copy")
    print("Classes in 'copy' after import:", sorted(copy_schema.entities.keys()))

    # ---------------------------------------------------------------- #
    # 6. Data export / import — after the schema
    # ---------------------------------------------------------------- #
    print()
    print(DIVIDER)
    print("6. Data export / import")
    print(DIVIDER)

    data_json = await kb2.export_database_data("main", include_provenance=True)
    print(f"Exported data: {len(data_json['entities'])} entities, {len(data_json['facts'])} facts, "
          f"{len(data_json['entity_properties'])} entity property values")

    await kb2.import_database_data("copy", data_json)
    copy_rows = await kb2.fetch_database_data("copy", compact=True)
    print(f"'copy' now has {len(copy_rows)} rows after data import")

    shutil.rmtree(tmp)
    print()
    print(f"Cleaned up {tmp}")
    print("Demo complete.")


asyncio.run(main())
