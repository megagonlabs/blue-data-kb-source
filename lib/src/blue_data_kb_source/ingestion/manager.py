"""IngestionManager: the ingest path's assert operations (schema half).

Every assert runs in two phases (decision 26): phase 1 resolves every operand
read-only and runs any needed judges, so any failure — ambiguity, a judge
crash, a missing API key — raises `IngestionError` (or the judge's own
`KBError`) before anything is written. Phase 2 writes through the existing
write path (`declare_*` and the provenance tracker), so change notification
and search indexing fire exactly as for direct writes.

The pick-or-raise resolution policy lives on `_pick`; the two edge asserts
(`assert_subclass`, `assert_subrelation`) report `match_type=None` always,
with `created` describing the edge.
"""

from __future__ import annotations

from typing import Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    IS_SUBCLASS_OF,
    PROPERTY,
    RELATION,
)
from blue_data_kb_source.ingestion.judges import judge_description
from blue_data_kb_source.knowledge.manager import KnowledgeManager
from blue_data_kb_source.provenance.tracker import ProvenanceTracker
from blue_data_kb_source.resolution.resolver import NodeResolver
from blue_data_kb_source.schema.manager import SchemaManager
from blue_data_kb_source.types import (
    AssertResult,
    IngestionError,
    KBError,
    NodeRef,
    ResolutionCandidate,
)

_IDENTITY_CERTAIN = ("exact", "alias", "structural")
_ExtractionType = Literal["text_span", "external_record", "assertion"]
_SchemaCreationMode = Literal["llm", "none"]


class IngestionManager:
    """Ingest-path assert operations: resolve-or-create with judges, two-phase writes."""

    def __init__(
        self,
        source: GraphSource,
        schema_manager: SchemaManager,
        knowledge_manager: KnowledgeManager,
        provenance: ProvenanceTracker,
        resolver: NodeResolver,
    ) -> None:
        self._source = source
        self._schema = schema_manager
        self._knowledge = knowledge_manager
        self._provenance = provenance
        self._resolver = resolver

    # ------------------------------------------------------------------ #
    # Schema asserts                                                       #
    # ------------------------------------------------------------------ #

    async def assert_class(
        self,
        database: str,
        name: str,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        hit = self._pick(await self._resolver.resolve_class(database, name), f"class {name!r}")
        if hit is not None:
            return AssertResult(node_id=hit.node_id, created=False, match_type=hit.match_type)
        desc = await self._describe("class", name, name, description, schema_creation_mode, model)
        class_id = await self._schema.declare_class(database, name, desc)
        ref = await self._evidence(
            database, class_id, CLASS, name, name, source_id, confidence, extraction_type
        )
        return AssertResult(node_id=class_id, created=True,
                            extraction_id=ref.extraction_id, created_nodes=[ref])

    async def assert_relation_type(
        self,
        database: str,
        name: str,
        domain: str | list[str],
        range: str | list[str],
        description: str | None = None,
        is_symmetric: bool = False,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        domain_names = [domain] if isinstance(domain, str) else list(domain)
        range_names = [range] if isinstance(range, str) else list(range)
        text = f"{', '.join(domain_names)} {name} {', '.join(range_names)}"

        # Phase 1: relation, then every domain/range class; plan judge output for creations.
        rel_hit = self._pick(await self._resolver.resolve_relation(database, name),
                             f"relation {name!r}")
        class_ids: dict[str, str] = {}
        planned: dict[str, str] = {}
        for cname in dict.fromkeys(domain_names + range_names):
            chit = self._pick(await self._resolver.resolve_class(database, cname),
                              f"class {cname!r}")
            if chit is not None:
                class_ids[cname] = chit.node_id
            else:
                planned[cname] = await self._describe(
                    "class", cname, text, None, schema_creation_mode, model
                )
        if rel_hit is None:
            rel_desc = await self._describe(
                "relation", name, text, description, schema_creation_mode, model
            )
        else:
            have = await self._schema.get_relation_domain_range(database, rel_hit.node_id)
            have_domain = {n.get("properties", {}).get("name") for n in have["domain"]}
            have_range = {n.get("properties", {}).get("name") for n in have["range"]}
            widen_domain = [c for c in domain_names if c not in have_domain]
            widen_range = [c for c in range_names if c not in have_range]

        # Phase 2.
        created = [
            await self._create_class(database, cname, cdesc, text,
                                     source_id, confidence, extraction_type, class_ids)
            for cname, cdesc in planned.items()
        ]
        if rel_hit is None:
            rid = await self._schema.declare_relation_type(
                database, name, rel_desc, is_symmetric, domain=domain_names, range=range_names
            )
            ref = await self._evidence(
                database, rid, RELATION, name, text, source_id, confidence, extraction_type
            )
            created.append(ref)
            return AssertResult(node_id=rid, created=True,
                                extraction_id=ref.extraction_id, created_nodes=created)
        for cname in widen_domain:
            await self._schema.attach_relation_domain(database, rel_hit.node_id, class_ids[cname])
        for cname in widen_range:
            await self._schema.attach_relation_range(database, rel_hit.node_id, class_ids[cname])
        widened = ([NodeRef(node_id=rel_hit.node_id, label=RELATION, name=rel_hit.name)]
                   if widen_domain or widen_range else [])
        return AssertResult(node_id=rel_hit.node_id, created=False, match_type=rel_hit.match_type,
                            created_nodes=created, widened_relations=widened)

    async def assert_class_property(
        self,
        database: str,
        name: str,
        class_name: str,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        text = f"{class_name} has {name}"
        chit = self._pick(await self._resolver.resolve_class(database, class_name),
                          f"class {class_name!r}")
        owner_id = chit.node_id if chit is not None else None
        if owner_id is not None:
            existing = self._own_property(database, owner_id, name)
            if existing is not None:
                return AssertResult(node_id=existing, created=False, match_type="exact")
        class_desc = None if chit is not None else await self._describe(
            "class", class_name, text, None, schema_creation_mode, model
        )
        prop_desc = await self._describe(
            "property", name, text, description, schema_creation_mode, model
        )
        created: list[NodeRef] = []
        class_ids: dict[str, str] = {}
        if owner_id is None:
            ref = await self._create_class(database, class_name, class_desc or "", text,
                                           source_id, confidence, extraction_type, class_ids)
            created.append(ref)
            owner_id = ref.node_id
        prop_id = await self._schema.declare_class_property(
            database, name, prop_desc, class_id=owner_id
        )
        ref = await self._evidence(
            database, prop_id, PROPERTY, name, text, source_id, confidence, extraction_type
        )
        created.append(ref)
        return AssertResult(node_id=prop_id, created=True,
                            extraction_id=ref.extraction_id, created_nodes=created)

    async def assert_relation_property(
        self,
        database: str,
        name: str,
        relation_name: str,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        text = f"{relation_name} has {name}"
        rhit = self._pick(await self._resolver.resolve_relation(database, relation_name),
                          f"relation {relation_name!r}")
        if rhit is None:
            raise IngestionError(
                f"relation {relation_name!r} does not exist and a relation cannot be created "
                "without a domain and range — assert it first via assert_relation_type. "
                "Nothing was written."
            )
        existing = self._own_property(database, rhit.node_id, name)
        if existing is not None:
            return AssertResult(node_id=existing, created=False, match_type="exact")
        prop_desc = await self._describe(
            "property", name, text, description, schema_creation_mode, model
        )
        prop_id = await self._schema.declare_relation_property(
            database, name, prop_desc, relation_id=rhit.node_id
        )
        ref = await self._evidence(
            database, prop_id, PROPERTY, name, text, source_id, confidence, extraction_type
        )
        return AssertResult(node_id=prop_id, created=True,
                            extraction_id=ref.extraction_id, created_nodes=[ref])

    async def assert_subclass(
        self,
        database: str,
        child: str,
        parent: str,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        text = f"{child} IS_SUBCLASS_OF {parent}"
        ids: dict[str, str] = {}
        planned: dict[str, str] = {}
        for cname in dict.fromkeys((child, parent)):
            chit = self._pick(await self._resolver.resolve_class(database, cname),
                              f"class {cname!r}")
            if chit is not None:
                ids[cname] = chit.node_id
            else:
                planned[cname] = await self._describe(
                    "class", cname, text, None, schema_creation_mode, model
                )
        created = [
            await self._create_class(database, cname, cdesc, text,
                                     source_id, confidence, extraction_type, ids)
            for cname, cdesc in planned.items()
        ]
        return await self._assert_edge(database, ids[child], ids[parent], created)

    async def assert_subrelation(
        self,
        database: str,
        child: str,
        parent: str,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        ids: dict[str, str] = {}
        for rname in dict.fromkeys((child, parent)):
            rhit = self._pick(await self._resolver.resolve_relation(database, rname),
                              f"relation {rname!r}")
            if rhit is None:
                raise IngestionError(
                    f"relation {rname!r} does not exist and a relation cannot be created "
                    "without a domain and range — assert it first via assert_relation_type. "
                    "Nothing was written."
                )
            ids[rname] = rhit.node_id
        return await self._assert_edge(database, ids[child], ids[parent], [])

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _pick(
        self, candidates: list[ResolutionCandidate], what: str
    ) -> ResolutionCandidate | None:
        """One identity-certain candidate wins; else one uncertain candidate wins;
        two or more in the deciding group raise with the candidates (decisions 2, 28)."""
        certain = [c for c in candidates if c.match_type in _IDENTITY_CERTAIN]
        group = certain if certain else candidates
        if len(group) == 1:
            return group[0]
        if len(group) >= 2:
            listing = ", ".join(f"{c.name} ({c.match_type})" for c in group)
            raise IngestionError(
                f"{what} is ambiguous between: {listing}. Nothing was written.",
                candidates=group,
            )
        return None

    async def _describe(
        self,
        kind: str,
        name: str,
        context: str,
        description: str | None,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
    ) -> str:
        if description is not None:
            return description
        if schema_creation_mode != "llm":
            return ""
        try:
            return await judge_description(
                name, f"a {kind} created while asserting: {context}", model=model
            )
        except IngestionError:
            raise
        except Exception as exc:
            if isinstance(exc, KBError):
                raise
            raise IngestionError(
                f"judge_description failed for {kind} {name!r}: {exc!r}. Nothing was written."
            ) from exc

    async def _evidence(
        self,
        database: str,
        node_id: str,
        label: str,
        name: str,
        text: str,
        source_id: str | None,
        confidence: float | None,
        extraction_type: _ExtractionType,
    ) -> NodeRef:
        extraction_id = await self._provenance.add_extraction(
            database, text=text, source_id=source_id,
            confidence=confidence, extraction_type=extraction_type, element_id=node_id,
        )
        return NodeRef(node_id=node_id, label=label, name=name, extraction_id=extraction_id)

    async def _create_class(
        self,
        database: str,
        name: str,
        description: str,
        text: str,
        source_id: str | None,
        confidence: float | None,
        extraction_type: _ExtractionType,
        class_ids: dict[str, str],
    ) -> NodeRef:
        class_id = await self._schema.declare_class(database, name, description)
        class_ids[name] = class_id
        return await self._evidence(
            database, class_id, CLASS, name, text, source_id, confidence, extraction_type
        )

    def _own_property(self, database: str, owner_id: str, name: str) -> str | None:
        """Exact-name PROPERTY already declared on this owner, or None."""
        for nb in self._source.get_neighbors(
            database, owner_id, edge_type=DEFINE_PROPERTY, direction="out"
        ):
            if nb["node"].get("properties", {}).get("name") == name:
                return nb["node"]["id"]
        return None

    async def _assert_edge(
        self, database: str, child_id: str, parent_id: str, created: list[NodeRef]
    ) -> AssertResult:
        if child_id == parent_id:
            raise IngestionError(
                "child and parent resolved to the same node — refusing a self-subclass "
                "edge. Nothing was written."
            )
        nbs = self._source.get_neighbors(
            database, child_id, edge_type=IS_SUBCLASS_OF, direction="out"
        )
        if any(nb["node"]["id"] == parent_id for nb in nbs):
            return AssertResult(node_id=child_id, created=False, created_nodes=created)
        await self._schema.add_is_subclass_of(database, child_id, parent_id)
        return AssertResult(node_id=child_id, created=True, created_nodes=created)
