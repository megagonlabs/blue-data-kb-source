"""`kb profile` — the profile command group, over blue_data.utils.config.

The engine (spec, `.profiles` I/O, precedence, secrets) lives in
`blue_data.utils.config` so every Blue CLI can share it; this module is the
Click layer over it and holds nothing reusable but the prompting.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from blue_data.utils.config import (
    MASK,
    ConfigError,
    ConfigResolver,
    ProfileError,
    ProfileStore,
    base_spec,
    display,
    encode,
    load_spec,
)
from rich.console import Console
from rich.table import Table

console = Console()

_SPEC_PATH = Path(__file__).with_name("profile_spec.json")


def cli_spec():
    """The family-wide parameters plus this CLI's own."""
    return base_spec().merged_with(load_spec(_SPEC_PATH))


def profile_store(home: str) -> ProfileStore:
    return ProfileStore(home)


def resolver_for(home: str, profile: str | None = None) -> ConfigResolver:
    return ConfigResolver(cli_spec(), profile_store(home), profile=profile)


def _target(obj: dict, name: str | None) -> str:
    """The profile a command acts on: the named one, else the selected one."""
    return name or obj["profile"]


def _prompt_for(param, current: str | None) -> str | None:
    """Ask for one parameter's value, honoring its cast.

    Returns None when the user accepts an empty value, which leaves the
    parameter unset rather than storing an empty string.
    """
    label = param.prompt or param.name
    if param.is_secret:
        shown = display(current) if current else "(unset)"
        click.echo(f"{label} [{shown}]")
        raw = click.prompt(
            "  value (blank keeps current; $VAR stores a reference)",
            default="", show_default=False, hide_input=True,
        )
        return raw or None
    raw = click.prompt(f"{label}", default=current if current is not None else "", show_default=True)
    return raw or None


def _store_value(param, value: str) -> str:
    """The form a value takes on disk — secrets encoded, everything else cast."""
    if param.is_secret:
        return encode(value)
    return str(param.cast_value(value))


# ------------------------------------------------------------------ #
# kb profile                                                          #
# ------------------------------------------------------------------ #

@click.group("profile")
def profile_group() -> None:
    """Profile and configuration management."""


@profile_group.command("add")
@click.argument("name")
@click.pass_obj
def profile_add(obj: dict, name: str) -> None:
    """Create a new profile."""
    try:
        profile_store(obj["home"]).add(name)
    except ProfileError as exc:
        raise click.ClickException(str(exc)) from None
    console.print(
        f"Created profile: [bold]{name}[/bold]\n"
        f"[dim]Configure it with `kb profile config` "
        f"(add `--profile {name}` if it is not the selected one).[/dim]"
    )


@profile_group.command("remove")
@click.argument("name")
@click.option("--confirm", is_flag=True, default=False, help="Skip the confirmation prompt.")
@click.pass_obj
def profile_remove(obj: dict, name: str, confirm: bool) -> None:
    """Delete a profile and everything configured in it."""
    store = profile_store(obj["home"])
    if not store.exists(name):
        raise click.ClickException(f"no such profile: {name!r}")
    if not confirm:
        click.confirm(
            f"Remove profile {name!r} and all of its configuration?", abort=True
        )
    try:
        store.remove(name)
    except ProfileError as exc:
        raise click.ClickException(str(exc)) from None
    console.print(f"Removed profile: [bold]{name}[/bold]")


@profile_group.command("list")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table", show_default=True)
@click.pass_obj
def profile_list(obj: dict, fmt: str) -> None:
    """List profiles."""
    store = profile_store(obj["home"])
    names = store.list_profiles()
    selected = store.selected()
    if fmt == "json":
        console.print_json(json.dumps(
            {"selected": selected, "profiles": names}, indent=2
        ))
        return
    if not names:
        console.print("[dim](no profiles — run `kb profile add NAME`)[/dim]")
        return
    t = Table(show_header=True, header_style="bold")
    t.add_column("Profile")
    t.add_column("Selected", style="dim")
    t.add_column("Configured", style="dim")
    for n in names:
        t.add_row(n, "*" if n == selected else "", str(len(store.values(n))))
    console.print(t)


@profile_group.command("select")
@click.argument("name")
@click.pass_obj
def profile_select(obj: dict, name: str) -> None:
    """Make a profile the default for subsequent commands."""
    try:
        profile_store(obj["home"]).select(name)
    except ProfileError as exc:
        raise click.ClickException(str(exc)) from None
    console.print(f"Selected profile: [bold]{name}[/bold]")


@profile_group.command("inspect")
@click.argument("name", required=False)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table", show_default=True)
@click.pass_obj
def profile_inspect(obj: dict, name: str | None, fmt: str) -> None:
    """Show every configuration value and where it came from.

    Secrets are masked; a `$VAR` reference is shown by name, with whether it
    currently resolves, so you can see what to export.
    """
    target = _target(obj, name)
    store = profile_store(obj["home"])
    spec = cli_spec()
    # Reuse the root group's resolver when reporting on the active profile: it
    # snapshotted the environment before applying the profile, so `inspect`
    # attributes values to the tier that really set them rather than to the
    # exports the CLI just made.
    res = obj["config"] if target == obj["profile"] else ConfigResolver(spec, store, profile=target)
    stored = res.profile_values()

    rows: list[dict[str, Any]] = []
    for param in spec:
        source = res.source_of(param.name)
        if param.is_secret:
            # Describe the tier that actually won. Showing the profile's stored
            # form while reporting "environment" as the source would contradict
            # itself — an exported variable outranks a profile that references one.
            raw = stored.get(param.name)
            if source == "environment":
                shown = MASK
            elif raw is not None:
                # Whatever the profile holds, including a reference that does
                # not currently resolve — naming the variable is how the user
                # learns what to export.
                shown = display(raw)
            elif source == "unset":
                shown = "(unset)"
            else:
                shown = MASK
        else:
            value = res.get(param.name)
            shown = "(unset)" if value is None else str(value)
        rows.append({"name": param.name, "value": shown, "source": source})

    if fmt == "json":
        console.print_json(json.dumps(
            {"profile": target, "values": {r["name"]: {"value": r["value"], "source": r["source"]} for r in rows}},
            indent=2,
        ))
        return
    t = Table(show_header=True, header_style="bold", title=f"profile: {target}")
    t.add_column("Parameter")
    t.add_column("Value")
    t.add_column("Source", style="dim")
    for r in rows:
        t.add_row(r["name"], r["value"], r["source"])
    console.print(t)


@profile_group.command("config")
@click.argument("param", required=False)
@click.argument("value", required=False)
@click.option("--profile", "profile_name", default=None, help="Profile to configure. Default: the selected one.")
@click.pass_obj
def profile_config(obj: dict, param: str | None, value: str | None, profile_name: str | None) -> None:
    """Set configuration values.

    \b
    kb profile config                  walk through every required parameter
    kb profile config PARAM            prompt for one parameter
    kb profile config PARAM VALUE      set one parameter directly

    Secrets are never taken as VALUE on the command line — a secret parameter
    always prompts, so it stays out of shell history. Enter `$VAR` at the
    prompt to store a reference to an environment variable instead of the
    secret itself.
    """
    target = _target(obj, profile_name)
    store = profile_store(obj["home"])
    spec = cli_spec()

    if not store.exists(target):
        # The startup hook creates `default`; this covers `--profile NAME` for
        # one that was never added.
        store.add(target)
        console.print(f"[dim]Created profile {target!r}.[/dim]")

    stored = store.values(target)

    if param is None:
        params = spec.required()
        console.print(
            f"Configuring profile [bold]{target}[/bold] "
            f"({len(params)} required parameters; blank keeps the current value).\n"
        )
    else:
        try:
            params = [spec[param]]
        except ConfigError as exc:
            raise click.ClickException(str(exc)) from None

    if value is not None:
        one = params[0]
        if one.is_secret:
            raise click.ClickException(
                f"{one.name} is a secret and cannot be set on the command line, where it "
                f"would land in shell history. Run `kb profile config {one.name}` and enter "
                f"it at the prompt, or enter `$VAR` there to reference an environment variable."
            )
        try:
            store.set_value(target, one.name, _store_value(one, value))
        except ConfigError as exc:
            raise click.ClickException(str(exc)) from None
        console.print(f"Set [bold]{one.name}[/bold] in profile [bold]{target}[/bold]")
        return

    changed = 0
    for p in params:
        current = stored.get(p.name)
        answer = _prompt_for(p, current)
        if answer is None:
            continue
        try:
            store.set_value(target, p.name, _store_value(p, answer))
        except ConfigError as exc:
            console.print(f"[red]{exc}[/red] [dim](skipped)[/dim]")
            continue
        changed += 1

    console.print(
        f"\nUpdated [bold]{changed}[/bold] parameter(s) in profile [bold]{target}[/bold]."
        if changed else "\nNo changes."
    )
