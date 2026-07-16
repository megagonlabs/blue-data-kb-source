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

from blue_data.source.graph.file_graph_source import FileGraphSource
from blue_data_kb_source import KBSource


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
        import openai  # type: ignore[import]
        model = ecfg.get("model", "text-embedding-3-small")
        client = openai.OpenAI()

        def _embed_openai(text: str) -> list[float]:
            return client.embeddings.create(input=text, model=model).data[0].embedding

        return _embed_openai
    raise ValueError(f"Unsupported embedding provider: {provider!r}")


# ------------------------------------------------------------------ #
# KBSource factory                                                     #
# ------------------------------------------------------------------ #

def _make_kb(database: str) -> KBSource:
    cfg = load_db_config(database)
    graph_backend = cfg.get("graph_backend", "file")
    search_mode = cfg.get("search", "none")

    # Determine graph kwargs and default BM25 directory
    graph_kwargs: dict[str, Any]
    default_index_dir: str

    if graph_backend == "neo4j":
        from blue_data.source.graph.neo4j_graph_source import Neo4JGraphSource  # type: ignore[import]

        class _KB(KBSource, Neo4JGraphSource):  # type: ignore[misc]
            pass

        gcfg = dict(cfg.get("graph", {}))
        graph_kwargs = gcfg
        default_index_dir = _data_dir()
    else:
        class _KB(KBSource, FileGraphSource):  # type: ignore[misc]
            pass

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

    return _KB(**graph_kwargs, **search_kwargs)
