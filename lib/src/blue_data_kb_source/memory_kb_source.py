from __future__ import annotations

from blue_data.source.graph.memory_graph_source import MemoryGraphSource

from blue_data_kb_source.kb_source import KBSource


class MemoryKBSource(KBSource, MemoryGraphSource):
    """In-memory KBSource — in-process, non-persistent. Development, testing, short-lived pipelines."""

    def _initialize_connection_properties(self) -> None:
        conn = self.properties.get("connection", {})
        conn.setdefault("protocol", "kb/memory")
        self.properties["connection"] = conn
