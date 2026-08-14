"""Per-database registry and KBSource factory for the kb CLI.

Registry lives at {KBDB_DATA_DIR}/registry.json.  Each key is a database name;
the value is a config dict that describes the graph backend and optional search
configuration.

Example registry entry:
  {
    "mydb": {
      "graph_backend": "file",
      "graph": {"directory": "~/.kbdb"},
      "search": "bm25",
      "bm25": {"index_dir": "~/.kbdb/bm25"},
      "vector": {"backend": "file", "directory": "~/.kbdb/vectors"},
      "embedding": {"provider": "openai", "model": "text-embedding-3-small"}
    }
  }
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from blue_data.source.source import DataSource
from blue_data_kb_source import KBSource

_FILE_KB_CLASS = "blue_data_kb_source.file_kb_source.FileKBSource"
_NEO4J_KB_CLASS = "blue_data_kb_source.neo4j.neo4j_kb_source.NEO4JKBSource"


def _data_dir() -> str:
    return str(Path(os.environ.get("KBDB_DATA_DIR", "~/.kbdb")).expanduser())


def _selected_database_path() -> Path:
    return Path(_data_dir()) / ".selected.database"


def _default_database() -> str:
    p = _selected_database_path()
    if p.exists():
        name = p.read_text().strip()
        if name:
            return name
    return "default"


def _registry_path() -> Path:
    return Path(_data_dir()) / "registry.json"


def _load_registry() -> dict[str, dict[str, Any]]:
    path = _registry_path()
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_registry(registry: dict[str, dict[str, Any]]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2))


def load_db_config(database: str) -> dict[str, Any]:
    """Return config for `database`, falling back to file-backend defaults."""
    registry = _load_registry()
    if database in registry:
        return registry[database]
    return {
        "graph_backend": "file",
        "graph": {"directory": _data_dir()},
        "search": "none",
    }


def register_database(database: str, config: dict[str, Any]) -> None:
    registry = _load_registry()
    registry[database] = config
    _save_registry(registry)


def unregister_database(database: str) -> None:
    registry = _load_registry()
    registry.pop(database, None)
    _save_registry(registry)


def update_db_config(database: str, updates: dict[str, Any]) -> None:
    """Deep-merge `updates` into the stored config for `database`."""
    registry = _load_registry()
    cfg = registry.get(database, load_db_config(database)).copy()
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    registry[database] = cfg
    _save_registry(registry)


# ------------------------------------------------------------------ #
# VectorSource factory                                                 #
# ------------------------------------------------------------------ #

def _make_vector_source(cfg: dict[str, Any]) -> Any:
    vcfg = cfg.get("vector", {})
    backend = vcfg.get("backend", "memory")
    if backend == "file":
        from blue_data.source.vector.file_vector_source import FileVectorSource  # type: ignore[import]
        directory = str(Path(vcfg.get("directory", _data_dir())).expanduser())
        return FileVectorSource("kb_vectors", directory=directory)
    if backend == "chromadb":
        from blue_data.source.vector.chromadb.chromadb_vector_source import ChromaDBVectorSource  # type: ignore[import]
        path = vcfg.get("path")
        return ChromaDBVectorSource(
            "kb_vectors",
            path=str(Path(path).expanduser()) if path else None,
        )
    if backend == "redis":
        from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource  # type: ignore[import]
        connection: dict[str, Any] = {}
        if "host" in vcfg:
            connection["host"] = vcfg["host"]
        if "port" in vcfg:
            connection["port"] = vcfg["port"]
        if "db" in vcfg:
            connection["db"] = vcfg["db"]
        connection.update(vcfg.get("connection", {}))
        return RedisVectorSource("kb_vectors", properties={"connection": connection})
    # memory (default)
    from blue_data.source.vector.memory_vector_source import MemoryVectorSource  # type: ignore[import]
    return MemoryVectorSource("kb_vectors")


# ------------------------------------------------------------------ #
# Embedding function factory                                           #
# ------------------------------------------------------------------ #

def _make_embedding_fn(cfg: dict[str, Any]) -> Callable[[str], list[float]]:
    ecfg = cfg.get("embedding", {})
    provider = ecfg.get("provider", "openai")
    if provider == "openai":
        model = ecfg.get("model", "text-embedding-3-small")
        _client: list[Any] = []  # lazy singleton — created on first call

        def _embed_openai(text: str) -> list[float]:
            if not _client:
                import openai  # type: ignore[import]
                _client.append(openai.OpenAI())
            return _client[0].embeddings.create(input=text, model=model).data[0].embedding

        return _embed_openai
    raise ValueError(f"Unsupported embedding provider: {provider!r}")


# ------------------------------------------------------------------ #
# Neo4j connection helpers                                             #
# ------------------------------------------------------------------ #

def _neo4j_connection_dict(gcfg: dict[str, Any]) -> dict[str, Any]:
    """Convert graph config (uri/username/password) to NEO4JSource connection dict."""
    connection: dict[str, Any] = {}
    uri = gcfg.get("uri", "")
    if uri:
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        connection["protocol"] = parsed.scheme or "bolt"
        connection["host"] = parsed.hostname or "localhost"
        if parsed.port:
            connection["port"] = parsed.port
    if "host" in gcfg:
        connection["host"] = gcfg["host"]
    if "port" in gcfg:
        connection["port"] = gcfg["port"]
    if "protocol" in gcfg:
        connection["protocol"] = gcfg["protocol"]
    connection["user"] = gcfg.get("username") or gcfg.get("user") or "neo4j"
    if "password" in gcfg:
        connection["password"] = gcfg["password"]
    return connection


# ------------------------------------------------------------------ #
# KBSource factory                                                     #
# ------------------------------------------------------------------ #

def _make_kb(database: str) -> KBSource:
    cfg = load_db_config(database)
    graph_backend = cfg.get("graph_backend", "file")
    search_mode = cfg.get("search", "none")

    # Determine class path, graph kwargs, and default BM25 directory.
    # Constructed via DataSource.instantiate() rather than importing the class
    # directly, since the backend is a runtime choice (from the registry), not a
    # fixed one — instantiate()'s dotted-path import also keeps the neo4j driver
    # out of the process unless graph_backend == "neo4j" is actually selected.
    kb_class: str
    graph_kwargs: dict[str, Any]
    default_index_dir: str

    if graph_backend == "neo4j":
        kb_class = _NEO4J_KB_CLASS
        gcfg = cfg.get("graph", {})
        connection = _neo4j_connection_dict(gcfg)
        graph_kwargs = {"name": "kb", "properties": {"connection": connection}}
        default_index_dir = _data_dir()
    else:
        kb_class = _FILE_KB_CLASS
        gcfg = cfg.get("graph", {})
        directory = str(Path(gcfg.get("directory", _data_dir())).expanduser())
        graph_kwargs = {"name": "kb", "directory": directory}
        default_index_dir = directory

    search_kwargs: dict[str, Any] = {}
    if search_mode in ("bm25", "semantic"):
        search_kwargs["enable_search"] = True
        bm25_dir = cfg.get("bm25", {}).get("index_dir") or default_index_dir
        search_kwargs["corpus_dir"] = str(Path(bm25_dir).expanduser())

    if search_mode == "semantic":
        search_kwargs["vector_source"] = _make_vector_source(cfg)
        search_kwargs["embedding_fn"] = _make_embedding_fn(cfg)

    return DataSource.instantiate(kb_class, **graph_kwargs, **search_kwargs)
