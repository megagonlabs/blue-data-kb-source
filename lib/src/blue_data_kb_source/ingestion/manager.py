"""IngestionManager: the ingest path's assert operations.

Every assert runs in two phases (decision 26): phase 1 resolves every operand
read-only and runs any needed judges, so any failure — ambiguity, a judge
crash, a missing API key — raises `IngestionError` (or the judge's own
`KBError`) before anything is written. Phase 2 writes through the existing
write path (`declare_*` and the provenance tracker), so change notification
and search indexing fire exactly as for direct writes.

The pick-or-raise resolution policy lives on `_pick` and `_pick_entity` (which
adds the class-expectation rule); the two edge asserts
(`assert_subclass`, `assert_subrelation`) report `match_type=None` always,
with `created` describing the edge.
"""

from __future__ import annotations

from typing import Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    ENTITY,
    FACT,
    IS_SUBCLASS_OF,
    LITERAL,
    PROPERTY,
    RELATION,
)
from blue_data_kb_source.ingestion.judges import (
    judge_class_name,
    judge_description,
    judge_property_placement,
)
from blue_data_kb_source.knowledge.helpers import (
    entity_classes,
    fact_parts,
    find_fact,
    find_literal,
)
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
_EVERYTHING_DESC = "Catch-all class for entities created without a type"


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
    # Instance asserts                                                     #
    # ------------------------------------------------------------------ #

    async def assert_entity(
        self,
        database: str,
        name: str,
        class_name: str | None = None,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        text = f"{name} is_a {class_name}" if class_name else name
        expected: set[str] = set()
        class_hit = None
        if class_name is not None:
            class_hit = self._pick(await self._resolver.resolve_class(database, class_name),
                                   f"class {class_name!r}")
            if class_hit is not None:
                expected = {class_hit.node_id}
        hit = await self._pick_entity(await self._resolver.resolve_entity(database, name),
                                f"entity {name!r}", database, expected)
        created: list[NodeRef] = []
        if hit is not None:
            # Decision 29: an entity an assert touches ends with at least one class.
            have = {c["id"] for c in entity_classes(self._source, database, hit.node_id)}
            attach_id: str | None = None
            overlap = ((await self._class_closure(database, have)) & expected
                       if expected else None)
            if class_name is not None and not overlap:
                attach_id, refs = await self._ensure_class(
                    database, class_name, text, source_id, confidence, extraction_type,
                    schema_creation_mode, model, class_hit,
                )
                created.extend(refs)
            elif not have:
                attach_id, refs = await self._membership_class(
                    database, name, text, source_id, confidence, extraction_type,
                    schema_creation_mode, model,
                )
                created.extend(refs)
            if attach_id is not None and attach_id not in have:
                await self._knowledge.attach_entity_class(database, hit.node_id, attach_id)
            return AssertResult(node_id=hit.node_id, created=False, match_type=hit.match_type,
                                created_nodes=created)
        if class_name is not None:
            class_id, refs = await self._ensure_class(
                database, class_name, text, source_id, confidence, extraction_type,
                schema_creation_mode, model, class_hit,
            )
        else:
            class_id, refs = await self._membership_class(
                database, name, text, source_id, confidence, extraction_type,
                schema_creation_mode, model,
            )
        created.extend(refs)
        entity_id = await self._knowledge.add_entity(
            database, name, description=description, class_id=class_id
        )
        ref = await self._evidence(
            database, entity_id, ENTITY, name, text, source_id, confidence, extraction_type
        )
        created.append(ref)
        return AssertResult(node_id=entity_id, created=True,
                            extraction_id=ref.extraction_id, created_nodes=created)

    async def assert_fact(
        self,
        database: str,
        head: str,
        relation: str,
        tail: str,
        name: str | None = None,
        description: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        text = f"{head} {relation} {tail}"
        rel_hit = self._pick(await self._resolver.resolve_relation(database, relation),
                             f"relation {relation!r}")
        expectations: dict[str, set[str]] = {"head": set(), "tail": set()}
        if rel_hit is not None:
            dr = await self._schema.get_relation_domain_range(database, rel_hit.node_id)
            expectations["head"] = {c["id"] for c in dr["domain"]}
            expectations["tail"] = {c["id"] for c in dr["range"]}
        ops: dict[str, dict] = {}
        for role, ename in (("head", head), ("tail", tail)):
            ehit = await self._pick_entity(await self._resolver.resolve_entity(database, ename),
                                     f"entity {ename!r}", database, expectations[role])
            ops[role] = {"name": ename, "hit": ehit}

        # Structural dedupe (decision 14): only checkable when all operands pre-exist.
        if rel_hit is not None and all(o["hit"] is not None for o in ops.values()):
            fact = find_fact(self._source, database, ops["head"]["hit"].node_id,
                             rel_hit.node_id, ops["tail"]["hit"].node_id)
            if fact is not None:
                ref = await self._evidence(database, fact["id"], FACT, text, text,
                                           source_id, confidence, extraction_type)
                return AssertResult(node_id=fact["id"], created=False, match_type="structural",
                                    extraction_id=ref.extraction_id)

        created: list[NodeRef] = []
        widened: list[NodeRef] = []
        entity_ids: dict[str, str] = {}
        role_class: dict[str, str] = {}
        for role in ("head", "tail"):
            ehit = ops[role]["hit"]
            ename = ops[role]["name"]
            if ehit is not None:
                entity_ids[role] = ehit.node_id
                classes = entity_classes(self._source, database, ehit.node_id)
                if classes:
                    role_class[role] = classes[0].get("properties", {}).get("name", "")
                else:
                    cid, refs = await self._membership_class(
                        database, ename, text, source_id, confidence, extraction_type,
                        schema_creation_mode, model,
                    )
                    created.extend(refs)
                    await self._knowledge.attach_entity_class(database, ehit.node_id, cid)
                    role_class[role] = self._name_of(database, cid)
            else:
                cid, refs = await self._membership_class(
                    database, ename, text, source_id, confidence, extraction_type,
                    schema_creation_mode, model,
                )
                created.extend(refs)
                eid = await self._knowledge.add_entity(database, ename, class_id=cid)
                eref = await self._evidence(database, eid, ENTITY, ename, text,
                                            source_id, confidence, extraction_type)
                created.append(eref)
                entity_ids[role] = eid
                role_class[role] = self._name_of(database, cid)

        if rel_hit is None:
            rel_desc = await self._describe("relation", relation, text, None,
                                            schema_creation_mode, model)
            rel_id = await self._schema.declare_relation_type(
                database, relation, rel_desc, domain=role_class["head"], range=role_class["tail"]
            )
            created.append(await self._evidence(database, rel_id, RELATION, relation, text,
                                                source_id, confidence, extraction_type))
        else:
            rel_id = rel_hit.node_id
            allowed = await self._knowledge.is_fact_allowed(
                database, entity_ids["head"], rel_id, entity_ids["tail"]
            )
            if not allowed["ok"]:
                if not allowed["details"].get("head_in_domain", False):
                    await self._widen(database, rel_id, "domain", role_class["head"], widened)
                if not allowed["details"].get("tail_in_range", False):
                    await self._widen(database, rel_id, "range", role_class["tail"], widened)
        fact_id = await self._knowledge.add_fact(
            database, name=name, description=description, head_entity_id=entity_ids["head"],
            tail_entity_id=entity_ids["tail"], relation_id=rel_id,
        )
        ref = await self._evidence(database, fact_id, FACT, text, text,
                                   source_id, confidence, extraction_type)
        created.append(ref)
        return AssertResult(node_id=fact_id, created=True, extraction_id=ref.extraction_id,
                            created_nodes=created, widened_relations=widened)

    async def assert_entity_property(
        self,
        database: str,
        entity: str,
        property: str,
        value: str | int | float | bool,
        literal_type: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        text = f"{entity} {property} {value}"
        entity_result = await self.assert_entity(
            database, entity, source_id=source_id, confidence=confidence,
            extraction_type=extraction_type, schema_creation_mode=schema_creation_mode,
            model=model,
        )
        entity_id = entity_result.node_id
        created = list(entity_result.created_nodes)
        prop_id = await self._owned_property_of_entity(database, entity_id, property)
        if prop_id is not None:
            existing = find_literal(self._source, database, entity_id, prop_id)
            if existing is not None:
                node = self._source.get_node(database, existing)
                if node is not None and str(node.get("properties", {}).get("name")) == str(value):
                    ref = await self._evidence(database, existing, LITERAL, str(value), text,
                                               source_id, confidence, extraction_type)
                    return AssertResult(node_id=existing, created=False, match_type="structural",
                                        extraction_id=ref.extraction_id, created_nodes=created)
        else:
            prop_desc = await self._describe("property", property, text, None,
                                             schema_creation_mode, model)
            owner_classes = await self._placement_classes(
                database, entity_id, property, schema_creation_mode, model
            )
            prop_id = await self._schema.declare_class_property(
                database, property, prop_desc, class_id=owner_classes[0]
            )
            for class_id in owner_classes[1:]:
                await self._schema.attach_property(database, class_id, prop_id)
            created.append(await self._evidence(database, prop_id, PROPERTY, property, text,
                                                source_id, confidence, extraction_type))
        literal_id = await self._knowledge.add_entity_property_value(
            database, value, literal_type=literal_type, entity_id=entity_id, property_id=prop_id
        )
        ref = await self._evidence(database, literal_id, LITERAL, str(value), text,
                                   source_id, confidence, extraction_type)
        created.append(ref)
        return AssertResult(node_id=literal_id, created=True, extraction_id=ref.extraction_id,
                            created_nodes=created)

    async def assert_fact_property(
        self,
        database: str,
        head: str,
        relation: str,
        tail: str,
        property: str,
        value: str | int | float | bool,
        literal_type: str | None = None,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str = "gpt-4o",
    ) -> AssertResult:
        text = f"{head} {relation} {tail}: {property} {value}"
        fact_result = await self.assert_fact(
            database, head, relation, tail, source_id=source_id, confidence=confidence,
            extraction_type=extraction_type, schema_creation_mode=schema_creation_mode,
            model=model,
        )
        fact_id = fact_result.node_id
        created = list(fact_result.created_nodes)
        rel_id = self._fact_relation_id(database, fact_id)
        prop_id = self._own_property(database, rel_id, property) if rel_id else None
        if prop_id is not None:
            existing = find_literal(self._source, database, fact_id, prop_id)
            if existing is not None:
                node = self._source.get_node(database, existing)
                if node is not None and str(node.get("properties", {}).get("name")) == str(value):
                    ref = await self._evidence(database, existing, LITERAL, str(value), text,
                                               source_id, confidence, extraction_type)
                    return AssertResult(node_id=existing, created=False, match_type="structural",
                                        extraction_id=ref.extraction_id, created_nodes=created)
        else:
            prop_desc = await self._describe("property", property, text, None,
                                             schema_creation_mode, model)
            prop_id = await self._schema.declare_relation_property(
                database, property, prop_desc, relation_id=rel_id
            )
            created.append(await self._evidence(database, prop_id, PROPERTY, property, text,
                                                source_id, confidence, extraction_type))
        literal_id = await self._knowledge.add_fact_property_value(
            database, value, literal_type=literal_type, fact_id=fact_id, property_id=prop_id
        )
        ref = await self._evidence(database, literal_id, LITERAL, str(value), text,
                                   source_id, confidence, extraction_type)
        created.append(ref)
        return AssertResult(node_id=literal_id, created=True, extraction_id=ref.extraction_id,
                            created_nodes=created)

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

    async def _pick_entity(
        self,
        candidates: list[ResolutionCandidate],
        what: str,
        database: str,
        expected: set[str],
    ) -> ResolutionCandidate | None:
        """`_pick`, plus decision 2's class-expectation rule: an identity-uncertain
        hit whose class closure misses every expected class raises instead of
        auto-resolving; identity-certain hits resolve regardless (decision 20 then
        widens membership rather than rejecting)."""
        certain = [c for c in candidates if c.match_type in _IDENTITY_CERTAIN]
        group = certain if certain else candidates
        if len(group) >= 2:
            listing = ", ".join(f"{c.name} ({c.match_type})" for c in group)
            raise IngestionError(
                f"{what} is ambiguous between: {listing}. Nothing was written.",
                candidates=group,
            )
        if not group:
            return None
        hit = group[0]
        if not certain and expected:
            have = {c["id"] for c in entity_classes(self._source, database, hit.node_id)}
            if have and not ((await self._class_closure(database, have)) & expected):
                raise IngestionError(
                    f"{what} matched {hit.name!r} only uncertainly ({hit.match_type}) and its "
                    "classes conflict with the expected class. Nothing was written.",
                    candidates=[hit],
                )
        return hit

    async def _class_closure(self, database: str, class_ids: set[str]) -> set[str]:
        """The classes plus their ancestors by id, via SchemaManager's walker
        (max_hops=5, matching the conformance checks)."""
        out = set(class_ids)
        for cid in class_ids:
            for a in await self._schema.get_class_ancestors(database, cid, max_hops=5):
                out.add(a["id"])
        return out

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

    async def _ensure_class(
        self,
        database: str,
        class_name: str,
        text: str,
        source_id: str | None,
        confidence: float | None,
        extraction_type: _ExtractionType,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
        class_hit: ResolutionCandidate | None,
    ) -> tuple[str, list[NodeRef]]:
        """Resolved class id, creating the class (judge-3 description) when missing."""
        if class_hit is not None:
            return class_hit.node_id, []
        desc = await self._describe("class", class_name, text, None, schema_creation_mode, model)
        ids: dict[str, str] = {}
        ref = await self._create_class(database, class_name, desc, text,
                                       source_id, confidence, extraction_type, ids)
        return ref.node_id, [ref]

    async def _membership_class(
        self,
        database: str,
        entity_name: str,
        text: str,
        source_id: str | None,
        confidence: float | None,
        extraction_type: _ExtractionType,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
    ) -> tuple[str, list[NodeRef]]:
        """A class for an untyped entity (decision 29): judge 2 under "llm",
        the EVERYTHING catch-all under "none" (decision 19)."""
        if schema_creation_mode != "llm":
            existing = await self._schema.find_class(database, "EVERYTHING")
            if existing is not None:
                return existing["id"], []
            ids: dict[str, str] = {}
            ref = await self._create_class(database, "EVERYTHING", _EVERYTHING_DESC, text,
                                           source_id, confidence, extraction_type, ids)
            return ref.node_id, [ref]
        listing = [
            {"name": n.get("properties", {}).get("name", ""),
             "description": n.get("properties", {}).get("description") or ""}
            for n in self._source.match_nodes(database, label=CLASS)
        ]
        try:
            chosen = await judge_class_name(entity_name, text, listing, model=model)
        except KBError:
            raise
        except Exception as exc:
            raise IngestionError(
                f"judge_class_name failed for entity {entity_name!r}: {exc!r}. "
                "Nothing was written."
            ) from exc
        hit = self._pick(await self._resolver.resolve_class(database, chosen),
                         f"class {chosen!r}")
        return await self._ensure_class(database, chosen, text, source_id, confidence,
                                        extraction_type, schema_creation_mode, model, hit)

    async def _placement_classes(
        self,
        database: str,
        entity_id: str,
        property_name: str,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
    ) -> list[str]:
        """Class ids that should declare a new property (decision 15): judge 1 picks
        from the entity's classes and ancestors under "llm"; under "none" the
        entity's direct classes (deterministic fallback, plan row 39d)."""
        direct = entity_classes(self._source, database, entity_id)
        direct_ids = [c["id"] for c in direct]
        if schema_creation_mode != "llm":
            return direct_ids
        offered = {c["id"]: c for c in direct}
        for cid in (await self._class_closure(database, set(direct_ids))) - set(direct_ids):
            node = self._source.get_node(database, cid)
            if node is not None:
                offered[cid] = node
        classes = [
            {"id": cid, "name": n.get("properties", {}).get("name", ""),
             "description": n.get("properties", {}).get("description") or ""}
            for cid, n in offered.items()
        ]
        try:
            chosen = await judge_property_placement(classes, property_name, model=model)
        except KBError:
            raise
        except Exception as exc:
            raise IngestionError(
                f"judge_property_placement failed for property {property_name!r}: {exc!r}. "
                "Nothing was written."
            ) from exc
        return chosen or direct_ids

    def _own_property(self, database: str, owner_id: str, name: str) -> str | None:
        """Exact-name PROPERTY already declared on this owner, or None."""
        for nb in self._source.get_neighbors(
            database, owner_id, edge_type=DEFINE_PROPERTY, direction="out"
        ):
            if nb["node"].get("properties", {}).get("name") == name:
                return nb["node"]["id"]
        return None

    async def _owned_property_of_entity(
        self, database: str, entity_id: str, name: str
    ) -> str | None:
        """A PROPERTY named *name* defined on any of the entity's classes or ancestors."""
        for cid in await self._class_closure(
            database, {c["id"] for c in entity_classes(self._source, database, entity_id)}
        ):
            found = self._own_property(database, cid, name)
            if found is not None:
                return found
        return None

    async def _widen(
        self, database: str, rel_id: str, side: str, class_name: str, widened: list[NodeRef]
    ) -> None:
        node = await self._schema.find_class(database, class_name)
        if node is None:
            return
        if side == "domain":
            await self._schema.attach_relation_domain(database, rel_id, node["id"])
        else:
            await self._schema.attach_relation_range(database, rel_id, node["id"])
        if not widened:
            widened.append(NodeRef(node_id=rel_id, label=RELATION,
                                   name=self._name_of(database, rel_id)))

    def _name_of(self, database: str, node_id: str) -> str:
        node = self._source.get_node(database, node_id)
        return (node or {}).get("properties", {}).get("name", "")

    def _fact_relation_id(self, database: str, fact_id: str) -> str | None:
        rel = fact_parts(self._source, database, fact_id)["relation"]
        return rel["id"] if rel is not None else None

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
