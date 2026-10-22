from __future__ import annotations

import itertools
import time
import uuid
from typing import Any

from blue_data.source.graph.graph_source import GraphSource
from blue_data.source.graph.neo4j.neo4j_graph_source import NEO4JSource
from neo4j import exceptions as neo4j_exceptions

from blue_data_kb_source.kb_source import KBSource, as_rows
from blue_data_kb_source.types import DuplicateNodeError, KBError

# Internal properties this class keeps on every node/relationship. They never appear in
# returned `properties`, matching what the memory/file backends return.
_ID = "_id"      # stable string id (a uuid unless the caller supplied one)
_DB = "_kb_db"   # logical KB database the node belongs to
_SEQ = "_seq"    # insertion order, so results come back in the order the memory backend uses
_NULLS = "_nulls"  # keys whose value is None — Neo4j can't store a null property, the others keep the key
_INTERNAL = (_ID, _DB, _SEQ, _NULLS)

# Uniqueness the store enforces itself (#82), on top of the KB's own lookups, which
# can't stop two writers that both look before either writes. Every key includes the
# logical database: all of them share one physical Neo4j database. Entity and
# property names are deliberately not unique (two "Apple"s; AGE on PERSON and ANIMAL).
_ID_UNIQUE_LABELS = ("CLASS", "RELATION", "PROPERTY", "ENTITY", "FACT", "LITERAL", "SOURCE", "EXTRACTION")
_NAME_UNIQUE_LABELS = ("CLASS", "RELATION")

_seq_counter = itertools.count()


def _next_seq() -> int:
    # Monotonic across processes sharing a server well enough for ordering ties: wall
    # clock in microseconds, with a per-process counter breaking ties within one tick.
    return time.time_ns() // 1000 * 1000 + next(_seq_counter) % 1000


def _public(props: dict) -> dict:
    """Stored properties as callers see them: internals dropped, None-valued keys restored."""
    out = {k: v for k, v in props.items() if k not in _INTERNAL}
    for k in props.get(_NULLS) or ():
        out.setdefault(k, None)
    return out


def _storable(props: dict | None) -> dict:
    """Properties to write: None values left out, their keys recorded in `_nulls`."""
    props = {k: v for k, v in (props or {}).items() if k not in _INTERNAL}
    stored = {k: v for k, v in props.items() if v is not None}
    stored[_NULLS] = [k for k, v in props.items() if v is None]
    return stored


# SET clause for an update: `+=` removes keys given as null, so track them in `_nulls`.
def _set_clause(var: str) -> str:
    return (f"SET {var} += $props, {var}.{_NULLS} = "
            f"[k IN coalesce({var}.{_NULLS}, []) WHERE NOT k IN $set_keys] + $null_keys")


def _update_params(properties: dict) -> dict:
    props = {k: v for k, v in properties.items() if k not in _INTERNAL}
    return {"props": props, "set_keys": list(props), "null_keys": [k for k, v in props.items() if v is None]}


class NEO4JKBSource(KBSource, NEO4JSource):
    """Neo4j-backed KBSource — persistent, concurrent, production graph backend.

    Kept out of blue_data_kb_source's top-level module and package __init__ so that
    `import blue_data_kb_source` never requires the `neo4j` driver to be installed —
    only importing this module does. Use DataSource.instantiate() with this class's
    dotted path to construct one from a config-driven backend choice without an
    eager import; see platform/cli/config.py and platform/mcp/config.py.

    The graph primitives the KB layer uses (add/get/update/delete/match nodes,
    edges, get_neighbors) are implemented here rather than inherited from blue-data's
    NEO4JSource, for two reasons (#120):

    - **Result contract.** The KB reads the memory/file backends' shapes —
      `get_node` → `{id, label, properties}`, `get_neighbors` → `{node, edge,
      direction}` with `edge` = `{id, source_id, target_id, edge_type, properties}`.
      NEO4JSource returns `node_id` instead of `id` and neighbors without `edge` or
      `direction`, so `get_metadata`, schema merge and the extraction agent's
      `find_entity` failed on Neo4j.
    - **Database isolation.** Neo4j Community has a single database, and NEO4JSource
      maps every logical database onto it — so `main` and `copy` shared one graph.
      Every node here carries its logical database in `_kb_db` and every query
      filters on it (relationships only ever join nodes of one database).

    Ids are stable strings kept in `_id` (a uuid unless the caller supplied one), never
    Neo4j element ids. All of it lives in one physical Neo4j database, `connection
    ["database"]` (default `neo4j`) — the `database` argument only ever picks the tag. `transaction_id` is passed through to NEO4JSource's native
    transactions unchanged.
    """

    def _initialize_connection_properties(self) -> None:
        super()._initialize_connection_properties()
        self.properties["connection"]["protocol"] = "kb/neo4j"
        # The one physical Neo4j database this KB lives in. Logical KB databases are
        # `_kb_db` tags inside it (see below), never separate Neo4j databases.
        self.properties["connection"].setdefault("database", "neo4j")

    def _override_database(self, database):
        """Every query and transaction runs in the configured physical database.

        NEO4JSource maps a logical name onto a physical Neo4j database on Enterprise
        (and onto `neo4j` on Community). Here logical databases are `_kb_db` tags, so a
        caller-supplied name — an MCP tool argument, i.e. text an agent chose — can
        never select another physical database the credentials reach, `system` or
        another application's included (#132).
        """
        return self.properties["connection"]["database"]

    def fetch_database_metadata(self, database, transaction_id=None):
        """Node and relationship counts for one logical KB database.

        NEO4JSource counts everything in the physical database, which here holds
        every logical KB database, so one KB database reported another's nodes (#136).
        fetch_database_stats reads its counts from here.
        """
        nodes = self._q(f"MATCH (n {{{_DB}: $db}}) RETURN count(n) AS cnt", database, {},
                        read=True, transaction_id=transaction_id)
        rels = self._q(f"MATCH (n {{{_DB}: $db}})-[r]->() RETURN count(r) AS cnt", database, {},
                       read=True, transaction_id=transaction_id)
        total_nodes = nodes[0]["cnt"] if nodes else 0
        total_rels = rels[0]["cnt"] if rels else 0
        return {
            "description": f"KB database '{database}' with {total_nodes} nodes and {total_rels} relationships",
            "node_count": total_nodes,
            "relationship_count": total_rels,
        }

    def setup_schema(self) -> None:
        """Create the uniqueness constraints the KB relies on, if missing (#82).

        Per logical database: one CLASS and one RELATION per name, and one node per
        id for every KB label. Idempotent (`IF NOT EXISTS`), and run automatically —
        by create_database and before the first write — so a fresh Neo4j server needs
        no separate setup step. Raises KBError naming the offenders if the data already
        holds duplicates, since such a constraint can't be created over them; nothing
        is deleted.
        """
        physical = self.properties["connection"]["database"]
        wanted = [(label, "id", (_DB, _ID)) for label in _ID_UNIQUE_LABELS]
        wanted += [(label, "name", (_DB, "name")) for label in _NAME_UNIQUE_LABELS]
        for label, kind, keys in wanted:
            fields = ", ".join(f"n.{k}" for k in keys)
            try:
                self._run_query(
                    f"CREATE CONSTRAINT kb_{label.lower()}_{kind} IF NOT EXISTS "
                    f"FOR (n:`{label}`) REQUIRE ({fields}) IS UNIQUE",
                    database=physical, read=False,
                )
            except neo4j_exceptions.Neo4jError as exc:
                dupes = self._run_query(
                    f"MATCH (n:`{label}`) WITH [{fields}] AS key, count(*) AS c WHERE c > 1 "
                    f"RETURN key LIMIT 10",
                    database=physical, read=True,
                )
                raise KBError(
                    f"cannot enforce unique {label} {kind}s per KB database: the data already holds "
                    f"duplicates (database, {kind}): {[r['key'] for r in dupes]}. Merge or delete "
                    f"them, then retry."
                ) from exc
        self._schema_ready = True

    def _ensure_schema(self) -> None:
        if not getattr(self, "_schema_ready", False):
            self.setup_schema()

    def create_database(self, database, properties=None, overwrite=False):
        """Make sure the physical database exists; with `overwrite`, empty the logical one.

        NEO4JSource's version answers `overwrite=True` with `DROP DATABASE` — and with
        `_override_database` that names the shared physical database, which on
        Enterprise would drop every logical KB database in it, not just *database*
        (#137). Here the physical database is never dropped; overwriting deletes only
        the nodes tagged with *database*.
        """
        result = super().create_database(database, properties=properties, overwrite=False)
        self._ensure_schema()
        if overwrite:
            self._q(f"MATCH (n {{{_DB}: $db}}) DETACH DELETE n", database, {}, read=False, transaction_id=None)
            return {"status": "success"}
        return result

    # ------------------------------------------------------------------ #
    # helpers                                                              #
    # ------------------------------------------------------------------ #

    def _q(self, query: str, database: str, params: dict, read: bool, transaction_id: str | None) -> list[dict]:
        return self._run_query(
            query, database=database, params={**params, "db": database},
            read=read, transaction_id=transaction_id,
        )

    def _log(self, transaction_id: str | None, entry: dict) -> None:
        if transaction_id is not None:
            self._transactions[transaction_id]["operations"].append(entry)

    @staticmethod
    def _node(node_obj: Any) -> dict:
        props = dict(node_obj)
        return {
            "id": props.get(_ID),
            "label": next(iter(node_obj.labels), None),
            "properties": _public(props),
        }

    @staticmethod
    def _edge(rel_obj: Any, source_id: str, target_id: str, edge_type: str) -> dict:
        props = dict(rel_obj)
        return {
            "id": props.get(_ID),
            "source_id": source_id,
            "edge_type": edge_type,
            "target_id": target_id,
            "properties": _public(props),
        }

    # ------------------------------------------------------------------ #
    # nodes                                                                #
    # ------------------------------------------------------------------ #

    def add_node(self, database, label, properties=None, node_id=None, transaction_id=None) -> str:
        self._ensure_schema()
        new_id = node_id if node_id is not None else str(uuid.uuid4())
        props = {**_storable(properties), _ID: new_id, _DB: database, _SEQ: _next_seq()}
        lbl = self._clean_neo4j_identifier(label)
        try:
            self._q(f"CREATE (n:`{lbl}` $props)", database, {"props": props}, False, transaction_id)
        except neo4j_exceptions.ConstraintError as exc:
            raise DuplicateNodeError(
                f"{label} {(properties or {}).get('name') or new_id!r} already exists in database {database!r}"
            ) from exc
        self._log(transaction_id, {"op": "add_node", "label": label, "properties": properties, "node_id": new_id})
        return new_id

    def get_node(self, database, node_id, transaction_id=None) -> dict | None:
        rows = self._q(
            f"MATCH (n {{{_ID}: $nid, {_DB}: $db}}) RETURN n LIMIT 1",
            database, {"nid": node_id}, True, transaction_id,
        )
        return self._node(rows[0]["n"]) if rows else None

    def delete_node(self, database, node_id, transaction_id=None) -> None:
        self._q(
            f"MATCH (n {{{_ID}: $nid, {_DB}: $db}}) DETACH DELETE n",
            database, {"nid": node_id}, False, transaction_id,
        )
        self._log(transaction_id, {"op": "delete_node", "node_id": node_id})

    def update_node(self, database, node_id, properties, transaction_id=None) -> None:
        try:
            self._q(
                f"MATCH (n {{{_ID}: $nid, {_DB}: $db}}) {_set_clause('n')}",
                database, {"nid": node_id, **_update_params(properties)}, False, transaction_id,
            )
        except neo4j_exceptions.ConstraintError as exc:   # e.g. a rename onto a taken name (#82)
            raise DuplicateNodeError(
                f"{(properties or {}).get('name')!r} is already taken in database {database!r}"
            ) from exc
        self._log(transaction_id, {"op": "update_node", "node_id": node_id, "properties": properties})

    def match_nodes(self, database, label=None, properties=None, transaction_id=None) -> list[dict]:
        label_clause = f":`{self._clean_neo4j_identifier(label)}`" if label else ""
        props = properties or {}
        conditions = [f"n.{_DB} = $db"] + [f"n.`{k}` = $p_{i}" for i, k in enumerate(props)]
        params = {f"p_{i}": v for i, v in enumerate(props.values())}
        rows = self._q(
            f"MATCH (n{label_clause}) WHERE {' AND '.join(conditions)} RETURN n ORDER BY n.{_SEQ}",
            database, params, True, transaction_id,
        )
        return [self._node(r["n"]) for r in rows]

    # ------------------------------------------------------------------ #
    # edges                                                                #
    # ------------------------------------------------------------------ #

    def add_edge(self, database, source_id, edge_type, target_id, properties=None, transaction_id=None) -> str:
        edge_id = str(uuid.uuid4())
        rel = self._clean_neo4j_identifier(edge_type)
        props = {**_storable(properties), _ID: edge_id, _SEQ: _next_seq()}
        # A missing endpoint creates nothing. That matches the memory/file backends, where
        # such an edge is stored but never surfaces (get_neighbors skips absent nodes) —
        # callers that need the endpoints to exist check them first (require_node_type).
        self._q(
            f"MATCH (a {{{_ID}: $src, {_DB}: $db}}) MATCH (b {{{_ID}: $tgt, {_DB}: $db}}) "
            f"CREATE (a)-[r:`{rel}` $props]->(b)",
            database, {"src": source_id, "tgt": target_id, "props": props}, False, transaction_id,
        )
        self._log(transaction_id, {
            "op": "add_edge", "source_id": source_id, "edge_type": edge_type,
            "target_id": target_id, "properties": properties, "edge_id": edge_id,
        })
        return edge_id

    def delete_edge(self, database, source_id, edge_type, target_id, transaction_id=None) -> None:
        rel = self._clean_neo4j_identifier(edge_type)
        self._q(
            f"MATCH (a {{{_ID}: $src, {_DB}: $db}})-[r:`{rel}`]->(b {{{_ID}: $tgt, {_DB}: $db}}) DELETE r",
            database, {"src": source_id, "tgt": target_id}, False, transaction_id,
        )
        self._log(transaction_id, {"op": "delete_edge", "source_id": source_id,
                                   "edge_type": edge_type, "target_id": target_id})

    def update_edge(self, database, source_id, edge_type, target_id, properties, transaction_id=None) -> None:
        rel = self._clean_neo4j_identifier(edge_type)
        self._q(
            f"MATCH (a {{{_ID}: $src, {_DB}: $db}})-[r:`{rel}`]->(b {{{_ID}: $tgt, {_DB}: $db}}) {_set_clause('r')}",
            database, {"src": source_id, "tgt": target_id, **_update_params(properties)}, False, transaction_id,
        )
        self._log(transaction_id, {"op": "update_edge", "source_id": source_id, "edge_type": edge_type,
                                   "target_id": target_id, "properties": properties})

    def match_edges(self, database, edge_type=None, source_id=None, target_id=None, properties=None,
                    transaction_id=None) -> list[dict]:
        rel_clause = f":`{self._clean_neo4j_identifier(edge_type)}`" if edge_type else ""
        conditions = [f"a.{_DB} = $db"]
        params: dict = {}
        if source_id is not None:
            conditions.append(f"a.{_ID} = $src")
            params["src"] = source_id
        if target_id is not None:
            conditions.append(f"b.{_ID} = $tgt")
            params["tgt"] = target_id
        for i, (k, v) in enumerate((properties or {}).items()):
            conditions.append(f"r.`{k}` = $p_{i}")
            params[f"p_{i}"] = v
        rows = self._q(
            f"MATCH (a)-[r{rel_clause}]->(b) WHERE {' AND '.join(conditions)} "
            f"RETURN r, type(r) AS t, a.{_ID} AS s, b.{_ID} AS o ORDER BY r.{_SEQ}",
            database, params, True, transaction_id,
        )
        return [self._edge(r["r"], r["s"], r["o"], r["t"]) for r in rows]

    def get_neighbors(self, database, node_id, edge_type=None, direction="both", transaction_id=None) -> list[dict]:
        rel_clause = f":`{self._clean_neo4j_identifier(edge_type)}`" if edge_type else ""
        results: list[tuple[int, dict]] = []
        # One query per direction, like the memory backend: a "both" lookup lists an
        # edge once from each end it touches, each tagged with its direction.
        for d, pattern in (("out", f"-[r{rel_clause}]->"), ("in", f"<-[r{rel_clause}]-")):
            if direction not in (d, "both"):
                continue
            rows = self._q(
                f"MATCH (n {{{_ID}: $nid, {_DB}: $db}}){pattern}(m) "
                f"RETURN m, r, type(r) AS t, r.{_SEQ} AS seq",
                database, {"nid": node_id}, True, transaction_id,
            )
            for row in rows:
                other = self._node(row["m"])
                src, tgt = (node_id, other["id"]) if d == "out" else (other["id"], node_id)
                results.append((row["seq"] or 0, {
                    "node": other, "edge": self._edge(row["r"], src, tgt, row["t"]), "direction": d,
                }))
        return [item for _, item in sorted(results, key=lambda x: x[0])]

    # ------------------------------------------------------------------ #
    # query                                                                #
    # ------------------------------------------------------------------ #

    def execute_query(self, query, database=None, collection=None, optional_properties=None):
        """KB.* and GRAPH.* dict ops, exactly as on the other backends.

        KBSource.execute_query forwards GRAPH.* ops to `super()`, which here would be
        NEO4JSource's raw-Cypher execute_query. The dict dispatcher is GraphSource's, so
        route those there; it calls this class's own primitives. A Cypher string is
        still accepted and runs as raw Cypher, for callers that want the native API.

        Raw Cypher sees the whole physical database — every logical KB database in it,
        and the internal `_id`/`_kb_db` properties — since it can't be scoped for the
        caller (#136). The logical database name is passed as `$db`, so a query stays
        within one KB database by matching on it: `MATCH (n {_kb_db: $db}) ...`.
        """
        if isinstance(query, str):
            props = dict(optional_properties or {})
            if database is not None:
                props["params"] = {"db": database, **(props.get("params") or {})}
            return as_rows(NEO4JSource.execute_query(self, query, database, collection, props))
        op = (query.get("op") or "").upper() if isinstance(query, dict) else ""
        if op.startswith("GRAPH."):
            return as_rows(GraphSource.execute_query(self, query, database, collection, optional_properties))
        return KBSource.execute_query(self, query, database, collection, optional_properties)
