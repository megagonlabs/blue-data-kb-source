"""
Neo4j-backed KBSource demo — the production graph backend.

Same schema/data/query shape as memory_quickstart.py and file_backend_demo.py;
the only thing that changes is the GraphSource mixed into KBSource. NEO4JKBSource
lives in its own blue_data_kb_source.neo4j subpackage rather than the top-level
package — importing it (as below) is what pulls in the neo4j driver; plain
`import blue_data_kb_source` never does, so installs that don't use Neo4j don't
need the driver installed. Requires a running Neo4j instance (Community or
Enterprise — create_database degrades gracefully on Community, where multiple
databases aren't supported).

    docker run -p 7687:7687 -p 7474:7474 -e NEO4J_AUTH=neo4j/<password> neo4j

Configure the connection via environment variables:

    export NEO4J_URI=bolt://localhost:7687
    export NEO4J_USER=neo4j
    export NEO4J_PASSWORD=<password>
    python3 neo4j_backend_demo.py
"""
import asyncio
import os
from urllib.parse import urlparse

from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource

DIVIDER = "-" * 60


def _connection_from_env() -> dict:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    parsed = urlparse(uri)
    return {
        "protocol": parsed.scheme or "bolt",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 7687,
        "user": os.environ.get("NEO4J_USER", "neo4j"),
        "password": os.environ.get("NEO4J_PASSWORD", "neo4j"),
    }


async def main():
    connection = _connection_from_env()
    print(f"Connecting to Neo4j at {connection['protocol']}://{connection['host']}:{connection['port']}")

    kb = NEO4JKBSource(name="kb", properties={"connection": connection})

    print()
    print(DIVIDER)
    print("1. Create database, declare schema")
    print(DIVIDER)

    result = kb.create_database("kb_example", overwrite=True)
    print("create_database result:", result)

    person = await kb.declare_class("kb_example", "PERSON", "A human individual")
    company = await kb.declare_class("kb_example", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "kb_example", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    print("Declared PERSON, COMPANY, WORKED_AT")

    print()
    print(DIVIDER)
    print("2. Add data")
    print(DIVIDER)

    alice = await kb.add_entity("kb_example", "Alice", class_id=person)
    ibm = await kb.add_entity("kb_example", "IBM", class_id=company)
    await kb.add_fact("kb_example", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)
    print("Added Alice, IBM, and the WORKED_AT fact between them")

    print()
    print(DIVIDER)
    print("3. Query")
    print(DIVIDER)

    rows = await kb.fetch_database_data("kb_example", compact=True)
    print(f"fetch_database_data → {len(rows)} rows:")
    for r in rows:
        print(" ", r)

    stats = kb.fetch_database_stats("kb_example")
    print("Neo4j-reported stats:", stats)

    print()
    print("Demo complete. (Database 'kb_example' left in place — rerun with overwrite to reset it.)")


asyncio.run(main())
