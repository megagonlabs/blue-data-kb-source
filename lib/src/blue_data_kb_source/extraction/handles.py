"""Short handles for node ids in what the extraction agent reads and writes (#134).

The agent used to see and copy 36-character uuids, and got them wrong often: in
one full benchmark run, 72 tool calls (about 5% of them) named an id that didn't
exist — a plausible-looking uuid with digits changed, or one with a segment
dropped — and the fact or value the call was writing was lost. The model now
sees `E3`, `R2`, `C1`, ... instead: every uuid of a node in the system prompt
and in tool results is replaced with a handle on the way out, and every `*_id`
argument holding a handle is replaced with the id on the way in. Nothing else
changes: the graph, callbacks and traces all keep the real ids, and an argument
that holds a real id (or a name) is left for the usual resolution.
"""
from __future__ import annotations

import re
from typing import Any

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    ENTITY,
    EXTRACTION,
    FACT,
    LITERAL,
    PROPERTY,
    RELATION,
    SOURCE,
)

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_PREFIX = {CLASS: "C", RELATION: "R", PROPERTY: "P", ENTITY: "E", FACT: "F",
           LITERAL: "V", EXTRACTION: "X", SOURCE: "S"}


class Handles:
    """One database's id <-> handle table. Handles are stable for the table's
    lifetime, so a handle means the same node in every stage, pass and chunk."""

    def __init__(self, source: GraphSource, database: str) -> None:
        self._source = source
        self._database = database
        self._handle: dict[str, str] = {}
        self._id: dict[str, str] = {}
        self._count: dict[str, int] = {}

    def shorten(self, text: str) -> str:
        """*text* with every node's uuid replaced by its handle; other uuids are kept."""
        return _UUID.sub(lambda m: self._handle_for(m.group(0)) or m.group(0), text)

    def expand(self, value: Any) -> Any:
        """The node id a handle stands for; anything else is returned unchanged."""
        if isinstance(value, str):
            return self._id.get(value.strip().upper(), value)
        return value

    def expand_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """*args* with each `*_id` argument's handle replaced by its node id."""
        return {k: (self.expand(v) if k.endswith("_id") else v) for k, v in args.items()}

    def _handle_for(self, node_id: str) -> str | None:
        if node_id in self._handle:
            return self._handle[node_id]
        node = self._source.get_node(self._database, node_id)
        if node is None:
            return None
        prefix = _PREFIX.get(node.get("label"), "N")
        self._count[prefix] = self._count.get(prefix, 0) + 1
        handle = f"{prefix}{self._count[prefix]}"
        self._handle[node_id] = handle
        self._id[handle] = node_id
        return handle
