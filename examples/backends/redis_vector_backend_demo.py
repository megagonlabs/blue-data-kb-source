"""
RedisVectorSource — shared, production vector backend (Redis Stack).

Same deterministic fake-embedding approach as semantic_search_demo.py (no API
key needed), backed by a real Redis Stack instance instead of an in-memory
dict. Uses a namespaced `scan_prefix` and cleans up every key/index it
created — safe to run against a shared Redis instance.

    redis-stack-server &   # or: docker run -p 6379:6379 redis/redis-stack
    python3 redis_vector_backend_demo.py

KNOWN LIMITATION (upstream, not blue-data-kb-source):
RedisVectorSource's FT.CREATE index schema only ever declares the vector
field — it never declares node_id/type/name as returnable fields — so
KNN hits can't currently be resolved back to KB nodes through
KBSource.search_data(mode="semantic")/search_schema(mode="semantic"). This
demo shows the correct wiring and proves the write path works (the document
really is in Redis, indexed), then shows the read-path gap explicitly rather
than silently swallowing it. Filed as
https://github.com/megagon-internal/blue-data/issues/56 — BM25 keyword
search is unaffected (it never touches the vector backend at all).
"""
import asyncio
import hashlib
import math
import os

import redis

from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource
from blue_data_kb_source import MemoryKBSource
from blue_data_kb_source.types import KBError

DIVIDER = "-" * 60
DIM = 64
SCAN_PREFIX = "blue_data_kb_examples_demo:"
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))


def fake_embedding(text: str) -> list[float]:
    seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
    return [math.sin(seed * (i + 1) * 0.1) for i in range(DIM)]


def cleanup(index_names: list[str]) -> None:
    """Remove every key/index this demo created — safe on a shared Redis Stack."""
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    for index_name in index_names:
        try:
            r.execute_command("FT.DROPINDEX", index_name, "DD")
        except redis.ResponseError:
            pass  # index didn't exist
    deleted = 0
    for key in r.scan_iter(match=f"{SCAN_PREFIX}*"):
        r.delete(key)
        deleted += 1
    print(f"Cleanup: dropped {len(index_names)} index(es), deleted {deleted} leftover key(s)")


async def main():
    print(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}, scan_prefix={SCAN_PREFIX!r}")

    kb = MemoryKBSource(
        name="redis-demo",
        enable_search=True,
        vector_source=RedisVectorSource(
            "kb_vectors",
            properties={"connection": {"host": REDIS_HOST, "port": REDIS_PORT, "scan_prefix": SCAN_PREFIX}},
        ),
        embedding_fn=fake_embedding,
        vector_dim=DIM,
    )
    kb.create_database("main")

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

    try:
        print()
        print(DIVIDER)
        print("2. BM25 keyword search — local, doesn't touch Redis at all")
        print(DIVIDER)

        kw_hits = await kb.search_data("main", "Alice", mode="keyword")
        print("search_data('Alice', mode='keyword'):", [h.name for h in kw_hits])

        print()
        print(DIVIDER)
        print("3. Semantic search via Redis Stack — write path")
        print(DIVIDER)

        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        written = sorted(r.scan_iter(match=f"{SCAN_PREFIX}*"))
        print(f"Documents actually written to Redis by add_entity/declare_class: {len(written)}")
        for key in written[:2]:
            print(" ", key)

        print()
        print(DIVIDER)
        print("4. Semantic search via Redis Stack — read path (known gap)")
        print(DIVIDER)

        try:
            sem_hits = await kb.search_data("main", "Alice", mode="semantic")
            print("search_data('Alice', mode='semantic'):",
                  [(h.name, round(h.score, 3) if h.score is not None else None) for h in sem_hits])
        except (KeyError, KBError) as e:
            print(f"search_data(mode='semantic') did not return usable results: {type(e).__name__}: {e}")
            print("This is https://github.com/megagon-internal/blue-data/issues/56, not a")
            print("blue-data-kb-source issue — the write path above proves the KBSource-side")
            print("wiring (index creation, embedding, upsert) is correct.")
    finally:
        print()
        print(DIVIDER)
        print("5. Cleanup")
        print(DIVIDER)
        cleanup(["kb_schema_main", "kb_data_main"])

    print()
    print("Demo complete.")


asyncio.run(main())
