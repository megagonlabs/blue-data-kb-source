from __future__ import annotations

from abc import ABC
from typing import Literal

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
        """Add a :CLASS node and return its graph node id."""
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
    ) -> str:
        """Add a :RELATION schema node and return its graph node id."""
        return self.add_node(
            database,
            self.RELATION,
            {"name": name, "description": description, "is_symmetric": is_symmetric},
            node_id=relation_id,
        )

    def declare_class_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        class_id: str | None = None,
    ) -> str:
        """Add a :PROPERTY schema node and return its graph node id."""
        node_id = self.add_node(
            database,
            self.PROPERTY,
            {"name": name, "description": description},
            node_id=property_id,
        )
        if class_id is not None:
            self.add_edge(database, node_id, self.DEFINE_PROPERTY, class_id)
        return node_id

    def declare_relation_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        relation_id: str | None = None,
    ) -> str:
        """Add a :PROPERTY schema node and return its graph node id."""
        node_id = self.add_node(
            database,
            self.PROPERTY,
            {"name": name, "description": description},
            node_id=property_id,
        )
        if relation_id is not None:
            self.add_edge(database, node_id, self.DEFINE_PROPERTY, relation_id)
        return node_id

    # ------------------------------------------------------------------ #
    # Instance node builders                                               #
    # ------------------------------------------------------------------ #

    def add_entity(
        self,
        database: str,
        name: str,
        description: str | None = None,
        entity_id: str| None = None,
        class_id: str | None = None,
    ) -> str:
        """Add an :ENTITY node and return its graph node id."""
        node_id = self.add_node(
            database,
            self.ENTITY,
            {"name": name, "description": description},
            node_id=entity_id,
        )
        if class_id is not None:
            self.add_edge(database, node_id, self.IS_INSTANCE_OF, class_id)
        return node_id

    def add_fact(
        self,
        database: str,
        name: str,
        description: str | None = None,
        fact_id: str | None = None,
        head_entity_id: str | None = None,
        tail_entity_id: str | None = None,
        relation_id: str | None = None,
    ) -> str:
        """Add a :FACT node (property-less) and return its graph node id."""
        node_id = self.add_node(database, self.FACT, {"name": name, "description": description}, node_id=fact_id)
        if head_entity_id is not None:
            self.add_edge(database, node_id, self.HEAD, head_entity_id)
        if tail_entity_id is not None:
            self.add_edge(database, node_id, self.TAIL, tail_entity_id)
        if relation_id is not None:
            self.add_edge(database, node_id, self.REPRESENTS, relation_id)
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
    ) -> str:
        """Create a :LITERAL, wire IS_VALUE_OF and HAS_PROPERTY from an entity; return literal node id."""
        literal_id = self.add_literal(database, value, name=name, description=description, literal_type=literal_type, property_id=property_id)
        if entity_id is not None:
            self.add_edge(database, entity_id, self.HAS_PROPERTY, literal_id)
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
    ) -> str:
        """Create a :LITERAL, wire IS_VALUE_OF and HAS_PROPERTY from a fact; return literal node id."""
        literal_id = self.add_literal(database, value, name=name, description=description, literal_type=literal_type, property_id=property_id)
        if fact_id is not None:
            self.add_edge(database, fact_id, self.HAS_PROPERTY, literal_id)
        return literal_id

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

    def add_define_property(
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

    def add_is_instance_of(self, database: str, entity_id: str, class_id: str) -> str:
        """:ENTITY -[:IS_INSTANCE_OF]-> :CLASS"""
        return self.add_edge(database, entity_id, self.IS_INSTANCE_OF, class_id)

    def add_represents(self, database: str, fact_id: str, relation_id: str) -> str:
        """:FACT -[:REPRESENTS]-> :RELATION"""
        return self.add_edge(database, fact_id, self.REPRESENTS, relation_id)

    def add_head(self, database: str, fact_id: str, entity_id: str) -> str:
        """:FACT -[:HEAD]-> :ENTITY  (subject of the fact)"""
        return self.add_edge(database, fact_id, self.HEAD, entity_id)

    def add_tail(self, database: str, fact_id: str, entity_id: str) -> str:
        """:FACT -[:TAIL]-> :ENTITY  (object of the fact)"""
        return self.add_edge(database, fact_id, self.TAIL, entity_id)

    def add_has_property(
        self, database: str, entity_or_fact_id: str, literal_id: str
    ) -> str:
        """(ENTITY|FACT) -[:HAS_PROPERTY]-> :LITERAL"""
        return self.add_edge(database, entity_or_fact_id, self.HAS_PROPERTY, literal_id)

    def add_is_value_of(self, database: str, literal_id: str, property_id: str) -> str:
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
                        schema.add_relation_property(
                            rel_key,
                            p.get("name", prop_node["id"]),
                            "str",
                            description=p.get("description", ""),
                        )

        return schema

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
        schema.add_relation("CLASS",    "IS_SUBCLASS_OF", "RELATION", description=_subclass_desc)
        schema.add_relation("RELATION", "IS_SUBCLASS_OF", "CLASS",    description=_subclass_desc)
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
