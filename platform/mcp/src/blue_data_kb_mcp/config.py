"""KBSource singleton factory — reads all config from environment variables.

Unlike the `blue-kb` CLI (`platform/cli/src/blue_data_kb_cli/config.py`), which resolves a
per-database backend from `~/.blue-kb/registry.json`, this server owns a single KBSource
instance for the process lifetime, configured entirely by BLUE_DATA_KB_SOURCE_* env
vars. Those variable names are deliberately the same ones the CLI uses (see its
`profile_spec.json`), so a value means the same thing in both; this server simply
reads them from the environment and has no connection to the CLI's profiles. `database` stays a per-tool-call argument on every tool — KBSource already
supports multiple logical databases on one backend.

Deliberately does not import anything from `blue_data_kb_cli`: this package depends
only on `blue-data-kb-source`, so it can be built, versioned, and deployed
independently (see design/07-repo-organization.md).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from blue_data.source.source import DataSource
from blue_data_kb_source import KBSource
from blue_data_kb_source.neo4j.uri import neo4j_uri_connection

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


# Model resolution, per function, matching the `blue-kb` CLI exactly: a
# function-specific variable falls back to the KB-wide one, then to the
# family-wide one, then to the default of the BLUE_DATA_LLM_PROVIDER provider.
# The CLI resolves these from a profile; this server reads them from the
# environment only, and has no connection to the CLI's profiles. The names are
# shared deliberately — the reference list is the CLI's `profile_spec.json` —
# and the library holds the chain, so the two can't drift.
def model_for(function: str | None = None) -> str:
    """The model to use, optionally for a named function.

    `function` is one of EXTRACTION, QUERY, VALIDATION, SCHEMA_EXPANSION,
    ENRICHMENT, INGESTION, ASSERTION — the same set the CLI uses.
    """
    from blue_data_kb_source import llm

    return llm.configured_model(function)


# ---------------------------------------------------------------------------
# Schema-expansion reference servers
# ---------------------------------------------------------------------------

SCHEMA_EXPANSION_MCP_SERVERS_ENV = "BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS"


def schema_expansion_mcp_servers() -> list[dict[str, Any]]:
    """MCP servers that expand_schema / import_text may connect for reference='mcp',
    as a JSON list in BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_MCP_SERVERS, e.g.
    '[{"transport": "stdio", "command": "blue-data-wordnet-mcp"}]'.

    Only the operator who launches this server sets these. Tool callers can't: a
    server entry is a command to run (stdio) or a URL and headers to connect to, so
    taking them from a tool argument let anything that steers the agent (a prompt
    injection in an ingested document, say) run arbitrary commands on the host (#111).
    Empty when unset.
    """
    raw = os.environ.get(SCHEMA_EXPANSION_MCP_SERVERS_ENV, "").strip()
    if not raw:
        return []
    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SCHEMA_EXPANSION_MCP_SERVERS_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(servers, list) or not all(isinstance(s, dict) for s in servers):
        raise ValueError(f"{SCHEMA_EXPANSION_MCP_SERVERS_ENV} must be a JSON list of server objects")
    return servers


def schema_expansion_timeout() -> float | None:
    """Overall cap, in seconds, on one expand_schema run — the same variable and default
    as the CLI's profile (BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_TIMEOUT, 1800; 0 = none).
    Without it a stalled model or reference server held the tool call forever (#113)."""
    raw = os.environ.get("BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_TIMEOUT", "1800").strip()
    value = float(raw) if raw else 0.0
    return value or None


# ---------------------------------------------------------------------------
# Directory / connection helpers
# ---------------------------------------------------------------------------

def _directory() -> str:
    return str(Path(os.environ.get("BLUE_DATA_KB_SOURCE_DATA_DIR", "~/.blue-kb")).expanduser())


def _neo4j_connection() -> dict[str, Any]:
    connection: dict[str, Any] = {"user": os.environ.get("BLUE_DATA_KB_SOURCE_NEO4J_USER", "neo4j")}
    uri = os.environ.get("BLUE_DATA_KB_SOURCE_NEO4J_URI", "")
    if uri:
        try:
            connection.update(neo4j_uri_connection(uri))
        except ValueError as exc:
            raise ValueError(f"BLUE_DATA_KB_SOURCE_NEO4J_URI: {exc}") from None
    password = os.environ.get("BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD")
    if password:
        connection["password"] = password
    # The single physical Neo4j database the KB uses. Tool calls' `database` argument
    # only selects a logical KB database inside it, never another Neo4j database (#132).
    physical = os.environ.get("BLUE_DATA_KB_SOURCE_NEO4J_DATABASE")
    if physical:
        connection["database"] = physical
    return connection


REDIS_SCAN_PREFIX_DEFAULT = "blue_kb:"  # same default as `blue-kb database register`


def _redis_connection() -> dict[str, Any]:
    """Redis vector connection from the REDIS_HOST/PORT/SCAN_PREFIX variables.
    Always carries a scan_prefix: RedisVectorSource can neither create an index nor
    build key names without one (#148). No logical db: RediSearch only indexes db 0,
    so tenants are separated by the prefix (#177)."""
    connection: dict[str, Any] = {
        "scan_prefix": os.environ.get("BLUE_DATA_KB_SOURCE_REDIS_SCAN_PREFIX") or REDIS_SCAN_PREFIX_DEFAULT,
    }
    if host := os.environ.get("BLUE_DATA_KB_SOURCE_REDIS_HOST"):
        connection["host"] = host
    if raw := os.environ.get("BLUE_DATA_KB_SOURCE_REDIS_PORT"):
        try:
            connection["port"] = int(raw)
        except ValueError:
            raise ValueError(f"BLUE_DATA_KB_SOURCE_REDIS_PORT must be an integer, got {raw!r}") from None
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
            # `file` by default, and a location for every on-disk backend, so the
            # embeddings outlive the server process the way the graph and the BM25
            # corpus do; `memory` and a path-less ChromaDB lost them on restart (#162).
            vector_backend = os.environ.get("BLUE_DATA_KB_SOURCE_VECTOR_BACKEND", "file")
            vector_connection: dict[str, Any] = {}
            if vector_backend == "file":
                vector_connection["directory"] = str(Path(
                    os.environ.get("BLUE_DATA_KB_SOURCE_VECTOR_DIRECTORY") or _directory()
                ).expanduser())
            elif vector_backend == "chromadb":
                vector_connection["path"] = str(Path(
                    os.environ.get("BLUE_DATA_KB_SOURCE_VECTOR_PATH") or Path(_directory()) / "chromadb"
                ).expanduser())
            elif vector_backend == "redis":
                vector_connection = _redis_connection()
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
