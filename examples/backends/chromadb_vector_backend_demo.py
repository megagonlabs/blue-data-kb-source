"""
ChromaDBVectorSource — embedded, persistent vector backend.

Same deterministic fake-embedding approach as semantic_search_demo.py (no API
key needed) but backed by a real ChromaDB instance persisted to disk instead
of an in-memory dict — no server to start, ChromaDB runs embedded in-process.
Paired with FileGraphSource so the whole demo — graph and vector index alike —
survives a reconnect, not just the vector side.

    pip install chromadb
    python3 chromadb_vector_backend_demo.py
"""
import asyncio
import hashlib
import math
import os
import shutil
import tempfile

from blue_data.source.graph.file_graph_source import FileGraphSource
from blue_data.source.vector.chromadb.chromadb_vector_source import ChromaDBVectorSource
from blue_data_kb_source import KBSource

DIVIDER = "-" * 60
DIM = 64


class FileKBSource(KBSource, FileGraphSource):
    pass


def fake_embedding(text: str) -> list[float]:
    seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
    return [math.sin(seed * (i + 1) * 0.1) for i in range(DIM)]


def build_kb(name: str, graph_dir: str, chroma_dir: str) -> FileKBSource:
    return FileKBSource(
        name=name,
        directory=graph_dir,
        enable_search=True,
        vector_source=ChromaDBVectorSource("kb_vectors", path=chroma_dir),
        embedding_fn=fake_embedding,
        vector_dim=DIM,
    )


async def main():
    tmp = tempfile.mkdtemp(prefix="blue-data-kb-chromadb-")
    graph_dir = os.path.join(tmp, "graph")
    chroma_dir = os.path.join(tmp, "chroma")
    print(f"Graph directory:   {graph_dir}")
    print(f"ChromaDB directory: {chroma_dir}")

    print()
    print(DIVIDER)
    print("1. Seed schema and data")
    print(DIVIDER)

    kb = build_kb("chromadb-demo", graph_dir, chroma_dir)
    kb.create_database("main")

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
    print("2. Semantic search via ChromaDB")
    print(DIVIDER)

    hits = await kb.search_data("main", "Alice", mode="semantic")
    print("search_data('Alice', mode='semantic'):", [(h.name, round(h.score, 3) if h.score is not None else None) for h in hits])

    schema_hits = await kb.search_schema("main", "employment", mode="semantic")
    print("search_schema('employment', mode='semantic'):", [(h.name, round(h.score, 3) if h.score is not None else None) for h in schema_hits])

    print()
    print(DIVIDER)
    print("3. Persistence — reopen the same graph + ChromaDB directories")
    print(DIVIDER)

    kb2 = build_kb("chromadb-demo-reopened", graph_dir, chroma_dir)
    reopened_rows = await kb2.fetch_database_data("main", compact=True)
    print(f"Reopened KB sees {len(reopened_rows)} graph rows")

    reopened_hits = await kb2.search_data("main", "Alice", mode="semantic")
    print("search_data('Alice', mode='semantic') after reconnect:",
          [(h.name, round(h.score, 3) if h.score is not None else None) for h in reopened_hits])

    shutil.rmtree(tmp)
    print()
    print(f"Cleaned up {tmp}")
    print("Demo complete.")


asyncio.run(main())
