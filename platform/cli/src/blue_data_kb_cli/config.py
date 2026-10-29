"""Per-database registry and KBSource factory for the blue-kb CLI.

Registered databases are persisted via blue-data's `DataRegistry`, backed by a
`FileJSONSource` at `{BLUE_DATA_KB_SOURCE_DATA_DIR}/registry.json`. Each database is a `DataRegistry`
"source" record: `properties["_class"]` is the dotted path of the concrete KBSource
subclass to reconstruct (auto-derived by `register_source`), `properties["connection"]`
is the graph backend's connection config, and `properties["search"]` is the KB's own
declarative search config (mode, BM25 corpus dir, and — for semantic search — the
vector backend's own `_class`+`connection`, plus the embedding provider/model, nested
the same way). `KBSource.__init__` itself knows how to turn `properties["search"]`
into a working search indexer, vector source, and embedding function (see
`blue_data_kb_source.kb_source.KBSource.__init__`), so reconstructing one is exactly
`registry.connect_source(name)` — no bespoke translation code needed here at all.

Example persisted record properties for a semantic-search-enabled database:
  {
    "_class": "blue_data_kb_source.file_kb_source.FileKBSource",
    "connection": {"directory": "~/.blue-kb"},
    "search": {
      "mode": "semantic",
      "bm25_index_dir": "~/.blue-kb/bm25",
      "vector": {
        "_class": "blue_data.source.vector.file_vector_source.FileVectorSource",
        "name": "kb_vectors",
        "connection": {"directory": "~/.blue-kb/vectors"}
      },
      "embedding": {"provider": "openai", "model": "text-embedding-3-small"}
    }
  }
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from blue_data.registry.data_registry import DataRegistry
from blue_data.source.json.file_json_source import FileJSONSource
from blue_data.source.source import DataSource
from blue_data.utils.config import MASK
from blue_data_kb_source import KBSource
from blue_data_kb_source.neo4j.uri import neo4j_uri_connection

_MEMORY_KB_CLASS = "blue_data_kb_source.memory_kb_source.MemoryKBSource"
_FILE_KB_CLASS = "blue_data_kb_source.file_kb_source.FileKBSource"
_NEO4J_KB_CLASS = "blue_data_kb_source.neo4j.neo4j_kb_source.NEO4JKBSource"

_KB_CLASS_BY_BACKEND = {"file": _FILE_KB_CLASS, "neo4j": _NEO4J_KB_CLASS}
_BACKEND_BY_KB_CLASS = {v: k for k, v in _KB_CLASS_BY_BACKEND.items()}

_VECTOR_CLASS_BY_BACKEND = {
    "memory": "blue_data.source.vector.memory_vector_source.MemoryVectorSource",
    "file": "blue_data.source.vector.file_vector_source.FileVectorSource",
    "chromadb": "blue_data.source.vector.chromadb.chromadb_vector_source.ChromaDBVectorSource",
    "redis": "blue_data.source.vector.redis.redis_vector_source.RedisVectorSource",
}

# Every Redis vector key and index is namespaced by this. Collection names already
# carry the database (kb_<collection>_<database>), so one prefix serves them all.
REDIS_SCAN_PREFIX_DEFAULT = "blue_kb:"


CLI_HOME_DEFAULT = "~/.blue-kb"


def _cli_home() -> str:
    """Where this CLI keeps its own state: profiles, selections, registry.

    Deliberately not a profile parameter — it is the directory the profiles
    themselves are read from, so it can only come from the environment.
    """
    return str(Path(os.environ.get("BLUE_DATA_KB_SOURCE_HOME", CLI_HOME_DEFAULT)).expanduser())


def _data_dir() -> str:
    """Directory for the registry and file-backed graph data.

    Resolved by the profile (BLUE_DATA_KB_SOURCE_DATA_DIR), which the root
    group exports into the environment before any command runs; the default
    keeps library-level and test use working without a profile.
    """
    return str(Path(
        os.environ.get("BLUE_DATA_KB_SOURCE_DATA_DIR") or CLI_HOME_DEFAULT
    ).expanduser())


def _selected_database_path() -> Path:
    return Path(_cli_home()) / ".selected.database"


def _resolve_database(option: str | None, resolver) -> str:
    """Which database a command acts on.

    Precedence: --database, then the `database select` file, then the profile's
    BLUE_DATA_KB_SOURCE_DATABASE, then "default". The selection file outranks
    the profile because `database select` is the more specific and more recent
    act; the profile supplies the default for a shell that has never run it.
    """
    if option:
        return option
    p = _selected_database_path()
    if p.exists():
        name = p.read_text().strip()
        if name:
            return name
    return resolver.get("BLUE_DATA_KB_SOURCE_DATABASE") or "default"


def _registry_path() -> Path:
    return Path(_data_dir()) / "registry.json"


def _get_registry() -> DataRegistry:
    """A fresh DataRegistry over the on-disk registry file. Stateless by design (no
    cached singleton) — matches the CLI's own short-lived-process-per-invocation
    model and avoids any risk of stale state leaking across CliRunner-based tests."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return DataRegistry(store=FileJSONSource(properties={"connection": {"path": str(path)}}))


# ------------------------------------------------------------------ #
# Registered-database config: load / register / update / remove       #
# ------------------------------------------------------------------ #

def load_db_config(database: str) -> dict[str, Any]:
    """Return the persisted properties for `database`, falling back to file-backend,
    no-search defaults if it was never registered."""
    props = _get_registry().get_source_properties(database)
    if props:
        return props
    return {
        "_class": _FILE_KB_CLASS,
        "connection": {"directory": _data_dir()},
        "search": {"mode": "none"},
    }


def register_database(
    name: str,
    class_path: str,
    connection: dict[str, Any] | None = None,
    search: dict[str, Any] | None = None,
) -> KBSource:
    """Construct the requested KBSource backend and register it — `_class` is derived
    automatically from the live object's own type by `DataRegistry.register_source`."""
    properties: dict[str, Any] = {}
    if connection:
        properties["connection"] = connection
    if search:
        properties["search"] = search
    kb = DataSource.instantiate(class_path, name, properties=_with_neo4j_password(class_path, properties))
    _get_registry().register_source(kb)  # strips the password from what it saves
    return kb


def unregister_database(name: str) -> None:
    _get_registry().deregister_source(name)


def registered_db_config(name: str) -> dict[str, Any] | None:
    """The persisted properties for `name`, or None if it was never registered
    (unlike `load_db_config`, no file-backend fallback)."""
    return _get_registry().get_source_properties(name) or None


def restore_db_config(name: str, previous: dict[str, Any] | None) -> None:
    """Put `name`'s registry entry back as it was: `previous` properties, or no entry."""
    if previous is None:
        if registered_db_config(name) is not None:
            unregister_database(name)
    else:
        _get_registry().register_record(name, "source", "/", properties=previous)


def update_db_config(name: str, updates: dict[str, Any]) -> None:
    """Shallow-merge `updates` into the persisted properties for `name` (one level —
    a dict value is merged key-by-key into the existing dict at that key; anything
    else replaces it outright), auto-registering with defaults if not already
    registered."""
    current = dict(load_db_config(name))
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key] = {**current[key], **value}
        else:
            current[key] = value
    _get_registry().register_record(name, "source", "/", properties=current)


def list_databases() -> list[dict[str, Any]]:
    """All persisted database records, each `{"name": ..., "properties": {...}}`."""
    return _get_registry().get_sources()


# ------------------------------------------------------------------ #
# Neo4j connection helper                                             #
# ------------------------------------------------------------------ #

def _neo4j_connection_dict(
    uri: str | None = None, user: str | None = None, password: str | None = None
) -> dict[str, Any]:
    """Build a *partial* connection dict from CLI flags — only includes keys that
    were actually given. Anything omitted is left for NEO4JKBSource's own
    `_initialize_connection_properties` defaults to fill in at construction time
    (scheme=bolt, host=localhost, port=7687, user=neo4j). Raises ValueError
    for a URI scheme the Neo4j driver doesn't accept — this is what keeps a
    partial `database config set --neo4j-uri ...` from clobbering an
    already-configured user/password with an unrequested default."""
    connection: dict[str, Any] = {}
    if uri:
        connection.update(neo4j_uri_connection(uri))
    if user:
        connection["user"] = user
    if password:
        connection["password"] = password
    return connection


# ------------------------------------------------------------------ #
# Vector backend config                                                #
# ------------------------------------------------------------------ #

def _vector_properties(backend: str, connection: dict[str, Any]) -> dict[str, Any]:
    """The declarative properties["search"]["vector"] shape KBSource.__init__
    expects — constructed via DataSource.instantiate() from `_class`, not built here."""
    return {"_class": _VECTOR_CLASS_BY_BACKEND[backend], "name": "kb_vectors", "connection": connection}


def _with_vector_location(vector: dict[str, Any], base_dir: str) -> dict[str, Any]:
    """`vector` with a storage location filled in where it has none (#162).

    Without one, the file backend writes to the current working directory and
    ChromaDB keeps everything in memory — and every blue-kb command is a new
    process, so semantic search would find nothing. Defaults sit beside the
    graph data, like the BM25 corpus: the directory itself for `file` (its files
    are `kb_<collection>_<database>.json`), a `chromadb/` folder for ChromaDB.
    """
    conn = dict(vector.get("connection") or {})
    if vector.get("_class") == _VECTOR_CLASS_BY_BACKEND["file"]:
        conn.setdefault("directory", base_dir)
    elif vector.get("_class") == _VECTOR_CLASS_BY_BACKEND["chromadb"]:
        conn.setdefault("path", str(Path(base_dir) / "chromadb"))
    return {**vector, "connection": conn}


def _with_search_defaults(props: dict[str, Any]) -> dict[str, Any]:
    """`props` with the vector location defaults applied, for databases registered
    before `register` filled them in."""
    vector = (props.get("search") or {}).get("vector")
    if not isinstance(vector, dict):
        return props
    base = (props.get("connection") or {}).get("directory") or _data_dir()
    return {**props, "search": {**props["search"], "vector": _with_vector_location(vector, base)}}


# ------------------------------------------------------------------ #
# KBSource factory                                                     #
# ------------------------------------------------------------------ #

NEO4J_PASSWORD_ENV = "BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD"

# Keys never shown by `database list`/`config show`, wherever they sit in a record.
# The registry strips them from what `register` saves, but a record written by an
# older `database config set --neo4j-password` still holds one in plaintext (#122).
_SECRET_KEYS = frozenset({
    "password", "passwd", "pwd", "token", "access_token", "refresh_token",
    "secret", "client_secret", "api_secret", "api_key", "private_key",
})


def masked(value: Any) -> Any:
    """A copy of a registry record (or any part of one) with secret values masked."""
    if isinstance(value, dict):
        return {k: (MASK if k in _SECRET_KEYS and v else masked(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [masked(v) for v in value]
    return value


def _with_neo4j_password(class_path: str, props: dict[str, Any]) -> dict[str, Any]:
    """*props* with the profile's Neo4j password in its connection, if *class_path* is Neo4j.

    The registry never stores the password, so it comes from the profile (or the
    environment) each time the CLI connects, as it does for the MCP server (#122).
    """
    password = os.environ.get(NEO4J_PASSWORD_ENV)
    if class_path != _NEO4J_KB_CLASS or not password:
        return props
    return {**props, "connection": {**(props.get("connection") or {}), "password": password}}


def _make_kb(database: str) -> KBSource:
    props = _get_registry().get_source_properties(database)
    if props:
        props = _with_search_defaults(props)
        return DataSource.instantiate(props["_class"], database, properties=_with_neo4j_password(props["_class"], props))
    # Not registered — default to an unregistered file-backed KB, matching the
    # CLI's long-standing "no explicit `register` required" behavior.
    return DataSource.instantiate(
        _FILE_KB_CLASS, database, properties={"connection": {"directory": _data_dir()}}
    )
