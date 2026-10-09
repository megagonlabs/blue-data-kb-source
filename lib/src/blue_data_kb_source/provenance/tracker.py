from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import ENTITY, EXTRACTED_FROM, EXTRACTION, FACT, LITERAL, SOURCE
from blue_data_kb_source.checks import require_free_id, require_node_type, require_node_type_one_of
from blue_data_kb_source.knowledge.helpers import recompute_fact_confidence
from blue_data_kb_source.timestamps import edge_stamped, stamped, touched
from blue_data_kb_source.types import ProvenanceError

if TYPE_CHECKING:
    from blue_data_kb_source.notification.notifier import ChangeNotifier


class ProvenanceTracker:
    """Creates and wires SOURCE and EXTRACTION provenance nodes.

    Wiring an item-to-EXTRACTION edge also refreshes that item's derived `confidence`
    when the item is a FACT.
    """

    def __init__(self, source: GraphSource, notifier: ChangeNotifier | None = None) -> None:
        self._source = source
        self._notifier = notifier

    def _fire(self, event: dict) -> None:
        if self._notifier is not None:
            self._notifier.fire(event)

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
        require_free_id(self._source, database, node_id, "node_id", ProvenanceError)
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
        confidence: float | None = None,
        extraction_type: Literal["text_span", "external_record", "assertion"] = "text_span",
        element_id: str | None = None,
    ) -> str:
        """Create an EXTRACTION node for `text`, wired to its SOURCE and/or to the item it
        supports when those ids are given. One node per call: repeated calls for the same
        element_id accumulate EXTRACTED_FROM edges rather than replacing the earlier one.
        """
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ProvenanceError(f"confidence must be within [0.0, 1.0], got {confidence!r}.")
        if name is None:
            name = text
        require_free_id(self._source, database, node_id, "node_id", ProvenanceError)
        n = self._source.add_node(
            database,
            EXTRACTION,
            stamped({"name": name, "description": description, "text": text,
                     "confidence": confidence, "extraction_type": extraction_type}),
            node_id=node_id,
        )
        if source_id is not None:
            self._source.add_edge(database, n, EXTRACTED_FROM, source_id, properties=edge_stamped())
        if element_id is not None:
            await self.add_extracted_from(database, element_id, n)
        return n

    async def add_extracted_from(
        self,
        database: str,
        element_id: str,
        extraction_id: str,
    ) -> None:
        self._source.add_edge(database, element_id, EXTRACTED_FROM, extraction_id, properties=edge_stamped())
        recompute_fact_confidence(self._source, database, element_id)

    async def attach_extraction(
        self, database: str, element_id: str, extraction_id: str
    ) -> None:
        """Attach an existing EXTRACTION to an ENTITY, FACT, or LITERAL (idempotent).

        Use when the extraction already exists — created up front by add_extraction with
        no element_id, or rebuilt on snapshot restore. Not a way to pool evidence: each
        item still gets its own EXTRACTION node.
        """
        require_node_type_one_of(
            self._source, database, element_id, (ENTITY, FACT, LITERAL),
            "element_id", ProvenanceError,
        )
        require_node_type(
            self._source, database, extraction_id, EXTRACTION, "extraction_id", ProvenanceError
        )
        nbs = self._source.get_neighbors(
            database, element_id, edge_type=EXTRACTED_FROM, direction="out"
        )
        if any(nb["node"]["id"] == extraction_id for nb in nbs):
            return
        await self.add_extracted_from(database, element_id, extraction_id)
        self._fire({
            "type": "extraction_attached",
            "database": database,
            "id": element_id,
            "element_id":    element_id,
            "extraction_id": extraction_id,
        })

    async def update_source(
        self,
        database: str,
        source_id: str,
        name: str | None = None,
        description: str | None = None,
        original: str | None = None,
    ) -> None:
        require_node_type(self._source, database, source_id, SOURCE, "source_id", ProvenanceError)
        props = {k: v for k, v in {"name": name, "description": description, "original": original}.items()
                 if v is not None}
        if not props:
            raise ValueError("At least one of name, description, or original must be provided.")
        self._source.update_node(database, source_id, touched(props))

    async def delete_source(self, database: str, source_id: str) -> None:
        """Delete a SOURCE node and all its EXTRACTION nodes.

        Instance nodes (ENTITY, FACT, LITERAL) are preserved; only the
        EXTRACTED_FROM edges from instances to the deleted EXTRACTIONs
        are removed (via delete_node's edge cleanup). Raises ProvenanceError if
        *source_id* isn't a SOURCE — given any other id this used to delete that
        node silently, bypassing its own manager's checks and events (#112).
        """
        require_node_type(self._source, database, source_id, SOURCE, "source_id", ProvenanceError)
        for nb in self._source.get_neighbors(database, source_id, edge_type=EXTRACTED_FROM, direction="in"):
            self._source.delete_node(database, nb["node"]["id"])
        self._source.delete_node(database, source_id)
