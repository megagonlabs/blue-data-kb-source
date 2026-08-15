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
from typing import Any, Optional
from urllib.parse import urlparse

from blue_data.source.source import DataSource
from blue_data_kb_source import KBSource

_FILE_KB_CLASS = "blue_data_kb_source.file_kb_source.FileKBSource"
_NEO4J_KB_CLASS = "blue_data_kb_source.neo4j.neo4j_kb_source.NEO4JKBSource"

_VECTOR_CLASS_BY_BACKEND = {
    "memory": "blue_data.source.vector.memory_vector_source.MemoryVectorSource",
    "file": "blue_data.source.vector.file_vector_source.FileVectorSource",
    "chromadb": "blue_data.source.vector.chromadb.chromadb_vector_source.ChromaDBVectorSource",
    "redis": "blue_data.source.vector.redis.redis_vector_source.RedisVectorSource",
}

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
# KBSource factory
# ---------------------------------------------------------------------------

def _build_kb() -> KBSource:
    graph_backend = os.environ.get("BLUE_DATA_KB_SOURCE_GRAPH_BACKEND", "file")
    search_mode = os.environ.get("BLUE_DATA_KB_SOURCE_SEARCH", "none")

    # Constructed via DataSource.instantiate() since the backend is chosen at
    # runtime from an env var, not a fixed class known at write-time — the
    # dotted-path import also keeps the neo4j driver out of the process unless
    # BLUE_DATA_KB_SOURCE_GRAPH_BACKEND=neo4j is actually set. Every other
    # kwarg KBSource.__init__ takes lives under properties too, matching
    # DataSource's own (name, properties=None) signature.
    kb_class: str
    connection: dict[str, Any]
    default_index_dir: str

    if graph_backend == "neo4j":
        kb_class = _NEO4J_KB_CLASS
        connection = _neo4j_connection()
        default_index_dir = _directory()
    else:
        kb_class = _FILE_KB_CLASS
        directory = _directory()
        connection = {"directory": directory}
        default_index_dir = directory

    properties: dict[str, Any] = {"connection": connection}

    if search_mode in ("bm25", "semantic"):
        bm25_dir = os.environ.get("BLUE_DATA_KB_SOURCE_BM25_INDEX_DIR") or default_index_dir
        search_cfg: dict[str, Any] = {"mode": search_mode, "bm25_index_dir": str(Path(bm25_dir).expanduser())}
        if search_mode == "semantic":
            vector_backend = os.environ.get("BLUE_DATA_KB_SOURCE_VECTOR_BACKEND", "memory")
            vector_connection: dict[str, Any] = {}
            if vector_backend == "file":
                vector_connection["directory"] = _directory()
            search_cfg["vector"] = {
                "_class": _VECTOR_CLASS_BY_BACKEND[vector_backend],
                "name": "kb_vectors",
                "connection": vector_connection,
            }
            search_cfg["embedding"] = {
                "provider": "openai",
                "model": os.environ.get("BLUE_DATA_KB_SOURCE_EMBEDDING_MODEL", "text-embedding-3-small"),
            }
        properties["search"] = search_cfg

    return DataSource.instantiate(kb_class, "kb", properties=properties)
