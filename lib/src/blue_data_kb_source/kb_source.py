from __future__ import annotations

from abc import ABC
from typing import Callable, Literal

from blue_data.core.schema import DataSchema
from blue_data.source.graph.graph_source import GraphSource


class KBSource(GraphSource, ABC):
    """Abstract knowledge-base source built on top of GraphSource.

    Provides typed builders for the KB schema:
      Nodes  — CLASS, RELATION, PROPERTY, ENTITY, FACT, LITERAL, SOURCE, EXTRACTION
      Edges  — IS_SUBCLASS_OF, IS_INSTANCE_OF, DEFINE_PROPERTY, IS_VALUE_OF,
               HAS_PROPERTY, REPRESENTS, HEAD, TAIL, DOMAIN, RANGE, EXTRACTED_FROM

    Subclasses must still implement the abstract GraphSource primitives
    (add_node, get_node, delete_node, add_edge, delete_edge, get_neighbors).
    """

    # ------------------------------------------------------------------ #
    # Node labels                                                          #
    # ------------------------------------------------------------------ #

    # Schema-level nodes
    CLASS = "CLASS"
    RELATION = "RELATION"
    PROPERTY = "PROPERTY"
    # Instance-level nodes
    ENTITY = "ENTITY"
    FACT = "FACT"
    LITERAL = "LITERAL"
    # Extraction provenance nodes
    SOURCE = "SOURCE"
    EXTRACTION = "EXTRACTION"

    # ------------------------------------------------------------------ #
    # Edge types                                                           #
    # ------------------------------------------------------------------ #

    # Schema-level edges
    IS_SUBCLASS_OF = "IS_SUBCLASS_OF"
    DEFINE_PROPERTY = "DEFINE_PROPERTY"
    DOMAIN = "DOMAIN"
    RANGE = "RANGE"
    # Instance-level edges
    IS_INSTANCE_OF = "IS_INSTANCE_OF"
    IS_VALUE_OF = "IS_VALUE_OF"
    HAS_PROPERTY = "HAS_PROPERTY"
    REPRESENTS = "REPRESENTS"
    HEAD = "HEAD"
    TAIL = "TAIL"
    # Extraction provenance edges
    EXTRACTED_FROM = "EXTRACTED_FROM"

    # ------------------------------------------------------------------ #
    # Literal type tokens                                                  #
    # ------------------------------------------------------------------ #

    NUMBER = "NUMBER"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"
    STRING = "STRING"

    # ------------------------------------------------------------------ #
    # Schema node builders                                                 #
    # ------------------------------------------------------------------ #

    def declare_class(
        self,
        database: str,
        name: str,
        description: str,
        class_id: str | None = None,
    ) -> str:
        """Add a :CLASS node and return its graph node id.

        Returns the existing node id if a CLASS with the same name is already present.
        """
        existing = self.match_nodes(database, label=self.CLASS, properties={"name": name})
        if existing:
            return existing[0]["id"]
        return self.add_node(
            database,
            self.CLASS,
            {"name": name, "description": description},
            node_id=class_id,
        )

    def declare_relation_type(
        self,
        database: str,
        name: str,
        description: str,
        is_symmetric: bool = False,
        relation_id: str | None = None,
        domain: str | list[str] | None = None,
        range_: str | list[str] | None = None,
    ) -> str:
        """Add a :RELATION schema node and return its graph node id.

        Returns the existing node id if a RELATION with the same name is already present
        (domain/range are not re-wired in that case).

        *domain* and *range_* accept a single class name or a list of class names.
        Each named class must already exist in *database*; a ``ValueError`` is raised
        otherwise.
        """
        existing = self.match_nodes(database, label=self.RELATION, properties={"name": name})
        if existing:
            return existing[0]["id"]
        rid = self.add_node(
            database,
            self.RELATION,
            {"name": name, "description": description, "is_symmetric": is_symmetric},
            node_id=relation_id,
        )
        for class_name in ([domain] if isinstance(domain, str) else (domain or [])):
            classes = self.match_nodes(database, label=self.CLASS, properties={"name": class_name})
            if not classes:
                raise ValueError(f"domain class '{class_name}' not found in '{database}'")
            self.add_edge(database, rid, self.DOMAIN, classes[0]["id"])
        for class_name in ([range_] if isinstance(range_, str) else (range_ or [])):
            classes = self.match_nodes(database, label=self.CLASS, properties={"name": class_name})
            if not classes:
                raise ValueError(f"range class '{class_name}' not found in '{database}'")
            self.add_edge(database, rid, self.RANGE, classes[0]["id"])
        return rid

    def declare_class_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        class_id: str | None = None,
    ) -> str:
        """Add a :PROPERTY schema node and return its graph node id.

        When *class_id* is given, checks whether a property with the same name is
        already connected to that class via DEFINE_PROPERTY and returns it if so.
        Without *class_id*, deduplicates globally by name across all PROPERTY nodes.
        """
        if class_id is not None:
            for nb in self.get_neighbors(database, class_id, edge_type=self.DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            existing = self.match_nodes(database, label=self.PROPERTY, properties={"name": name})
            if existing:
                return existing[0]["id"]
        node_id = self.add_node(
            database,
            self.PROPERTY,
            {"name": name, "description": description},
            node_id=property_id,
        )
        if class_id is not None:
            self.add_edge(database, class_id, self.DEFINE_PROPERTY, node_id)
        return node_id

    def declare_relation_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        relation_id: str | None = None,
    ) -> str:
        """Add a :PROPERTY schema node and return its graph node id.

        When *relation_id* is given, checks whether a property with the same name is
        already connected to that relation via DEFINE_PROPERTY and returns it if so.
        Without *relation_id*, deduplicates globally by name across all PROPERTY nodes.
        """
        if relation_id is not None:
            for nb in self.get_neighbors(database, relation_id, edge_type=self.DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            existing = self.match_nodes(database, label=self.PROPERTY, properties={"name": name})
            if existing:
                return existing[0]["id"]
        node_id = self.add_node(
            database,
            self.PROPERTY,
            {"name": name, "description": description},
            node_id=property_id,
        )
        if relation_id is not None:
            self.add_edge(database, relation_id, self.DEFINE_PROPERTY, node_id)
        return node_id

    # ------------------------------------------------------------------ #
    # Instance node builders                                               #
    # ------------------------------------------------------------------ #

    def add_entity(
        self,
        database: str,
        name: str,
        description: str | None = None,
        entity_id: str | None = None,
        class_id: str | None = None,
        text: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """Add an :ENTITY node and return its graph node id.

        When *text* is provided an :EXTRACTION node is created from that text
        (optionally linked to *source_id*) and wired via EXTRACTED_FROM.
        """
        node_id = self.add_node(
            database,
            self.ENTITY,
            {"name": name, "description": description},
            node_id=entity_id,
        )
        if class_id is not None:
            self.add_edge(database, node_id, self.IS_INSTANCE_OF, class_id)
        if text is not None:
            extraction_id = self.add_extraction(database, text=text, source_id=source_id)
            self.add_edge(database, node_id, self.EXTRACTED_FROM, extraction_id)
        self._fire_change({"type": "entity_added", "database": database, "id": node_id,
                           "name": name, "class_id": class_id,
                           "class_name": self._node_name(database, class_id)})
        return node_id

    def add_fact(
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
        """Add a :FACT node and return its graph node id.

        When *text* is provided an :EXTRACTION node is created from that text
        (optionally linked to *source_id*) and wired via EXTRACTED_FROM.
        """
        if name is None:
            head_name = self.get_node(database, head_entity_id).get("properties", {}).get("name") if head_entity_id else "?"
            tail_name = self.get_node(database, tail_entity_id).get("properties", {}).get("name") if tail_entity_id else "?"
            rel_name = self.get_node(database, relation_id).get("properties", {}).get("name") if relation_id else "?"
            name = f"{head_name}_{rel_name}_{tail_name}"

        node_id = self.add_node(database, self.FACT, {"name": name, "description": description}, node_id=fact_id)
        if head_entity_id is not None:
            self.add_edge(database, node_id, self.HEAD, head_entity_id)
        if tail_entity_id is not None:
            self.add_edge(database, node_id, self.TAIL, tail_entity_id)
        if relation_id is not None:
            self.add_edge(database, node_id, self.REPRESENTS, relation_id)
        if text is not None:
            extraction_id = self.add_extraction(database, text=text, source_id=source_id)
            self.add_edge(database, node_id, self.EXTRACTED_FROM, extraction_id)
        self._fire_change({"type": "fact_added", "database": database, "id": node_id,
                           "head_entity_id": head_entity_id, "tail_entity_id": tail_entity_id,
                           "relation_id": relation_id,
                           "head_entity_name": self._node_name(database, head_entity_id),
                           "tail_entity_name": self._node_name(database, tail_entity_id),
                           "relation_name": self._node_name(database, relation_id)})
        return node_id

    def add_literal(
        self,
        database: str,
        value: str | int | float | bool,
        name: str | None = None,
        description: str | None = None,
        literal_type: Literal["NUMBER", "DATE", "BOOLEAN", "STRING"] | None = None,
        property_id: str | None = None,
        node_id: str | None = None,
    ) -> str:
        """Add a :LITERAL node and return its graph node id."""
        if name is None:
            name = str(value)
        if literal_type is None:
            if isinstance(value, (int, float)):
                literal_type = self.NUMBER
            elif isinstance(value, bool):
                literal_type = self.BOOLEAN
            else:
                literal_type = self.STRING
        node_id = self.add_node(
            database,
            self.LITERAL,
            {"name": name, "description": description, "value": value, "type": literal_type},
            node_id=node_id,
        )
        if property_id is not None:
            self.add_edge(database, node_id, self.IS_VALUE_OF, property_id)
        return node_id

    def add_entity_property_value(
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
        """Create a :LITERAL, wire IS_VALUE_OF and HAS_PROPERTY from an entity; return literal node id.

        When *text* is provided an :EXTRACTION node is created from that text
        (optionally linked to *source_id*) and wired via EXTRACTED_FROM from the literal.
        """
        literal_id = self.add_literal(database, value, name=name, description=description, literal_type=literal_type, property_id=property_id)
        if entity_id is not None:
            self.add_edge(database, entity_id, self.HAS_PROPERTY, literal_id)
        if text is not None:
            extraction_id = self.add_extraction(database, text=text, source_id=source_id)
            self.add_edge(database, literal_id, self.EXTRACTED_FROM, extraction_id)
        self._fire_change({"type": "entity_property_added", "database": database, "id": literal_id,
                           "entity_id": entity_id, "property_id": property_id, "value": value,
                           "property_name": self._node_name(database, property_id)})
        return literal_id

    def add_fact_property_value(
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
        """Create a :LITERAL, wire IS_VALUE_OF and HAS_PROPERTY from a fact; return literal node id.

        When *text* is provided an :EXTRACTION node is created from that text
        (optionally linked to *source_id*) and wired via EXTRACTED_FROM from the literal.
        """
        literal_id = self.add_literal(database, value, name=name, description=description, literal_type=literal_type, property_id=property_id)
        if fact_id is not None:
            self.add_edge(database, fact_id, self.HAS_PROPERTY, literal_id)
        if text is not None:
            extraction_id = self.add_extraction(database, text=text, source_id=source_id)
            self.add_edge(database, literal_id, self.EXTRACTED_FROM, extraction_id)
        self._fire_change({"type": "fact_property_added", "database": database, "id": literal_id,
                           "fact_id": fact_id, "property_id": property_id, "value": value,
                           "property_name": self._node_name(database, property_id),
                           **self._fact_context(database, fact_id)})
        return literal_id

    # ------------------------------------------------------------------ #
    # Change hooks                                                          #
    # ------------------------------------------------------------------ #

    def add_change_hook(self, database: str, fn: Callable[[dict], None]) -> None:
        """Register *fn* to be called after every instance-level write to *database*.

        *fn* receives a single dict with at minimum ``"type"``, ``"database"``,
        and ``"id"`` keys.  Event types and their extra keys:

        ``entity_added``
            ``name``, ``class_id``, ``class_name``
        ``fact_added``
            ``head_entity_id``, ``head_entity_name``,
            ``tail_entity_id``, ``tail_entity_name``,
            ``relation_id``, ``relation_name``
        ``entity_property_added``
            ``entity_id``, ``property_id``, ``property_name``, ``value``
        ``fact_property_added``
            ``fact_id``, ``property_id``, ``property_name``, ``value``,
            ``head_entity_name``, ``tail_entity_name``, ``relation_name``
        """
        self.__dict__.setdefault("_kb_hooks", {}).setdefault(database, []).append(fn)

    def remove_change_hook(self, database: str, fn: Callable[[dict], None]) -> None:
        """Deregister a previously added hook for *database* (no-op if not found)."""
        try:
            self.__dict__.get("_kb_hooks", {}).get(database, []).remove(fn)
        except ValueError:
            pass

    def _fire_change(self, event: dict) -> None:
        for hook in self.__dict__.get("_kb_hooks", {}).get(event["database"], []):
            hook(event)

    def _node_name(self, database: str, node_id: str | None) -> str | None:
        if node_id is None:
            return None
        node = self.get_node(database, node_id)
        if node is None:
            return None
        return node.get("properties", {}).get("name")

    def _fact_context(self, database: str, fact_id: str | None) -> dict:
        """Return head/tail/relation names for a fact node (for event payloads)."""
        if fact_id is None:
            return {"head_entity_name": None, "tail_entity_name": None, "relation_name": None}
        def _first(edge_type: str) -> str | None:
            nbs = self.get_neighbors(database, fact_id, edge_type=edge_type, direction="out")
            return nbs[0]["node"].get("properties", {}).get("name") if nbs else None
        return {
            "head_entity_name": _first(self.HEAD),
            "tail_entity_name": _first(self.TAIL),
            "relation_name": _first(self.REPRESENTS),
        }

    # ------------------------------------------------------------------ #
    # Provenance node builders                                             #
    # ------------------------------------------------------------------ #

    def add_source(
        self,
        database: str,
        source: str,
        name: str | None = None,
        description: str | None = None,
        node_id: str | None = None,
    ) -> str:
        """Add a :SOURCE node and return its graph node id."""
        if name is None:
            name = source
        return self.add_node(
            database,
            self.SOURCE,
            {"name": name, "description": description, "source": source},
            node_id=node_id,
        )

    def add_extraction(
        self,
        database: str,
        text: str,
        name: str | None = None,
        description: str | None = None,
        node_id: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """Add an :EXTRACTION node and return its graph node id."""
        if name is None:
            name = text
        n = self.add_node(
            database,
            self.EXTRACTION,
            {"name": name, "description": description, "text": text},
            node_id=node_id,
        )
        if source_id is not None:
            self.add_edge(database, n, self.EXTRACTED_FROM, source_id)
        return n

    # ------------------------------------------------------------------ #
    # Schema edge builders                                                 #
    # ------------------------------------------------------------------ #

    def add_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> str:
        """(CLASS|RELATION) -[:IS_SUBCLASS_OF]-> (CLASS|RELATION)"""
        return self.add_edge(database, child_id, self.IS_SUBCLASS_OF, parent_id)

    def _add_define_property(
        self, database: str, class_or_relation_id: str, property_id: str
    ) -> str:
        """(CLASS|RELATION) -[:DEFINE_PROPERTY]-> :PROPERTY"""
        return self.add_edge(database, class_or_relation_id, self.DEFINE_PROPERTY, property_id)

    def add_domain(self, database: str, relation_id: str, class_id: str) -> str:
        """:RELATION -[:DOMAIN]-> :CLASS  (valid HEAD entity type)"""
        return self.add_edge(database, relation_id, self.DOMAIN, class_id)

    def add_range(self, database: str, relation_id: str, class_id: str) -> str:
        """:RELATION -[:RANGE]-> :CLASS  (valid TAIL entity type)"""
        return self.add_edge(database, relation_id, self.RANGE, class_id)

    # ------------------------------------------------------------------ #
    # Instance edge builders                                               #
    # ------------------------------------------------------------------ #

    def _add_is_instance_of(self, database: str, entity_id: str, class_id: str) -> str:
        """:ENTITY -[:IS_INSTANCE_OF]-> :CLASS"""
        return self.add_edge(database, entity_id, self.IS_INSTANCE_OF, class_id)

    def _add_represents(self, database: str, fact_id: str, relation_id: str) -> str:
        """:FACT -[:REPRESENTS]-> :RELATION"""
        return self.add_edge(database, fact_id, self.REPRESENTS, relation_id)

    def _add_head(self, database: str, fact_id: str, entity_id: str) -> str:
        """:FACT -[:HEAD]-> :ENTITY  (subject of the fact)"""
        return self.add_edge(database, fact_id, self.HEAD, entity_id)

    def _add_tail(self, database: str, fact_id: str, entity_id: str) -> str:
        """:FACT -[:TAIL]-> :ENTITY  (object of the fact)"""
        return self.add_edge(database, fact_id, self.TAIL, entity_id)

    def _add_has_property(
        self, database: str, entity_or_fact_id: str, literal_id: str
    ) -> str:
        """(ENTITY|FACT) -[:HAS_PROPERTY]-> :LITERAL"""
        return self.add_edge(database, entity_or_fact_id, self.HAS_PROPERTY, literal_id)

    def _add_is_value_of(self, database: str, literal_id: str, property_id: str) -> str:
        """:LITERAL -[:IS_VALUE_OF]-> :PROPERTY"""
        return self.add_edge(database, literal_id, self.IS_VALUE_OF, property_id)

    # ------------------------------------------------------------------ #
    # Provenance edge builder                                              #
    # ------------------------------------------------------------------ #

    def add_extracted_from(
        self, database: str, element_id: str, extraction_id: str
    ) -> str:
        """(ENTITY|LITERAL|FACT) -[:EXTRACTED_FROM]-> :EXTRACTION"""
        return self.add_edge(database, element_id, self.EXTRACTED_FROM, extraction_id)

    # ------------------------------------------------------------------ #
    # Database schema                                                      #
    # ------------------------------------------------------------------ #

    def fetch_database_schema(self, database: str) -> DataSchema:
        """Return a DataSchema reflecting the declared classes and relations in *database*.

        Entities in the returned schema correspond to :CLASS nodes; their attributes come
        from :PROPERTY nodes connected via DEFINE_PROPERTY edges.  Relations correspond to
        :RELATION nodes; source/target are taken from DOMAIN/RANGE edges (falling back to
        "*" when not set), and their properties also come from DEFINE_PROPERTY edges.
        """
        schema = DataSchema()

        # --- declared classes → DataSchema entities ---
        for class_node in self.match_nodes(database, label=self.CLASS):
            nid = class_node["id"]
            props = class_node.get("properties", {})
            class_name = props.get("name", nid)
            schema.add_entity(class_name, description=props.get("description", ""))

            for nb in self.get_neighbors(database, nid, edge_type=self.DEFINE_PROPERTY, direction="out"):
                p = nb["node"].get("properties", {})
                schema.add_entity_attribute(
                    class_name,
                    p.get("name", nb["node"]["id"]),
                    "str",
                    description=p.get("description", ""),
                )

        # --- IS_SUBCLASS_OF edges between CLASS nodes ---
        for class_node in self.match_nodes(database, label=self.CLASS):
            nid = class_node["id"]
            child_name = class_node.get("properties", {}).get("name", nid)
            for nb in self.get_neighbors(database, nid, edge_type=self.IS_SUBCLASS_OF, direction="out"):
                parent_name = nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                schema.add_relation(child_name, self.IS_SUBCLASS_OF, parent_name)

        
        # --- declared relation types → DataSchema relations ---
        for rel_node in self.match_nodes(database, label=self.RELATION):
            nid = rel_node["id"]
            props = rel_node.get("properties", {})
            rel_name = props.get("name", nid)
            rel_desc = props.get("description", "")

            domains = [
                nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                for nb in self.get_neighbors(database, nid, edge_type=self.DOMAIN, direction="out")
            ] or ["*"]

            ranges = [
                nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                for nb in self.get_neighbors(database, nid, edge_type=self.RANGE, direction="out")
            ] or ["*"]

            rel_prop_nodes = [
                nb["node"]
                for nb in self.get_neighbors(database, nid, edge_type=self.DEFINE_PROPERTY, direction="out")
            ]

            for domain in domains:
                for range_ in ranges:
                    rel_key = schema.add_relation(domain, rel_name, range_, description=rel_desc)
                    for prop_node in rel_prop_nodes:
                        p = prop_node.get("properties", {})
                        schema.add_relation_attribute(
                            rel_key,
                            p.get("name", prop_node["id"]),
                            "str",
                            description=p.get("description", ""),
                        )

        # --- IS_SUBCLASS_OF edges between RELATION nodes ---
        for rel_node in self.match_nodes(database, label=self.RELATION):
            nid = rel_node["id"]
            child_name = rel_node.get("properties", {}).get("name", nid)
            for nb in self.get_neighbors(database, nid, edge_type=self.IS_SUBCLASS_OF, direction="out"):
                parent_name = nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                schema.add_relation(child_name, self.IS_SUBCLASS_OF, parent_name)

        return schema

    # ------------------------------------------------------------------ #
    # Database data                                                        #
    # ------------------------------------------------------------------ #

    def fetch_database_data(
        self,
        database: str,
        include_provenance: bool = False,
        compact: bool = False,
    ) -> dict | list:
        """Return all instance-level nodes and edges from *database*.

        **Default** (``compact=False``):
        Returns ``{"nodes": [...], "edges": [...]}`` where every item carries a
        ``label`` (nodes) or ``type`` (edges) field so callers can filter by kind.

        Node shape:  ``{"id": ..., "label": ..., "properties": {...}}``
        Edge shape:  ``{"type": ..., "source_id": ..., "target_id": ...}``

        Node labels included:  ENTITY, FACT, LITERAL
        Edge types included:   IS_INSTANCE_OF, REPRESENTS, HEAD, TAIL,
                               HAS_PROPERTY, IS_VALUE_OF

        When *include_provenance* is ``True``, also adds SOURCE and EXTRACTION
        nodes and EXTRACTED_FROM edges.

        **Compact** (``compact=True``):
        Returns a flat list of relation dicts, one per semantic edge:

        ``IS_INSTANCE_OF``::

            {"relation": "IS_INSTANCE_OF", "relation_description": ...,
             "entity_name": ..., "class_name": ...,
             "extraction": ...}   # extraction only when include_provenance=True

        ``IS_VALUE_OF``::

            {"relation": "IS_VALUE_OF", "relation_description": ...,
             "entity_name": ..., "property_name": ...,
             "extraction": ...}

        ``REPRESENTS``::

            {"relation": "REPRESENTS", "relation_description": ...,
             "head_name": ..., "tail_name": ..., "relation_name": ...,
             "extraction": ...}
        """
        if compact:
            return self._fetch_database_data_compact(database, include_provenance)

        nodes: list[dict] = []
        edges: list[dict] = []

        def _edge(type_: str, source_id: str, target_id: str) -> dict:
            return {"type": type_, "source_id": source_id, "target_id": target_id}

        # --- ENTITY nodes ---
        for node in self.match_nodes(database, label=self.ENTITY):
            nid = node["id"]
            nodes.append({"id": nid, "label": self.ENTITY, "properties": node.get("properties", {})})
            for nb in self.get_neighbors(database, nid, edge_type=self.IS_INSTANCE_OF, direction="out"):
                edges.append(_edge(self.IS_INSTANCE_OF, nid, nb["node"]["id"]))
            for nb in self.get_neighbors(database, nid, edge_type=self.HAS_PROPERTY, direction="out"):
                edges.append(_edge(self.HAS_PROPERTY, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self.get_neighbors(database, nid, edge_type=self.EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(self.EXTRACTED_FROM, nid, nb["node"]["id"]))

        # --- FACT nodes ---
        for node in self.match_nodes(database, label=self.FACT):
            nid = node["id"]
            nodes.append({"id": nid, "label": self.FACT, "properties": node.get("properties", {})})
            for nb in self.get_neighbors(database, nid, edge_type=self.REPRESENTS, direction="out"):
                edges.append(_edge(self.REPRESENTS, nid, nb["node"]["id"]))
            for nb in self.get_neighbors(database, nid, edge_type=self.HEAD, direction="out"):
                edges.append(_edge(self.HEAD, nid, nb["node"]["id"]))
            for nb in self.get_neighbors(database, nid, edge_type=self.TAIL, direction="out"):
                edges.append(_edge(self.TAIL, nid, nb["node"]["id"]))
            for nb in self.get_neighbors(database, nid, edge_type=self.HAS_PROPERTY, direction="out"):
                edges.append(_edge(self.HAS_PROPERTY, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self.get_neighbors(database, nid, edge_type=self.EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(self.EXTRACTED_FROM, nid, nb["node"]["id"]))

        # --- LITERAL nodes ---
        for node in self.match_nodes(database, label=self.LITERAL):
            nid = node["id"]
            nodes.append({"id": nid, "label": self.LITERAL, "properties": node.get("properties", {})})
            for nb in self.get_neighbors(database, nid, edge_type=self.IS_VALUE_OF, direction="out"):
                edges.append(_edge(self.IS_VALUE_OF, nid, nb["node"]["id"]))
            if include_provenance:
                for nb in self.get_neighbors(database, nid, edge_type=self.EXTRACTED_FROM, direction="out"):
                    edges.append(_edge(self.EXTRACTED_FROM, nid, nb["node"]["id"]))

        # --- provenance nodes ---
        if include_provenance:
            for node in self.match_nodes(database, label=self.SOURCE):
                nodes.append({"id": node["id"], "label": self.SOURCE, "properties": node.get("properties", {})})
            for node in self.match_nodes(database, label=self.EXTRACTION):
                nodes.append({"id": node["id"], "label": self.EXTRACTION, "properties": node.get("properties", {})})

        return {"nodes": nodes, "edges": edges}

    def _fetch_database_data_compact(
        self,
        database: str,
        include_provenance: bool,
    ) -> list[dict]:
        """Compact view: one dict per IS_INSTANCE_OF, IS_VALUE_OF, or REPRESENTS edge."""

        results: list[dict] = []

        def _name(node: dict) -> str:
            return node.get("properties", {}).get("name", node["id"])

        def _extraction(nid: str) -> str | None:
            for nb in self.get_neighbors(database, nid, edge_type=self.EXTRACTED_FROM, direction="out"):
                return nb["node"].get("properties", {}).get("text")
            return None

        # --- IS_INSTANCE_OF: ENTITY → CLASS ---
        for entity_node in self.match_nodes(database, label=self.ENTITY):
            nid = entity_node["id"]
            entity_name = _name(entity_node)
            for nb in self.get_neighbors(database, nid, edge_type=self.IS_INSTANCE_OF, direction="out"):
                entry: dict = {
                    "relation_type": self.IS_INSTANCE_OF,
                    "entity_name": entity_name,
                    "class_name": _name(nb["node"]),
                }
                if include_provenance:
                    entry["extraction"] = _extraction(nid)
                results.append(entry)

        # --- IS_VALUE_OF: LITERAL → PROPERTY ---
        for literal_node in self.match_nodes(database, label=self.LITERAL):
            nid = literal_node["id"]
            # find the owning ENTITY or FACT via incoming HAS_PROPERTY edge
            owner_name: str | None = None
            for owner_nb in self.get_neighbors(database, nid, edge_type=self.HAS_PROPERTY, direction="in"):
                owner_name = _name(owner_nb["node"])
                break
            for nb in self.get_neighbors(database, nid, edge_type=self.IS_VALUE_OF, direction="out"):
                entry = {
                    "relation_type": self.IS_VALUE_OF,
                    "entity_name": owner_name,
                    "property_value": _name(literal_node),
                    "property_name": _name(nb["node"]),
                }
                if include_provenance:
                    entry["extraction"] = _extraction(nid)
                results.append(entry)

        # --- REPRESENTS: FACT → RELATION (with HEAD / TAIL lookups) ---
        for fact_node in self.match_nodes(database, label=self.FACT):
            nid = fact_node["id"]
            relation_name: str | None = None
            head_name: str | None = None
            tail_name: str | None = None
            for nb in self.get_neighbors(database, nid, edge_type=self.REPRESENTS, direction="out"):
                relation_name = _name(nb["node"])
                break
            for nb in self.get_neighbors(database, nid, edge_type=self.HEAD, direction="out"):
                head_name = _name(nb["node"])
                break
            for nb in self.get_neighbors(database, nid, edge_type=self.TAIL, direction="out"):
                tail_name = _name(nb["node"])
                break
            entry = {
                "relation_type": self.REPRESENTS,
                "entity_name": _name(fact_node),
                "source_name": head_name,
                "target_name": tail_name,
                "relation_name": relation_name,
            }
            if include_provenance:
                entry["extraction"] = _extraction(nid)
            results.append(entry)

        return results

    def import_database_schema(self, database: str, schema_json: dict) -> KBSource:
        """Populate *database* from a DataSchema JSON dict and return self.

        Steps:
        - Each DataSchema entity → a :CLASS node; its ``contents.attributes``
          become :PROPERTY nodes wired via DEFINE_PROPERTY.
        - DataSchema relations are grouped by name so that one :RELATION node is
          created per unique relation type.  DOMAIN and RANGE edges are wired from
          the ``source`` / ``target`` class names found in the schema; relation
          ``contents.attributes`` become :PROPERTY nodes via DEFINE_PROPERTY.

        Usage::

            kb = MyKBSource("my-kb")
            kb.create_database("main")
            kb.import_database_schema("main", schema.to_json())
        """
        schema = DataSchema.from_json(schema_json)

        # --- declare classes ---
        class_ids: dict[str, str] = {}  # class_name → graph node id
        for entity_obj in schema.entities.values():
            class_name = entity_obj["name"]
            class_id = self.declare_class(
                database,
                name=class_name,
                description=entity_obj.get("description", ""),
            )
            class_ids[class_name] = class_id

            for attr_name, attr_obj in entity_obj.get("contents", {}).get("attributes", {}).items():
                self.declare_class_property(
                    database,
                    name=attr_name,
                    description=attr_obj.get("description", "") if isinstance(attr_obj, dict) else "",
                    class_id=class_id,
                )

        # --- declare relation types (group by name to deduplicate) ---
        # rel_name → {description, domains, ranges, attributes}
        rel_data: dict[str, dict] = {}
        for rel_obj in schema.relations.values():
            rel_name = rel_obj["name"]
            if rel_name not in rel_data:
                rel_data[rel_name] = {
                    "description": rel_obj.get("description", ""),
                    "domains": [],
                    "ranges": [],
                    "attributes": rel_obj.get("contents", {}).get("attributes", {}),
                }
            source = rel_obj.get("source", "")
            target = rel_obj.get("target", "")
            if source and source != "*" and source not in rel_data[rel_name]["domains"]:
                rel_data[rel_name]["domains"].append(source)
            if target and target != "*" and target not in rel_data[rel_name]["ranges"]:
                rel_data[rel_name]["ranges"].append(target)

        for rel_name, rd in rel_data.items():
            relation_id = self.declare_relation_type(
                database,
                name=rel_name,
                description=rd["description"],
            )

            for domain_name in rd["domains"]:
                if domain_name in class_ids:
                    self.add_domain(database, relation_id, class_ids[domain_name])

            for range_name in rd["ranges"]:
                if range_name in class_ids:
                    self.add_range(database, relation_id, class_ids[range_name])

            for attr_name, attr_obj in rd["attributes"].items():
                self.declare_relation_property(
                    database,
                    name=attr_name,
                    description=attr_obj.get("description", "") if isinstance(attr_obj, dict) else "",
                    relation_id=relation_id,
                )

        return self

    # ------------------------------------------------------------------ #
    # Meta-schema                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def fetch_meta_schema() -> DataSchema:
        """Return a DataSchema describing the KB graph schema itself.

        Entities correspond to KB node types; relations correspond to KB edge types.
        Descriptions and examples are taken verbatim from the KB schema specification.
        """
        schema = DataSchema()

        # ---- node types (entities) ----

        schema.add_entity("CLASS",
            description="Defines a category or type. Example: PERSON, COMPANY")
        schema.add_entity_attribute("CLASS", "name", "str",
            description="name of the class")
        schema.add_entity_attribute("CLASS", "description", "str",
            description="description of the class")

        schema.add_entity("RELATION",
            description="Defines the nature of a relationship between two entity classes. "
                        "Example: WORKED_AT, LIVES_IN, PURCHASED")
        schema.add_entity_attribute("RELATION", "name", "str",
            description="name of the relation")
        schema.add_entity_attribute("RELATION", "description", "str",
            description="description of the relation")
        schema.add_entity_attribute("RELATION", "is_symmetric", "bool",
            description="boolean indicating symmetry")

        schema.add_entity("PROPERTY",
            description="Feature of an entity, class, relation, or fact. Example: AGE, START_DATE")
        schema.add_entity_attribute("PROPERTY", "name", "str",
            description="name of the property")
        schema.add_entity_attribute("PROPERTY", "description", "str",
            description="description of the property")

        schema.add_entity("ENTITY",
            description="A specific instance of a class. Example: Alice, IBM")
        schema.add_entity_attribute("ENTITY", "name", "str",
            description="name of the entity")
        schema.add_entity_attribute("ENTITY", "description", "str",
            description="description of the entity")

        schema.add_entity("FACT",
            description="Represents a specific factual relation between entities. "
                        "Example: Alice_WORKED_AT_IBM")
        schema.add_entity_attribute("FACT", "name", "str",
            description="name of the fact")
        schema.add_entity_attribute("FACT", "description", "str",
            description="description of the fact")

        schema.add_entity("LITERAL",
            description="Specifies a property value. Example: 18, 1998")
        schema.add_entity_attribute("LITERAL", "name", "str",
            description="name of the literal")
        schema.add_entity_attribute("LITERAL", "description", "str",
            description="description of the literal")
        schema.add_entity_attribute("LITERAL", "value", "str | int | float | bool",
            description="value of the property")
        schema.add_entity_attribute("LITERAL", "type", "NUMBER | DATE | BOOLEAN | STRING",
            description="type of the value")

        schema.add_entity("SOURCE",
            description="A source of data.")
        schema.add_entity_attribute("SOURCE", "id", "str",
            description="id of the source")
        schema.add_entity_attribute("SOURCE", "name", "str",
            description="name of the source")
        schema.add_entity_attribute("SOURCE", "description", "str",
            description="description of the source")

        schema.add_entity("EXTRACTION",
            description="Extraction from a source.")
        schema.add_entity_attribute("EXTRACTION", "name", "str",
            description="name of the extraction")
        schema.add_entity_attribute("EXTRACTION", "description", "str",
            description="description of the extraction")
        schema.add_entity_attribute("EXTRACTION", "text", "str",
            description="text segment of extraction from source")

        # ---- edge types (relations) ----

        _subclass_desc = (
            "Defines hierarchical inheritance between two schema elements. "
            "The child inherits properties and semantics from the parent. "
            "Example: EMPLOYEE IS_SUBCLASS_OF PERSON"
        )
        schema.add_relation("CLASS",    "IS_SUBCLASS_OF", "CLASS",    description=_subclass_desc)
        schema.add_relation("RELATION", "IS_SUBCLASS_OF", "RELATION", description=_subclass_desc)

        schema.add_relation("ENTITY", "IS_INSTANCE_OF", "CLASS",
            description="Connects a specific entity to a class, asserting that the entity is a "
                        "concrete realization of that class. Example: IBM IS_INSTANCE_OF COMPANY")

        _define_prop_desc = (
            "Declares that a class or relation has a given property in its schema definition. "
            "Example: PERSON DEFINE_PROPERTY AGE"
        )
        schema.add_relation("CLASS",    "DEFINE_PROPERTY", "PROPERTY", description=_define_prop_desc)
        schema.add_relation("RELATION", "DEFINE_PROPERTY", "PROPERTY", description=_define_prop_desc)

        schema.add_relation("LITERAL", "IS_VALUE_OF", "PROPERTY",
            description="Links a literal value to the property it instantiates. "
                        "Example: 18 IS_VALUE_OF AGE")

        _has_prop_desc = (
            "Assigns a literal value or attribute to an entity or fact instance. "
            "Example: Alice HAS_PROPERTY 28"
        )
        schema.add_relation("ENTITY", "HAS_PROPERTY", "LITERAL", description=_has_prop_desc)
        schema.add_relation("FACT",   "HAS_PROPERTY", "LITERAL", description=_has_prop_desc)

        schema.add_relation("FACT", "REPRESENTS", "RELATION",
            description="Connects a fact instance to the relation type it instantiates, "
                        "grounding the fact in schema semantics. "
                        "Example: Alice_WORKED_AT_IBM REPRESENTS WORKED_AT")

        schema.add_relation("FACT", "HEAD", "ENTITY",
            description="Specifies the subject (from) entity of a fact. Example: FACT → Alice")

        schema.add_relation("FACT", "TAIL", "ENTITY",
            description="Specifies the object (to) entity of a fact. Example: FACT → IBM")

        schema.add_relation("RELATION", "DOMAIN", "CLASS",
            description="Declares the valid source class of a relation (what type of entity can "
                        "appear in HEAD position). Example: WORKED_AT DOMAIN EMPLOYEE")

        schema.add_relation("RELATION", "RANGE", "CLASS",
            description="Declares the valid target class of a relation (what type of entity can "
                        "appear in TAIL position). Example: WORKED_AT RANGE ORGANIZATION")

        _extracted_desc = (
            "Links any schema element (entity, fact, or literal) to its originating extraction "
            "record or source evidence. "
            "Example: Alice WORKED_AT IBM EXTRACTED_FROM Resume_Text_1"
        )
        schema.add_relation("ENTITY",  "EXTRACTED_FROM", "EXTRACTION", description=_extracted_desc)
        schema.add_relation("LITERAL", "EXTRACTED_FROM", "EXTRACTION", description=_extracted_desc)
        schema.add_relation("FACT",    "EXTRACTED_FROM", "EXTRACTION", description=_extracted_desc)

        return schema

    # ------------------------------------------------------------------ #
    # LLM-based text import                                                #
    # ------------------------------------------------------------------ #

    def import_text(
        self,
        database: str,
        text: str,
        queries: list[str] | None = None,
        model: str = "gpt-4o",
        source_id: str | None = None,
    ) -> None:
        """Extract KB facts from *text* using an OpenAI LLM and store them in *database*.

        The LLM receives the current schema and *queries* as context, then calls
        KB builder functions as tools to populate entities, facts, and properties.
        When the schema lacks a suitable class, relation, or property the LLM
        declares it first (schema expansion is automatic).

        For each entity, fact, or property value created the LLM may supply the
        verbatim text span it was extracted from. When provided, an :EXTRACTION node
        is created and linked to *source_id* (if given) and to the created node via
        EXTRACTED_FROM.

        Args:
            database:  Target database name.
            text:      Source text to process.
            queries:   Guiding extraction questions (e.g. ``["Who works where?"]``).
            model:     OpenAI model id (default ``"gpt-4o"``).
            source_id: Optional id of an existing :SOURCE provenance node.

        Requires the ``openai`` package::

            pip install openai
            # or: pip install "blue-data-kb-source[openai]"
        """
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "import_text requires the 'openai' package: pip install openai"
            ) from exc
        import json

        # --- schema context for the system prompt ---
        schema_str = self.fetch_database_schema(database).to_str(simplified=True)
        queries_str = (
            "\n".join(f"- {q}" for q in queries)
            if queries
            else "Extract all notable entities, relations, and property values."
        )

        system_prompt = f"""You are a knowledge base (KB) extraction agent.

The KB stores:
  CLASS     — category of entities (e.g. PERSON, COMPANY)
  RELATION  — type of relationship (e.g. WORKED_AT, LIVES_IN)
  PROPERTY  — attribute of a class or relation (e.g. AGE, START_DATE)
  ENTITY    — a specific instance of a class (e.g. Alice, IBM)
  FACT      — a specific relation between two entities
  LITERAL   — a concrete property value (e.g. "28", "1998-06-01")

Workflow:
1. For each entity in the text: find or declare its CLASS, then call add_entity.
2. For each relationship: find or declare the RELATION type, then call add_fact.
3. For each attribute value: find or declare the PROPERTY on its class/relation,
   then call add_entity_property_value or add_fact_property_value.
4. Prefer existing schema elements; only declare new ones when nothing fits.
5. Use names exactly as they appear in the text for entities.
   Use UPPER_SNAKE_CASE for CLASS, RELATION, and PROPERTY names.
6. When calling add_entity, add_fact, add_entity_property_value, or
   add_fact_property_value, always pass the verbatim sentence or phrase from
   the source text that the item was extracted from as the `text` argument.

Current schema:
{schema_str}

Extraction queries:
{queries_str}

Work through the text systematically and stop calling tools when you are done."""

        # --- OpenAI tool definitions ---
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_schema",
                    "description": "Return the current KB schema (classes, relations, properties).",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_class",
                    "description": "Look up a CLASS node by name. Returns {id, name, description} or {id: null}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Class name to look up"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_relation_type",
                    "description": "Look up a RELATION node by name. Returns {id, name, description} or {id: null}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Relation name to look up"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_entity",
                    "description": "Look up an ENTITY node by name. Returns {id, name, description} or {id: null}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Entity name to look up"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "declare_class",
                    "description": "Declare a CLASS in the schema (idempotent). Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Class name e.g. PERSON"},
                            "description": {"type": "string", "description": "What the class represents"},
                        },
                        "required": ["name", "description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "declare_class_property",
                    "description": "Declare a PROPERTY on a CLASS (idempotent). Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "class_id": {"type": "string", "description": "ID of the owning class"},
                            "name": {"type": "string", "description": "Property name e.g. AGE"},
                            "description": {"type": "string", "description": "What the property represents"},
                        },
                        "required": ["class_id", "name", "description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "declare_relation_type",
                    "description": "Declare a RELATION type in the schema (idempotent). Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Relation name e.g. WORKED_AT"},
                            "description": {"type": "string", "description": "What the relation represents"},
                            "is_symmetric": {"type": "boolean", "description": "True when the relation is symmetric"},
                        },
                        "required": ["name", "description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "declare_relation_property",
                    "description": "Declare a PROPERTY on a RELATION (idempotent). Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "relation_id": {"type": "string", "description": "ID of the owning relation"},
                            "name": {"type": "string", "description": "Property name e.g. START_DATE"},
                            "description": {"type": "string", "description": "What the property represents"},
                        },
                        "required": ["relation_id", "name", "description"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_entity",
                    "description": "Add an ENTITY instance. Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Entity name (as it appears in the text)"},
                            "description": {"type": "string", "description": "Optional description"},
                            "class_id": {"type": "string", "description": "ID of the class this entity belongs to"},
                            "text": {"type": "string", "description": "Verbatim sentence or phrase this entity was extracted from"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_fact",
                    "description": "Add a FACT (relation instance between two entities). Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "head_entity_id": {"type": "string", "description": "ID of the subject entity"},
                            "tail_entity_id": {"type": "string", "description": "ID of the object entity"},
                            "relation_id": {"type": "string", "description": "ID of the relation type"},
                            "name": {"type": "string", "description": "Optional fact name"},
                            "text": {"type": "string", "description": "Verbatim sentence or phrase this fact was extracted from"},
                        },
                        "required": ["head_entity_id", "tail_entity_id", "relation_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_entity_property_value",
                    "description": "Attach a LITERAL property value to an ENTITY. Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_id": {"type": "string", "description": "ID of the entity"},
                            "property_id": {"type": "string", "description": "ID of the property"},
                            "value": {"type": "string", "description": "The literal value"},
                            "literal_type": {
                                "type": "string",
                                "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                                "description": "Value type (inferred from value when omitted)",
                            },
                            "text": {"type": "string", "description": "Verbatim sentence or phrase this value was extracted from"},
                        },
                        "required": ["entity_id", "property_id", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_fact_property_value",
                    "description": "Attach a LITERAL property value to a FACT. Returns {id}.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fact_id": {"type": "string", "description": "ID of the fact"},
                            "property_id": {"type": "string", "description": "ID of the property"},
                            "value": {"type": "string", "description": "The literal value"},
                            "literal_type": {
                                "type": "string",
                                "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                                "description": "Value type (inferred from value when omitted)",
                            },
                            "text": {"type": "string", "description": "Verbatim sentence or phrase this value was extracted from"},
                        },
                        "required": ["fact_id", "property_id", "value"],
                    },
                },
            },
        ]

        # --- agent loop ---
        client = openai.OpenAI()
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        while True:
            response = client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,
                tool_choice="auto",
            )
            msg = response.choices[0].message

            msg_dict: dict = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(msg_dict)

            if not msg.tool_calls:
                break

            for tc in msg.tool_calls:
                result = self._dispatch_import_tool(
                    database,
                    tc.function.name,
                    json.loads(tc.function.arguments),
                    source_id,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

    def _dispatch_import_tool(
        self,
        database: str,
        fn: str,
        args: dict,
        source_id: str | None,
    ) -> dict:
        """Route a single tool call from import_text to the appropriate KB method."""
        if fn == "get_schema":
            return {"schema": self.fetch_database_schema(database).to_str(simplified=True)}

        if fn == "find_class":
            nodes = self.match_nodes(database, label=self.CLASS, properties={"name": args["name"]})
            if nodes:
                n = nodes[0]
                p = n.get("properties", {})
                return {"id": n["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_relation_type":
            nodes = self.match_nodes(database, label=self.RELATION, properties={"name": args["name"]})
            if nodes:
                n = nodes[0]
                p = n.get("properties", {})
                return {"id": n["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_entity":
            nodes = self.match_nodes(database, label=self.ENTITY, properties={"name": args["name"]})
            if nodes:
                n = nodes[0]
                p = n.get("properties", {})
                return {"id": n["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "declare_class":
            cid = self.declare_class(database, name=args["name"], description=args["description"])
            return {"id": cid}

        if fn == "declare_class_property":
            pid = self.declare_class_property(
                database,
                name=args["name"],
                description=args["description"],
                class_id=args["class_id"],
            )
            return {"id": pid}

        if fn == "declare_relation_type":
            rid = self.declare_relation_type(
                database,
                name=args["name"],
                description=args["description"],
                is_symmetric=args.get("is_symmetric", False),
            )
            return {"id": rid}

        if fn == "declare_relation_property":
            pid = self.declare_relation_property(
                database,
                name=args["name"],
                description=args["description"],
                relation_id=args["relation_id"],
            )
            return {"id": pid}

        if fn == "add_entity":
            eid = self.add_entity(
                database,
                name=args["name"],
                description=args.get("description"),
                class_id=args.get("class_id"),
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": eid}

        if fn == "add_fact":
            fid = self.add_fact(
                database,
                name=args.get("name"),
                head_entity_id=args["head_entity_id"],
                tail_entity_id=args["tail_entity_id"],
                relation_id=args["relation_id"],
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": fid}

        if fn == "add_entity_property_value":
            lit_id = self.add_entity_property_value(
                database,
                value=args["value"],
                literal_type=args.get("literal_type"),
                entity_id=args["entity_id"],
                property_id=args["property_id"],
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": lit_id}

        if fn == "add_fact_property_value":
            lit_id = self.add_fact_property_value(
                database,
                value=args["value"],
                literal_type=args.get("literal_type"),
                fact_id=args["fact_id"],
                property_id=args["property_id"],
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": lit_id}

        return {"error": f"unknown tool: {fn}"}
