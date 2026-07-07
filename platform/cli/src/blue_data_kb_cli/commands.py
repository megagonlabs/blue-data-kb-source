"""Click command groups for the kb CLI.

State is persisted to ~/.kbdb/state.json between invocations so that IDs
returned by one command can be supplied to the next. Override the path with
the KBDB_STATE_FILE environment variable.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_kb_source import KBSource
from blue_data_kb_source.types import KBError

console = Console()
err = Console(stderr=True)

_STATE_PATH = Path(os.environ.get("KBDB_STATE_FILE", "~/.kbdb/state.json")).expanduser()


# ------------------------------------------------------------------ #
# Backend                                                              #
# ------------------------------------------------------------------ #

class MemoryKBSource(KBSource, MemoryGraphSource):
    pass


def _load_kb() -> MemoryKBSource:
    kb = MemoryKBSource(name="kb")
    if _STATE_PATH.exists():
        try:
            raw = json.loads(_STATE_PATH.read_text())
            kb._nodes = defaultdict(dict, raw.get("nodes", {}))
            kb._edges = defaultdict(dict, raw.get("edges", {}))
        except Exception:
            err.print("[yellow]Warning:[/yellow] could not read state file — starting fresh.")
    return kb


def _save_kb(kb: MemoryKBSource) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(
        {
            "nodes": {db: dict(nodes) for db, nodes in kb._nodes.items()},
            "edges": {db: dict(edges) for db, edges in kb._edges.items()},
        },
        indent=2,
    ))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ------------------------------------------------------------------ #
# Root group                                                           #
# ------------------------------------------------------------------ #

@click.group()
@click.option(
    "--database",
    envvar="KBDB_DATABASE",
    default="default",
    show_default=True,
    help="Target database. Overrides KBDB_DATABASE.",
)
@click.pass_context
def cli(ctx: click.Context, database: str) -> None:
    """kb — Knowledge Base CLI.

    State is stored in ~/.kbdb/state.json (override: KBDB_STATE_FILE).
    """
    ctx.ensure_object(dict)
    kb = _load_kb()
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


@database_group.command("create")
@click.argument("name")
@click.pass_obj
def database_create(obj: dict, name: str) -> None:
    """Create a new database."""
    kb: MemoryKBSource = obj["kb"]
    try:
        kb.create_database(name)
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Created database: [bold]{name}[/bold]")


# ------------------------------------------------------------------ #
# kb schema                                                            #
# ------------------------------------------------------------------ #

@cli.group("schema")
def schema_group() -> None:
    """Schema management."""


@schema_group.group("declare")
def schema_declare() -> None:
    """Declare schema elements."""


@schema_declare.command("class")
@click.argument("name")
@click.argument("description")
@click.pass_obj
def declare_class_cmd(obj: dict, name: str, description: str) -> None:
    """Declare a CLASS node (idempotent)."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        class_id = _run(kb.declare_class(db, name, description))
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Declared class: [bold]{name}[/bold] (id: {class_id})")


@schema_declare.command("relation")
@click.argument("name")
@click.argument("description")
@click.option("--domain", default=None, help="Domain class name.")
@click.option("--range", "range_", default=None, help="Range class name.")
@click.pass_obj
def declare_relation_cmd(
    obj: dict, name: str, description: str, domain: str | None, range_: str | None
) -> None:
    """Declare a RELATION node (idempotent)."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        rel_id = _run(kb.declare_relation_type(db, name, description, domain=domain, range_=range_))
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Declared relation: [bold]{name}[/bold] (id: {rel_id})")


@schema_declare.command("property")
@click.argument("name")
@click.argument("description")
@click.option("--class", "class_id", default=None, help="Attach to class by ID.")
@click.option("--relation", "relation_id", default=None, help="Attach to relation by ID.")
@click.pass_obj
def declare_property_cmd(
    obj: dict, name: str, description: str, class_id: str | None, relation_id: str | None
) -> None:
    """Declare a PROPERTY node (idempotent). Use --class or --relation to attach it."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        if class_id:
            prop_id = _run(kb.declare_class_property(db, name, description, class_id=class_id))
        else:
            prop_id = _run(kb.declare_relation_property(db, name, description, relation_id=relation_id))
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Declared property: [bold]{name}[/bold] (id: {prop_id})")


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
    kb: MemoryKBSource = obj["kb"]
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
# kb source                                                            #
# ------------------------------------------------------------------ #

@cli.group("source")
def source_group() -> None:
    """Provenance source management."""


@source_group.command("add")
@click.argument("source")
@click.option("--name", default=None, help="Human-readable label (defaults to source value).")
@click.option("--description", default=None, help="Optional description.")
@click.pass_obj
def source_add(obj: dict, source: str, name: str | None, description: str | None) -> None:
    """Register a source document and return its ID."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        src_id = _run(kb.add_source(db, source, name=name, description=description))
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added source: [bold]{source}[/bold] (id: {src_id})")


# ------------------------------------------------------------------ #
# kb entity                                                            #
# ------------------------------------------------------------------ #

@cli.group("entity")
def entity_group() -> None:
    """Entity instance management."""


@entity_group.command("add")
@click.argument("name")
@click.option("--class",       "class_id",   default=None, help="Class ID.")
@click.option("--description",               default=None, help="Optional description.")
@click.option("--text",                      default=None, help="Source text span (creates provenance).")
@click.option("--source",      "source_id",  default=None, help="Source node ID.")
@click.pass_obj
def entity_add(
    obj: dict,
    name: str,
    class_id: str | None,
    description: str | None,
    text: str | None,
    source_id: str | None,
) -> None:
    """Add an entity instance."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        entity_id = _run(kb.add_entity(db, name, description=description,
                                        class_id=class_id, text=text, source_id=source_id))
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added entity: [bold]{name}[/bold] (id: {entity_id})")


# ------------------------------------------------------------------ #
# kb fact                                                              #
# ------------------------------------------------------------------ #

@cli.group("fact")
def fact_group() -> None:
    """Fact management."""


@fact_group.command("add")
@click.option("--head",     "head_entity_id", required=True, help="Head entity ID.")
@click.option("--relation", "relation_id",    required=True, help="Relation ID.")
@click.option("--tail",     "tail_entity_id", required=True, help="Tail entity ID.")
@click.option("--text",                        default=None,  help="Source text span (creates provenance).")
@click.option("--source",   "source_id",       default=None,  help="Source node ID.")
@click.pass_obj
def fact_add(
    obj: dict,
    head_entity_id: str,
    relation_id: str,
    tail_entity_id: str,
    text: str | None,
    source_id: str | None,
) -> None:
    """Add a fact (relation instance) between two entities."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        fact_id = _run(kb.add_fact(db,
                                    head_entity_id=head_entity_id,
                                    tail_entity_id=tail_entity_id,
                                    relation_id=relation_id,
                                    text=text, source_id=source_id))
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print(f"Added fact (id: {fact_id})")


# ------------------------------------------------------------------ #
# kb extract                                                           #
# ------------------------------------------------------------------ #

@cli.command("extract")
@click.argument("text")
@click.option("--query",  "queries",   multiple=True, help="Guiding question (repeatable).")
@click.option("--source", "source_id", default=None,  help="Source node ID for provenance.")
@click.option("--model",               default="gpt-4o", show_default=True, help="OpenAI model.")
@click.pass_obj
def extract_cmd(
    obj: dict,
    text: str,
    queries: tuple[str, ...],
    source_id: str | None,
    model: str,
) -> None:
    """Extract entities, facts, and property values from free text.

    Requires the openai extra: pip install 'blue-data-kb-source[openai]'
    and OPENAI_API_KEY in the environment.
    """
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        with console.status("Extracting…"):
            _run(kb.import_text(db, text,
                                queries=list(queries) or None,
                                model=model,
                                source_id=source_id))
        _save_kb(kb)
    except KBError as exc:
        raise click.ClickException(str(exc))
    console.print("Extraction complete.")


# ------------------------------------------------------------------ #
# kb answer                                                            #
# ------------------------------------------------------------------ #

@cli.command("answer")
@click.argument("question")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
)
@click.pass_obj
def answer_cmd(obj: dict, question: str, fmt: str) -> None:
    """Answer a natural language question using the knowledge base."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        results = _run(kb.answer(db, question))
    except KBError as exc:
        raise click.ClickException(str(exc))
    if fmt == "json":
        console.print_json(json.dumps([
            {"entity": r.entity, "relation": r.relation,
             "value": r.value, "provenance": r.provenance}
            for r in results
        ]))
    else:
        t = Table(show_header=True, header_style="bold")
        t.add_column("Entity")
        t.add_column("Relation")
        t.add_column("Value")
        t.add_column("Provenance")
        for r in results:
            t.add_row(r.entity or "", r.relation or "", r.value or "", r.provenance or "")
        console.print(t)


# ------------------------------------------------------------------ #
# kb validate                                                          #
# ------------------------------------------------------------------ #

@cli.command("validate")
@click.argument("statement")
@click.pass_obj
def validate_cmd(obj: dict, statement: str) -> None:
    """Return supporting and counter evidence for a statement."""
    kb: MemoryKBSource = obj["kb"]
    db: str = obj["db"]
    try:
        result = _run(kb.validate(db, statement))
    except KBError as exc:
        raise click.ClickException(str(exc))

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
