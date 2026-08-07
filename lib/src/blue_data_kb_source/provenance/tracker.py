from __future__ import annotations

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import EXTRACTED_FROM, EXTRACTION, SOURCE
from blue_data_kb_source.timestamps import edge_stamped, stamped, touched


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
        original: str | None = None,
    ) -> str:
        if name is None:
            name = source
        return self._source.add_node(
            database,
            SOURCE,
            stamped({"name": name, "description": description, "source": source, "original": original}),
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
            stamped({"name": name, "description": description, "text": text}),
            node_id=node_id,
        )
        if source_id is not None:
            self._source.add_edge(database, n, EXTRACTED_FROM, source_id, properties=edge_stamped())
        return n

    async def link_extraction(
        self,
        database: str,
        element_id: str,
        text: str,
        source_id: str | None = None,
    ) -> str:
        """Create an EXTRACTION node and wire element_id -[EXTRACTED_FROM]-> extraction.

        Elements may accumulate multiple EXTRACTED_FROM edges over time as
        additional text spans are found; calling this again for the same
        element_id adds a new edge rather than replacing the existing one.
        """
        extraction_id = await self.add_extraction(database, text=text, source_id=source_id)
        self._source.add_edge(database, element_id, EXTRACTED_FROM, extraction_id, properties=edge_stamped())
        return extraction_id

    async def add_extracted_from(
        self,
        database: str,
        element_id: str,
        extraction_id: str,
    ) -> None:
        self._source.add_edge(database, element_id, EXTRACTED_FROM, extraction_id, properties=edge_stamped())

    async def update_source(
        self,
        database: str,
        source_id: str,
        name: str | None = None,
        description: str | None = None,
        original: str | None = None,
    ) -> None:
        props = {k: v for k, v in {"name": name, "description": description, "original": original}.items()
                 if v is not None}
        if not props:
            raise ValueError("At least one of name, description, or original must be provided.")
        self._source.update_node(database, source_id, touched(props))

    async def delete_source(self, database: str, source_id: str) -> None:
        """Delete a SOURCE node and all its EXTRACTION nodes.

        Instance nodes (ENTITY, FACT, LITERAL) are preserved; only the
        EXTRACTED_FROM edges from instances to the deleted EXTRACTIONs
        are removed (via delete_node's edge cleanup).
        """
        for nb in self._source.get_neighbors(database, source_id, edge_type=EXTRACTED_FROM, direction="in"):
            self._source.delete_node(database, nb["node"]["id"])
        self._source.delete_node(database, source_id)
