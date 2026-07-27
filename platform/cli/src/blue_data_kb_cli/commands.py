"""Click command groups for the kb CLI.

Databases are registered in {KBDB_DATA_DIR}/registry.json (defaults to ~/.kbdb).
Each entry stores the graph backend and optional search configuration.  Run
`kb database register` to create and configure a new database.

FileGraphSource persists each database as a JSON file — no explicit save step
is needed.  The graph backend (neo4j, …) is chosen per database in the registry.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from blue_data_kb_source import KBSource
from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_SUBCLASS_OF,
    IS_VALUE_OF,
    PROPERTY,
    RELATION,
    REPRESENTS,
    SOURCE,
    TAIL,
)
from blue_data_kb_source.types import KBError

from blue_data_kb_cli.config import (
    _data_dir,
    _default_database,
    _load_registry,
    _make_kb,
    _registry_path,
    _selected_database_path,
    load_db_config,
    register_database,
    unregister_database,
    update_db_config,
)

console = Console()

# Keep a local alias so existing helpers still compile
FileKBSource = KBSource  # type alias — runtime type is the MRO class from _make_kb


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _truncate(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _status_tool_callbacks(
    status: Any, label: str, verbose: bool = False, log: bool = False, log_limit: int = 80
) -> tuple[Any, Any]:
    def on_tool_call(name: str, args: dict) -> None:
        if not (verbose or log):
            return
        if verbose and args:
            args_str = _truncate(", ".join(f"{k}={v!r}" for k, v in args.items()), log_limit)
            detail = f"({name}: {args_str})"
        else:
            detail = f"({name})"
        status.update(f"{label}… [dim]{detail}[/dim]")
        if log:
            console.print(f"[dim]{label}… {detail}[/dim]")

    def on_tool_result(name: str, args: dict, result: Any) -> None:
        if not log:
            return
        result_str = _truncate(repr(result), log_limit)
        console.print(f"[dim]{label}… ({name}) -> {result_str}[/dim]")

    return on_tool_call, on_tool_result


def _resolve_node(
    kb: FileKBSource,
    db: str,
    label: str,
    name: str | None,
    node_id: str | None,
    what: str,
) -> str:
    """Return node_id, resolving name→id when only a name is supplied."""
    if node_id:
        return node_id
    if name:
        nodes = kb.match_nodes(db, label=label, properties={"name": name})
        if not nodes:
            raise click.ClickException(f"{what} '{name}' not found in database '{db}'")
        return nodes[0]["id"]
    raise click.ClickException(f"Provide --{what.lower()} or --{what.lower()}-id")


def _resolve_owned_property(
    kb: FileKBSource,
    db: str,
    property_name: str | None,
    property_id: str | None,
    owner_id: str | None,
) -> str:
    """Resolve a PROPERTY node, filtering by owner (class/relation) when provided."""
    if property_id:
        return property_id
    if not property_name:
        raise click.ClickException("Provide --property or --property-id")
    if owner_id:
        for nb in kb.get_neighbors(db, owner_id, edge_type=DEFINE_PROPERTY, direction="out"):
            if nb["node"].get("properties", {}).get("name") == property_name:
                return nb["node"]["id"]
        raise click.ClickException(
            f"Property '{property_name}' not found on the specified class/relation in '{db}'"
        )
    nodes = kb.match_nodes(db, label=PROPERTY, properties={"name": property_name})
    if not nodes:
        raise click.ClickException(f"Property '{property_name}' not found in database '{db}'")
    return nodes[0]["id"]


def _coerce_value(raw: str, literal_type: str | None) -> str | int | float | bool:
    if literal_type == "NUMBER":
        try:
            return int(raw)
        except ValueError:
            return float(raw)
    if literal_type == "BOOLEAN":
        return raw.lower() in ("true", "1", "yes")
    return raw


def _provenance_text(kb: FileKBSource, db: str, node_id: str) -> str:
    nbs = kb.get_neighbors(db, node_id, edge_type=EXTRACTED_FROM, direction="out")
    texts = [nb["node"].get("properties", {}).get("text", "") for nb in nbs]
    return " | ".join(t for t in texts if t)


# ------------------------------------------------------------------ #
# Root group                                                           #
# ------------------------------------------------------------------ #

@click.group()
@click.option(
    "--database",
    envvar="KBDB_DATABASE",
    default=_default_database,
    show_default=True,
    help="Target database. Overrides KBDB_DATABASE. Defaults to selection in ~/.kbdb/.selected.database.",
)
@click.pass_context
def cli(ctx: click.Context, database: str) -> None:
    """kb — Knowledge Base CLI.

    Databases are configured via ~/.kbdb/registry.json (KBDB_DATA_DIR).
    Run `kb database register` to create and configure a database.
    """
    ctx.ensure_object(dict)
    kb = _make_kb(database)
    if database not in kb.fetch_databases():
        kb.create_database(database)
    ctx.obj["kb"] = kb
    ctx.obj["db"] = database


# ------------------------------------------------------------------ #
# kb database                                                          #
# ------------------------------------------------------------------ #

@cli.group("database")
def database_group() -> None:
    """Database management."""


@database_group.command("list")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
def database_list(fmt: str) -> None:
    """List registered databases."""
    registry = _load_registry()
    if fmt == "json":
        console.print_json(json.dumps(
            {name: cfg for name, cfg in sorted(registry.items())}
        ))
        return
    if not registry:
        console.print("[dim](no databases registered — run `kb database register NAME`)[/dim]")
        return
    t = Table(show_header=True, header_style="bold")
    t.add_column("Database")
    t.add_column("Graph Backend", style="dim")
    t.add_column("Search",        style="dim")
    for name, cfg in sorted(registry.items()):
        t.add_row(name, cfg.get("graph_backend", "file"), cfg.get("search", "none"))
    console.print(t)


@database_group.command("register")
@click.argument("name")
@click.option("--graph-backend", "graph_backend", type=click.Choice(["file", "neo4j"]), default="file", show_default=True)
@click.option("--directory",      help="Graph data directory (file backend). Default: KBDB_DATA_DIR.")
@click.option("--neo4j-uri",      help="Neo4j bolt URI.")
@click.option("--neo4j-user",     help="Neo4j username.")
@click.option("--neo4j-password", help="Neo4j password.")
@click.option("--search",         type=click.Choice(["none", "bm25", "semantic"]), default="none", show_default=True)
@click.option("--bm25-index-dir", help="Directory for BM25 corpus files. Default: graph directory.")
@click.option("--vector-backend", "vector_backend", type=click.Choice(["memory", "file", "chromadb", "redis"]), default="memory", show_default=True)
@click.option("--vector-directory", help="Directory for file vector backend.")
@click.option("--vector-path",    help="Path for ChromaDB backend.")
@click.option("--redis-host",     help="Redis host.")
@click.option("--redis-port",     type=int, help="Redis port.")
@click.option("--redis-db",       type=int, help="Redis database number.")
@click.option("--embedding-provider", "embedding_provider", type=click.Choice(["openai"]), default="openai", show_default=True)
@click.option("--embedding-model", help="Embedding model name.")
def database_register(
    name: str,
    graph_backend: str,
    directory: str | None,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    search: str,
    bm25_index_dir: str | None,
    vector_backend: str,
    vector_directory: str | None,
    vector_path: str | None,
    redis_host: str | None,
    redis_port: int | None,
    redis_db: int | None,
    embedding_provider: str,
    embedding_model: str | None,
) -> None:
    """Register a new database with the specified backend and search config."""
    cfg: dict[str, Any] = {"graph_backend": graph_backend, "search": search}

    if graph_backend == "neo4j":
        graph_cfg: dict[str, Any] = {}
        if neo4j_uri:
            graph_cfg["uri"] = neo4j_uri
        if neo4j_user:
            graph_cfg["username"] = neo4j_user
        if neo4j_password:
            graph_cfg["password"] = neo4j_password
        cfg["graph"] = graph_cfg
    else:
        cfg["graph"] = {"directory": directory or _data_dir()}

    if search in ("bm25", "semantic"):
        if bm25_index_dir:
            cfg["bm25"] = {"index_dir": bm25_index_dir}

    if search == "semantic":
        vcfg: dict[str, Any] = {"backend": vector_backend}
        if vector_backend == "file" and vector_directory:
            vcfg["directory"] = vector_directory
        if vector_backend == "chromadb" and vector_path:
            vcfg["path"] = vector_path
        if vector_backend == "redis":
            conn: dict[str, Any] = {}
            if redis_host:
                conn["host"] = redis_host
            if redis_port is not None:
                conn["port"] = redis_port
            if redis_db is not None:
                conn["db"] = redis_db
            if conn:
                vcfg["connection"] = conn
        cfg["vector"] = vcfg
        ecfg: dict[str, Any] = {"provider": embedding_provider}
        if embedding_model:
            ecfg["model"] = embedding_model
        cfg["embedding"] = ecfg

    register_database(name, cfg)
    # Ensure the graph database exists
    try:
        kb = _make_kb(name)
        if name not in kb.fetch_databases():
            kb.create_database(name)
    except Exception as exc:
        raise click.ClickException(f"Registered config but failed to initialize backend: {exc}")
    console.print(f"Registered database: [bold]{name}[/bold] (graph={graph_backend}, search={search})")


@database_group.command("create")
@click.argument("name")
@click.pass_obj
def database_create(obj: dict, name: str) -> None:
    """Create a database using the current session's backend (shortcut for simple file setups)."""
    kb: KBSource = obj["kb"]
    try:
        kb.create_database(name)
    except KBError as exc:
        raise click.ClickException(str(exc))
    # Auto-register with defaults if not present
    registry = _load_registry()
    if name not in registry:
        register_database(name, {
            "graph_backend": "file",
            "graph": {"directory": _data_dir()},
            "search": "none",
        })
    console.print(f"Created database: [bold]{name}[/bold]")


@database_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def database_delete(name: str, yes: bool) -> None:
    """Delete a database and remove it from the registry."""
    cfg = load_db_config(name)
    graph_backend = cfg.get("graph_backend", "file")

    if graph_backend == "file":
        gcfg = cfg.get("graph", {})
        directory = Path(gcfg.get("directory", _data_dir())).expanduser()
        path = directory / f"{name}.json"
        if not path.exists():
            # Not in registry or file not found
            raise click.ClickException(f"Database '{name}' not found.")
        if not yes:
            click.confirm(f"Delete database '{name}'? This cannot be undone.", abort=True)
        path.unlink()
    else:
        if not yes:
            click.confirm(
                f"Remove database '{name}' from the registry? "
                f"(Data in {graph_backend} backend is NOT deleted.)",
                abort=True,
            )

    # Remove BM25 index files if present
    bm25_dir = cfg.get("bm25", {}).get("index_dir")
    if bm25_dir:
        import glob
        for f in glob.glob(str(Path(bm25_dir).expanduser() / f"{name}_*_bm25.json")):
            try:
                Path(f).unlink()
            except OSError:
                pass

    unregister_database(name)
    console.print(f"Deleted database: [bold]{name}[/bold]")


@database_group.command("select")
@click.argument("name")
def database_select(name: str) -> None:
    """Set the default target database for subsequent commands."""
    p = _selected_database_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(name)
    console.print(f"Default database set to: [bold]{name}[/bold]")


# ------------------------------------------------------------------ #
# kb database config                                                   #
# ------------------------------------------------------------------ #

@database_group.group("config")
def database_config_group() -> None:
    """Show or update database backend configuration."""


@database_config_group.command("show")
@click.pass_obj
def database_config_show(obj: dict) -> None:
    """Print the configuration for the current database."""
    db: str = obj["db"]
    cfg = load_db_config(db)
    console.print_json(json.dumps(cfg, indent=2))


@database_config_group.command("set")
@click.option("--graph-backend", "graph_backend", type=click.Choice(["file", "neo4j"]))
@click.option("--directory",      help="Graph data directory (file backend).")
@click.option("--neo4j-uri",      help="Neo4j bolt URI.")
@click.option("--neo4j-user",     help="Neo4j username.")
@click.option("--neo4j-password", help="Neo4j password.")
@click.pass_obj
def database_config_set(
    obj: dict,
    graph_backend: str | None,
    directory: str | None,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
) -> None:
    """Update graph backend configuration for the current database."""
    db: str = obj["db"]
    updates: dict[str, Any] = {}
    if graph_backend:
        updates["graph_backend"] = graph_backend
    graph_updates: dict[str, Any] = {}
    if directory:
        graph_updates["directory"] = directory
    if neo4j_uri:
        graph_updates["uri"] = neo4j_uri
    if neo4j_user:
        graph_updates["username"] = neo4j_user
    if neo4j_password:
        graph_updates["password"] = neo4j_password
    if graph_updates:
        updates["graph"] = graph_updates
    if not updates:
        raise click.ClickException("No options provided.")
    update_db_config(db, updates)
    console.print(f"Updated config for database: [bold]{db}[/bold]")


# ------------------------------------------------------------------ #
# kb database search config                                            #
# ------------------------------------------------------------------ #

@database_group.group("search")
def database_search_group() -> None:
    """Search backend configuration."""


@database_search_group.group("config")
def database_search_config_group() -> None:
    """Show or update search configuration for the current database."""


@database_search_config_group.command("show")
@click.pass_obj
def database_search_config_show(obj: dict) -> None:
    """Print search configuration for the current database."""
    db: str = obj["db"]
    cfg = load_db_config(db)
    search_cfg = {
        "search": cfg.get("search", "none"),
        "bm25": cfg.get("bm25", {}),
        "vector": cfg.get("vector", {}),
        "embedding": cfg.get("embedding", {}),
    }
    console.print_json(json.dumps(search_cfg, indent=2))


@database_search_config_group.command("set")
@click.option("--search",         type=click.Choice(["none", "bm25", "semantic"]), help="Search mode.")
@click.option("--bm25-index-dir", help="Directory for BM25 corpus files.")
@click.option("--vector-backend", "vector_backend", type=click.Choice(["memory", "file", "chromadb", "redis"]))
@click.option("--vector-directory", help="Directory for file vector backend.")
@click.option("--vector-path",    help="Path for ChromaDB backend.")
@click.option("--redis-host",     help="Redis host.")
@click.option("--redis-port",     type=int, help="Redis port.")
@click.option("--redis-db",       type=int, help="Redis database number.")
@click.option("--embedding-provider", "embedding_provider", type=click.Choice(["openai"]))
@click.option("--embedding-model", help="Embedding model name.")
@click.pass_obj
def database_search_config_set(
    obj: dict,
    search: str | None,
    bm25_index_dir: str | None,
    vector_backend: str | None,
    vector_directory: str | None,
    vector_path: str | None,
    redis_host: str | None,
    redis_port: int | None,
    redis_db: int | None,
    embedding_provider: str | None,
    embedding_model: str | None,
) -> None:
    """Update search configuration for the current database."""
    db: str = obj["db"]
    updates: dict[str, Any] = {}
    if search:
        updates["search"] = search
    if bm25_index_dir:
        updates["bm25"] = {"index_dir": bm25_index_dir}
    vcfg: dict[str, Any] = {}
    if vector_backend:
        vcfg["backend"] = vector_backend
    if vector_directory:
        vcfg["directory"] = vector_directory
    if vector_path:
        vcfg["path"] = vector_path
    conn: dict[str, Any] = {}
    if redis_host:
        conn["host"] = redis_host
    if redis_port is not None:
        conn["port"] = redis_port
    if redis_db is not None:
        conn["db"] = redis_db
    if conn:
        vcfg["connection"] = conn
    if vcfg:
        updates["vector"] = vcfg
    ecfg: dict[str, Any] = {}
    if embedding_provider:
        ecfg["provider"] = embedding_provider
    if embedding_model:
        ecfg["model"] = embedding_model
    if ecfg:
        updates["embedding"] = ecfg
    if not updates:
        raise click.ClickException("No options provided.")
    update_db_config(db, updates)
    console.print(f"Updated search config for database: [bold]{db}[/bold]")


# ------------------------------------------------------------------ #
# kb schema                                                            #
# ------------------------------------------------------------------ #

@cli.group("schema")
def schema_group() -> None:
    """Schema management."""


@schema_group.command("list")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_list(obj: dict, fmt: str) -> None:
    """List classes, relations, and their properties for the current database."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        schema = _run(kb.fetch_database_schema(db))
    except KBError as exc:
        raise click.ClickException(str(exc))

    if fmt == "json":
        console.print_json(json.dumps(schema.to_json()))
        return

    if not schema.entities and not schema.relations:
        console.print("[dim](no schema declared)[/dim]")
        return

    # IS_SUBCLASS_OF entries appear in schema.relations; separate them out
    class_names = set(schema.entities.keys())
    subclass_rels = [r for r in schema.relations.values() if r["name"] == "IS_SUBCLASS_OF"]
    real_relations = {k: v for k, v in schema.relations.items() if v["name"] != "IS_SUBCLASS_OF"}
    class_hierarchy  = [(r["source"], r["target"]) for r in subclass_rels if r["source"] in class_names]
    relation_hierarchy = [(r["source"], r["target"]) for r in subclass_rels if r["source"] not in class_names]

    # ---- Classes ----
    if schema.entities:
        t = Table(title="Classes", show_header=True, header_style="bold")
        t.add_column("Name",        style="bold", min_width=12)
        t.add_column("Description")
        for entity in sorted(schema.entities.values(), key=lambda e: e["name"]):
            t.add_row(entity["name"], entity.get("description", ""))
        console.print(t)

    # ---- Class Properties ----
    class_props = [
        (entity["name"], attr_name, attr.get("description", ""))
        for entity in schema.entities.values()
        for attr_name, attr in entity.get("contents", {}).get("attributes", {}).items()
    ]
    if class_props:
        t = Table(title="Class Properties", show_header=True, header_style="bold")
        t.add_column("Class",       style="bold", min_width=12)
        t.add_column("Property",    style="bold", min_width=12)
        t.add_column("Description")
        for row in sorted(class_props):
            t.add_row(*row)
        console.print(t)

    # ---- Class Hierarchy ----
    if class_hierarchy:
        t = Table(title="Class Hierarchy", show_header=True, header_style="bold")
        t.add_column("Child",  style="bold", min_width=12)
        t.add_column("Parent", style="dim",  min_width=12)
        for child, parent in sorted(class_hierarchy):
            t.add_row(child, parent)
        console.print(t)

    # ---- Relations ----
    if real_relations:
        t = Table(title="Relations", show_header=True, header_style="bold")
        t.add_column("Source",   style="dim",  min_width=10)
        t.add_column("Relation", style="bold", min_width=12)
        t.add_column("Target",   style="dim",  min_width=10)
        t.add_column("Description")
        for rel in sorted(real_relations.values(), key=lambda r: r["name"]):
            t.add_row(
                rel.get("source", ""),
                rel["name"],
                rel.get("target", ""),
                rel.get("description", ""),
            )
        console.print(t)

    # ---- Relation Properties ----
    rel_props = [
        (rel["name"], attr_name, attr.get("description", ""))
        for rel in real_relations.values()
        for attr_name, attr in rel.get("contents", {}).get("attributes", {}).items()
    ]
    if rel_props:
        t = Table(title="Relation Properties", show_header=True, header_style="bold")
        t.add_column("Relation", style="bold", min_width=12)
        t.add_column("Property", style="bold", min_width=12)
        t.add_column("Description")
        for row in sorted(rel_props):
            t.add_row(*row)
        console.print(t)

    # ---- Relation Hierarchy ----
    if relation_hierarchy:
        t = Table(title="Relation Hierarchy", show_header=True, header_style="bold")
        t.add_column("Child",  style="bold", min_width=12)
        t.add_column("Parent", style="dim",  min_width=12)
        for child, parent in sorted(relation_hierarchy):
            t.add_row(child, parent)
        console.print(t)


# ------------------------------------------------------------------ #
# kb schema class                                                      #
# ------------------------------------------------------------------ #

@schema_group.group("class")
def schema_class_group() -> None:
    """Class schema management."""


@schema_class_group.command("add")
@click.argument("name")
@click.argument("description")
@click.pass_obj
def schema_class_add(obj: dict, name: str, description: str) -> None:
    """Declare a CLASS node (idempotent)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        class_id = _run(kb.declare_class(db, name, description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added class: [bold]{name}[/bold] (id: {class_id})")


@schema_class_group.command("list")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_class_list(obj: dict, fmt: str) -> None:
    """List all declared classes."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        schema = _run(kb.fetch_database_schema(db))
    except KBError as exc:
        raise click.ClickException(str(exc))
    rows = [
        {"name": e["name"], "description": e.get("description", "")}
        for e in sorted(schema.entities.values(), key=lambda e: e["name"])
    ]
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no classes)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name",        style="bold", min_width=12)
        t.add_column("Description")
        for r in rows:
            t.add_row(r["name"], r["description"])
        console.print(t)


@schema_class_group.command("update")
@click.option("--class",     "class_name", default=None, help="Class name.")
@click.option("--class-id",               default=None, help="Class node ID (overrides --class).")
@click.option("--name",                   default=None, help="New name.")
@click.option("--description",            default=None, help="New description.")
@click.pass_obj
def schema_class_update(
    obj: dict, class_name: str | None, class_id: str | None,
    name: str | None, description: str | None,
) -> None:
    """Update the name and/or description of a class."""
    if not name and not description:
        raise click.UsageError("Provide at least one of --name or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    cid = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    try:
        _run(kb.update_class(db, cid, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Updated class: [bold]{name or class_name or class_id}[/bold]")


# ------------------------------------------------------------------ #
# kb schema class parent / child                                       #
# ------------------------------------------------------------------ #

def _class_ancestors(kb: FileKBSource, db: str, class_id: str, visited: set[str] | None = None) -> list[dict]:
    """Return all ancestor classes via IS_SUBCLASS_OF, breadth-first."""
    if visited is None:
        visited = set()
    results: list[dict] = []
    queue = [class_id]
    while queue:
        current = queue.pop(0)
        for nb in kb.get_neighbors(db, current, edge_type=IS_SUBCLASS_OF, direction="out"):
            node = nb["node"]
            nid = node["id"]
            if nid not in visited:
                visited.add(nid)
                results.append(node)
                queue.append(nid)
    return results


def _class_descendants(kb: FileKBSource, db: str, class_id: str, visited: set[str] | None = None) -> list[dict]:
    """Return all descendant classes via IS_SUBCLASS_OF, breadth-first."""
    if visited is None:
        visited = set()
    results: list[dict] = []
    queue = [class_id]
    while queue:
        current = queue.pop(0)
        for nb in kb.get_neighbors(db, current, edge_type=IS_SUBCLASS_OF, direction="in"):
            node = nb["node"]
            nid = node["id"]
            if nid not in visited:
                visited.add(nid)
                results.append(node)
                queue.append(nid)
    return results


def _build_parent_tree(kb: FileKBSource, db: str, class_id: str, label: str, tree: Tree, visited: set[str]) -> None:
    """Recursively attach parent nodes to a Rich Tree."""
    for nb in kb.get_neighbors(db, class_id, edge_type=IS_SUBCLASS_OF, direction="out"):
        node = nb["node"]
        nid = node["id"]
        name = node.get("properties", {}).get("name", nid)
        branch = tree.add(f"[bold]{name}[/bold]")
        if nid not in visited:
            visited.add(nid)
            _build_parent_tree(kb, db, nid, name, branch, visited)


def _build_child_tree(kb: FileKBSource, db: str, class_id: str, label: str, tree: Tree, visited: set[str]) -> None:
    """Recursively attach child nodes to a Rich Tree."""
    for nb in kb.get_neighbors(db, class_id, edge_type=IS_SUBCLASS_OF, direction="in"):
        node = nb["node"]
        nid = node["id"]
        name = node.get("properties", {}).get("name", nid)
        branch = tree.add(f"[bold]{name}[/bold]")
        if nid not in visited:
            visited.add(nid)
            _build_child_tree(kb, db, nid, name, branch, visited)


@schema_class_group.group("parent")
def schema_class_parent_group() -> None:
    """Class parent (IS_SUBCLASS_OF) management."""


@schema_class_parent_group.command("add")
@click.option("--class",     "class_name",  default=None, required=False, help="Child class name.")
@click.option("--class-id",                 default=None, required=False, help="Child class node ID.")
@click.option("--parent",    "parent_name", default=None, required=False, help="Parent class name.")
@click.option("--parent-id",                default=None, required=False, help="Parent class node ID.")
@click.pass_obj
def schema_class_parent_add(
    obj: dict, class_name: str | None, class_id: str | None,
    parent_name: str | None, parent_id: str | None,
) -> None:
    """Wire child IS_SUBCLASS_OF parent."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_child  = _resolve_node(kb, db, CLASS, class_name,  class_id,  "class")
    resolved_parent = _resolve_node(kb, db, CLASS, parent_name, parent_id, "parent class")
    try:
        _run(kb.add_is_subclass_of(db, resolved_child, resolved_parent))
    except KBError as exc:
        raise click.ClickException(str(exc))
    child_label  = class_name  or class_id
    parent_label = parent_name or parent_id
    console.print(f"[bold]{child_label}[/bold] is now a subclass of [bold]{parent_label}[/bold]")


@schema_class_parent_group.command("remove")
@click.option("--class",     "class_name",  default=None, required=False, help="Child class name.")
@click.option("--class-id",                 default=None, required=False, help="Child class node ID.")
@click.option("--parent",    "parent_name", default=None, required=False, help="Parent class name.")
@click.option("--parent-id",                default=None, required=False, help="Parent class node ID.")
@click.pass_obj
def schema_class_parent_remove(
    obj: dict, class_name: str | None, class_id: str | None,
    parent_name: str | None, parent_id: str | None,
) -> None:
    """Remove IS_SUBCLASS_OF edge between child and parent."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_child  = _resolve_node(kb, db, CLASS, class_name,  class_id,  "class")
    resolved_parent = _resolve_node(kb, db, CLASS, parent_name, parent_id, "parent class")
    try:
        _run(kb.remove_is_subclass_of(db, resolved_child, resolved_parent))
    except KBError as exc:
        raise click.ClickException(str(exc))
    child_label  = class_name  or class_id
    parent_label = parent_name or parent_id
    console.print(f"Removed IS_SUBCLASS_OF: [bold]{child_label}[/bold] → [bold]{parent_label}[/bold]")


@schema_class_parent_group.command("list")
@click.option("--class",     "class_name", default=None, required=False, help="Class name.")
@click.option("--class-id",               default=None, required=False, help="Class node ID.")
@click.option("--recursive", is_flag=True, default=False, help="Include all ancestors.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json", "tree"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_class_parent_list(
    obj: dict, class_name: str | None, class_id: str | None, recursive: bool, fmt: str,
) -> None:
    """List parent classes (direct or all ancestors)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_id = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    root_label  = class_name or class_id
    if recursive:
        nodes = _class_ancestors(kb, db, resolved_id)
    else:
        nodes = [nb["node"] for nb in kb.get_neighbors(db, resolved_id, edge_type=IS_SUBCLASS_OF, direction="out")]
    if fmt == "json":
        console.print_json(json.dumps([
            {"name": n.get("properties", {}).get("name", n["id"]), "id": n["id"]}
            for n in nodes
        ]))
    elif fmt == "tree":
        t = Tree(f"[bold]{root_label}[/bold]")
        _build_parent_tree(kb, db, resolved_id, root_label, t, visited=set())
        console.print(t)
    else:
        if not nodes:
            console.print("[dim](no parents)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name", style="bold", min_width=14)
        t.add_column("ID",   style="dim")
        for n in nodes:
            t.add_row(n.get("properties", {}).get("name", n["id"]), n["id"])
        console.print(t)


@schema_class_group.group("child")
def schema_class_child_group() -> None:
    """Class child (subclasses) management."""


@schema_class_child_group.command("list")
@click.option("--class",     "class_name", default=None, required=False, help="Class name.")
@click.option("--class-id",               default=None, required=False, help="Class node ID.")
@click.option("--recursive", is_flag=True, default=False, help="Include all descendants.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json", "tree"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_class_child_list(
    obj: dict, class_name: str | None, class_id: str | None, recursive: bool, fmt: str,
) -> None:
    """List child classes (direct subclasses or all descendants)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_id = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    root_label  = class_name or class_id
    if recursive:
        nodes = _class_descendants(kb, db, resolved_id)
    else:
        nodes = [nb["node"] for nb in kb.get_neighbors(db, resolved_id, edge_type=IS_SUBCLASS_OF, direction="in")]
    if fmt == "json":
        console.print_json(json.dumps([
            {"name": n.get("properties", {}).get("name", n["id"]), "id": n["id"]}
            for n in nodes
        ]))
    elif fmt == "tree":
        t = Tree(f"[bold]{root_label}[/bold]")
        _build_child_tree(kb, db, resolved_id, root_label, t, visited=set())
        console.print(t)
    else:
        if not nodes:
            console.print("[dim](no children)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name", style="bold", min_width=14)
        t.add_column("ID",   style="dim")
        for n in nodes:
            t.add_row(n.get("properties", {}).get("name", n["id"]), n["id"])
        console.print(t)


@schema_class_group.group("property")
def schema_class_property_group() -> None:
    """Class property management."""


@schema_class_property_group.command("add")
@click.argument("name")
@click.argument("description")
@click.option("--class",    "class_name", default=None, help="Owning class name.")
@click.option("--class-id",              default=None, help="Owning class node ID (overrides --class).")
@click.pass_obj
def schema_class_property_add(
    obj: dict, name: str, description: str, class_name: str | None, class_id: str | None
) -> None:
    """Declare a PROPERTY on a class (idempotent)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_class_id: str | None = None
    if class_name or class_id:
        resolved_class_id = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    try:
        prop_id = _run(kb.declare_class_property(db, name, description, class_id=resolved_class_id))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added class property: [bold]{name}[/bold] (id: {prop_id})")


@schema_class_property_group.command("list")
@click.option("--class",    "class_name", default=None, help="Filter by class name.")
@click.option("--class-id",              default=None, help="Filter by class node ID.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_class_property_list(
    obj: dict, class_name: str | None, class_id: str | None, fmt: str
) -> None:
    """List class properties, optionally filtered by class."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    if class_name or class_id:
        resolved_class_id = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    else:
        resolved_class_id = None
    try:
        schema = _run(kb.fetch_database_schema(db))
    except KBError as exc:
        raise click.ClickException(str(exc))
    rows = []
    for ent in sorted(schema.entities.values(), key=lambda e: e["name"]):
        if resolved_class_id and ent.get("id") != resolved_class_id:
            if class_name and ent["name"] != class_name:
                continue
        for attr_name, attr in ent.get("contents", {}).get("attributes", {}).items():
            rows.append({
                "class":       ent["name"],
                "property":    attr_name,
                "description": attr.get("description", ""),
            })
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no class properties)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Class",       style="bold", min_width=12)
        t.add_column("Property",    style="bold", min_width=12)
        t.add_column("Description")
        for r in sorted(rows, key=lambda r: (r["class"], r["property"])):
            t.add_row(r["class"], r["property"], r["description"])
        console.print(t)


@schema_class_property_group.command("update")
@click.option("--property",  "property_name", default=None, help="Property name.")
@click.option("--property-id",                default=None, help="Property node ID (overrides --property).")
@click.option("--class",     "class_name",    default=None, help="Owning class name (disambiguates).")
@click.option("--class-id",                   default=None, help="Owning class node ID (disambiguates).")
@click.option("--name",                       default=None, help="New name.")
@click.option("--description",                default=None, help="New description.")
@click.pass_obj
def schema_class_property_update(
    obj: dict,
    property_name: str | None, property_id: str | None,
    class_name: str | None, class_id: str | None,
    name: str | None, description: str | None,
) -> None:
    """Update the name and/or description of a class property."""
    if not name and not description:
        raise click.UsageError("Provide at least one of --name or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    owner_id: str | None = None
    if class_name or class_id:
        owner_id = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    pid = _resolve_owned_property(kb, db, property_name, property_id, owner_id)
    try:
        _run(kb.update_property(db, pid, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Updated class property: [bold]{name or property_name or property_id}[/bold]")


@schema_class_property_group.command("delete")
@click.option("--property",  "property_name", default=None, help="Property name.")
@click.option("--property-id",                default=None, help="Property node ID (overrides --property).")
@click.option("--class",     "class_name",    default=None, help="Owning class name (disambiguates).")
@click.option("--class-id",                   default=None, help="Owning class node ID (disambiguates).")
@click.option("--yes",       is_flag=True,    default=False, help="Skip confirmation prompt.")
@click.pass_obj
def schema_class_property_delete(
    obj: dict,
    property_name: str | None, property_id: str | None,
    class_name: str | None, class_id: str | None,
    yes: bool,
) -> None:
    """Delete a class property (errors if values exist)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    owner_id: str | None = None
    if class_name or class_id:
        owner_id = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    pid = _resolve_owned_property(kb, db, property_name, property_id, owner_id)
    label = property_name or property_id
    if not yes:
        click.confirm(f"Delete class property '{label}'?", abort=True)
    try:
        _run(kb.delete_property(db, pid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted class property: [bold]{label}[/bold]")


@schema_class_group.command("delete")
@click.option("--class",    "class_name", default=None, help="Class name.")
@click.option("--class-id",               default=None, help="Class node ID (overrides --class).")
@click.option("--yes",      is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_obj
def schema_class_delete(
    obj: dict, class_name: str | None, class_id: str | None, yes: bool,
) -> None:
    """Delete a class (errors if instances or subclasses exist)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    cid = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    label = class_name or class_id
    if not yes:
        click.confirm(f"Delete class '{label}'?", abort=True)
    try:
        _run(kb.delete_class(db, cid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted class: [bold]{label}[/bold]")


# ------------------------------------------------------------------ #
# kb schema relation                                                   #
# ------------------------------------------------------------------ #

@schema_group.group("relation")
def schema_relation_group() -> None:
    """Relation schema management."""


@schema_relation_group.command("add")
@click.argument("name")
@click.argument("description")
@click.option("--domain", default=None, help="Domain class name.")
@click.option("--range", "range_", default=None, help="Range class name.")
@click.pass_obj
def schema_relation_add(
    obj: dict, name: str, description: str, domain: str | None, range_: str | None
) -> None:
    """Declare a RELATION node (idempotent)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        rel_id = _run(kb.declare_relation_type(db, name, description, domain=domain, range_=range_))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added relation: [bold]{name}[/bold] (id: {rel_id})")


@schema_relation_group.command("list")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_relation_list(obj: dict, fmt: str) -> None:
    """List all declared relations."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        schema = _run(kb.fetch_database_schema(db))
    except KBError as exc:
        raise click.ClickException(str(exc))
    real_relations = {k: v for k, v in schema.relations.items() if v["name"] != "IS_SUBCLASS_OF"}
    rows = [
        {
            "name":        r["name"],
            "source":      r.get("source", ""),
            "target":      r.get("target", ""),
            "description": r.get("description", ""),
        }
        for r in sorted(real_relations.values(), key=lambda r: r["name"])
    ]
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no relations)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name",        style="bold", min_width=12)
        t.add_column("Source",      min_width=10)
        t.add_column("Target",      min_width=10)
        t.add_column("Description")
        for r in rows:
            t.add_row(r["name"], r["source"], r["target"], r["description"])
        console.print(t)


@schema_relation_group.command("update")
@click.option("--relation",    "relation_name", default=None, help="Relation name.")
@click.option("--relation-id",                  default=None, help="Relation node ID (overrides --relation).")
@click.option("--name",                         default=None, help="New name.")
@click.option("--description",                  default=None, help="New description.")
@click.pass_obj
def schema_relation_update(
    obj: dict, relation_name: str | None, relation_id: str | None,
    name: str | None, description: str | None,
) -> None:
    """Update the name and/or description of a relation."""
    if not name and not description:
        raise click.UsageError("Provide at least one of --name or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    rid = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    try:
        _run(kb.update_relation(db, rid, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Updated relation: [bold]{name or relation_name or relation_id}[/bold]")


# ------------------------------------------------------------------ #
# kb schema relation parent / child                                    #
# ------------------------------------------------------------------ #

def _relation_ancestors(kb: FileKBSource, db: str, relation_id: str, visited: set[str] | None = None) -> list[dict]:
    """Return all ancestor relations via IS_SUBCLASS_OF, breadth-first."""
    if visited is None:
        visited = set()
    results: list[dict] = []
    queue = [relation_id]
    while queue:
        current = queue.pop(0)
        for nb in kb.get_neighbors(db, current, edge_type=IS_SUBCLASS_OF, direction="out"):
            node = nb["node"]
            nid = node["id"]
            if nid not in visited:
                visited.add(nid)
                results.append(node)
                queue.append(nid)
    return results


def _relation_descendants(kb: FileKBSource, db: str, relation_id: str, visited: set[str] | None = None) -> list[dict]:
    """Return all descendant relations via IS_SUBCLASS_OF, breadth-first."""
    if visited is None:
        visited = set()
    results: list[dict] = []
    queue = [relation_id]
    while queue:
        current = queue.pop(0)
        for nb in kb.get_neighbors(db, current, edge_type=IS_SUBCLASS_OF, direction="in"):
            node = nb["node"]
            nid = node["id"]
            if nid not in visited:
                visited.add(nid)
                results.append(node)
                queue.append(nid)
    return results


def _build_relation_parent_tree(kb: FileKBSource, db: str, relation_id: str, tree: Tree, visited: set[str]) -> None:
    for nb in kb.get_neighbors(db, relation_id, edge_type=IS_SUBCLASS_OF, direction="out"):
        node = nb["node"]
        nid = node["id"]
        name = node.get("properties", {}).get("name", nid)
        branch = tree.add(f"[bold]{name}[/bold]")
        if nid not in visited:
            visited.add(nid)
            _build_relation_parent_tree(kb, db, nid, branch, visited)


def _build_relation_child_tree(kb: FileKBSource, db: str, relation_id: str, tree: Tree, visited: set[str]) -> None:
    for nb in kb.get_neighbors(db, relation_id, edge_type=IS_SUBCLASS_OF, direction="in"):
        node = nb["node"]
        nid = node["id"]
        name = node.get("properties", {}).get("name", nid)
        branch = tree.add(f"[bold]{name}[/bold]")
        if nid not in visited:
            visited.add(nid)
            _build_relation_child_tree(kb, db, nid, branch, visited)


@schema_relation_group.group("parent")
def schema_relation_parent_group() -> None:
    """Relation parent (IS_SUBCLASS_OF) management."""


@schema_relation_parent_group.command("add")
@click.option("--relation",    "relation_name", default=None, required=False, help="Child relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Child relation node ID.")
@click.option("--parent",      "parent_name",   default=None, required=False, help="Parent relation name.")
@click.option("--parent-id",                    default=None, required=False, help="Parent relation node ID.")
@click.pass_obj
def schema_relation_parent_add(
    obj: dict, relation_name: str | None, relation_id: str | None,
    parent_name: str | None, parent_id: str | None,
) -> None:
    """Wire child IS_SUBCLASS_OF parent for relations."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_child  = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    resolved_parent = _resolve_node(kb, db, RELATION, parent_name,   parent_id,   "parent relation")
    try:
        _run(kb.add_is_subclass_of(db, resolved_child, resolved_parent))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(
        f"[bold]{relation_name or relation_id}[/bold] is now a sub-relation of "
        f"[bold]{parent_name or parent_id}[/bold]"
    )


@schema_relation_parent_group.command("remove")
@click.option("--relation",    "relation_name", default=None, required=False, help="Child relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Child relation node ID.")
@click.option("--parent",      "parent_name",   default=None, required=False, help="Parent relation name.")
@click.option("--parent-id",                    default=None, required=False, help="Parent relation node ID.")
@click.pass_obj
def schema_relation_parent_remove(
    obj: dict, relation_name: str | None, relation_id: str | None,
    parent_name: str | None, parent_id: str | None,
) -> None:
    """Remove IS_SUBCLASS_OF edge between child and parent relations."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_child  = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    resolved_parent = _resolve_node(kb, db, RELATION, parent_name,   parent_id,   "parent relation")
    try:
        _run(kb.remove_is_subclass_of(db, resolved_child, resolved_parent))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(
        f"Removed IS_SUBCLASS_OF: [bold]{relation_name or relation_id}[/bold]"
        f" → [bold]{parent_name or parent_id}[/bold]"
    )


@schema_relation_parent_group.command("list")
@click.option("--relation",    "relation_name", default=None, required=False, help="Relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Relation node ID.")
@click.option("--recursive", is_flag=True, default=False, help="Include all ancestors.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json", "tree"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_relation_parent_list(
    obj: dict, relation_name: str | None, relation_id: str | None, recursive: bool, fmt: str,
) -> None:
    """List parent relations (direct or all ancestors)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    root_label  = relation_name or relation_id
    if recursive:
        nodes = _relation_ancestors(kb, db, resolved_id)
    else:
        nodes = [nb["node"] for nb in kb.get_neighbors(db, resolved_id, edge_type=IS_SUBCLASS_OF, direction="out")]
    if fmt == "json":
        console.print_json(json.dumps([
            {"name": n.get("properties", {}).get("name", n["id"]), "id": n["id"]}
            for n in nodes
        ]))
    elif fmt == "tree":
        t = Tree(f"[bold]{root_label}[/bold]")
        _build_relation_parent_tree(kb, db, resolved_id, t, visited=set())
        console.print(t)
    else:
        if not nodes:
            console.print("[dim](no parents)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name", style="bold", min_width=14)
        t.add_column("ID",   style="dim")
        for n in nodes:
            t.add_row(n.get("properties", {}).get("name", n["id"]), n["id"])
        console.print(t)


@schema_relation_group.group("child")
def schema_relation_child_group() -> None:
    """Relation child (sub-relations) management."""


@schema_relation_child_group.command("list")
@click.option("--relation",    "relation_name", default=None, required=False, help="Relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Relation node ID.")
@click.option("--recursive", is_flag=True, default=False, help="Include all descendants.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json", "tree"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_relation_child_list(
    obj: dict, relation_name: str | None, relation_id: str | None, recursive: bool, fmt: str,
) -> None:
    """List child relations (direct sub-relations or all descendants)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    root_label  = relation_name or relation_id
    if recursive:
        nodes = _relation_descendants(kb, db, resolved_id)
    else:
        nodes = [nb["node"] for nb in kb.get_neighbors(db, resolved_id, edge_type=IS_SUBCLASS_OF, direction="in")]
    if fmt == "json":
        console.print_json(json.dumps([
            {"name": n.get("properties", {}).get("name", n["id"]), "id": n["id"]}
            for n in nodes
        ]))
    elif fmt == "tree":
        t = Tree(f"[bold]{root_label}[/bold]")
        _build_relation_child_tree(kb, db, resolved_id, t, visited=set())
        console.print(t)
    else:
        if not nodes:
            console.print("[dim](no children)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name", style="bold", min_width=14)
        t.add_column("ID",   style="dim")
        for n in nodes:
            t.add_row(n.get("properties", {}).get("name", n["id"]), n["id"])
        console.print(t)


@schema_relation_group.group("property")
def schema_relation_property_group() -> None:
    """Relation property management."""


@schema_relation_property_group.command("add")
@click.argument("name")
@click.argument("description")
@click.option("--relation",    "relation_name", default=None, help="Owning relation name.")
@click.option("--relation-id",                  default=None, help="Owning relation node ID (overrides --relation).")
@click.pass_obj
def schema_relation_property_add(
    obj: dict, name: str, description: str, relation_name: str | None, relation_id: str | None
) -> None:
    """Declare a PROPERTY on a relation (idempotent)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_relation_id: str | None = None
    if relation_name or relation_id:
        resolved_relation_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    try:
        prop_id = _run(kb.declare_relation_property(db, name, description, relation_id=resolved_relation_id))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added relation property: [bold]{name}[/bold] (id: {prop_id})")


@schema_relation_property_group.command("list")
@click.option("--relation",    "relation_name", default=None, help="Filter by relation name.")
@click.option("--relation-id",                  default=None, help="Filter by relation node ID.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_relation_property_list(
    obj: dict, relation_name: str | None, relation_id: str | None, fmt: str
) -> None:
    """List relation properties, optionally filtered by relation."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    if relation_name or relation_id:
        resolved_relation_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    else:
        resolved_relation_id = None
    try:
        schema = _run(kb.fetch_database_schema(db))
    except KBError as exc:
        raise click.ClickException(str(exc))
    real_relations = {k: v for k, v in schema.relations.items() if v["name"] != "IS_SUBCLASS_OF"}
    rows = []
    for rel in sorted(real_relations.values(), key=lambda r: r["name"]):
        if resolved_relation_id and rel.get("id") != resolved_relation_id:
            if relation_name and rel["name"] != relation_name:
                continue
        for attr_name, attr in rel.get("contents", {}).get("attributes", {}).items():
            rows.append({
                "relation":    rel["name"],
                "property":    attr_name,
                "description": attr.get("description", ""),
            })
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no relation properties)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Relation",    style="bold", min_width=12)
        t.add_column("Property",    style="bold", min_width=12)
        t.add_column("Description")
        for r in sorted(rows, key=lambda r: (r["relation"], r["property"])):
            t.add_row(r["relation"], r["property"], r["description"])
        console.print(t)


@schema_relation_property_group.command("update")
@click.option("--property",    "property_name",  default=None, help="Property name.")
@click.option("--property-id",                   default=None, help="Property node ID (overrides --property).")
@click.option("--relation",    "relation_name",  default=None, help="Owning relation name (disambiguates).")
@click.option("--relation-id",                   default=None, help="Owning relation node ID (disambiguates).")
@click.option("--name",                          default=None, help="New name.")
@click.option("--description",                   default=None, help="New description.")
@click.pass_obj
def schema_relation_property_update(
    obj: dict,
    property_name: str | None, property_id: str | None,
    relation_name: str | None, relation_id: str | None,
    name: str | None, description: str | None,
) -> None:
    """Update the name and/or description of a relation property."""
    if not name and not description:
        raise click.UsageError("Provide at least one of --name or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    owner_id: str | None = None
    if relation_name or relation_id:
        owner_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    pid = _resolve_owned_property(kb, db, property_name, property_id, owner_id)
    try:
        _run(kb.update_property(db, pid, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Updated relation property: [bold]{name or property_name or property_id}[/bold]")


@schema_relation_property_group.command("delete")
@click.option("--property",    "property_name",  default=None, help="Property name.")
@click.option("--property-id",                   default=None, help="Property node ID (overrides --property).")
@click.option("--relation",    "relation_name",  default=None, help="Owning relation name (disambiguates).")
@click.option("--relation-id",                   default=None, help="Owning relation node ID (disambiguates).")
@click.option("--yes",         is_flag=True,     default=False, help="Skip confirmation prompt.")
@click.pass_obj
def schema_relation_property_delete(
    obj: dict,
    property_name: str | None, property_id: str | None,
    relation_name: str | None, relation_id: str | None,
    yes: bool,
) -> None:
    """Delete a relation property (errors if values exist)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    owner_id: str | None = None
    if relation_name or relation_id:
        owner_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    pid = _resolve_owned_property(kb, db, property_name, property_id, owner_id)
    label = property_name or property_id
    if not yes:
        click.confirm(f"Delete relation property '{label}'?", abort=True)
    try:
        _run(kb.delete_property(db, pid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted relation property: [bold]{label}[/bold]")


@schema_relation_group.command("delete")
@click.option("--relation",    "relation_name", default=None, help="Relation name.")
@click.option("--relation-id",                  default=None, help="Relation node ID (overrides --relation).")
@click.option("--yes",         is_flag=True,    default=False, help="Skip confirmation prompt.")
@click.pass_obj
def schema_relation_delete(
    obj: dict, relation_name: str | None, relation_id: str | None, yes: bool,
) -> None:
    """Delete a relation (errors if facts or sub-relations exist)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    rid = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    label = relation_name or relation_id
    if not yes:
        click.confirm(f"Delete relation '{label}'?", abort=True)
    try:
        _run(kb.delete_relation(db, rid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted relation: [bold]{label}[/bold]")


@schema_group.command("search")
@click.argument("query")
@click.option(
    "--type", "types",
    type=click.Choice(["class", "relation", "property"], case_sensitive=False),
    multiple=True,
    help="Restrict to a node type (repeatable). Default: all types.",
)
@click.option(
    "--mode",
    type=click.Choice(["keyword", "semantic"]),
    default="keyword",
    show_default=True,
    help="Search mode. 'semantic' requires the database to be registered with --search semantic.",
)
@click.option(
    "--top-k", "top_k", default=10, show_default=True, help="Maximum results to return.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_search(obj: dict, query: str, types: tuple[str, ...], mode: str, top_k: int, fmt: str) -> None:
    """Search schema elements by keyword or semantic similarity."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    type_filter = [t.upper() for t in types] if types else None
    try:
        results = _run(kb.search_schema(db, query, k=top_k, mode=mode, type_filter=type_filter))
    except KBError as exc:
        raise click.ClickException(str(exc))
    if fmt == "json":
        console.print_json(json.dumps([
            {"id": r.id, "type": r.type, "name": r.name, "description": r.description}
            for r in results
        ]))
    else:
        if not results:
            console.print("[dim](no results)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Type",        style="dim", width=10)
        t.add_column("Name",        style="bold")
        t.add_column("Description")
        for r in results:
            t.add_row(r.type, r.name, r.description)
        console.print(t)


# ------------------------------------------------------------------ #
# kb data                                                              #
# ------------------------------------------------------------------ #

@cli.group("data")
def data_group() -> None:
    """Instance data management."""


@data_group.command("reindex")
@click.pass_obj
def data_reindex(obj: dict) -> None:
    """Rebuild the search index for the current database.

    Iterates every node in the graph and re-populates both the BM25 corpus
    and vector index (if configured).  Required after enabling search on an
    existing database or after bulk imports that bypass the event system.
    """
    kb: KBSource = obj["kb"]
    db: str = obj["db"]
    if not hasattr(kb, "_search_indexer") or kb._search_indexer is None:
        raise click.ClickException(
            "Search is not enabled for this database.  "
            "Register it with --search bm25 or --search semantic first."
        )
    try:
        kb.reindex(db)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Search index rebuilt for database: [bold]{db}[/bold]")


# ------------------------------------------------------------------ #
# kb data source                                                       #
# ------------------------------------------------------------------ #

@data_group.group("source")
def source_group() -> None:
    """Provenance source management."""


@source_group.command("add")
@click.argument("source")
@click.option("--name", default=None, help="Human-readable label (defaults to source value).")
@click.option("--description", default=None, help="Optional description.")
@click.pass_obj
def source_add(obj: dict, source: str, name: str | None, description: str | None) -> None:
    """Register a source document and return its ID."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        src_id = _run(kb.add_source(db, source, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added source: [bold]{source}[/bold] (id: {src_id})")


@source_group.command("list")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def source_list(obj: dict, fmt: str) -> None:
    """List all registered provenance sources."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    rows = [
        {
            "id":          n["id"],
            "name":        n.get("properties", {}).get("name", ""),
            "source":      n.get("properties", {}).get("source", ""),
            "description": n.get("properties", {}).get("description", ""),
        }
        for n in kb.match_nodes(db, label=SOURCE)
    ]
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no sources)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name",        style="bold")
        t.add_column("Source",      style="dim")
        t.add_column("Description", style="dim")
        for r in sorted(rows, key=lambda x: x["name"]):
            t.add_row(r["name"], r["source"], r["description"])
        console.print(t)


@source_group.command("update")
@click.option("--source",      "source_name", default=None, help="Source name.")
@click.option("--source-id",                  default=None, help="Source node ID (overrides --source).")
@click.option("--name",                       default=None, help="New name.")
@click.option("--description",                default=None, help="New description.")
@click.pass_obj
def source_update(
    obj: dict, source_name: str | None, source_id: str | None,
    name: str | None, description: str | None,
) -> None:
    """Update the name and/or description of a source."""
    if not name and not description:
        raise click.UsageError("Provide at least one of --name or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    sid = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        _run(kb.update_source(db, sid, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Updated source: [bold]{name or source_name or source_id}[/bold]")


@source_group.command("delete")
@click.option("--source",    "source_name", default=None, help="Source name.")
@click.option("--source-id",               default=None, help="Source node ID (overrides --source).")
@click.option("--yes",       is_flag=True,  default=False, help="Skip confirmation prompt.")
@click.pass_obj
def source_delete(
    obj: dict, source_name: str | None, source_id: str | None, yes: bool,
) -> None:
    """Delete a source and all its extraction records.

    Instance nodes (entities, facts, property values) are preserved;
    only the provenance links to this source are removed.
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    sid = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    label = source_name or source_id
    if not yes:
        click.confirm(
            f"Delete source '{label}' and its extraction records?", abort=True
        )
    try:
        _run(kb.delete_source(db, sid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted source: [bold]{label}[/bold]")


# ------------------------------------------------------------------ #
# kb data extraction                                                   #
# ------------------------------------------------------------------ #

# ------------------------------------------------------------------ #
# kb data entity                                                       #
# ------------------------------------------------------------------ #

@data_group.group("entity")
def entity_group() -> None:
    """Entity instance management."""


@entity_group.command("add")
@click.option("--name",                        default=None, help="Entity name.")
@click.option("--class",       "class_name",   default=None, help="Class name (resolved to id).")
@click.option("--class-id",                    default=None, help="Class node ID (overrides --class).")
@click.option("--description",                 default=None, help="Optional description.")
@click.option("--text",                        default=None, help="Source text span (creates provenance).")
@click.option("--source",      "source_name",  default=None, help="Source name.")
@click.option("--source-id",                   default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def entity_add(
    obj: dict,
    name: str | None,
    class_name: str | None,
    class_id: str | None,
    description: str | None,
    text: str | None,
    source_name: str | None,
    source_id: str | None,
) -> None:
    """Add an entity instance.

    Identify the class by name (--class PERSON) or by ID (--class-id <uuid>).
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_class_id: str | None = None
    if class_id or class_name:
        resolved_class_id = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        entity_id = _run(kb.add_entity(
            db, name, description=description,
            class_id=resolved_class_id, text=text, source_id=resolved_source_id,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    label = f"[bold]{name}[/bold]" if name else ""
    console.print(f"Added entity{': ' + label if label else ''} (id: {entity_id})")


@entity_group.command("list")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def entity_list(obj: dict, show_provenance: bool, fmt: str) -> None:
    """List all entity instances."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    rows = []
    for node in kb.match_nodes(db, label=ENTITY):
        nid = node["id"]
        class_nbs = kb.get_neighbors(db, nid, edge_type=IS_INSTANCE_OF, direction="out")
        class_name = (
            class_nbs[0]["node"].get("properties", {}).get("name", "") if class_nbs else ""
        )
        row: dict = {
            "id":    nid,
            "name":  node.get("properties", {}).get("name", ""),
            "class": class_name,
        }
        if show_provenance:
            row["provenance"] = _provenance_text(kb, db, nid)
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no entities)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name",  style="bold")
        t.add_column("Class", style="dim")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in sorted(rows, key=lambda x: (x["class"], x["name"])):
            row_cells = [r["name"], r["class"]]
            if show_provenance:
                row_cells.append(r.get("provenance", ""))
            t.add_row(*row_cells)
        console.print(t)


@entity_group.command("update")
@click.option("--entity",      "entity_name", default=None, help="Entity name.")
@click.option("--entity-id",                  default=None, help="Entity node ID (overrides --entity).")
@click.option("--name",                       default=None, help="New name.")
@click.option("--description",                default=None, help="New description.")
@click.pass_obj
def entity_update(
    obj: dict, entity_name: str | None, entity_id: str | None,
    name: str | None, description: str | None,
) -> None:
    """Update the name and/or description of an entity."""
    if not name and not description:
        raise click.UsageError("Provide at least one of --name or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    try:
        _run(kb.update_entity(db, eid, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    label = name or entity_name or entity_id
    console.print(f"Updated entity: [bold]{label}[/bold]")


@entity_group.command("delete")
@click.option("--entity",    "entity_name", default=None, help="Entity name.")
@click.option("--entity-id",               default=None, help="Entity node ID (overrides --entity).")
@click.option("--cascade",   is_flag=True,  default=False, help="Also delete all facts referencing this entity.")
@click.option("--yes",       is_flag=True,  default=False, help="Skip confirmation prompt.")
@click.pass_obj
def entity_delete(
    obj: dict, entity_name: str | None, entity_id: str | None, cascade: bool, yes: bool,
) -> None:
    """Delete an entity, its property values, and optionally its facts."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    label = entity_name or entity_id
    if not yes:
        suffix = " and all referencing facts" if cascade else ""
        click.confirm(f"Delete entity '{label}'{suffix}?", abort=True)
    try:
        _run(kb.delete_entity(db, eid, cascade=cascade))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted entity: [bold]{label}[/bold]")


# ------------------------------------------------------------------ #
# kb data entity extraction                                            #
# ------------------------------------------------------------------ #

@entity_group.group("extraction")
def entity_extraction_group() -> None:
    """Provenance extraction management for entities."""


@entity_extraction_group.command("add")
@click.option("--entity",      "entity_name", default=None, help="Entity name.")
@click.option("--entity-id",                  default=None, help="Entity node ID (overrides --entity).")
@click.option("--text",        required=True, help="New source text span.")
@click.option("--source",      "source_name", default=None, help="Source name.")
@click.option("--source-id",                  default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def entity_extraction_add(
    obj: dict,
    entity_name: str | None, entity_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
) -> None:
    """Link additional source text to an existing entity.

    Unlike `--text` on `entity add`, this does not create a new node — it
    adds another EXTRACTED_FROM edge to an entity that already exists, so
    multiple text spans can accumulate over time.
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        extraction_id = _run(kb.add_entity_extraction(db, eid, text, source_id=resolved_source_id))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added extraction (id: {extraction_id})")


# ------------------------------------------------------------------ #
# kb entity property                                                   #
# ------------------------------------------------------------------ #

@entity_group.group("property")
def entity_property_group() -> None:
    """Entity property-value management."""


@entity_property_group.command("set")
@click.argument("value")
@click.option("--name",                          default=None, help="Literal name (defaults to value).")
@click.option("--description",                   default=None, help="Optional description.")
@click.option("--entity",       "entity_name",   default=None, help="Entity name.")
@click.option("--entity-id",                     default=None, help="Entity node ID (overrides --entity).")
@click.option("--property",     "property_name", default=None, help="Property name.")
@click.option("--property-id",                   default=None, help="Property node ID (overrides --property).")
@click.option(
    "--type", "literal_type",
    type=click.Choice(["NUMBER", "DATE", "BOOLEAN", "STRING"]),
    default=None,
    help="Value type (inferred if omitted).",
)
@click.option("--text",                          default=None, help="Source text span (creates provenance).")
@click.option("--source",       "source_name",   default=None, help="Source name.")
@click.option("--source-id",                     default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def entity_property_set(
    obj: dict,
    value: str,
    name: str | None,
    description: str | None,
    entity_name: str | None,
    entity_id: str | None,
    property_name: str | None,
    property_id: str | None,
    literal_type: str | None,
    text: str | None,
    source_name: str | None,
    source_id: str | None,
) -> None:
    """Set a property value on an entity.

    \b
    Examples:
      kb data entity property set 42 --entity Alice --property AGE --type NUMBER
      kb data entity property set "1990-03-15" --entity Alice --property BIRTH_DATE --type DATE
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    pid: str | None = None
    if property_name or property_id:
        pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    coerced = _coerce_value(value, literal_type)
    try:
        lit_id = _run(kb.add_entity_property_value(
            db, coerced,
            name=name, description=description,
            entity_id=eid, property_id=pid, literal_type=literal_type,
            text=text, source_id=resolved_source_id,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Set entity property (literal id: {lit_id})")


@entity_property_group.command("list")
@click.option("--entity",          "entity_name",    default=None, help="Entity name.")
@click.option("--entity-id",                         default=None, help="Entity node ID (overrides --entity).")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def entity_property_list(
    obj: dict, entity_name: str | None, entity_id: str | None, show_provenance: bool, fmt: str
) -> None:
    """List all property values for an entity."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    rows = []
    for nb in kb.get_neighbors(db, eid, edge_type=HAS_PROPERTY, direction="out"):
        lit = nb["node"]
        lit_props = lit.get("properties", {})
        prop_nbs = kb.get_neighbors(db, lit["id"], edge_type=IS_VALUE_OF, direction="out")
        prop_name = (
            prop_nbs[0]["node"].get("properties", {}).get("name", "?") if prop_nbs else "?"
        )
        row: dict = {
            "property": prop_name,
            "value":    str(lit_props.get("value", "")),
            "type":     lit_props.get("type", ""),
            "id":       lit["id"],
        }
        if show_provenance:
            row["provenance"] = _provenance_text(kb, db, lit["id"])
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        t = Table(show_header=True, header_style="bold")
        t.add_column("Property", style="bold")
        t.add_column("Value")
        t.add_column("Type",     style="dim")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in rows:
            row_cells = [r["property"], r["value"], r["type"]]
            if show_provenance:
                row_cells.append(r.get("provenance", ""))
            t.add_row(*row_cells)
        console.print(t)


@entity_property_group.command("update")
@click.option("--entity",       "entity_name",   default=None, help="Entity name.")
@click.option("--entity-id",                     default=None, help="Entity node ID (overrides --entity).")
@click.option("--property",     "property_name", default=None, help="Property name.")
@click.option("--property-id",                   default=None, help="Property node ID (overrides --property).")
@click.option("--value",                         default=None, help="New value.")
@click.option("--name",                          default=None, help="New literal name.")
@click.option("--description",                   default=None, help="New description.")
@click.pass_obj
def entity_property_update(
    obj: dict,
    entity_name: str | None, entity_id: str | None,
    property_name: str | None, property_id: str | None,
    value: str | None, name: str | None, description: str | None,
) -> None:
    """Update the value, name, and/or description of an entity property value."""
    if not value and not name and not description:
        raise click.UsageError("Provide at least one of --value, --name, or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    coerced = _coerce_value(value, None) if value is not None else None
    try:
        _run(kb.update_entity_property_value(db, eid, pid, value=coerced, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Updated entity property value.")


@entity_property_group.command("delete")
@click.option("--entity",       "entity_name",   default=None, help="Entity name.")
@click.option("--entity-id",                     default=None, help="Entity node ID (overrides --entity).")
@click.option("--property",     "property_name", default=None, help="Property name.")
@click.option("--property-id",                   default=None, help="Property node ID (overrides --property).")
@click.option("--yes",          is_flag=True,    default=False, help="Skip confirmation prompt.")
@click.pass_obj
def entity_property_delete(
    obj: dict,
    entity_name: str | None, entity_id: str | None,
    property_name: str | None, property_id: str | None,
    yes: bool,
) -> None:
    """Delete a property value from an entity."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    if not yes:
        click.confirm(
            f"Delete property '{property_name or property_id}' value from entity '{entity_name or entity_id}'?",
            abort=True,
        )
    try:
        _run(kb.delete_entity_property_value(db, eid, pid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted entity property value.")


# ------------------------------------------------------------------ #
# kb data entity property extraction                                   #
# ------------------------------------------------------------------ #

@entity_property_group.group("extraction")
def entity_property_extraction_group() -> None:
    """Provenance extraction management for entity property values."""


@entity_property_extraction_group.command("add")
@click.option("--entity",       "entity_name",   default=None, help="Entity name.")
@click.option("--entity-id",                     default=None, help="Entity node ID (overrides --entity).")
@click.option("--property",     "property_name", default=None, help="Property name.")
@click.option("--property-id",                   default=None, help="Property node ID (overrides --property).")
@click.option("--text",                          required=True, help="New source text span.")
@click.option("--source",       "source_name",   default=None, help="Source name.")
@click.option("--source-id",                     default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def entity_property_extraction_add(
    obj: dict,
    entity_name: str | None, entity_id: str | None,
    property_name: str | None, property_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
) -> None:
    """Link additional source text to an entity's existing property value."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        extraction_id = _run(
            kb.add_entity_property_value_extraction(db, eid, pid, text, source_id=resolved_source_id)
        )
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added extraction (id: {extraction_id})")


# ------------------------------------------------------------------ #
# kb data fact                                                         #
# ------------------------------------------------------------------ #

@data_group.group("fact")
def fact_group() -> None:
    """Fact management."""


@fact_group.command("add")
@click.option("--name",                         default=None, help="Fact name (auto-generated from head/relation/tail if omitted).")
@click.option("--description",                  default=None, help="Optional description.")
@click.option("--head",        "head_name",     default=None, help="Head entity name.")
@click.option("--head-id",                      default=None, help="Head entity node ID (overrides --head).")
@click.option("--relation",    "relation_name", default=None, help="Relation name.")
@click.option("--relation-id",                  default=None, help="Relation node ID (overrides --relation).")
@click.option("--tail",        "tail_name",     default=None, help="Tail entity name.")
@click.option("--tail-id",                      default=None, help="Tail entity node ID (overrides --tail).")
@click.option("--text",                         default=None, help="Source text span (creates provenance).")
@click.option("--source",      "source_name",   default=None, help="Source name.")
@click.option("--source-id",                    default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def fact_add(
    obj: dict,
    name: str | None,
    description: str | None,
    head_name: str | None,
    head_id: str | None,
    relation_name: str | None,
    relation_id: str | None,
    tail_name: str | None,
    tail_id: str | None,
    text: str | None,
    source_name: str | None,
    source_id: str | None,
) -> None:
    """Add a fact (relation instance) between two entities.

    \b
    Resolve by name or by ID:
      --head Alice          --tail IBM       --relation WORKED_AT
      --head-id <uuid>      --tail-id <uuid> --relation-id <uuid>
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    head_entity_id: str | None = None
    if head_name or head_id:
        head_entity_id = _resolve_node(kb, db, ENTITY, head_name, head_id, "entity")
    resolved_relation_id: str | None = None
    if relation_name or relation_id:
        resolved_relation_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    tail_entity_id: str | None = None
    if tail_name or tail_id:
        tail_entity_id = _resolve_node(kb, db, ENTITY, tail_name, tail_id, "entity")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        fact_id = _run(kb.add_fact(
            db,
            name=name,
            description=description,
            head_entity_id=head_entity_id,
            tail_entity_id=tail_entity_id,
            relation_id=resolved_relation_id,
            text=text, source_id=resolved_source_id,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added fact (id: {fact_id})")


@fact_group.command("list")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def fact_list(obj: dict, show_provenance: bool, fmt: str) -> None:
    """List all facts."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]

    def _name(node_id: str | None) -> str:
        if node_id is None:
            return ""
        n = kb.get_node(db, node_id)
        return (n or {}).get("properties", {}).get("name", "") if n else ""

    def _first(node_id: str, edge: str) -> str | None:
        nbs = kb.get_neighbors(db, node_id, edge_type=edge, direction="out")
        return nbs[0]["node"]["id"] if nbs else None

    rows = []
    for node in kb.match_nodes(db, label=FACT):
        nid = node["id"]
        head_id = _first(nid, HEAD)
        tail_id = _first(nid, TAIL)
        rel_id  = _first(nid, REPRESENTS)
        row: dict = {
            "id":       nid,
            "head":     _name(head_id),
            "relation": _name(rel_id),
            "tail":     _name(tail_id),
        }
        if show_provenance:
            row["provenance"] = _provenance_text(kb, db, nid)
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no facts)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Head",     style="bold")
        t.add_column("Relation", style="dim")
        t.add_column("Tail",     style="bold")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in sorted(rows, key=lambda x: (x["relation"], x["head"])):
            row_cells = [r["head"], r["relation"], r["tail"]]
            if show_provenance:
                row_cells.append(r.get("provenance", ""))
            t.add_row(*row_cells)
        console.print(t)


@fact_group.command("update")
@click.option("--fact",    "fact_name", default=None, help="Fact name.")
@click.option("--fact-id",             default=None, help="Fact node ID (overrides --fact).")
@click.option("--name",                default=None, help="New name.")
@click.option("--description",         default=None, help="New description.")
@click.pass_obj
def fact_update(
    obj: dict, fact_name: str | None, fact_id: str | None,
    name: str | None, description: str | None,
) -> None:
    """Update the name and/or description of a fact."""
    if not name and not description:
        raise click.UsageError("Provide at least one of --name or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
    try:
        _run(kb.update_fact(db, fid, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    label = name or fact_name or fact_id
    console.print(f"Updated fact: [bold]{label}[/bold]")


@fact_group.command("delete")
@click.option("--fact",    "fact_name", default=None, help="Fact name.")
@click.option("--fact-id",             default=None, help="Fact node ID (overrides --fact).")
@click.option("--yes",     is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_obj
def fact_delete(
    obj: dict, fact_name: str | None, fact_id: str | None, yes: bool,
) -> None:
    """Delete a fact and its property values."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
    label = fact_name or fact_id
    if not yes:
        click.confirm(f"Delete fact '{label}'?", abort=True)
    try:
        _run(kb.delete_fact(db, fid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted fact: [bold]{label}[/bold]")


# ------------------------------------------------------------------ #
# kb data fact extraction                                              #
# ------------------------------------------------------------------ #

@fact_group.group("extraction")
def fact_extraction_group() -> None:
    """Provenance extraction management for facts."""


@fact_extraction_group.command("add")
@click.option("--fact",        "fact_name", default=None, help="Fact name.")
@click.option("--fact-id",                  default=None, help="Fact node ID (overrides --fact).")
@click.option("--text",        required=True, help="New source text span.")
@click.option("--source",      "source_name", default=None, help="Source name.")
@click.option("--source-id",                  default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def fact_extraction_add(
    obj: dict,
    fact_name: str | None, fact_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
) -> None:
    """Link additional source text to an existing fact.

    Unlike `--text` on `fact add`, this does not create a new node — it
    adds another EXTRACTED_FROM edge to a fact that already exists, so
    multiple text spans can accumulate over time.
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        extraction_id = _run(kb.add_fact_extraction(db, fid, text, source_id=resolved_source_id))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added extraction (id: {extraction_id})")


# ------------------------------------------------------------------ #
# kb fact property                                                     #
# ------------------------------------------------------------------ #

@fact_group.group("property")
def fact_property_group() -> None:
    """Fact property-value management."""


@fact_property_group.command("set")
@click.argument("value")
@click.option("--name",                       default=None,  help="Literal name (defaults to value).")
@click.option("--description",                default=None,  help="Optional description.")
@click.option("--fact-id",                    required=True, help="Fact node ID.")
@click.option("--property",  "property_name", default=None,  help="Property name.")
@click.option("--property-id",                default=None,  help="Property node ID (overrides --property).")
@click.option(
    "--type", "literal_type",
    type=click.Choice(["NUMBER", "DATE", "BOOLEAN", "STRING"]),
    default=None,
    help="Value type (inferred if omitted).",
)
@click.option("--text",                       default=None, help="Source text span (creates provenance).")
@click.option("--source",    "source_name",   default=None, help="Source name.")
@click.option("--source-id",                  default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def fact_property_set(
    obj: dict,
    value: str,
    name: str | None,
    description: str | None,
    fact_id: str,
    property_name: str | None,
    property_id: str | None,
    literal_type: str | None,
    text: str | None,
    source_name: str | None,
    source_id: str | None,
) -> None:
    """Set a property value on a fact.

    \b
    Example:
      kb data fact property set "1998-06-01" --fact-id <uuid> --property START_DATE --type DATE
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    pid: str | None = None
    if property_name or property_id:
        pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    coerced = _coerce_value(value, literal_type)
    try:
        lit_id = _run(kb.add_fact_property_value(
            db, coerced,
            name=name, description=description,
            fact_id=fact_id, property_id=pid, literal_type=literal_type,
            text=text, source_id=resolved_source_id,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Set fact property (literal id: {lit_id})")


@fact_property_group.command("list")
@click.option("--fact-id",         required=True,    help="Fact node ID.")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def fact_property_list(obj: dict, fact_id: str, show_provenance: bool, fmt: str) -> None:
    """List all property values for a fact."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    rows = []
    for nb in kb.get_neighbors(db, fact_id, edge_type=HAS_PROPERTY, direction="out"):
        lit = nb["node"]
        lit_props = lit.get("properties", {})
        prop_nbs = kb.get_neighbors(db, lit["id"], edge_type=IS_VALUE_OF, direction="out")
        prop_name = (
            prop_nbs[0]["node"].get("properties", {}).get("name", "?") if prop_nbs else "?"
        )
        row: dict = {
            "property": prop_name,
            "value":    str(lit_props.get("value", "")),
            "type":     lit_props.get("type", ""),
            "id":       lit["id"],
        }
        if show_provenance:
            row["provenance"] = _provenance_text(kb, db, lit["id"])
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        t = Table(show_header=True, header_style="bold")
        t.add_column("Property", style="bold")
        t.add_column("Value")
        t.add_column("Type",     style="dim")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in rows:
            row_cells = [r["property"], r["value"], r["type"]]
            if show_provenance:
                row_cells.append(r.get("provenance", ""))
            t.add_row(*row_cells)
        console.print(t)


@fact_property_group.command("update")
@click.option("--fact",        "fact_name",     default=None, help="Fact name.")
@click.option("--fact-id",                      default=None, help="Fact node ID (overrides --fact).")
@click.option("--property",    "property_name", default=None, help="Property name.")
@click.option("--property-id",                  default=None, help="Property node ID (overrides --property).")
@click.option("--value",                        default=None, help="New value.")
@click.option("--name",                         default=None, help="New literal name.")
@click.option("--description",                  default=None, help="New description.")
@click.pass_obj
def fact_property_update(
    obj: dict,
    fact_name: str | None, fact_id: str | None,
    property_name: str | None, property_id: str | None,
    value: str | None, name: str | None, description: str | None,
) -> None:
    """Update the value, name, and/or description of a fact property value."""
    if not value and not name and not description:
        raise click.UsageError("Provide at least one of --value, --name, or --description.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
    pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    coerced = _coerce_value(value, None) if value is not None else None
    try:
        _run(kb.update_fact_property_value(db, fid, pid, value=coerced, name=name, description=description))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Updated fact property value.")


@fact_property_group.command("delete")
@click.option("--fact",        "fact_name",     default=None, help="Fact name.")
@click.option("--fact-id",                      default=None, help="Fact node ID (overrides --fact).")
@click.option("--property",    "property_name", default=None, help="Property name.")
@click.option("--property-id",                  default=None, help="Property node ID (overrides --property).")
@click.option("--yes",         is_flag=True,    default=False, help="Skip confirmation prompt.")
@click.pass_obj
def fact_property_delete(
    obj: dict,
    fact_name: str | None, fact_id: str | None,
    property_name: str | None, property_id: str | None,
    yes: bool,
) -> None:
    """Delete a property value from a fact."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
    pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    if not yes:
        click.confirm(
            f"Delete property '{property_name or property_id}' value from fact '{fact_name or fact_id}'?",
            abort=True,
        )
    try:
        _run(kb.delete_fact_property_value(db, fid, pid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Deleted fact property value.")


# ------------------------------------------------------------------ #
# kb data fact property extraction                                     #
# ------------------------------------------------------------------ #

@fact_property_group.group("extraction")
def fact_property_extraction_group() -> None:
    """Provenance extraction management for fact property values."""


@fact_property_extraction_group.command("add")
@click.option("--fact",         "fact_name",     default=None, help="Fact name.")
@click.option("--fact-id",                       default=None, help="Fact node ID (overrides --fact).")
@click.option("--property",     "property_name", default=None, help="Property name.")
@click.option("--property-id",                   default=None, help="Property node ID (overrides --property).")
@click.option("--text",                          required=True, help="New source text span.")
@click.option("--source",       "source_name",   default=None, help="Source name.")
@click.option("--source-id",                     default=None, help="Source node ID (overrides --source).")
@click.pass_obj
def fact_property_extraction_add(
    obj: dict,
    fact_name: str | None, fact_id: str | None,
    property_name: str | None, property_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
) -> None:
    """Link additional source text to a fact's existing property value."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
    pid = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        extraction_id = _run(
            kb.add_fact_property_value_extraction(db, fid, pid, text, source_id=resolved_source_id)
        )
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added extraction (id: {extraction_id})")


# ------------------------------------------------------------------ #
# kb data list                                                         #
# ------------------------------------------------------------------ #

@data_group.command("list")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def list_cmd(obj: dict, show_provenance: bool, fmt: str) -> None:
    """List all entities, property values, and facts in the current database."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        rows: list[dict] = _run(kb.fetch_database_data(db, include_provenance=show_provenance, compact=True))  # type: ignore[assignment]
    except KBError as exc:
        raise click.ClickException(str(exc))

    if fmt == "json":
        console.print_json(json.dumps(rows))
        return

    entities    = [r for r in rows if r.get("relation_type") == "IS_INSTANCE_OF"]
    all_props   = [r for r in rows if r.get("relation_type") == "IS_VALUE_OF"]
    ent_props   = [r for r in all_props if r.get("owner_label") != "FACT"]
    fact_props  = [r for r in all_props if r.get("owner_label") == "FACT"]
    facts       = [r for r in rows if r.get("relation_type") == "REPRESENTS"]

    if entities:
        t = Table(title="Entities", show_header=True, header_style="bold")
        t.add_column("Name",  style="bold")
        t.add_column("Class", style="dim")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in sorted(entities, key=lambda x: (x.get("class_name") or "", x.get("entity_name") or "")):
            row = [r.get("entity_name") or "", r.get("class_name") or ""]
            if show_provenance:
                row.append(r.get("extraction", "") or "")
            t.add_row(*row)
        console.print(t)

    if ent_props:
        t = Table(title="Entity Property Values", show_header=True, header_style="bold")
        t.add_column("Entity",   style="bold")
        t.add_column("Property", style="dim")
        t.add_column("Value")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in sorted(ent_props, key=lambda x: (x.get("entity_name") or "", x.get("property_name") or "")):
            row = [r.get("entity_name") or "", r.get("property_name") or "", str(r.get("property_value", ""))]
            if show_provenance:
                row.append(r.get("extraction", "") or "")
            t.add_row(*row)
        console.print(t)

    if facts:
        t = Table(title="Facts", show_header=True, header_style="bold")
        t.add_column("Head",     style="bold")
        t.add_column("Relation", style="dim")
        t.add_column("Tail",     style="bold")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in sorted(facts, key=lambda x: (x.get("relation_name") or "", x.get("source_name") or "")):
            row = [r.get("source_name") or "", r.get("relation_name") or "", r.get("target_name") or ""]
            if show_provenance:
                row.append(r.get("extraction", "") or "")
            t.add_row(*row)
        console.print(t)

    if fact_props:
        t = Table(title="Fact Property Values", show_header=True, header_style="bold")
        t.add_column("Head",     style="bold")
        t.add_column("Relation", style="dim")
        t.add_column("Tail",     style="bold")
        t.add_column("Property", style="dim")
        t.add_column("Value")
        if show_provenance:
            t.add_column("Provenance", style="dim")
        for r in sorted(fact_props, key=lambda x: (x.get("relation_name") or "", x.get("head_name") or "", x.get("property_name") or "")):
            row = [
                r.get("head_name") or "",
                r.get("relation_name") or "",
                r.get("tail_name") or "",
                r.get("property_name") or "",
                str(r.get("property_value", "")),
            ]
            if show_provenance:
                row.append(r.get("extraction", "") or "")
            t.add_row(*row)
        console.print(t)

    if not entities and not all_props and not facts:
        console.print("[dim](no instances)[/dim]")


# ------------------------------------------------------------------ #
# kb data extract                                                      #
# ------------------------------------------------------------------ #

@data_group.command("extract")
@click.argument("text")
@click.option("--query",     "queries",     multiple=True, help="Guiding question (repeatable).")
@click.option("--source",    "source_name", default=None,  help="Source name for provenance.")
@click.option("--source-id",               default=None,  help="Source node ID (overrides --source).")
@click.option("--model",                   default="gpt-4o", show_default=True, help="OpenAI model.")
@click.option(
    "--schema-discovery-mode",
    type=click.Choice(["search", "llm", "none"]),
    default="search",
    show_default=True,
    help=(
        "How the agent checks for existing schema elements before declaring new ones: "
        "'search' uses the database's configured search backend (keyword/semantic); "
        "'llm' asks the model to judge synonyms (e.g. JOIN_DATE vs START_DATE), at the cost "
        "of an extra call per check; 'none' disables the check."
    ),
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show tool call arguments in the status line.")
@click.option("--log", "-l", is_flag=True, default=False, help="Also log each tool call and its result on its own line (in addition to the status line).")
@click.option("--log-limit", type=int, default=80, show_default=True, help="Max characters to show per truncated verbose/log line.")
@click.pass_obj
def extract_cmd(
    obj: dict,
    text: str,
    queries: tuple[str, ...],
    source_name: str | None,
    source_id: str | None,
    model: str,
    schema_discovery_mode: str,
    verbose: bool,
    log: bool,
    log_limit: int,
) -> None:
    """Extract entities, facts, and property values from free text.

    Requires the openai extra: pip install 'blue-data-kb-source[openai]'
    and OPENAI_API_KEY in the environment.
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        with console.status("Extracting…") as status:
            on_call, on_result = _status_tool_callbacks(status, "Extracting", verbose, log, log_limit)
            _run(kb.import_text(db, text,
                                queries=list(queries) or None,
                                model=model,
                                source_id=resolved_source_id,
                                schema_discovery_mode=schema_discovery_mode,
                                on_tool_call=on_call,
                                on_tool_result=on_result))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print("Extraction complete.")


# ------------------------------------------------------------------ #
# kb data answer                                                       #
# ------------------------------------------------------------------ #

@data_group.command("answer")
@click.argument("question")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json", "nl"]),
    default="table",
    show_default=True,
    help="Output format: table, json, or nl (prose natural-language answer).",
)
@click.option("--model", default="gpt-4o", show_default=True, help="OpenAI model.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show tool call arguments in the status line.")
@click.option("--log", "-l", is_flag=True, default=False, help="Also log each tool call and its result on its own line (in addition to the status line).")
@click.option("--log-limit", type=int, default=80, show_default=True, help="Max characters to show per truncated verbose/log line.")
@click.pass_obj
def answer_cmd(obj: dict, question: str, fmt: str, model: str, verbose: bool, log: bool, log_limit: int) -> None:
    """Answer a natural language question using the knowledge base."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        with console.status("Answering…") as status:
            on_call, on_result = _status_tool_callbacks(status, "Answering", verbose, log, log_limit)
            result = _run(kb.answer(db, question, model=model, natural_language=(fmt == "nl"),
                                     on_tool_call=on_call, on_tool_result=on_result))
    except KBError as exc:
        raise click.ClickException(str(exc))
    if fmt == "nl":
        console.print(result)
    elif fmt == "json":
        console.print_json(json.dumps(result))
    else:
        if not result:
            console.print("(no results)")
            return
        columns: list[str] = list(dict.fromkeys(k for row in result for k in row))
        t = Table(show_header=True, header_style="bold")
        for col in columns:
            t.add_column(col.replace("_", " ").title())
        for row in result:
            t.add_row(*[str(row.get(col, "")) for col in columns])
        console.print(t)


# ------------------------------------------------------------------ #
# kb data validate                                                     #
# ------------------------------------------------------------------ #

@data_group.command("validate")
@click.argument("statement")
@click.option("--natural-language", "natural_language", is_flag=True, default=False,
              help="Return a prose assessment instead of supporting/counter lists.")
@click.option("--model", default="gpt-4o", show_default=True, help="OpenAI model.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show tool call arguments in the status line.")
@click.option("--log", "-l", is_flag=True, default=False, help="Also log each tool call and its result on its own line (in addition to the status line).")
@click.option("--log-limit", type=int, default=80, show_default=True, help="Max characters to show per truncated verbose/log line.")
@click.pass_obj
def validate_cmd(
    obj: dict, statement: str, natural_language: bool, model: str, verbose: bool, log: bool, log_limit: int
) -> None:
    """Return supporting and counter evidence for a statement."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        with console.status("Validating…") as status:
            on_call, on_result = _status_tool_callbacks(status, "Validating", verbose, log, log_limit)
            result = _run(kb.validate(db, statement, model=model, natural_language=natural_language,
                                       on_tool_call=on_call, on_tool_result=on_result))
    except KBError as exc:
        raise click.ClickException(str(exc))

    if natural_language:
        console.print(result)
        return

    console.print("\n[bold]Supporting:[/bold]")
    if result.supporting:
        for fact in result.supporting:
            head  = fact.get("head") or ""
            rel   = fact.get("relation") or ""
            tail  = fact.get("tail") or ""
            label = f"{head} —{rel}→ {tail}" if rel else fact.get("fact", str(fact))
            console.print(f"  • {label}")
    else:
        console.print("  (none)")

    console.print("\n[bold]Counter:[/bold]")
    if result.counter:
        for fact in result.counter:
            console.print(f"  • {fact}")
    else:
        console.print("  (none)")


@data_group.command("search")
@click.argument("query")
@click.option(
    "--type", "types",
    type=click.Choice(["entity", "fact"], case_sensitive=False),
    multiple=True,
    help="Restrict to a node type (repeatable). Default: all types.",
)
@click.option(
    "--mode",
    type=click.Choice(["keyword", "semantic"]),
    default="keyword",
    show_default=True,
    help="Search mode. 'semantic' requires the database to be registered with --search semantic.",
)
@click.option("--top-k", "top_k", default=10, show_default=True, help="Maximum results to return.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def data_search(obj: dict, query: str, types: tuple[str, ...], mode: str, top_k: int, fmt: str) -> None:
    """Search entities and facts by keyword or semantic similarity."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    type_filter = [t.upper() for t in types] if types else None
    try:
        results = _run(kb.search_data(db, query, k=top_k, mode=mode, type_filter=type_filter))
    except KBError as exc:
        raise click.ClickException(str(exc))

    if fmt == "json":
        console.print_json(json.dumps([
            {
                "id": r.id, "type": r.type, "name": r.name, "description": r.description,
                "class_name": r.class_name,
                "head_name": r.head_name, "relation_name": r.relation_name, "tail_name": r.tail_name,
            }
            for r in results
        ]))
        return

    if not results:
        console.print("[dim](no results)[/dim]")
        return

    entities = [r for r in results if r.type == ENTITY]
    facts    = [r for r in results if r.type == FACT]

    if entities:
        t = Table(title="Entities", show_header=True, header_style="bold")
        t.add_column("Name",        style="bold")
        t.add_column("Class",       style="dim")
        t.add_column("Description")
        for r in entities:
            t.add_row(r.name, r.class_name or "", r.description or "")
        console.print(t)

    if facts:
        t = Table(title="Facts", show_header=True, header_style="bold")
        t.add_column("Head",     style="bold")
        t.add_column("Relation", style="dim")
        t.add_column("Tail",     style="bold")
        t.add_column("Description")
        for r in facts:
            t.add_row(r.head_name or "", r.relation_name or "", r.tail_name or "", r.description or "")
        console.print(t)
