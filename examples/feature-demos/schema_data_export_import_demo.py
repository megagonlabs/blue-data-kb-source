"""
Schema/data export-import -- migrating a KB across backends.

file_backend_demo.py already shows export/import into a second database on
the *same* FileKBSource instance. This demo does the other half: migrating
from one backend to a completely different one (MemoryKBSource -> a
FileKBSource on disk), and the one real gotcha in this API -- schema import
is safely idempotent by name, but data import is NOT: entities/facts get new
ids on every import call, so importing the same export twice duplicates
everything.
"""
import asyncio
import shutil
import tempfile

from blue_data.source.graph.file_graph_source import FileGraphSource
from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_kb_source import KBSource

DIVIDER = "-" * 60


class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


class FileKBSource(KBSource, FileGraphSource):
    pass


async def main():
    tmp = tempfile.mkdtemp(prefix="blue-data-kb-migration-")
    print(f"File backend directory: {tmp}")

    print()
    print(DIVIDER)
    print("1. Seed a MemoryKBSource with schema, data, and provenance")
    print(DIVIDER)

    source_kb = MemoryKBSource(name="source")
    source_kb.create_database("main")

    person = await source_kb.declare_class("main", "PERSON", "A human individual")
    company = await source_kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await source_kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    src = await source_kb.add_source("main", source="resume.pdf", name="Alice's resume")
    alice = await source_kb.add_entity(
        "main", "Alice", class_id=person, description="Software engineer",
        text="Alice is a software engineer.", source_id=src,
    )
    ibm = await source_kb.add_entity("main", "IBM", class_id=company, description="Technology company")
    await source_kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)
    print("Declared PERSON/COMPANY/WORKED_AT and added Alice, IBM, and their fact")

    print()
    print(DIVIDER)
    print("2. Migrate the schema -- import is idempotent by name, safe to repeat")
    print(DIVIDER)

    target_kb = FileKBSource(name="target", directory=tmp)
    target_kb.create_database("migrated")

    schema_json = await source_kb.export_database_schema("main")
    print(f"export_database_schema returned {len(schema_json.get('entities', {}))} class(es)")
    await target_kb.import_database_schema("migrated", schema_json)
    await target_kb.import_database_schema("migrated", schema_json)  # repeat on purpose
    target_schema = await target_kb.fetch_database_schema("migrated")
    print(f"Importing the same schema twice is safe: has_entity('PERSON')="
          f"{target_schema.has_entity('PERSON')}, has_entity('COMPANY')="
          f"{target_schema.has_entity('COMPANY')} -- no duplicate PERSON/COMPANY classes")

    print()
    print(DIVIDER)
    print("3. Migrate the data")
    print(DIVIDER)

    data_json = await source_kb.export_database_data("main", include_provenance=True)
    print(f"export_database_data: {len(data_json['entities'])} entities, {len(data_json['facts'])} facts")
    print(f"Each entity carries a 'ref' join key, e.g.: "
          f"{ {k: v for k, v in data_json['entities'][0].items() if k in ('ref', 'name', 'class')} }")

    await target_kb.import_database_data("migrated", data_json)
    rows = await target_kb.fetch_database_data("migrated", compact=True)
    print(f"Migrated KB now has {len(rows)} graph rows via fetch_database_data(compact=True)")

    print()
    print(DIVIDER)
    print("4. Gotcha: import_database_data is NOT idempotent -- it always creates new nodes")
    print(DIVIDER)

    await target_kb.import_database_data("migrated", data_json)
    rows_after_second_import = await target_kb.fetch_database_data("migrated", compact=True)
    print(f"Importing the same export a second time: {len(rows_after_second_import)} graph rows "
          f"(was {len(rows)}) -- entities and facts are duplicated, unlike schema import.")
    print("Unlike declare_class/declare_relation_type (idempotent by name), add_entity/add_fact")
    print("never dedupe -- import_database_data mints brand-new ids on every call. Only import")
    print("a given export once, or track what you've already migrated.")

    shutil.rmtree(tmp)
    print()
    print(f"Cleaned up {tmp}")
    print("Demo complete.")


asyncio.run(main())
