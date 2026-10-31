"""created/modified timestamp helpers shared by SchemaManager, KnowledgeManager,
and ProvenanceTracker.

Every node's `properties` dict carries a `created` timestamp (set once, at
node-creation time) and a `modified` timestamp (refreshed whenever the node's
own attributes -- not its edges -- change). Both are ISO-8601 UTC strings.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> str:
    """Current time as an ISO-8601 UTC string."""
    return datetime.now(UTC).isoformat()


def stamped(properties: dict) -> dict:
    """Return *properties* with created/modified set to the same new timestamp.

    Use when building the properties dict passed to add_node.
    """
    ts = now()
    return {**properties, "created": ts, "modified": ts}


def touched(properties: dict) -> dict:
    """Return *properties* with modified refreshed to now.

    Use when building the properties dict passed to update_node.
    """
    return {**properties, "modified": now()}


def edge_stamped() -> dict:
    """Properties dict for a newly created edge: just `created` (edges have no `modified`).

    Use as the `properties=` argument to add_edge.
    """
    return {"created": now()}
