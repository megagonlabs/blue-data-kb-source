"""Click command groups for the kb CLI.

Databases are registered in {BLUE_DATA_KB_SOURCE_DATA_DIR}/registry.json (defaults to ~/.kb).
That value, and every other configuration parameter, comes from the selected
profile in ~/.kb/.profiles — see `kb profile --help`.
Each entry stores the graph backend and optional search configuration.  Run
`kb database register` to create and configure a new database.

FileGraphSource persists each database as a JSON file — no explicit save step
is needed.  The graph backend (neo4j, …) is chosen per database in the registry.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import arrow
import click
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
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from blue_data_kb_cli.config import (
    _BACKEND_BY_KB_CLASS,
    _FILE_KB_CLASS,
    _KB_CLASS_BY_BACKEND,
    _cli_home,
    _data_dir,
    _get_registry,
    _make_kb,
    _neo4j_connection_dict,
    _resolve_database,
    _selected_database_path,
    _vector_properties,
    list_databases,
    load_db_config,
    register_database,
    unregister_database,
    update_db_config,
)
from blue_data_kb_cli.profile import profile_group, profile_store, resolver_for

console = Console()

# Keep a local alias so existing helpers still compile
FileKBSource = KBSource  # type alias — runtime type is the MRO class from _make_kb


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _parse_mcp_servers(raw: tuple[str, ...]) -> list[dict] | None:
    """Parse repeated --*mcp-server JSON strings, e.g.
    '{"transport":"stdio","command":"blue-data-wordnet-mcp"}', into a list of dicts."""
    if not raw:
        return None
    servers = []
    for entry in raw:
        try:
            servers.append(json.loads(entry))
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"invalid JSON in --mcp-server: {exc}")
    return servers


def _truncate(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _humanize_date(iso_str: str | None) -> str:
    """Format an ISO-8601 created/modified timestamp for table display.

    JSON output keeps the raw ISO string; this is only for `--format table`.
    """
    if not iso_str:
        return ""
    try:
        return arrow.get(iso_str).to("local").format("MMM D, YYYY h:mm A")
    except arrow.parser.ParserError:
        return iso_str


def _render_confidence(value: float | None) -> str:
    """Format a confidence for table display; `--` when unstated, so a blank cell
    means only that the row continues the one above it."""
    return "--" if value is None else str(value)


def _metadata_display_name(meta: dict) -> str:
    """Human-readable identity string for a get_metadata entry, type-dependent."""
    if meta.get("type") == "LITERAL":
        return f"{meta.get('owner_name') or '?'}.{meta.get('property_name') or '?'} = {meta.get('value')}"
    return meta.get("name") or ""


def _evidence_columns(t: Table, text_header: str = "Provenance",
                      with_original: bool = False) -> None:
    """Per-evidence columns, added after an item's own columns."""
    t.add_column("Ev.", style="dim")
    t.add_column(text_header, style="dim")
    t.add_column("Source", style="dim")
    if with_original:
        t.add_column("Source Original", style="dim")


def _evidence_rows(base: list[str], records: list[dict] | None,
                   with_original: bool = False) -> list[list[str]]:
    """Expand one item into a row per evidence record.

    The first row carries *base*; later rows blank those cells so the item reads
    as one group. An item with no evidence yields one row built from an empty
    record, so nothing disappears from a listing and the cell count cannot drift
    from `_evidence_columns`.
    """
    blank = [""] * len(base)
    rows: list[list[str]] = []
    for i, record in enumerate(records or [{}]):
        cells = (base if i == 0 else blank) + [
            _render_confidence(record.get("confidence")),
            record.get("text") or "",
            record.get("source") or "",
        ]
        if with_original:
            cells.append(record.get("source_original") or "")
        rows.append(cells)
    return rows


def _print_metadata_table(metadata: dict) -> None:
    """Render a get_metadata dict (id -> metadata) as a 'Metadata' table, if non-empty."""
    if not metadata:
        return
    t = Table(title="Metadata", show_header=True, header_style="bold", show_lines=True)
    t.add_column("Type",     style="dim")
    t.add_column("Name",     style="bold")
    t.add_column("Created",  style="dim")
    t.add_column("Modified", style="dim")
    _evidence_columns(t, text_header="Extraction", with_original=True)
    for meta in metadata.values():
        base = [
            meta.get("type") or "",
            _metadata_display_name(meta),
            _humanize_date(meta.get("created")),
            _humanize_date(meta.get("modified")),
        ]
        for row in _evidence_rows(base, meta.get("evidence"), with_original=True):
            t.add_row(*row)
    console.print(t)


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


def _assert_kwargs(obj: dict, source_name, source_id,
                   confidence, extraction_type, schema_creation_mode, model) -> dict:
    """Resolve --source/--source-id and collect the shared assert options."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_source_id = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    return {
        "source_id": resolved_source_id, "confidence": confidence,
        "extraction_type": extraction_type, "schema_creation_mode": schema_creation_mode,
        "model": _model_for(obj, "ASSERTION", model),
    }


def _print_assert_result(result, kind: str, label: str) -> None:
    if result.created:
        outcome = "created"
    elif result.match_type == "structural":
        outcome = "deduped, evidence attached"
    else:
        outcome = f"reused, {result.match_type} match" if result.match_type else "already present"
    console.print(f"Asserted {kind}: [bold]{label}[/bold] ({outcome}, id: {result.node_id})")
    if result.created_nodes:
        parts = ", ".join(f"{r.name} ({r.label})" for r in result.created_nodes)
        console.print(f"  created: {parts}")
    if result.widened_relations:
        console.print(f"  widened: {', '.join(r.name for r in result.widened_relations)}")


_ASSERT_OPTIONS = [
    click.option("--description", default=None,
                 help="Optional description (judge-generated when omitted under "
                      "--schema-creation-mode llm)."),
    click.option("--source", "source_name", default=None, help="Source name."),
    click.option("--source-id", default=None, help="Source node ID (overrides --source)."),
    click.option("--confidence", type=float, default=None,
                 help="Confidence in [0.0, 1.0] for the recorded assertion."),
    click.option("--extraction-type", "extraction_type",
                 type=click.Choice(["text_span", "external_record", "assertion"]),
                 default="assertion", show_default=True,
                 help="Form of the evidence recorded for the assertion."),
    click.option("--schema-creation-mode", "schema_creation_mode",
                 type=click.Choice(["llm", "none"]), default="llm", show_default=True,
                 help="How new schema elements are named and described."),
    click.option("--model", default=None, help="OpenAI model for judges. Default: the profile's model for this command."),
]


def _with_assert_options(command):
    for option in reversed(_ASSERT_OPTIONS):
        command = option(command)
    return command


def _run_assert(coro, kind: str, label: str) -> None:
    try:
        result = _run(coro)
    except KBError as exc:
        raise click.ClickException(str(exc))
    _print_assert_result(result, kind, label)


def _coerce_value(raw: str, literal_type: str | None) -> str | int | float | bool:
    if literal_type == "NUMBER":
        try:
            return int(raw)
        except ValueError:
            return float(raw)
    if literal_type == "BOOLEAN":
        return raw.lower() in ("true", "1", "yes")
    return raw


def _evidence_records(kb: FileKBSource, db: str, node_id: str) -> list[dict]:
    """One record per EXTRACTION on node_id, for the listing commands.

    Deliberately narrower than `get_metadata`'s `evidence`: it omits `created`,
    which no CLI surface displays. Not an oversight — add it here when something
    renders it.
    """
    records: list[dict] = []
    for nb in kb.get_neighbors(db, node_id, edge_type=EXTRACTED_FROM, direction="out"):
        extraction_node = nb["node"]
        props = extraction_node.get("properties", {})
        src_nbs = kb.get_neighbors(db, extraction_node["id"], edge_type=EXTRACTED_FROM, direction="out")
        source_props = src_nbs[0]["node"].get("properties", {}) if src_nbs else {}
        records.append({
            "text": props.get("text"),
            "confidence": props.get("confidence"),
            "source": source_props.get("name"),
            "source_original": source_props.get("original"),
        })
    return records


# ------------------------------------------------------------------ #
# Root group                                                           #
# ------------------------------------------------------------------ #

class _CommandContext(dict):
    """Click context object whose KBSource is built on first use.

    The root group used to construct the target database — creating it if
    absent — on every single invocation, including commands that never touch
    one: `kb profile list` would leave a `default.json` and a `registry.json`
    behind. Commands still read `obj["kb"]` exactly as before; it simply isn't
    built until one of them actually asks for it.
    """

    def __init__(self, build_kb, **values) -> None:
        super().__init__(**values)
        self._build_kb = build_kb

    def __getitem__(self, key):
        if key == "kb" and "kb" not in self:
            super().__setitem__("kb", self._build_kb())
        return super().__getitem__(key)


@click.group()
@click.option(
    "--profile",
    "profile_name",
    envvar="BLUE_DATA_PROFILE",
    default=None,
    help="Configuration profile. Overrides BLUE_DATA_PROFILE. "
         "Defaults to the selection in ~/.kb/.selected.profile.",
)
@click.option(
    "--database",
    default=None,
    help="Target database. Defaults to the selection in ~/.kb/.selected.database, "
         "then BLUE_DATA_KB_SOURCE_DATABASE from the profile.",
)
@click.pass_context
def cli(ctx: click.Context, profile_name: str | None, database: str | None) -> None:
    """kb — Knowledge Base CLI.

    Configuration lives in profiles: `kb profile config` to set values,
    `kb profile inspect` to see them and where each came from.
    Run `kb database register` to create and configure a database.
    """
    home = _cli_home()

    # A fresh install gets its `default` profile here, rather than being told to
    # run `kb profile add` before anything works. Keyed on the profiles file, so
    # a profile the user removed on purpose is not resurrected on the next
    # command.
    profile_store(home).initialize()

    # Apply the profile before anything else runs: resolved values are written
    # into the environment, which is where the library, the OpenAI SDK and the
    # remaining `envvar=` options already read their configuration from.
    resolver = resolver_for(home, profile_name)
    resolver.export()
    database = _resolve_database(database, resolver)

    def build_kb():
        kb = _make_kb(database)
        if database not in kb.fetch_databases():
            kb.create_database(database)
        return kb

    ctx.obj = _CommandContext(
        build_kb, home=home, profile=resolver.profile, config=resolver, db=database
    )


cli.add_command(profile_group)


def _model_for(obj: dict, function: str, option: str | None) -> str:
    """The model a command should use.

    `--model` wins; otherwise the profile's parameter for this function, which
    itself falls back through BLUE_DATA_KB_SOURCE_LLM_MODEL to
    BLUE_DATA_LLM_MODEL. The Click options default to None rather than a
    hardcoded model so an unset option is distinguishable from a chosen one.
    """
    if option:
        return option
    resolver = obj["config"]
    return resolver.get(f"BLUE_DATA_KB_SOURCE_{function}_LLM_MODEL") or "gpt-4o"


def _log_limit(obj: dict, option: int | None) -> int:
    """`--log-limit` if given, else the family-wide BLUE_DATA_CLI_LOG_LIMIT."""
    if option is not None:
        return option
    return obj["config"].get("BLUE_DATA_CLI_LOG_LIMIT") or 80


def _extraction_default(obj: dict, param: str, option):
    """An extraction knob: the command option if given, else the profile value."""
    if option is not None:
        return option
    return obj["config"].get(f"BLUE_DATA_KB_SOURCE_EXTRACTION_{param}")


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
    records = sorted(list_databases(), key=lambda r: r["name"])
    if fmt == "json":
        console.print_json(json.dumps({r["name"]: r["properties"] for r in records}))
        return
    if not records:
        console.print("[dim](no databases registered — run `kb database register NAME`)[/dim]")
        return
    t = Table(show_header=True, header_style="bold")
    t.add_column("Database")
    t.add_column("Graph Backend", style="dim")
    t.add_column("Search",        style="dim")
    for r in records:
        props = r["properties"]
        backend = _BACKEND_BY_KB_CLASS.get(props.get("_class"), props.get("_class", "?"))
        search_mode = (props.get("search") or {}).get("mode", "none")
        t.add_row(r["name"], backend, search_mode)
    console.print(t)


@database_group.command("register")
@click.argument("name")
@click.option("--graph-backend", "graph_backend", type=click.Choice(["file", "neo4j"]), default="file", show_default=True)
@click.option("--directory",      help="Graph data directory (file backend). Default: the profile's BLUE_DATA_KB_SOURCE_DATA_DIR.")
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
    class_path = _KB_CLASS_BY_BACKEND[graph_backend]

    if graph_backend == "neo4j":
        connection = _neo4j_connection_dict(uri=neo4j_uri, user=neo4j_user, password=neo4j_password)
    else:
        connection = {"directory": directory or _data_dir()}

    search_cfg: dict[str, Any] | None = None
    if search in ("bm25", "semantic"):
        search_cfg = {"mode": search}
        if bm25_index_dir:
            search_cfg["bm25_index_dir"] = bm25_index_dir
        if search == "semantic":
            vconn: dict[str, Any] = {}
            if vector_backend == "file" and vector_directory:
                vconn["directory"] = vector_directory
            if vector_backend == "chromadb" and vector_path:
                vconn["path"] = vector_path
            if vector_backend == "redis":
                if redis_host:
                    vconn["host"] = redis_host
                if redis_port is not None:
                    vconn["port"] = redis_port
                if redis_db is not None:
                    vconn["db"] = redis_db
            search_cfg["vector"] = _vector_properties(vector_backend, vconn)
            embedding_cfg: dict[str, Any] = {"provider": embedding_provider}
            if embedding_model:
                embedding_cfg["model"] = embedding_model
            search_cfg["embedding"] = embedding_cfg

    try:
        kb = register_database(name, class_path, connection=connection, search=search_cfg)
        if name not in kb.fetch_databases():
            kb.create_database(name)
    except Exception as exc:
        raise click.ClickException(f"Failed to register/initialize backend: {exc}")
    console.print(f"Registered database: [bold]{name}[/bold] (graph={graph_backend}, search={search})")


@database_group.command("create")
@click.argument("name")
@click.pass_obj
def database_create(obj: dict, name: str) -> None:
    """Create a database using the current session's backend and search config.

    The shortcut for `database register`: it takes no configuration options and
    instead inherits the session database's, so `create` in a session using
    neo4j and semantic search produces another such database rather than a
    file-backed one with search silently off. Use `database register` to
    configure a database differently from the session's.
    """
    kb: KBSource = obj["kb"]
    try:
        kb.create_database(name)
    except KBError as exc:
        raise click.ClickException(str(exc))
    # Auto-register if not present, mirroring the session database's own
    # backend and search config. It used to hardcode the file backend with no
    # search, which contradicted the "current session's backend" this command
    # advertises and left `properties["search"]` absent — so KBSource built no
    # indexer and search_schema/search_data silently degraded to substring
    # lookups on a database the user never configured that way.
    #
    # Sharing one search config across databases is safe: BM25 corpus files are
    # named `{database}_{collection}_bm25.json` and vector indices are keyed by
    # (database, collection), so neither collides.
    if _get_registry().get_source_record(name) is None:
        session_cfg = load_db_config(obj["db"])
        session_search = session_cfg.get("search") or {}
        register_database(
            name,
            session_cfg.get("_class", _FILE_KB_CLASS),
            connection=session_cfg.get("connection") or {"directory": _data_dir()},
            # An explicit mode="none" is not worth persisting — it is what an
            # absent "search" key already means to KBSource.__init__.
            search=session_search if session_search.get("mode", "none") != "none" else None,
        )
    console.print(f"Created database: [bold]{name}[/bold]")


@database_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def database_delete(name: str, yes: bool) -> None:
    """Delete a database and remove it from the registry."""
    cfg = load_db_config(name)
    class_path = cfg.get("_class", _FILE_KB_CLASS)
    graph_backend = _BACKEND_BY_KB_CLASS.get(class_path, class_path)

    if class_path == _FILE_KB_CLASS:
        directory = Path(cfg.get("connection", {}).get("directory", _data_dir())).expanduser()
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
    bm25_dir = (cfg.get("search") or {}).get("bm25_index_dir")
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
        updates["_class"] = _KB_CLASS_BY_BACKEND[graph_backend]
    connection_updates: dict[str, Any] = {}
    if directory:
        connection_updates["directory"] = directory
    connection_updates.update(_neo4j_connection_dict(uri=neo4j_uri, user=neo4j_user, password=neo4j_password))
    if connection_updates:
        updates["connection"] = connection_updates
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
    console.print_json(json.dumps(cfg.get("search") or {"mode": "none"}, indent=2))


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
    search_updates: dict[str, Any] = {}
    if search:
        search_updates["mode"] = search
    if bm25_index_dir:
        search_updates["bm25_index_dir"] = bm25_index_dir
    if vector_backend or vector_directory or vector_path or redis_host or redis_port is not None or redis_db is not None:
        vconn: dict[str, Any] = {}
        if vector_directory:
            vconn["directory"] = vector_directory
        if vector_path:
            vconn["path"] = vector_path
        if redis_host:
            vconn["host"] = redis_host
        if redis_port is not None:
            vconn["port"] = redis_port
        if redis_db is not None:
            vconn["db"] = redis_db
        search_updates["vector"] = _vector_properties(vector_backend or "memory", vconn)
    embedding_updates: dict[str, Any] = {}
    if embedding_provider:
        embedding_updates["provider"] = embedding_provider
    if embedding_model:
        embedding_updates["model"] = embedding_model
    if embedding_updates:
        search_updates["embedding"] = embedding_updates
    if not search_updates:
        raise click.ClickException("No options provided.")
    update_db_config(db, {"search": search_updates})
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


@schema_group.command("export")
@click.option("--output", "-o", type=click.File("w"), default="-", show_default=True,
              help="Write JSON to this file (default: stdout).")
@click.pass_obj
def schema_export(obj: dict, output) -> None:
    """Export the database's schema as JSON, in the shape `kb schema import` reads back."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        schema_json = _run(kb.export_database_schema(db))
    except KBError as exc:
        raise click.ClickException(str(exc))
    json.dump(schema_json, output, indent=2)
    output.write("\n")


@schema_group.command("import")
@click.argument("input_file", type=click.File("r"))
@click.pass_obj
def schema_import(obj: dict, input_file) -> None:
    """Import a schema previously produced by `kb schema export` (declares idempotently)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        schema_json = json.load(input_file)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {input_file.name}: {exc}")
    try:
        _run(kb.import_database_schema(db, schema_json))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Imported schema into database: [bold]{db}[/bold]")


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


@schema_class_group.command("assert")
@click.argument("name")
@_with_assert_options
@click.pass_obj
def schema_class_assert(obj, name, description, source_name, source_id,
                        confidence, extraction_type, schema_creation_mode, model):
    """Assert a class: reuse it when one resolves, create it otherwise."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_class(db, name, description, **kwargs), "class", name)


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


@schema_class_parent_group.command("assert")
@click.argument("child")
@click.argument("parent")
@_with_assert_options
@click.pass_obj
def schema_class_parent_assert(obj, child, parent, description, source_name, source_id,
                               confidence, extraction_type, schema_creation_mode, model):
    """Assert CHILD IS_SUBCLASS_OF PARENT; missing classes are created."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_subclass(db, child, parent, **kwargs),
                "subclass", f"{child} -> {parent}")


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
        nodes = _run(kb.get_class_ancestors(db, resolved_id, max_hops=None))
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
        nodes = _run(kb.get_class_descendants(db, resolved_id, max_hops=None))
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


@schema_class_property_group.command("attach")
@click.option("--property",    "property_name", default=None, help="Existing property name.")
@click.option("--property-id",                  default=None, help="Existing property node ID (overrides --property).")
@click.option("--class",       "class_name",     default=None, help="Target class name.")
@click.option("--class-id",                      default=None, help="Target class node ID (overrides --class).")
@click.pass_obj
def schema_class_property_attach(
    obj: dict, property_name: str | None, property_id: str | None,
    class_name: str | None, class_id: str | None,
) -> None:
    """Attach an existing PROPERTY to a class (idempotent) — reuse it instead of declaring a
    same-meaning duplicate under a new name."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_property = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    resolved_class = _resolve_node(kb, db, CLASS, class_name, class_id, "class")
    try:
        _run(kb.attach_property(db, resolved_class, resolved_property))
    except KBError as exc:
        raise click.ClickException(str(exc))
    prop_label = property_name or property_id
    class_label = class_name or class_id
    console.print(f"Attached property [bold]{prop_label}[/bold] to class [bold]{class_label}[/bold]")


@schema_class_property_group.command("assert")
@click.argument("name")
@click.option("--class", "class_name", required=True, help="Owning class name.")
@_with_assert_options
@click.pass_obj
def schema_class_property_assert(obj, name, class_name, description, source_name, source_id,
                                 confidence, extraction_type, schema_creation_mode, model):
    """Assert a class property; the owning class is created when missing."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_class_property(db, name, class_name, description, **kwargs),
                "property", name)


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
@click.option("--domain", required=True, help="Domain class name.")
@click.option("--range", "range", required=True, help="Range class name.")
@click.pass_obj
def schema_relation_add(
    obj: dict, name: str, description: str, domain: str, range: str
) -> None:
    """Declare a RELATION node (idempotent)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        rel_id = _run(kb.declare_relation_type(db, name, description, domain=domain, range=range))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added relation: [bold]{name}[/bold] (id: {rel_id})")


@schema_relation_group.command("assert")
@click.argument("name")
@click.option("--domain", multiple=True, required=True, help="Domain class name (repeatable).")
@click.option("--range", "range_", multiple=True, required=True,
              help="Range class name (repeatable).")
@click.option("--is-symmetric", is_flag=True, default=False, help="Mark the relation symmetric.")
@_with_assert_options
@click.pass_obj
def schema_relation_assert(obj, name, domain, range_, is_symmetric, description, source_name,
                           source_id, confidence, extraction_type, schema_creation_mode, model):
    """Assert a relation type; missing domain/range classes are created, existing ones widened."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_relation_type(db, name, list(domain), list(range_), description,
                                        is_symmetric, **kwargs), "relation", name)


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


@schema_relation_parent_group.command("assert")
@click.argument("child")
@click.argument("parent")
@_with_assert_options
@click.pass_obj
def schema_relation_parent_assert(obj, child, parent, description, source_name, source_id,
                                  confidence, extraction_type, schema_creation_mode, model):
    """Assert CHILD IS_SUBCLASS_OF PARENT for relations; both must already exist."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_subrelation(db, child, parent, **kwargs),
                "subrelation", f"{child} -> {parent}")


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
        nodes = _run(kb.get_relation_ancestors(db, resolved_id, max_hops=None))
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
        nodes = _run(kb.get_relation_descendants(db, resolved_id, max_hops=None))
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


# ------------------------------------------------------------------ #
# kb schema relation domain / range                                    #
# ------------------------------------------------------------------ #


@schema_relation_group.group("domain")
def schema_relation_domain_group() -> None:
    """Relation domain management (attach and list only — no detach counterpart by design)."""


@schema_relation_domain_group.command("attach")
@click.option("--relation",    "relation_name", default=None, required=False, help="Relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Relation node ID.")
@click.option("--class",       "class_name",    default=None, required=False, help="Class name to attach.")
@click.option("--class-id",                     default=None, required=False, help="Class node ID to attach.")
@click.pass_obj
def schema_relation_domain_attach(
    obj: dict, relation_name: str | None, relation_id: str | None,
    class_name: str | None, class_id: str | None,
) -> None:
    """Attach a class to the relation's domain (deduplicated; no detach counterpart by design)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_rel   = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    resolved_class = _resolve_node(kb, db, CLASS,    class_name,    class_id,    "class")
    try:
        _run(kb.attach_relation_domain(db, resolved_rel, resolved_class))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(
        f"[bold]{class_name or class_id}[/bold] attached to domain of "
        f"[bold]{relation_name or relation_id}[/bold]"
    )


@schema_relation_domain_group.command("list")
@click.option("--relation",    "relation_name", default=None, required=False, help="Relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Relation node ID.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_relation_domain_list(
    obj: dict, relation_name: str | None, relation_id: str | None, fmt: str,
) -> None:
    """List the relation's domain classes."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    try:
        dr = _run(kb.get_relation_domain_range(db, resolved_id))
    except KBError as exc:
        raise click.ClickException(str(exc))
    nodes = dr["domain"]
    if fmt == "json":
        console.print_json(json.dumps([
            {"name": n.get("properties", {}).get("name", n["id"]), "id": n["id"]}
            for n in nodes
        ]))
    else:
        if not nodes:
            console.print("[dim](no domain classes)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name", style="bold", min_width=14)
        t.add_column("ID",   style="dim")
        for n in nodes:
            t.add_row(n.get("properties", {}).get("name", n["id"]), n["id"])
        console.print(t)


@schema_relation_group.group("range")
def schema_relation_range_group() -> None:
    """Relation range management (attach and list only — no detach counterpart by design)."""


@schema_relation_range_group.command("attach")
@click.option("--relation",    "relation_name", default=None, required=False, help="Relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Relation node ID.")
@click.option("--class",       "class_name",    default=None, required=False, help="Class name to attach.")
@click.option("--class-id",                     default=None, required=False, help="Class node ID to attach.")
@click.pass_obj
def schema_relation_range_attach(
    obj: dict, relation_name: str | None, relation_id: str | None,
    class_name: str | None, class_id: str | None,
) -> None:
    """Attach a class to the relation's range (deduplicated; no detach counterpart by design)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_rel   = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    resolved_class = _resolve_node(kb, db, CLASS,    class_name,    class_id,    "class")
    try:
        _run(kb.attach_relation_range(db, resolved_rel, resolved_class))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(
        f"[bold]{class_name or class_id}[/bold] attached to range of "
        f"[bold]{relation_name or relation_id}[/bold]"
    )


@schema_relation_range_group.command("list")
@click.option("--relation",    "relation_name", default=None, required=False, help="Relation name.")
@click.option("--relation-id",                  default=None, required=False, help="Relation node ID.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_relation_range_list(
    obj: dict, relation_name: str | None, relation_id: str | None, fmt: str,
) -> None:
    """List the relation's range classes."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_id = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    try:
        dr = _run(kb.get_relation_domain_range(db, resolved_id))
    except KBError as exc:
        raise click.ClickException(str(exc))
    nodes = dr["range"]
    if fmt == "json":
        console.print_json(json.dumps([
            {"name": n.get("properties", {}).get("name", n["id"]), "id": n["id"]}
            for n in nodes
        ]))
    else:
        if not nodes:
            console.print("[dim](no range classes)[/dim]")
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


@schema_relation_property_group.command("attach")
@click.option("--property",    "property_name", default=None, help="Existing property name.")
@click.option("--property-id",                  default=None, help="Existing property node ID (overrides --property).")
@click.option("--relation",    "relation_name",  default=None, help="Target relation name.")
@click.option("--relation-id",                   default=None, help="Target relation node ID (overrides --relation).")
@click.pass_obj
def schema_relation_property_attach(
    obj: dict, property_name: str | None, property_id: str | None,
    relation_name: str | None, relation_id: str | None,
) -> None:
    """Attach an existing PROPERTY to a relation (idempotent) — reuse it instead of declaring a
    same-meaning duplicate under a new name."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_property = _resolve_node(kb, db, PROPERTY, property_name, property_id, "property")
    resolved_relation = _resolve_node(kb, db, RELATION, relation_name, relation_id, "relation")
    try:
        _run(kb.attach_property(db, resolved_relation, resolved_property))
    except KBError as exc:
        raise click.ClickException(str(exc))
    prop_label = property_name or property_id
    relation_label = relation_name or relation_id
    console.print(f"Attached property [bold]{prop_label}[/bold] to relation [bold]{relation_label}[/bold]")


@schema_relation_property_group.command("assert")
@click.argument("name")
@click.option("--relation", "relation_name", required=True, help="Owning relation name.")
@_with_assert_options
@click.pass_obj
def schema_relation_property_assert(obj, name, relation_name, description, source_name,
                                    source_id, confidence, extraction_type,
                                    schema_creation_mode, model):
    """Assert a relation property; the relation must already exist."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_relation_property(db, name, relation_name, description, **kwargs),
                "property", name)


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


@schema_group.command("expand")
@click.argument("text")
@click.option(
    "--depth", type=int, default=1, show_default=True,
    help="Hierarchy levels beyond what's directly evidenced in TEXT to also propose.",
)
@click.option(
    "--reference",
    type=click.Choice(["llm", "mcp"]),
    default="llm",
    show_default=True,
    help=(
        "'llm' proposes schema freely from TEXT; 'mcp' additionally gives the model "
        "tools from --mcp-server (e.g. WordNet hypernyms/synonyms) to ground names and "
        "hierarchy placement before declaring."
    ),
)
@click.option(
    "--mcp-server", "mcp_servers_raw",
    multiple=True,
    help=(
        "MCP server to connect (repeatable), as a JSON object — e.g. "
        '\'{"transport":"stdio","command":"blue-data-wordnet-mcp"}\' or '
        '\'{"transport":"sse","url":"https://..."}\'. Required when --reference mcp.'
    ),
)
@click.option(
    "--dedup-mode",
    type=click.Choice(["search", "llm", "none"]),
    default="search",
    show_default=True,
    help=(
        "How to check for an existing schema element before declaring a new one: "
        "'search' uses the database's configured search backend; 'llm' asks the model "
        "to judge synonyms (e.g. JOIN_DATE vs START_DATE); 'none' disables the check."
    ),
)
@click.option("--model", default=None, help="OpenAI model. Default: the profile's model for this command.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show tool call arguments in the status line.")
@click.option("--log", "-l", is_flag=True, default=False, help="Also log each tool call and its result on its own line (in addition to the status line).")
@click.option("--log-limit", type=int, default=None, help="Max characters per truncated verbose/log line. Default: the profile's BLUE_DATA_CLI_LOG_LIMIT.")
@click.pass_obj
def schema_expand(
    obj: dict,
    text: str,
    depth: int,
    reference: str,
    mcp_servers_raw: tuple[str, ...],
    dedup_mode: str,
    model: str,
    verbose: bool,
    log: bool,
    log_limit: int | None,
) -> None:
    """Grow the schema (classes, relations, properties) grounded by TEXT — without
    extracting any data. Run separately from `kb data extract`, so the same
    expanded schema can be reused across many extraction calls.

    Requires the openai extra: pip install 'blue-data-kb-source[openai]'
    and OPENAI_API_KEY in the environment.
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    mcp_servers = _parse_mcp_servers(mcp_servers_raw)
    try:
        with console.status("Expanding schema…") as status:
            on_call, on_result = _status_tool_callbacks(status, "Expanding", verbose, log, _log_limit(obj, log_limit))
            _run(kb.expand_schema(
                db, text,
                depth=depth,
                reference=reference,
                mcp_servers=mcp_servers,
                dedup_mode=dedup_mode,
                model=_model_for(obj, "SCHEMA_EXPANSION", model),
                on_tool_call=on_call,
                on_tool_result=on_result,
            ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print("Schema expansion complete.")


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

@data_group.command("ingest")
@click.argument("input_file", type=click.File("r"))
@click.option("--source", "source_name", default=None, help="Source name.")
@click.option("--source-id", default=None, help="Source node ID (overrides --source).")
@click.option("--confidence", type=float, default=None,
              help="Run-level confidence default; rows may override.")
@click.option("--extraction-type", "extraction_type",
              type=click.Choice(["text_span", "external_record", "assertion"]),
              default="assertion", show_default=True,
              help="Run-level evidence form; rows may override.")
@click.option("--schema-creation-mode", "schema_creation_mode",
              type=click.Choice(["llm", "none"]), default="llm", show_default=True,
              help="How new schema elements are named and described.")
@click.option("--model", default=None, help="OpenAI model for judges. Default: the profile's model for this command.")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table",
              show_default=True, help="Report format.")
@click.pass_obj
def data_ingest(obj, input_file, source_name, source_id, confidence, extraction_type,
                schema_creation_mode, model, fmt):
    """Bulk-ingest a ten-arm JSON payload (same ergonomics as `kb data import`; `-` reads
    stdin). Rows run through the per-item asserts in file order; failures collect into
    the report instead of stopping the run."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        payload = json.load(input_file)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {input_file.name}: {exc}")
    resolved_source_id = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        report = _run(kb.ingest(
            db, payload, source_id=resolved_source_id, confidence=confidence,
            extraction_type=extraction_type, schema_creation_mode=schema_creation_mode,
            model=_model_for(obj, "INGESTION", model),
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    if fmt == "json":
        click.echo(json.dumps(asdict(report), indent=2))
        return
    total = report.written + report.deduped + report.skipped + len(report.failures)
    console.print(f"Ingested {total} rows into database: [bold]{db}[/bold]")
    console.print(f"  written {report.written}   deduped {report.deduped}   "
                  f"skipped {report.skipped}   failed {len(report.failures)}")
    if report.created:
        parts = ", ".join(f"{label} {count}" for label, count in sorted(report.created.items()))
        console.print(f"  created: {parts}")
    if report.widened:
        console.print(f"  widened: {', '.join(dict.fromkeys(report.widened))}")
    if report.failures:
        t = Table(title="Failures")
        for col in ("arm", "row", "reason"):
            t.add_column(col)
        for f in report.failures:
            t.add_row(f.arm, str(f.index), f.reason)
        console.print(t)


@data_group.group("source")
def source_group() -> None:
    """Provenance source management."""


@source_group.command("add")
@click.argument("source")
@click.option("--name", default=None, help="Human-readable label (defaults to source value).")
@click.option("--description", default=None, help="Optional description.")
@click.option("--original", default=None, help="Original date of the source (e.g. when it was originally published).")
@click.pass_obj
def source_add(obj: dict, source: str, name: str | None, description: str | None, original: str | None) -> None:
    """Register a source document and return its ID."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        src_id = _run(kb.add_source(db, source, name=name, description=description, original=original))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added source: [bold]{source}[/bold] (id: {src_id})")


@source_group.command("list")
@click.option("--show-detailed", "show_detailed", is_flag=True,
              help="Include created/modified timestamps in the table output.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def source_list(obj: dict, show_detailed: bool, fmt: str) -> None:
    """List all registered provenance sources."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    rows = [
        {
            "id":          n["id"],
            "name":        n.get("properties", {}).get("name", ""),
            "source":      n.get("properties", {}).get("source", ""),
            "description": n.get("properties", {}).get("description", ""),
            "original":    n.get("properties", {}).get("original", ""),
            "created":     n.get("properties", {}).get("created", ""),
            "modified":    n.get("properties", {}).get("modified", ""),
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
        t.add_column("Original",    style="dim")
        if show_detailed:
            t.add_column("Created",  style="dim")
            t.add_column("Modified", style="dim")
        for r in sorted(rows, key=lambda x: x["name"]):
            row = [r["name"], r["source"], r["description"], r["original"]]
            if show_detailed:
                row += [_humanize_date(r["created"]), _humanize_date(r["modified"])]
            t.add_row(*row)
        console.print(t)


@source_group.command("update")
@click.option("--source",      "source_name", default=None, help="Source name.")
@click.option("--source-id",                  default=None, help="Source node ID (overrides --source).")
@click.option("--name",                       default=None, help="New name.")
@click.option("--description",                default=None, help="New description.")
@click.option("--original",                   default=None, help="New original date.")
@click.pass_obj
def source_update(
    obj: dict, source_name: str | None, source_id: str | None,
    name: str | None, description: str | None, original: str | None,
) -> None:
    """Update the name, description, and/or original date of a source."""
    if not name and not description and not original:
        raise click.UsageError("Provide at least one of --name, --description, or --original.")
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    sid = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    try:
        _run(kb.update_source(db, sid, name=name, description=description, original=original))
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
@click.option("--confidence",      type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion; requires --text.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
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
    confidence: float | None,
    extraction_type: str,
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
            confidence=confidence, extraction_type=extraction_type,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    label = f"[bold]{name}[/bold]" if name else ""
    console.print(f"Added entity{': ' + label if label else ''} (id: {entity_id})")


@entity_group.command("assert")
@click.argument("name")
@click.option("--class", "class_name", default=None, help="Class name (resolved or created).")
@_with_assert_options
@click.pass_obj
def entity_assert(obj, name, class_name, description, source_name, source_id,
                  confidence, extraction_type, schema_creation_mode, model):
    """Assert an entity by name: resolve it, or create it with a judged class."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_entity(db, name, class_name, description, **kwargs), "entity", name)


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
            row["evidence"] = _evidence_records(kb, db, nid)
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no entities)[/dim]")
            return
        t = Table(show_header=True, header_style="bold", show_lines=show_provenance)
        t.add_column("Name",  style="bold")
        t.add_column("Class", style="dim")
        if show_provenance:
            _evidence_columns(t)
        for r in sorted(rows, key=lambda x: (x["class"], x["name"])):
            base = [r["name"], r["class"]]
            if not show_provenance:
                t.add_row(*base)
                continue
            for cells in _evidence_rows(base, r.get("evidence")):
                t.add_row(*cells)
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
# kb data entity class                                                 #
# ------------------------------------------------------------------ #


@entity_group.group("class")
def entity_class_group() -> None:
    """Entity class (IS_INSTANCE_OF) membership (attach and list only — no detach
    counterpart by design)."""


@entity_class_group.command("attach")
@click.option("--entity",    "entity_name", default=None, help="Entity name.")
@click.option("--entity-id",                default=None, help="Entity node ID.")
@click.option("--class",     "class_name",  default=None, help="Class name to attach.")
@click.option("--class-id",                 default=None, help="Class node ID to attach.")
@click.pass_obj
def entity_class_attach(
    obj: dict,
    entity_name: str | None, entity_id: str | None,
    class_name: str | None, class_id: str | None,
) -> None:
    """Attach a class to an existing entity (deduplicated; no detach counterpart by design).

    `entity add --class` wires an entity's first class at creation; this adds a
    further one to an entity that already exists.
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    cid = _resolve_node(kb, db, CLASS,  class_name,  class_id,  "class")
    try:
        _run(kb.attach_entity_class(db, eid, cid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(
        f"[bold]{class_name or class_id}[/bold] attached to "
        f"[bold]{entity_name or entity_id}[/bold]"
    )


@entity_class_group.command("list")
@click.option("--entity",    "entity_name", default=None, help="Entity name.")
@click.option("--entity-id",                default=None, help="Entity node ID.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def entity_class_list(
    obj: dict, entity_name: str | None, entity_id: str | None, fmt: str,
) -> None:
    """List the classes an entity is an instance of."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    eid = _resolve_node(kb, db, ENTITY, entity_name, entity_id, "entity")
    try:
        nodes = _run(kb.get_entity_classes(db, eid))
    except KBError as exc:
        raise click.ClickException(str(exc))
    if fmt == "json":
        console.print_json(json.dumps([
            {"name": n.get("properties", {}).get("name", n["id"]), "id": n["id"]}
            for n in nodes
        ]))
    else:
        if not nodes:
            console.print("[dim](no classes)[/dim]")
            return
        t = Table(show_header=True, header_style="bold")
        t.add_column("Name", style="bold", min_width=14)
        t.add_column("ID",   style="dim")
        for n in nodes:
            t.add_row(n.get("properties", {}).get("name", n["id"]), n["id"])
        console.print(t)


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
@click.option("--confidence",     type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
@click.pass_obj
def entity_extraction_add(
    obj: dict,
    entity_name: str | None, entity_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
    confidence: float | None,
    extraction_type: str,
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
        extraction_id = _run(kb.add_entity_extraction(
            db, eid, text, source_id=resolved_source_id,
            confidence=confidence, extraction_type=extraction_type,
        ))
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
@click.option("--confidence",        type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion; requires --text.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
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
    confidence: float | None,
    extraction_type: str,
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
            confidence=confidence, extraction_type=extraction_type,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Set entity property (literal id: {lit_id})")


@entity_property_group.command("assert")
@click.option("--entity", required=True, help="Entity name.")
@click.option("--property", "property_name", required=True, help="Property name.")
@click.option("--value", required=True, help="Property value.")
@click.option("--type", "literal_type", default=None, help="Literal type (STRING, NUMBER, ...).")
@_with_assert_options
@click.pass_obj
def entity_property_assert(obj, entity, property_name, value, literal_type, description,
                           source_name, source_id, confidence, extraction_type,
                           schema_creation_mode, model):
    """Assert a property value on an entity; equal values dedupe, property placement judged."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    coerced = _coerce_value(value, literal_type)
    _run_assert(kb.assert_entity_property(db, entity, property_name, coerced, literal_type,
                                          **kwargs), "property value", f"{entity} {property_name}")


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
            row["evidence"] = _evidence_records(kb, db, lit["id"])
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        t = Table(show_header=True, header_style="bold", show_lines=show_provenance)
        t.add_column("Property", style="bold")
        t.add_column("Value")
        t.add_column("Type",     style="dim")
        if show_provenance:
            _evidence_columns(t)
        for r in rows:
            base = [r["property"], r["value"], r["type"]]
            if not show_provenance:
                t.add_row(*base)
                continue
            for cells in _evidence_rows(base, r.get("evidence")):
                t.add_row(*cells)
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
@click.option("--confidence",        type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
@click.pass_obj
def entity_property_extraction_add(
    obj: dict,
    entity_name: str | None, entity_id: str | None,
    property_name: str | None, property_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
    confidence: float | None,
    extraction_type: str,
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
            kb.add_entity_property_value_extraction(
                db, eid, pid, text, source_id=resolved_source_id,
                confidence=confidence, extraction_type=extraction_type,
            )
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
@click.option("--confidence",       type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion; requires --text.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
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
    confidence: float | None,
    extraction_type: str,
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
            confidence=confidence, extraction_type=extraction_type,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added fact (id: {fact_id})")


@fact_group.command("assert")
@click.option("--head", required=True, help="Head entity name.")
@click.option("--relation", required=True, help="Relation name.")
@click.option("--tail", required=True, help="Tail entity name.")
@click.option("--name", default=None, help="Optional fact name.")
@_with_assert_options
@click.pass_obj
def fact_assert(obj, head, relation, tail, name, description, source_name, source_id,
                confidence, extraction_type, schema_creation_mode, model):
    """Assert a fact by names; operands are resolved or created, duplicates deduped."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    _run_assert(kb.assert_fact(db, head, relation, tail, name, description, **kwargs),
                "fact", f"{head} {relation} {tail}")


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
            "confidence": node.get("properties", {}).get("confidence"),
        }
        if show_provenance:
            row["evidence"] = _evidence_records(kb, db, nid)
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        if not rows:
            console.print("[dim](no facts)[/dim]")
            return
        t = Table(show_header=True, header_style="bold", show_lines=show_provenance)
        t.add_column("Head",       style="bold")
        t.add_column("Relation",   style="dim")
        t.add_column("Tail",       style="bold")
        t.add_column("Conf.", style="dim")
        if show_provenance:
            _evidence_columns(t)
        for r in sorted(rows, key=lambda x: (x["relation"], x["head"])):
            base = [r["head"], r["relation"], r["tail"], _render_confidence(r["confidence"])]
            if not show_provenance:
                t.add_row(*base)
                continue
            for cells in _evidence_rows(base, r.get("evidence")):
                t.add_row(*cells)
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
@click.option("--fact",        "fact_name",   default=None, help="Fact name.")
@click.option("--fact-id",                    default=None, help="Fact node ID (overrides --fact).")
@click.option("--text",        required=True, help="New source text span.")
@click.option("--source",      "source_name", default=None, help="Source name.")
@click.option("--source-id",                  default=None, help="Source node ID (overrides --source).")
@click.option("--confidence",     type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
@click.pass_obj
def fact_extraction_add(
    obj: dict,
    fact_name: str | None, fact_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
    confidence: float | None,
    extraction_type: str,
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
        extraction_id = _run(kb.add_fact_extraction(
            db, fid, text, source_id=resolved_source_id,
            confidence=confidence, extraction_type=extraction_type,
        ))
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
@click.option("--fact",      "fact_name",     default=None,  help="Fact name.")
@click.option("--fact-id",                    default=None,  help="Fact node ID (overrides --fact).")
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
@click.option("--confidence",     type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion; requires --text.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
@click.pass_obj
def fact_property_set(
    obj: dict,
    value: str,
    name: str | None,
    description: str | None,
    fact_name: str | None,
    fact_id: str | None,
    property_name: str | None,
    property_id: str | None,
    literal_type: str | None,
    text: str | None,
    source_name: str | None,
    source_id: str | None,
    confidence: float | None,
    extraction_type: str,
) -> None:
    """Set a property value on a fact.

    \b
    Example:
      kb data fact property set "1998-06-01" --fact-id <uuid> --property START_DATE --type DATE
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
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
            fact_id=fid, property_id=pid, literal_type=literal_type,
            text=text, source_id=resolved_source_id,
            confidence=confidence, extraction_type=extraction_type,
        ))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Set fact property (literal id: {lit_id})")


@fact_property_group.command("assert")
@click.option("--head", required=True, help="Head entity name.")
@click.option("--relation", required=True, help="Relation name.")
@click.option("--tail", required=True, help="Tail entity name.")
@click.option("--property", "property_name", required=True, help="Property name.")
@click.option("--value", required=True, help="Property value.")
@click.option("--type", "literal_type", default=None, help="Literal type (STRING, NUMBER, ...).")
@_with_assert_options
@click.pass_obj
def fact_property_assert(obj, head, relation, tail, property_name, value, literal_type,
                         description, source_name, source_id, confidence, extraction_type,
                         schema_creation_mode, model):
    """Assert a property value on the fact identified by its (head, relation, tail) triple."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    kwargs = _assert_kwargs(obj, source_name, source_id, confidence,
                            extraction_type, schema_creation_mode, model)
    coerced = _coerce_value(value, literal_type)
    _run_assert(kb.assert_fact_property(db, head, relation, tail, property_name, coerced,
                                        literal_type, **kwargs),
                "property value", f"{head} {relation} {tail} {property_name}")


@fact_property_group.command("list")
@click.option("--fact",            "fact_name",      default=None, help="Fact name.")
@click.option("--fact-id",                           default=None, help="Fact node ID (overrides --fact).")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def fact_property_list(
    obj: dict, fact_name: str | None, fact_id: str | None, show_provenance: bool, fmt: str
) -> None:
    """List all property values for a fact."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    fid = _resolve_node(kb, db, FACT, fact_name, fact_id, "fact")
    rows = []
    for nb in kb.get_neighbors(db, fid, edge_type=HAS_PROPERTY, direction="out"):
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
            row["evidence"] = _evidence_records(kb, db, lit["id"])
        rows.append(row)
    if fmt == "json":
        console.print_json(json.dumps(rows))
    else:
        t = Table(show_header=True, header_style="bold", show_lines=show_provenance)
        t.add_column("Property", style="bold")
        t.add_column("Value")
        t.add_column("Type",     style="dim")
        if show_provenance:
            _evidence_columns(t)
        for r in rows:
            base = [r["property"], r["value"], r["type"]]
            if not show_provenance:
                t.add_row(*base)
                continue
            for cells in _evidence_rows(base, r.get("evidence")):
                t.add_row(*cells)
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
@click.option("--confidence",        type=float, default=None,
              help="Confidence in [0.0, 1.0] for the recorded assertion.")
@click.option(
    "--extraction-type", "extraction_type",
    type=click.Choice(["text_span", "external_record", "assertion"]),
    default="text_span",
    show_default=True,
    help="Form of the evidence recorded for --text.",
)
@click.pass_obj
def fact_property_extraction_add(
    obj: dict,
    fact_name: str | None, fact_id: str | None,
    property_name: str | None, property_id: str | None,
    text: str, source_name: str | None, source_id: str | None,
    confidence: float | None,
    extraction_type: str,
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
            kb.add_fact_property_value_extraction(
                db, fid, pid, text, source_id=resolved_source_id,
                confidence=confidence, extraction_type=extraction_type,
            )
        )
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added extraction (id: {extraction_id})")


# ------------------------------------------------------------------ #
# kb data list                                                         #
# ------------------------------------------------------------------ #

@data_group.command("list")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option("--show-detailed", "show_detailed", is_flag=True,
              help="Include each row's node attributes: description, created, modified, "
                   "and confidence on fact rows.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def list_cmd(obj: dict, show_provenance: bool, show_detailed: bool, fmt: str) -> None:
    """List all entities, property values, and facts in the current database."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        rows: list[dict] = _run(kb.fetch_database_data(  # type: ignore[assignment]
            db, include_provenance=show_provenance, compact=True, detailed=show_detailed,
        ))
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

    def _detail_columns(t: Table) -> None:
        t.add_column("Description", style="dim")
        t.add_column("Created",     style="dim")
        t.add_column("Modified",    style="dim")

    def _detail_row(r: dict) -> list[str]:
        return [r.get("description") or "", _humanize_date(r.get("created")), _humanize_date(r.get("modified"))]

    def _add_rows(t: Table, base: list[str], r: dict) -> None:
        """One row per evidence record when provenance is shown, else a single row."""
        if not show_provenance:
            t.add_row(*base)
            return
        for cells in _evidence_rows(base, r.get("evidence"), with_original=show_detailed):
            t.add_row(*cells)

    if entities:
        t = Table(title="Entities", show_header=True, header_style="bold",
                  show_lines=show_provenance)
        t.add_column("Name",  style="bold")
        t.add_column("Class", style="dim")
        if show_detailed:
            _detail_columns(t)
        if show_provenance:
            _evidence_columns(t, with_original=show_detailed)
        for r in sorted(entities, key=lambda x: (x.get("class_name") or "", x.get("entity_name") or "")):
            base = [r.get("entity_name") or "", r.get("class_name") or ""]
            if show_detailed:
                base += _detail_row(r)
            _add_rows(t, base, r)
        console.print(t)

    if ent_props:
        t = Table(title="Entity Property Values", show_header=True, header_style="bold",
                  show_lines=show_provenance)
        t.add_column("Entity",   style="bold")
        t.add_column("Property", style="dim")
        t.add_column("Value")
        if show_detailed:
            _detail_columns(t)
        if show_provenance:
            _evidence_columns(t, with_original=show_detailed)
        for r in sorted(ent_props, key=lambda x: (x.get("entity_name") or "", x.get("property_name") or "")):
            base = [r.get("entity_name") or "", r.get("property_name") or "", str(r.get("property_value", ""))]
            if show_detailed:
                base += _detail_row(r)
            _add_rows(t, base, r)
        console.print(t)

    if facts:
        t = Table(title="Facts", show_header=True, header_style="bold",
                  show_lines=show_provenance)
        t.add_column("Head",     style="bold")
        t.add_column("Relation", style="dim")
        t.add_column("Tail",     style="bold")
        if show_detailed:
            t.add_column("Conf.", style="dim")
            _detail_columns(t)
        if show_provenance:
            _evidence_columns(t, with_original=show_detailed)
        for r in sorted(facts, key=lambda x: (x.get("relation_name") or "", x.get("source_name") or "")):
            base = [r.get("source_name") or "", r.get("relation_name") or "", r.get("target_name") or ""]
            if show_detailed:
                base += [_render_confidence(r.get("confidence"))]
                base += _detail_row(r)
            _add_rows(t, base, r)
        console.print(t)

    if fact_props:
        t = Table(title="Fact Property Values", show_header=True, header_style="bold",
                  show_lines=show_provenance)
        t.add_column("Head",     style="bold")
        t.add_column("Relation", style="dim")
        t.add_column("Tail",     style="bold")
        t.add_column("Property", style="dim")
        t.add_column("Value")
        if show_detailed:
            _detail_columns(t)
        if show_provenance:
            _evidence_columns(t, with_original=show_detailed)
        for r in sorted(fact_props, key=lambda x: (x.get("relation_name") or "", x.get("head_name") or "", x.get("property_name") or "")):
            base = [
                r.get("head_name") or "",
                r.get("relation_name") or "",
                r.get("tail_name") or "",
                r.get("property_name") or "",
                str(r.get("property_value", "")),
            ]
            if show_detailed:
                base += _detail_row(r)
            _add_rows(t, base, r)
        console.print(t)

    if not entities and not all_props and not facts:
        console.print("[dim](no instances)[/dim]")


# ------------------------------------------------------------------ #
# kb data export / import                                              #
# ------------------------------------------------------------------ #

@data_group.command("export")
@click.option("--show-provenance", "show_provenance", is_flag=True, help="Include provenance text.")
@click.option("--output", "-o", type=click.File("w"), default="-", show_default=True,
              help="Write JSON to this file (default: stdout).")
@click.pass_obj
def data_export(obj: dict, show_provenance: bool, output) -> None:
    """Export the database's instance data (entities, facts, property values) as JSON,
    in the shape `kb data import` reads. Does not include schema — use `kb schema export`
    for that, and import it first so `kb data import` has classes/relations/properties
    to resolve names against."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        data_json = _run(kb.export_database_data(db, include_provenance=show_provenance))
    except KBError as exc:
        raise click.ClickException(str(exc))
    json.dump(data_json, output, indent=2)
    output.write("\n")


@data_group.command("import")
@click.argument("input_file", type=click.File("r"))
@click.pass_obj
def data_import(obj: dict, input_file) -> None:
    """Import instance data previously produced by `kb data export`. Referenced
    classes/relations/properties must already exist (e.g. via `kb schema import`)."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        data_json = json.load(input_file)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {input_file.name}: {exc}")
    try:
        _run(kb.import_database_data(db, data_json))
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Imported data into database: [bold]{db}[/bold]")


# ------------------------------------------------------------------ #
# kb data extract                                                      #
# ------------------------------------------------------------------ #

@data_group.command("extract")
@click.argument("text")
@click.option("--query",     "queries",     multiple=True, help="Guiding question (repeatable).")
@click.option("--source",    "source_name", default=None,  help="Source name for provenance.")
@click.option("--source-id",               default=None,  help="Source node ID (overrides --source).")
@click.option("--model", default=None, help="OpenAI model. Default: the profile's model for this command.")
@click.option(
    "--expand-schema/--no-expand-schema", "expand_schema_flag", default=None,
    help="Run schema expansion (SchemaManager.expand_schema) before extracting. "
         "Use --no-expand-schema to extract only against the schema as it stands. "
         "Default: the profile's BLUE_DATA_KB_SOURCE_EXTRACTION_EXPAND_SCHEMA (true).",
)
@click.option(
    "--schema-expansion-target", "schema_expansion_target",
    type=click.Choice(["text", "queries"]), multiple=True,
    help="What to ground schema expansion in (repeatable). Default: both TEXT and --query.",
)
@click.option(
    "--schema-expansion-depth", type=int, default=1, show_default=True,
    help="Hierarchy levels beyond what's directly evidenced to also propose during expansion.",
)
@click.option(
    "--schema-expansion-mode",
    type=click.Choice(["llm", "mcp"]),
    default="llm",
    show_default=True,
    help="'mcp' additionally gives schema expansion tools from --schema-expansion-mcp-server "
         "(e.g. WordNet) to ground names and hierarchy placement.",
)
@click.option(
    "--schema-expansion-mcp-server", "schema_expansion_mcp_servers_raw",
    multiple=True,
    help='MCP server for schema expansion (repeatable), as a JSON object — e.g. '
         '\'{"transport":"stdio","command":"blue-data-wordnet-mcp"}\'. '
         "Required when --schema-expansion-mode mcp.",
)
@click.option(
    "--schema-expansion-dedup-mode",
    type=click.Choice(["search", "llm", "none"]),
    default="search",
    show_default=True,
    help=(
        "How schema expansion checks for existing schema elements before declaring new ones: "
        "'search' uses the database's configured search backend (keyword/semantic); "
        "'llm' asks the model to judge synonyms (e.g. JOIN_DATE vs START_DATE), at the cost "
        "of an extra call per check; 'none' disables the check."
    ),
)
@click.option(
    "--effort", type=click.Choice(["low", "medium", "high"]), default=None,
    help="Passes over the text (1/2/3). Default: the profile's "
         "BLUE_DATA_KB_SOURCE_EXTRACTION_EFFORT.",
)
@click.option(
    "--max-concurrency", type=int, default=None,
    help="Chunks to extract concurrently. Default: the profile's "
         "BLUE_DATA_KB_SOURCE_EXTRACTION_MAX_CONCURRENCY.",
)
@click.option(
    "--max-turns", type=int, default=None,
    help="Max agent turns per extraction stage. Default: the profile's "
         "BLUE_DATA_KB_SOURCE_EXTRACTION_MAX_TURNS.",
)
@click.option(
    "--related-data-k", type=int, default=None,
    help="Existing entities/facts surfaced as leads; 0 disables. Default: the "
         "profile's BLUE_DATA_KB_SOURCE_EXTRACTION_RELATED_DATA_K.",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show tool call arguments in the status line.")
@click.option("--log", "-l", is_flag=True, default=False, help="Also log each tool call and its result on its own line (in addition to the status line).")
@click.option("--log-limit", type=int, default=None, help="Max characters per truncated verbose/log line. Default: the profile's BLUE_DATA_CLI_LOG_LIMIT.")
@click.pass_obj
def extract_cmd(
    obj: dict,
    text: str,
    queries: tuple[str, ...],
    source_name: str | None,
    source_id: str | None,
    model: str,
    expand_schema_flag: bool | None,
    schema_expansion_target: tuple[str, ...],
    schema_expansion_depth: int,
    schema_expansion_mode: str,
    schema_expansion_mcp_servers_raw: tuple[str, ...],
    schema_expansion_dedup_mode: str,
    effort: str | None,
    max_concurrency: int | None,
    max_turns: int | None,
    related_data_k: int | None,
    verbose: bool,
    log: bool,
    log_limit: int | None,
) -> None:
    """Extract entities, facts, and property values from free text. By default this
    also expands the schema first (see --no-expand-schema and `kb schema expand`).

    Requires the openai extra: pip install 'blue-data-kb-source[openai]'
    and OPENAI_API_KEY in the environment.
    """
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    resolved_source_id: str | None = None
    if source_name or source_id:
        resolved_source_id = _resolve_node(kb, db, SOURCE, source_name, source_id, "source")
    schema_expansion_mcp_servers = _parse_mcp_servers(schema_expansion_mcp_servers_raw)
    # The flag pair defaults to None, so "not given" is distinguishable from an
    # explicit --expand-schema and the profile can supply the default.
    expand_schema_resolved = bool(_extraction_default(obj, "EXPAND_SCHEMA", expand_schema_flag))
    try:
        with console.status("Extracting…") as status:
            on_call, on_result = _status_tool_callbacks(
                status, "Extracting", verbose, log, _log_limit(obj, log_limit)
            )
            _run(kb.import_text(db, text,
                                queries=list(queries) or None,
                                model=_model_for(obj, "EXTRACTION", model),
                                source_id=resolved_source_id,
                                effort=_extraction_default(obj, "EFFORT", effort),
                                max_concurrency=_extraction_default(obj, "MAX_CONCURRENCY", max_concurrency),
                                max_turns=_extraction_default(obj, "MAX_TURNS", max_turns),
                                related_data_k=_extraction_default(obj, "RELATED_DATA_K", related_data_k),
                                expand_schema=expand_schema_resolved,
                                schema_expansion_target=list(schema_expansion_target) or None,
                                schema_expansion_depth=schema_expansion_depth,
                                schema_expansion_mode=schema_expansion_mode,
                                schema_expansion_mcp_servers=schema_expansion_mcp_servers,
                                schema_expansion_dedup_mode=schema_expansion_dedup_mode,
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
@click.option("--model", default=None, help="OpenAI model. Default: the profile's model for this command.")
@click.option("--include-metadata", "include_metadata", is_flag=True, default=False,
              help="Also collect created/modified/confidence/evidence metadata for cited entities/facts/properties.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show tool call arguments in the status line.")
@click.option("--log", "-l", is_flag=True, default=False, help="Also log each tool call and its result on its own line (in addition to the status line).")
@click.option("--log-limit", type=int, default=None, help="Max characters per truncated verbose/log line. Default: the profile's BLUE_DATA_CLI_LOG_LIMIT.")
@click.pass_obj
def answer_cmd(
    obj: dict, question: str, fmt: str, model: str, include_metadata: bool,
    verbose: bool, log: bool, log_limit: int,
) -> None:
    """Answer a natural language question using the knowledge base."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        with console.status("Answering…") as status:
            on_call, on_result = _status_tool_callbacks(status, "Answering", verbose, log, _log_limit(obj, log_limit))
            raw = _run(kb.answer(db, question, model=_model_for(obj, "QUERY", model), natural_language=(fmt == "nl"),
                                  include_metadata=include_metadata,
                                  on_tool_call=on_call, on_tool_result=on_result))
    except KBError as exc:
        raise click.ClickException(str(exc))
    result, metadata = (raw if include_metadata else (raw, None))

    if fmt == "nl":
        console.print(result)
    elif fmt == "json":
        console.print_json(json.dumps({"result": result, "metadata": metadata} if include_metadata else result))
    else:
        if not result:
            console.print("(no results)")
        else:
            columns: list[str] = list(dict.fromkeys(k for row in result for k in row))
            t = Table(show_header=True, header_style="bold")
            for col in columns:
                t.add_column(col.replace("_", " ").title())
            for row in result:
                t.add_row(*[str(row.get(col, "")) for col in columns])
            console.print(t)
        if include_metadata:
            _print_metadata_table(metadata)


# ------------------------------------------------------------------ #
# kb data validate                                                     #
# ------------------------------------------------------------------ #

@data_group.command("validate")
@click.argument("statement")
@click.option("--natural-language", "natural_language", is_flag=True, default=False,
              help="Return a prose assessment instead of supporting/counter lists.")
@click.option("--model", default=None, help="OpenAI model. Default: the profile's model for this command.")
@click.option("--include-metadata", "include_metadata", is_flag=True, default=False,
              help="Also collect created/modified/confidence/evidence metadata for cited entities/facts/properties.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show tool call arguments in the status line.")
@click.option("--log", "-l", is_flag=True, default=False, help="Also log each tool call and its result on its own line (in addition to the status line).")
@click.option("--log-limit", type=int, default=None, help="Max characters per truncated verbose/log line. Default: the profile's BLUE_DATA_CLI_LOG_LIMIT.")
@click.pass_obj
def validate_cmd(
    obj: dict, statement: str, natural_language: bool, model: str, include_metadata: bool,
    verbose: bool, log: bool, log_limit: int,
) -> None:
    """Return supporting and counter evidence for a statement."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        with console.status("Validating…") as status:
            on_call, on_result = _status_tool_callbacks(status, "Validating", verbose, log, _log_limit(obj, log_limit))
            raw = _run(kb.validate(db, statement, model=_model_for(obj, "VALIDATION", model), natural_language=natural_language,
                                    include_metadata=include_metadata,
                                    on_tool_call=on_call, on_tool_result=on_result))
    except KBError as exc:
        raise click.ClickException(str(exc))
    result, metadata = (raw if include_metadata else (raw, None))

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

    if include_metadata:
        console.print()
        _print_metadata_table(metadata)


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
