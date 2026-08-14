"""KBSource singleton factory — reads all config from environment variables.

Unlike the `kb` CLI (`platform/cli/src/blue_data_kb_cli/config.py`), which resolves a
per-database backend from `~/.kbdb/registry.json`, this server owns a single KBSource
instance for the process lifetime, configured entirely by BLUE_DATA_KB_SOURCE_* env
vars. `database` stays a per-tool-call argument on every tool — KBSource already
supports multiple logical databases on one backend.

Deliberately does not import anything from `blue_data_kb_cli`: this package depends
only on `blue-data-kb-source`, so it can be built, versioned, and deployed
independently (see design/07-repo-organization.md).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from blue_data.source.source import DataSource
from blue_data_kb_source import KBSource

_FILE_KB_CLASS = "blue_data_kb_source.file_kb_source.FileKBSource"
_NEO4J_KB_CLASS = "blue_data_kb_source.neo4j.neo4j_kb_source.NEO4JKBSource"

_kb: Optional[KBSource] = None


def get_kb() -> KBSource:
    global _kb
    if _kb is None:
        _kb = _build_kb()
    return _kb


def reset_kb() -> None:
    """Replace the singleton with a fresh instance — for tests."""
    global _kb
    _kb = None


def default_model() -> str:
    return os.environ.get("BLUE_DATA_KB_SOURCE_DEFAULT_MODEL", "gpt-4o")


# ---------------------------------------------------------------------------
# Directory / connection helpers
# ---------------------------------------------------------------------------

def _directory() -> str:
    return str(Path(os.environ.get("BLUE_DATA_KB_SOURCE_DIRECTORY", "~/.kbdb")).expanduser())


def _neo4j_connection() -> dict[str, Any]:
    connection: dict[str, Any] = {"user": os.environ.get("BLUE_DATA_KB_SOURCE_NEO4J_USER", "neo4j")}
    uri = os.environ.get("BLUE_DATA_KB_SOURCE_NEO4J_URI", "")
    if uri:
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        connection["protocol"] = parsed.scheme or "bolt"
        connection["host"] = parsed.hostname or "localhost"
        if parsed.port:
            connection["port"] = parsed.port
    password = os.environ.get("BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD")
    if password:
        connection["password"] = password
    return connection


# ---------------------------------------------------------------------------
# Search backend helpers
# ---------------------------------------------------------------------------

def _vector_source() -> Any:
    backend = os.environ.get("BLUE_DATA_KB_SOURCE_VECTOR_BACKEND", "memory")
    if backend == "file":
        from blue_data.source.vector.file_vector_source import FileVectorSource
        return FileVectorSource("kb_vectors", directory=_directory())
    if backend == "chromadb":
        from blue_data.source.vector.chromadb.chromadb_vector_source import ChromaDBVectorSource
        return ChromaDBVectorSource("kb_vectors")
    if backend == "redis":
        from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource
        return RedisVectorSource("kb_vectors")
    from blue_data.source.vector.memory_vector_source import MemoryVectorSource
    return MemoryVectorSource("kb_vectors")


def _embedding_fn() -> Callable[[str], list[float]]:
    model = os.environ.get("BLUE_DATA_KB_SOURCE_EMBEDDING_MODEL", "text-embedding-3-small")
    _client: list[Any] = []  # lazy singleton, created on first call

    def _embed(text: str) -> list[float]:
        if not _client:
            import openai
            _client.append(openai.OpenAI())
        return _client[0].embeddings.create(input=text, model=model).data[0].embedding

    return _embed


# ---------------------------------------------------------------------------
# KBSource factory
# ---------------------------------------------------------------------------

def _build_kb() -> KBSource:
    graph_backend = os.environ.get("BLUE_DATA_KB_SOURCE_GRAPH_BACKEND", "file")
    search_mode = os.environ.get("BLUE_DATA_KB_SOURCE_SEARCH", "none")

    # Constructed via DataSource.instantiate() since the backend is chosen at
    # runtime from an env var, not a fixed class known at write-time — the
    # dotted-path import also keeps the neo4j driver out of the process unless
    # BLUE_DATA_KB_SOURCE_GRAPH_BACKEND=neo4j is actually set.
    kb_class: str
    graph_kwargs: dict[str, Any]
    default_index_dir: str

    if graph_backend == "neo4j":
        kb_class = _NEO4J_KB_CLASS
        graph_kwargs = {"name": "kb", "properties": {"connection": _neo4j_connection()}}
        default_index_dir = _directory()
    else:
        kb_class = _FILE_KB_CLASS
        directory = _directory()
        graph_kwargs = {"name": "kb", "directory": directory}
        default_index_dir = directory

    search_kwargs: dict[str, Any] = {}
    if search_mode in ("bm25", "semantic"):
        search_kwargs["enable_search"] = True
        bm25_dir = os.environ.get("BLUE_DATA_KB_SOURCE_BM25_INDEX_DIR") or default_index_dir
        search_kwargs["corpus_dir"] = str(Path(bm25_dir).expanduser())

    if search_mode == "semantic":
        search_kwargs["vector_source"] = _vector_source()
        search_kwargs["embedding_fn"] = _embedding_fn()

    return DataSource.instantiate(kb_class, **graph_kwargs, **search_kwargs)
