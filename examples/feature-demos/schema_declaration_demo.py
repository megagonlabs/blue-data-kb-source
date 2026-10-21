"""
Schema declaration — idempotency, hierarchy, and consolidation.

Covers the parts of the schema API that backends/*.py only touch in
passing: declare_* idempotency semantics, class/relation hierarchy via
add_is_subclass_of, property scoping by class_id, and consolidate_schema's
"merge" operation for redirecting instances from one schema element to
another.
"""
import asyncio

from blue_data.core.schema import DataSchema
from blue_data_kb_source import MemoryKBSource
from blue_data_kb_source.types import SchemaError

DIVIDER = "-" * 60


async def main():
    kb = MemoryKBSource(name="schema-demo")
    kb.create_database("main")

    print(DIVIDER)
    print("1. declare_class is idempotent by name")
    print(DIVIDER)

    id1 = await kb.declare_class("main", "PERSON", "A human individual")
    id2 = await kb.declare_class("main", "PERSON", "This description is ignored")
    print(f"declare_class('PERSON', ...) twice -> same id: {id1 == id2} ({id1})")
    schema = DataSchema.from_json(kb.fetch_database_schema("main"))
    person_desc = schema.entities["PERSON"]["description"]
    print(f"Description is still the first one: {person_desc!r}")

    print()
    print(DIVIDER)
    print("2. declare_relation_type requires domain/range by class name")
    print(DIVIDER)

    company_id = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    worked_at_again = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain=["PERSON"], range=["COMPANY"]
    )
    print(f"declare_relation_type('WORKED_AT', ...) twice -> same id: {worked_at == worked_at_again}")

    print()
    print(DIVIDER)
    print("3. Class hierarchy via add_is_subclass_of")
    print(DIVIDER)

    employee = await kb.declare_class("main", "EMPLOYEE", "A person employed by a company")
    person = id1
    await kb.add_is_subclass_of("main", child_id=employee, parent_id=person)
    schema = DataSchema.from_json(kb.fetch_database_schema("main"))
    print(f"EMPLOYEE is_subclass_of PERSON: {schema.has_relation('EMPLOYEE', 'IS_SUBCLASS_OF', 'PERSON')}")
    await kb.add_is_subclass_of("main", child_id=employee, parent_id=person)
    parents = kb.get_neighbors("main", employee, edge_type="IS_SUBCLASS_OF", direction="out")
    print(f"add_is_subclass_of twice -> still one edge: {len(parents) == 1}")
    for child, parent, label in [(person, person, "PERSON -> PERSON"),
                                 (person, employee, "PERSON -> EMPLOYEE")]:
        try:
            await kb.add_is_subclass_of("main", child_id=child, parent_id=parent)
        except SchemaError as exc:
            print(f"{label} rejected: {exc}")

    print()
    print(DIVIDER)
    print("4. declare_class_property is scoped by class_id, not global")
    print(DIVIDER)

    name_on_person = await kb.declare_class_property(
        "main", "NAME", "Given name", class_id=person
    )
    name_on_company = await kb.declare_class_property(
        "main", "NAME", "Trading name", class_id=company_id
    )
    print(f"Two different PROPERTY nodes both named 'NAME': {name_on_person != name_on_company}")

    print()
    print(DIVIDER)
    print("5. consolidate_schema('merge') folds the source class into the target")
    print(DIVIDER)

    organization = await kb.declare_class("main", "ORGANIZATION", "A generic organizational entity")
    acme = await kb.add_entity("main", "Acme Corp", class_id=organization, description="A generic org")

    before = await kb.get_metadata("main", acme)
    print(f"Before merge, Acme Corp's class_name: {before['class_name']!r}")

    await kb.consolidate_schema(
        "main", operation="merge", source_names=["ORGANIZATION"], target_name="COMPANY"
    )

    # get_neighbors is the underlying GraphSource's own inspection method (KBSource mixes
    # in a GraphSource directly) -- shown here to make it visible that the merge leaves
    # exactly one class edge behind rather than adding a second one.
    classes = [
        nb["node"]["properties"]["name"]
        for nb in kb.get_neighbors("main", acme, edge_type="IS_INSTANCE_OF", direction="out")
    ]
    print(f"Acme Corp's IS_INSTANCE_OF edges after merge: {classes}")
    after = await kb.get_metadata("main", acme)
    print(f"get_metadata reports the merge target: class_name={after['class_name']!r}")
    survivors = [c["name"] for c in DataSchema.from_json(kb.fetch_database_schema("main")).entities.values()]
    print(f"ORGANIZATION is gone from the schema: {sorted(survivors)}")
    print("consolidate_schema('merge') re-points every edge that referenced the source")
    print("class -- instances, subclass links, domain/range, property definitions -- at the")
    print("target, then deletes the source node. Change events are fired for each affected")
    print("node, so a search-enabled KB stops returning the merged-away class as well.")

    print()
    print("Demo complete.")


asyncio.run(main())
