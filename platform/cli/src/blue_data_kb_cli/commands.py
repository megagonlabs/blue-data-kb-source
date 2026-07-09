"""Click command groups for the kb CLI.

Each database is persisted as a JSON file inside the data directory:

    {KBDB_DATA_DIR}/{database}.json   (KBDB_DATA_DIR defaults to ~/.kbdb)

FileGraphSource handles all I/O automatically — no explicit save step is needed.
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

from blue_data.source.graph.file_graph_source import FileGraphSource
from blue_data_kb_source import KBSource
from blue_data_kb_source._constants import (
    CLASS,
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_SUBCLASS_OF,
    IS_VALUE_OF,
    META_DATABASE,
    PROPERTY,
    RELATION,
    REPRESENTS,
    SOURCE,
    TAIL,
)
from blue_data_kb_source.types import KBError

console = Console()


# ------------------------------------------------------------------ #
# Backend                                                              #
# ------------------------------------------------------------------ #

class FileKBSource(KBSource, FileGraphSource):
    pass


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


def _make_kb() -> FileKBSource:
    return FileKBSource(name="kb", directory=_data_dir())


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


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
    return nbs[0]["node"].get("properties", {}).get("text", "") if nbs else ""


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

    Each database is stored as {KBDB_DATA_DIR}/{database}.json
    (KBDB_DATA_DIR defaults to ~/.kbdb).
    """
    ctx.ensure_object(dict)
    kb = _make_kb()
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
@click.pass_obj
def database_list(obj: dict, fmt: str) -> None:
    """List all databases."""
    kb: FileKBSource = obj["kb"]
    dbs = sorted(d for d in kb.fetch_databases() if d != META_DATABASE)
    if fmt == "json":
        console.print_json(json.dumps(dbs))
    else:
        t = Table(show_header=True, header_style="bold")
        t.add_column("Database")
        for d in dbs:
            t.add_row(d)
        console.print(t)


@database_group.command("create")
@click.argument("name")
@click.pass_obj
def database_create(obj: dict, name: str) -> None:
    """Create a new database."""
    kb: FileKBSource = obj["kb"]
    try:
        kb.create_database(name)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Created database: [bold]{name}[/bold]")


@database_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def database_delete(name: str, yes: bool) -> None:
    """Delete a database and all its data."""
    if name == META_DATABASE:
        raise click.ClickException(f"Cannot delete the reserved '{META_DATABASE}' database.")
    path = Path(_data_dir()) / f"{name}.json"
    if not path.exists():
        raise click.ClickException(f"Database '{name}' not found.")
    if not yes:
        click.confirm(f"Delete database '{name}'? This cannot be undone.", abort=True)
    path.unlink()
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
        kb.delete_edge(db, resolved_child, IS_SUBCLASS_OF, resolved_parent)
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
        kb.delete_edge(db, resolved_child, IS_SUBCLASS_OF, resolved_parent)
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


@schema_group.command("search")
@click.argument("query")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def schema_search(obj: dict, query: str, fmt: str) -> None:
    """Search schema elements by keyword."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        results = _run(kb.search_schema(db, query))
    except KBError as exc:
        raise click.ClickException(str(exc))
    if fmt == "json":
        console.print_json(json.dumps([
            {"id": r.id, "type": r.type, "name": r.name, "description": r.description}
            for r in results
        ]))
    else:
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
        for r in sorted(entities, key=lambda x: (x.get("class_name", ""), x.get("entity_name", ""))):
            row = [r.get("entity_name", ""), r.get("class_name", "")]
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
        for r in sorted(ent_props, key=lambda x: (x.get("entity_name", ""), x.get("property_name", ""))):
            row = [r.get("entity_name", ""), r.get("property_name", ""), str(r.get("property_value", ""))]
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
        for r in sorted(facts, key=lambda x: (x.get("relation_name", ""), x.get("source_name", ""))):
            row = [r.get("source_name", ""), r.get("relation_name", ""), r.get("target_name", "")]
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
        for r in sorted(fact_props, key=lambda x: (x.get("relation_name", ""), x.get("head_name", ""), x.get("property_name", ""))):
            row = [
                r.get("head_name", ""),
                r.get("relation_name", ""),
                r.get("tail_name", ""),
                r.get("property_name", ""),
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
@click.pass_obj
def extract_cmd(
    obj: dict,
    text: str,
    queries: tuple[str, ...],
    source_name: str | None,
    source_id: str | None,
    model: str,
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
        with console.status("Extracting…"):
            _run(kb.import_text(db, text,
                                queries=list(queries) or None,
                                model=model,
                                source_id=resolved_source_id))
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
@click.pass_obj
def answer_cmd(obj: dict, question: str, fmt: str, model: str) -> None:
    """Answer a natural language question using the knowledge base."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        result = _run(kb.answer(db, question, model=model, natural_language=(fmt == "nl")))
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
@click.pass_obj
def validate_cmd(obj: dict, statement: str, natural_language: bool, model: str) -> None:
    """Return supporting and counter evidence for a statement."""
    kb: FileKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        result = _run(kb.validate(db, statement, model=model, natural_language=natural_language))
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
