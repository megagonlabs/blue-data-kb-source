"""
Semantic (vector) search — MemoryVectorSource + a deterministic fake embedding.

The embedding function here is a hash of the text, not a real model — this
demo is about the *wiring*, not embedding quality, so it needs no API key and
no external service. See chromadb_vector_backend_demo.py / redis_vector_backend_demo.py
for the two production vector backends, wired the same way.

Swapping vector backends is a one-line change to properties["search"]["vector"]'s
`_class` (KBSource.__init__ constructs it via DataSource.instantiate() — see
design/06-library.md):

    "blue_data.source.vector.memory_vector_source.MemoryVectorSource"           # dev/test, this demo
    "blue_data.source.vector.file_vector_source.FileVectorSource"               # persistent, single-process
    "blue_data.source.vector.chromadb.chromadb_vector_source.ChromaDBVectorSource"  # embedded, persistent
    "blue_data.source.vector.redis.redis_vector_source.RedisVectorSource"       # shared, production

Everything downstream (`search_schema`/`search_data(mode="semantic")`, and the
`answer`/`validate`/`enrich` agents' search tools) is identical regardless of
which one is plugged in.
"""
import asyncio
import hashlib
import math

from blue_data_kb_source import MemoryKBSource

DIVIDER = "-" * 60
DIM = 64


def fake_embedding(text: str) -> list[float]:
    """Deterministic, model-free 'embedding' — same text always maps to the
    same vector, and it's cheap enough to call for every node. Not remotely
    meaningful semantically; only good enough to demonstrate the plumbing."""
    seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
    return [math.sin(seed * (i + 1) * 0.1) for i in range(DIM)]


async def main():
    kb = MemoryKBSource(
        name="semantic",
        properties={
            "search": {
                "mode": "semantic",
                "vector_dim": DIM,
                # Declarative: a dotted _class path, constructed internally via
                # DataSource.instantiate() -- swap this string to change backend.
                "vector": {
                    "_class": "blue_data.source.vector.memory_vector_source.MemoryVectorSource",
                    "name": "kb_vectors",
                    "connection": {},
                },
                # embedding_fn is a live callable, not a DataSource -- it isn't
                # something a "_class" path can build, so it's passed directly.
                "embedding_fn": fake_embedding,
            }
        },
    )
    kb.create_database("main")
    print("has_semantic_search:", kb.has_semantic_search)

    print()
    print(DIVIDER)
    print("1. Seed schema and data")
    print(DIVIDER)

    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )

    alice = await kb.add_entity("main", "Alice", class_id=person, description="Software engineer")
    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology and consulting company")
    await kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at)
    print("Declared PERSON/COMPANY/WORKED_AT, added Alice, IBM, and their fact")

    print()
    print(DIVIDER)
    print("2. keyword vs semantic search")
    print(DIVIDER)

    kw_hits = await kb.search_data("main", "Alice", mode="keyword")
    print("search_data('Alice', mode='keyword'):", [(h.name, round(h.score, 3) if h.score is not None else None) for h in kw_hits])

    sem_hits = await kb.search_data("main", "Alice", mode="semantic")
    print("search_data('Alice', mode='semantic'):", [(h.name, round(h.score, 3) if h.score is not None else None) for h in sem_hits])

    sem_schema_hits = await kb.search_schema("main", "employment", mode="semantic")
    print("search_schema('employment', mode='semantic'):", [(h.name, round(h.score, 3) if h.score is not None else None) for h in sem_schema_hits])

    print()
    print("Demo complete.")


asyncio.run(main())
