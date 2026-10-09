"""Node-id/type checks shared by SchemaManager and KnowledgeManager.

Public write methods on those managers accept many id-typed parameters
(class_id, relation_id, entity_id, ...) that reference an existing node.
Neither the managers nor the underlying GraphSource validate that the id
exists or names the expected node label, so a wrong or stale id silently
no-ops (delete/update) or wires an edge to nowhere (add_edge). These helpers
give callers — including the LLM extraction agent, which supplies ids
programmatically and sees only ``str(exc)`` back as a tool result — a clear,
actionable error instead.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blue_data.source.graph.graph_source import GraphSource


def require_node_type(
    source: "GraphSource",
    database: str,
    node_id: str,
    expected_label: str,
    param_name: str,
    error_cls: type[Exception],
) -> dict:
    """Return the node for *node_id*, raising *error_cls* if it doesn't exist
    or isn't labeled *expected_label*.

    Callers should only invoke this when *node_id* is not None — params that
    are legitimately optional (omit to skip creating an edge/link) should
    skip this check entirely rather than pass None through.
    """
    node = source.get_node(database, node_id)
    if node is None:
        raise error_cls(
            f"{param_name} {node_id!r} does not exist in database {database!r}. "
            f"Expected the id of an existing {expected_label} node."
        )
    actual_label = node.get("label")
    if actual_label != expected_label:
        raise error_cls(
            f"{param_name} {node_id!r} refers to a {actual_label} node, "
            f"but a {expected_label} node id was expected."
        )
    return node


def require_node_type_one_of(
    source: "GraphSource",
    database: str,
    node_id: str,
    expected_labels: tuple[str, ...],
    param_name: str,
    error_cls: type[Exception],
) -> dict:
    """Like `require_node_type`, but accepts any label in *expected_labels*."""
    node = source.get_node(database, node_id)
    if node is None:
        expected = " or ".join(expected_labels)
        raise error_cls(
            f"{param_name} {node_id!r} does not exist in database {database!r}. "
            f"Expected the id of an existing {expected} node."
        )
    actual_label = node.get("label")
    if actual_label not in expected_labels:
        expected = " or ".join(expected_labels)
        raise error_cls(
            f"{param_name} {node_id!r} refers to a {actual_label} node, "
            f"but a {expected} node id was expected."
        )
    return node


_DATABASE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")


def require_database_name(database: str) -> str:
    """Return *database* if it is a plain database name — letters, digits, `_` and `-`,
    at most 64 characters — and raise KBError otherwise.

    File-backed stores turn the name into a file path (`<dir>/<database>.json`, the
    BM25 corpus files), so a separator, `..` or an absolute path would read or write
    outside the data directory. Callers include MCP tool arguments, i.e. text an
    agent chose (#116).
    """
    from blue_data_kb_source.types import KBError

    if not isinstance(database, str) or not _DATABASE_NAME.fullmatch(database):
        raise KBError(
            f"invalid database name {database!r}: use 1-64 letters, digits, '_' or '-' "
            "(no path separators, '..' or absolute paths)"
        )
    return database


def require_free_id(
    source: "GraphSource",
    database: str,
    node_id: str | None,
    param_name: str,
    error_cls: type[Exception],
) -> None:
    """Raise *error_cls* if a caller-supplied *node_id* for a new node is already taken.

    GraphSource.add_node doesn't check: the memory/file backends silently replace the
    node holding that id (an entity could become a CLASS), and Neo4j creates a second
    node answering to the same id (#117). None — let the backend mint an id — passes.
    """
    if node_id is not None and source.get_node(database, node_id) is not None:
        raise error_cls(
            f"{param_name} {node_id!r} is already the id of an existing node; "
            "omit it to have one generated, or pass an unused id."
        )

