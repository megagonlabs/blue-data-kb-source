from __future__ import annotations

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import EXTRACTED_FROM, EXTRACTION, SOURCE


class ProvenanceTracker:
    """Creates and wires SOURCE and EXTRACTION provenance nodes."""

    def __init__(self, source: GraphSource) -> None:
        self._source = source

    async def add_source(
        self,
        database: str,
        source: str,
        name: str | None = None,
        description: str | None = None,
        node_id: str | None = None,
    ) -> str:
        if name is None:
            name = source
        return self._source.add_node(
            database,
            SOURCE,
            {"name": name, "description": description, "source": source},
            node_id=node_id,
        )

    async def add_extraction(
        self,
        database: str,
        text: str,
        name: str | None = None,
        description: str | None = None,
        node_id: str | None = None,
        source_id: str | None = None,
    ) -> str:
        if name is None:
            name = text
        n = self._source.add_node(
            database,
            EXTRACTION,
            {"name": name, "description": description, "text": text},
            node_id=node_id,
        )
        if source_id is not None:
            self._source.add_edge(database, n, EXTRACTED_FROM, source_id)
        return n

    async def track(
        self,
        database: str,
        instance_id: str,
        text: str,
        source_id: str | None = None,
    ) -> None:
        """Create an EXTRACTION node and wire instance_id -[EXTRACTED_FROM]-> extraction."""
        extraction_id = await self.add_extraction(database, text=text, source_id=source_id)
        self._source.add_edge(database, instance_id, EXTRACTED_FROM, extraction_id)

    async def add_extracted_from(
        self,
        database: str,
        element_id: str,
        extraction_id: str,
    ) -> None:
        self._source.add_edge(database, element_id, EXTRACTED_FROM, extraction_id)
