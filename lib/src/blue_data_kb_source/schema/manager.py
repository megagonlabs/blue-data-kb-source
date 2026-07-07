from __future__ import annotations

from blue_data.core.schema import DataSchema
from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    DOMAIN,
    IS_SUBCLASS_OF,
    PROPERTY,
    RANGE,
    RELATION,
)
from blue_data_kb_source.types import SchemaError, SchemaSearchResult


class SchemaManager:
    """Manages schema-level nodes (CLASS, RELATION, PROPERTY) and schema-level edges."""

    def __init__(self, source: GraphSource) -> None:
        self._source = source

    # ------------------------------------------------------------------ #
    # Declarations                                                         #
    # ------------------------------------------------------------------ #

    async def declare_class(
        self,
        database: str,
        name: str,
        description: str,
        class_id: str | None = None,
    ) -> str:
        existing = self._source.match_nodes(database, label=CLASS, properties={"name": name})
        if existing:
            return existing[0]["id"]
        return self._source.add_node(
            database,
            CLASS,
            {"name": name, "description": description},
            node_id=class_id,
        )

    async def declare_relation_type(
        self,
        database: str,
        name: str,
        description: str,
        is_symmetric: bool = False,
        relation_id: str | None = None,
        domain: str | list[str] | None = None,
        range_: str | list[str] | None = None,
    ) -> str:
        existing = self._source.match_nodes(database, label=RELATION, properties={"name": name})
        if existing:
            return existing[0]["id"]
        rid = self._source.add_node(
            database,
            RELATION,
            {"name": name, "description": description, "is_symmetric": is_symmetric},
            node_id=relation_id,
        )
        for class_name in ([domain] if isinstance(domain, str) else (domain or [])):
            classes = self._source.match_nodes(database, label=CLASS, properties={"name": class_name})
            if not classes:
                raise SchemaError(f"domain class '{class_name}' not found in '{database}'")
            self._source.add_edge(database, rid, DOMAIN, classes[0]["id"])
        for class_name in ([range_] if isinstance(range_, str) else (range_ or [])):
            classes = self._source.match_nodes(database, label=CLASS, properties={"name": class_name})
            if not classes:
                raise SchemaError(f"range class '{class_name}' not found in '{database}'")
            self._source.add_edge(database, rid, RANGE, classes[0]["id"])
        return rid

    async def declare_class_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        class_id: str | None = None,
    ) -> str:
        if class_id is not None:
            for nb in self._source.get_neighbors(database, class_id, edge_type=DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            existing = self._source.match_nodes(database, label=PROPERTY, properties={"name": name})
            if existing:
                return existing[0]["id"]
        node_id = self._source.add_node(
            database,
            PROPERTY,
            {"name": name, "description": description},
            node_id=property_id,
        )
        if class_id is not None:
            self._source.add_edge(database, class_id, DEFINE_PROPERTY, node_id)
        return node_id

    async def declare_relation_property(
        self,
        database: str,
        name: str,
        description: str,
        property_id: str | None = None,
        relation_id: str | None = None,
    ) -> str:
        if relation_id is not None:
            for nb in self._source.get_neighbors(database, relation_id, edge_type=DEFINE_PROPERTY, direction="out"):
                if nb["node"].get("properties", {}).get("name") == name:
                    return nb["node"]["id"]
        else:
            existing = self._source.match_nodes(database, label=PROPERTY, properties={"name": name})
            if existing:
                return existing[0]["id"]
        node_id = self._source.add_node(
            database,
            PROPERTY,
            {"name": name, "description": description},
            node_id=property_id,
        )
        if relation_id is not None:
            self._source.add_edge(database, relation_id, DEFINE_PROPERTY, node_id)
        return node_id

    async def add_is_subclass_of(self, database: str, child_id: str, parent_id: str) -> None:
        self._source.add_edge(database, child_id, IS_SUBCLASS_OF, parent_id)

    # ------------------------------------------------------------------ #
    # Schema export / import                                               #
    # ------------------------------------------------------------------ #

    async def fetch_database_schema(self, database: str) -> DataSchema:
        schema = DataSchema()

        for class_node in self._source.match_nodes(database, label=CLASS):
            nid = class_node["id"]
            props = class_node.get("properties", {})
            class_name = props.get("name", nid)
            schema.add_entity(class_name, description=props.get("description", ""))
            for nb in self._source.get_neighbors(database, nid, edge_type=DEFINE_PROPERTY, direction="out"):
                p = nb["node"].get("properties", {})
                schema.add_entity_attribute(
                    class_name,
                    p.get("name", nb["node"]["id"]),
                    "str",
                    description=p.get("description", ""),
                )

        for class_node in self._source.match_nodes(database, label=CLASS):
            nid = class_node["id"]
            child_name = class_node.get("properties", {}).get("name", nid)
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_SUBCLASS_OF, direction="out"):
                parent_name = nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                schema.add_relation(child_name, IS_SUBCLASS_OF, parent_name)

        for rel_node in self._source.match_nodes(database, label=RELATION):
            nid = rel_node["id"]
            props = rel_node.get("properties", {})
            rel_name = props.get("name", nid)
            rel_desc = props.get("description", "")
            domains = [
                nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                for nb in self._source.get_neighbors(database, nid, edge_type=DOMAIN, direction="out")
            ] or ["*"]
            ranges = [
                nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                for nb in self._source.get_neighbors(database, nid, edge_type=RANGE, direction="out")
            ] or ["*"]
            rel_prop_nodes = [
                nb["node"]
                for nb in self._source.get_neighbors(database, nid, edge_type=DEFINE_PROPERTY, direction="out")
            ]
            for domain_name in domains:
                for range_name in ranges:
                    rel_key = schema.add_relation(domain_name, rel_name, range_name, description=rel_desc)
                    for prop_node in rel_prop_nodes:
                        p = prop_node.get("properties", {})
                        schema.add_relation_attribute(
                            rel_key,
                            p.get("name", prop_node["id"]),
                            "str",
                            description=p.get("description", ""),
                        )

        for rel_node in self._source.match_nodes(database, label=RELATION):
            nid = rel_node["id"]
            child_name = rel_node.get("properties", {}).get("name", nid)
            for nb in self._source.get_neighbors(database, nid, edge_type=IS_SUBCLASS_OF, direction="out"):
                parent_name = nb["node"].get("properties", {}).get("name", nb["node"]["id"])
                schema.add_relation(child_name, IS_SUBCLASS_OF, parent_name)

        return schema

    async def import_database_schema(self, database: str, schema_json: dict) -> None:
        schema = DataSchema.from_json(schema_json)

        class_ids: dict[str, str] = {}
        for entity_obj in schema.entities.values():
            class_name = entity_obj["name"]
            class_id = await self.declare_class(
                database, name=class_name, description=entity_obj.get("description", "")
            )
            class_ids[class_name] = class_id
            for attr_name, attr_obj in entity_obj.get("contents", {}).get("attributes", {}).items():
                await self.declare_class_property(
                    database,
                    name=attr_name,
                    description=attr_obj.get("description", "") if isinstance(attr_obj, dict) else "",
                    class_id=class_id,
                )

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
            src = rel_obj.get("source", "")
            tgt = rel_obj.get("target", "")
            if src and src != "*" and src not in rel_data[rel_name]["domains"]:
                rel_data[rel_name]["domains"].append(src)
            if tgt and tgt != "*" and tgt not in rel_data[rel_name]["ranges"]:
                rel_data[rel_name]["ranges"].append(tgt)

        for rel_name, rd in rel_data.items():
            relation_id = await self.declare_relation_type(
                database, name=rel_name, description=rd["description"]
            )
            for domain_name in rd["domains"]:
                if domain_name in class_ids:
                    self._source.add_edge(database, relation_id, DOMAIN, class_ids[domain_name])
            for range_name in rd["ranges"]:
                if range_name in class_ids:
                    self._source.add_edge(database, relation_id, RANGE, class_ids[range_name])
            for attr_name, attr_obj in rd["attributes"].items():
                await self.declare_relation_property(
                    database,
                    name=attr_name,
                    description=attr_obj.get("description", "") if isinstance(attr_obj, dict) else "",
                    relation_id=relation_id,
                )

    # ------------------------------------------------------------------ #
    # Search and consolidation                                             #
    # ------------------------------------------------------------------ #

    async def search_schema(self, database: str, query: str) -> list[SchemaSearchResult]:
        """Return schema elements whose name or description contains *query* (case-insensitive)."""
        q = query.lower()
        results: list[SchemaSearchResult] = []
        for label in (CLASS, RELATION, PROPERTY):
            for node in self._source.match_nodes(database, label=label):
                props = node.get("properties", {})
                name = props.get("name", "")
                desc = props.get("description", "") or ""
                if q in name.lower() or q in desc.lower():
                    results.append(SchemaSearchResult(id=node["id"], type=label, name=name, description=desc))
        return results

    async def consolidate_schema(
        self,
        database: str,
        operation: str,
        source_names: list[str],
        target_name: str,
        predicate: object | None = None,
    ) -> None:
        """Merge or split schema elements and rewire instances accordingly."""
        if operation == "merge":
            await self._merge_schema(database, source_names, target_name)
        else:
            raise SchemaError(f"consolidate_schema operation '{operation}' is not supported")

    async def _merge_schema(
        self,
        database: str,
        source_names: list[str],
        target_name: str,
    ) -> None:
        # Collect source nodes, all must share the same label
        source_nodes: list[dict] = []
        label: str | None = None
        for name in source_names:
            found = False
            for lbl in (CLASS, RELATION, PROPERTY):
                nodes = self._source.match_nodes(database, label=lbl, properties={"name": name})
                if nodes:
                    if label is not None and label != lbl:
                        raise SchemaError("cannot merge schema elements of different types")
                    label = lbl
                    source_nodes.extend(nodes)
                    found = True
                    break
            if not found:
                raise SchemaError(f"schema element '{name}' not found in '{database}'")

        if label is None:
            raise SchemaError("no source schema elements found")

        existing_target = self._source.match_nodes(database, label=label, properties={"name": target_name})
        target_id = (
            existing_target[0]["id"]
            if existing_target
            else self._source.add_node(database, label, {"name": target_name, "description": ""})
        )

        # Edge type and direction for finding instances that reference each source node
        instance_edge_map: dict[str, str] = {
            CLASS: IS_SUBCLASS_OF,   # overridden below; main instance edge is IS_INSTANCE_OF
            RELATION: "REPRESENTS",
            PROPERTY: "IS_VALUE_OF",
        }
        # Primary instance rewire: incoming edges pointing to the schema node
        primary: dict[str, str] = {
            CLASS: "IS_INSTANCE_OF",
            RELATION: "REPRESENTS",
            PROPERTY: "IS_VALUE_OF",
        }
        _ = instance_edge_map  # suppress unused warning

        edge_type = primary[label]
        for src_node in source_nodes:
            if src_node["id"] == target_id:
                continue
            for nb in self._source.get_neighbors(database, src_node["id"], edge_type=edge_type, direction="in"):
                instance_id = nb["node"]["id"]
                try:
                    self._source.add_edge(database, instance_id, edge_type, target_id)
                except Exception:  # noqa: BLE001
                    pass
