from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    ENTITY,
    EXTRACTED_FROM,
    FACT,
    HAS_PROPERTY,
    HEAD,
    IS_INSTANCE_OF,
    IS_VALUE_OF,
    LITERAL,
    REPRESENTS,
    SOURCE,
    EXTRACTION,
    TAIL,
)
from blue_data_kb_source.types import AnswerResult, EnrichmentResult, ValidationResult

if TYPE_CHECKING:
    from blue_data_kb_source.notification.notifier import ChangeNotifier
    from blue_data_kb_source.provenance.tracker import ProvenanceTracker


class KnowledgeManager:
    """Manages instance-level nodes (ENTITY, FACT, LITERAL) and knowledge queries."""

    def __init__(
        self,
        source: GraphSource,
        provenance_tracker: ProvenanceTracker,
        change_notifier: ChangeNotifier,
    ) -> None:
        self._source = source
        self._provenance = provenance_tracker
        self._notifier = change_notifier

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _node_name(self, database: str, node_id: str | None) -> str | None:
        if node_id is None:
            return None
        node = self._source.get_node(database, node_id)
        if node is None:
            return None
        return node.get("properties", {}).get("name")

    def _first_neighbor(
        self, database: str, node_id: str, edge_type: str, direction: str = "out"
    ) -> str | None:
        nbs = self._source.get_neighbors(database, node_id, edge_type=edge_type, direction=direction)
        return nbs[0]["node"]["id"] if nbs else None

    def _fact_context(self, database: str, fact_id: str | None) -> dict:
        if fact_id is None:
            return {"head_entity_name": None, "tail_entity_name": None, "relation_name": None}
        return {
            "head_entity_name": self._node_name(database, self._first_neighbor(database, fact_id, HEAD)),
            "tail_entity_name": self._node_name(database, self._first_neighbor(database, fact_id, TAIL)),
            "relation_name": self._node_name(database, self._first_neighbor(database, fact_id, REPRESENTS)),
        }

    def _provenance_text(self, database: str, node_id: str) -> str | None:
        nbs = self._source.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out")
        return nbs[0]["node"].get("properties", {}).get("text") if nbs else None

    # ------------------------------------------------------------------ #
    # Instance node builders                                               #
    # ------------------------------------------------------------------ #

    async def add_entity(
        self,
        database: str,
        name: str,
        description: str | None = None,
        entity_id: str | None = None,
        class_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        node_id = self._source.add_node(
            database,
            ENTITY,
            {"name": name, "description": description},
            node_id=entity_id,
        )
        if class_id is not None:
            self._source.add_edge(database, node_id, IS_INSTANCE_OF, class_id)
        if text is not None:
            await self._provenance.track(database, node_id, text, source_id)
        self._notifier.fire({
            "type": "entity_added",
            "database": database,
            "id": node_id,
            "name": name,
            "class_id": class_id,
            "class_name": self._node_name(database, class_id),
        })
        return node_id

    async def add_fact(
        self,
        database: str,
        name: str | None = None,
        description: str | None = None,
        fact_id: str | None = None,
        head_entity_id: str | None = None,
        tail_entity_id: str | None = None,
        relation_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        if name is None:
            head_name = self._node_name(database, head_entity_id) or "?"
            tail_name = self._node_name(database, tail_entity_id) or "?"
            rel_name = self._node_name(database, relation_id) or "?"
            name = f"{head_name}_{rel_name}_{tail_name}"
        node_id = self._source.add_node(
            database, FACT, {"name": name, "description": description}, node_id=fact_id
        )
        if head_entity_id is not None:
            self._source.add_edge(database, node_id, HEAD, head_entity_id)
        if tail_entity_id is not None:
            self._source.add_edge(database, node_id, TAIL, tail_entity_id)
        if relation_id is not None:
            self._source.add_edge(database, node_id, REPRESENTS, relation_id)
        if text is not None:
            await self._provenance.track(database, node_id, text, source_id)
        self._notifier.fire({
            "type": "fact_added",
            "database": database,
            "id": node_id,
            "head_entity_id": head_entity_id,
            "tail_entity_id": tail_entity_id,
            "relation_id": relation_id,
            "head_entity_name": self._node_name(database, head_entity_id),
            "tail_entity_name": self._node_name(database, tail_entity_id),
            "relation_name": self._node_name(database, relation_id),
        })
        return node_id

    async def add_literal(
        self,
        database: str,
        value: str | int | float | bool,
        name: str | None = None,
        description: str | None = None,
        literal_type: Literal["NUMBER", "DATE", "BOOLEAN", "STRING"] | None = None,
        property_id: str | None = None,
        node_id: str | None = None,
    ) -> str:
        if name is None:
            name = str(value)
        if literal_type is None:
            # bool must be checked before int (bool is a subclass of int)
            if isinstance(value, bool):
                literal_type = "BOOLEAN"
            elif isinstance(value, (int, float)):
                literal_type = "NUMBER"
            else:
                literal_type = "STRING"
        lid = self._source.add_node(
            database,
            LITERAL,
            {"name": name, "description": description, "value": value, "type": literal_type},
            node_id=node_id,
        )
        if property_id is not None:
            self._source.add_edge(database, lid, IS_VALUE_OF, property_id)
        return lid

    async def add_entity_property_value(
        self,
        database: str,
        value: str | int | float | bool,
        name: str | None = None,
        description: str | None = None,
        literal_type: Literal["NUMBER", "DATE", "BOOLEAN", "STRING"] | None = None,
        entity_id: str | None = None,
        property_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        literal_id = await self.add_literal(
            database, value, name=name, description=description,
            literal_type=literal_type, property_id=property_id
        )
        if entity_id is not None:
            self._source.add_edge(database, entity_id, HAS_PROPERTY, literal_id)
        if text is not None:
            await self._provenance.track(database, literal_id, text, source_id)
        self._notifier.fire({
            "type": "entity_property_added",
            "database": database,
            "id": literal_id,
            "entity_id": entity_id,
            "property_id": property_id,
            "value": value,
            "property_name": self._node_name(database, property_id),
        })
        return literal_id

    async def add_fact_property_value(
        self,
        database: str,
        value: str | int | float | bool,
        name: str | None = None,
        description: str | None = None,
        literal_type: Literal["NUMBER", "DATE", "BOOLEAN", "STRING"] | None = None,
        fact_id: str | None = None,
        property_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        literal_id = await self.add_literal(
            database, value, name=name, description=description,
            literal_type=literal_type, property_id=property_id
        )
        if fact_id is not None:
            self._source.add_edge(database, fact_id, HAS_PROPERTY, literal_id)
        if text is not None:
            await self._provenance.track(database, literal_id, text, source_id)
        self._notifier.fire({
            "type": "fact_property_added",
            "database": database,
            "id": literal_id,
            "fact_id": fact_id,
            "property_id": property_id,
            "value": value,
            "property_name": self._node_name(database, property_id),
            **self._fact_context(database, fact_id),
        })
        return literal_id

    # ------------------------------------------------------------------ #
    # Data export                                                          #
    # ------------------------------------------------------------------ #

    async def fetch_database_data(
        self,
        database: str,
        include_provenance: bool = False,
        compact: bool = False,
    ) -> dict | list:
        if compact:
            return self._fetch_compact(database, include_provenance)

        nodes: list[dict] = []
        edges: list[dict] = []

        def _edge(type_: str, src: str, tgt: str) -> dict:
            return {"type": type_, "source_id": src, "target_id": tgt}

        for node in self._source.match_nodes(database, label=ENTITY):
            nid = node["id"]
            nodes.append({"id": nid, "label": ENTITY, "properties": node.get("properties", {})})
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_INSTANCE_OF, direction="out"):
                edges.append(_edge(IS_INSTANCE_OF, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=HAS_PROPERTY, direction="out"):
                edges.append(_edge(HAS_PROPERTY, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self._source.get_neighbors(database, nid, edge_type=EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(EXTRACTED_FROM, nid, nb["node"]["id"]))

        for node in self._source.match_nodes(database, label=FACT):
            nid = node["id"]
            nodes.append({"id": nid, "label": FACT, "properties": node.get("properties", {})})
            for nb in self._source.get_neighbors(database, nid, edge_type=REPRESENTS, direction="out"):
                edges.append(_edge(REPRESENTS, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=HEAD, direction="out"):
                edges.append(_edge(HEAD, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=TAIL, direction="out"):
                edges.append(_edge(TAIL, nid, nb["node"]["id"]))
            for nb in self._source.get_neighbors(database, nid, edge_type=HAS_PROPERTY, direction="out"):
                edges.append(_edge(HAS_PROPERTY, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self._source.get_neighbors(database, nid, edge_type=EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(EXTRACTED_FROM, nid, nb["node"]["id"]))

        for node in self._source.match_nodes(database, label=LITERAL):
            nid = node["id"]
            nodes.append({"id": nid, "label": LITERAL, "properties": node.get("properties", {})})
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_VALUE_OF, direction="out"):
                edges.append(_edge(IS_VALUE_OF, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self._source.get_neighbors(database, nid, edge_type=EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(EXTRACTED_FROM, nid, nb["node"]["id"]))

        if include_provenance:
            for node in self._source.match_nodes(database, label=SOURCE):
                nodes.append({"id": node["id"], "label": SOURCE, "properties": node.get("properties", {})})
            for node in self._source.match_nodes(database, label=EXTRACTION):
                nodes.append({"id": node["id"], "label": EXTRACTION, "properties": node.get("properties", {})})

        return {"nodes": nodes, "edges": edges}

    def _fetch_compact(self, database: str, include_provenance: bool) -> list[dict]:
        results: list[dict] = []

        def _name(node: dict) -> str:
            return node.get("properties", {}).get("name", node["id"])

        for entity_node in self._source.match_nodes(database, label=ENTITY):
            nid = entity_node["id"]
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_INSTANCE_OF, direction="out"):
                entry: dict = {
                    "relation_type": IS_INSTANCE_OF,
                    "entity_name": _name(entity_node),
                    "class_name": _name(nb["node"]),
                }
                if include_provenance:
                    entry["extraction"] = self._provenance_text(database, nid)
                results.append(entry)

        for literal_node in self._source.match_nodes(database, label=LITERAL):
            nid = literal_node["id"]
            owner_name: str | None = None
            for owner_nb in self._source.get_neighbors(database, nid, edge_type=HAS_PROPERTY, direction="in"):
                owner_name = _name(owner_nb["node"])
                break
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_VALUE_OF, direction="out"):
                entry = {
                    "relation_type": IS_VALUE_OF,
                    "entity_name": owner_name,
                    "property_value": _name(literal_node),
                    "property_name": _name(nb["node"]),
                }
                if include_provenance:
                    entry["extraction"] = self._provenance_text(database, nid)
                results.append(entry)

        for fact_node in self._source.match_nodes(database, label=FACT):
            nid = fact_node["id"]
            relation_name: str | None = None
            head_name: str | None = None
            tail_name: str | None = None
            for nb in self._source.get_neighbors(database, nid, edge_type=REPRESENTS, direction="out"):
                relation_name = _name(nb["node"])
                break
            for nb in self._source.get_neighbors(database, nid, edge_type=HEAD, direction="out"):
                head_name = _name(nb["node"])
                break
            for nb in self._source.get_neighbors(database, nid, edge_type=TAIL, direction="out"):
                tail_name = _name(nb["node"])
                break
            entry = {
                "relation_type": REPRESENTS,
                "entity_name": _name(fact_node),
                "source_name": head_name,
                "target_name": tail_name,
                "relation_name": relation_name,
            }
            if include_provenance:
                entry["extraction"] = self._provenance_text(database, nid)
            results.append(entry)

        return results

    # ------------------------------------------------------------------ #
    # NL queries                                                           #
    # ------------------------------------------------------------------ #

    async def answer(self, database: str, question: str) -> list[AnswerResult]:
        """Return knowledge items relevant to *question* via keyword graph search."""
        keywords = [w.lower() for w in question.split() if len(w) > 2]
        results: list[AnswerResult] = []
        seen: set[str] = set()

        def _matches(text: str) -> bool:
            t = (text or "").lower()
            return any(kw in t for kw in keywords)

        for entity_node in self._source.match_nodes(database, label=ENTITY):
            props = entity_node.get("properties", {})
            name = props.get("name", "")
            nid = entity_node["id"]
            if _matches(name) or _matches(props.get("description", "") or ""):
                if nid not in seen:
                    seen.add(nid)
                    results.append(AnswerResult(entity=name, provenance=self._provenance_text(database, nid)))

        for fact_node in self._source.match_nodes(database, label=FACT):
            nid = fact_node["id"]
            name = fact_node.get("properties", {}).get("name", "")
            if _matches(name):
                if nid not in seen:
                    seen.add(nid)
                    rel_name = self._node_name(database, self._first_neighbor(database, nid, REPRESENTS))
                    head_name = self._node_name(database, self._first_neighbor(database, nid, HEAD))
                    tail_name = self._node_name(database, self._first_neighbor(database, nid, TAIL))
                    label = f"{head_name} → {tail_name}" if head_name and tail_name else name
                    results.append(AnswerResult(entity=label, relation=rel_name, provenance=self._provenance_text(database, nid)))

        for literal_node in self._source.match_nodes(database, label=LITERAL):
            nid = literal_node["id"]
            props = literal_node.get("properties", {})
            value = str(props.get("value", ""))
            name = props.get("name", value)
            if _matches(name) or _matches(value):
                if nid not in seen:
                    seen.add(nid)
                    prop_name = self._node_name(database, self._first_neighbor(database, nid, IS_VALUE_OF))
                    results.append(AnswerResult(value=value, relation=prop_name, provenance=self._provenance_text(database, nid)))

        return results

    async def validate(self, database: str, statement: str) -> ValidationResult:
        """Return supporting evidence from the graph for *statement* via keyword matching."""
        keywords = [w.lower() for w in statement.split() if len(w) > 2]
        supporting: list[dict] = []
        counter: list[dict] = []

        for fact_node in self._source.match_nodes(database, label=FACT):
            nid = fact_node["id"]
            fact_name = fact_node.get("properties", {}).get("name", "")
            head_name = self._node_name(database, self._first_neighbor(database, nid, HEAD))
            tail_name = self._node_name(database, self._first_neighbor(database, nid, TAIL))
            rel_name = self._node_name(database, self._first_neighbor(database, nid, REPRESENTS))
            all_text = " ".join(filter(None, [fact_name, head_name, tail_name, rel_name])).lower()
            match_count = sum(1 for kw in keywords if kw in all_text)
            if match_count > 0:
                supporting.append({
                    "fact": fact_name,
                    "head": head_name,
                    "tail": tail_name,
                    "relation": rel_name,
                    "match_score": match_count,
                })

        return ValidationResult(supporting=supporting, counter=counter)

    async def enrich(self, database: str, query: str, depth: int = 2) -> list[EnrichmentResult]:
        """Return knowledge within *depth* hops of entities matching *query*."""
        keywords = [w.lower() for w in query.split() if len(w) > 2]
        results: list[EnrichmentResult] = []
        visited: set[str] = set()

        seeds: list[tuple[str, int]] = [
            (node["id"], 0)
            for node in self._source.match_nodes(database, label=ENTITY)
            if any(kw in node.get("properties", {}).get("name", "").lower() for kw in keywords)
        ]

        queue = list(seeds)
        while queue:
            node_id, dist = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            for edge_type in (HEAD, TAIL):
                for nb in self._source.get_neighbors(database, node_id, edge_type=edge_type, direction="in"):
                    fact_node = nb["node"]
                    fact_id = fact_node["id"]
                    if fact_id in visited:
                        continue
                    visited.add(fact_id)
                    rel_name = self._node_name(database, self._first_neighbor(database, fact_id, REPRESENTS))
                    head_name = self._node_name(database, self._first_neighbor(database, fact_id, HEAD))
                    tail_name = self._node_name(database, self._first_neighbor(database, fact_id, TAIL))
                    entity_label = (
                        f"{head_name} → {tail_name}"
                        if head_name and tail_name
                        else fact_node.get("properties", {}).get("name")
                    )
                    results.append(EnrichmentResult(entity=entity_label, relation=rel_name, distance=dist + 1))
                    if dist + 1 < depth:
                        for end_type in (HEAD, TAIL):
                            for fact_nb in self._source.get_neighbors(database, fact_id, edge_type=end_type, direction="out"):
                                eid = fact_nb["node"]["id"]
                                if eid not in visited:
                                    queue.append((eid, dist + 1))

        return results
