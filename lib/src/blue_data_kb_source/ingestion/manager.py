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

from dataclasses import dataclass, field
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
    IngestFailure,
    IngestionError,
    IngestReport,
    KBError,
    NodeRef,
    ResolutionCandidate,
)

_IDENTITY_CERTAIN = ("exact", "alias", "structural")
_ExtractionType = Literal["text_span", "external_record", "assertion"]
_SchemaCreationMode = Literal["llm", "none"]
_EVERYTHING_DESC = "Catch-all class for entities created without a type"
# Stands for a class the same assert is about to create, where an id is expected.
_PENDING_CLASS = "__pending_class__"


class _Unresolved:
    """Marker: the entity hasn't been resolved yet (None means resolved to nothing)."""


@dataclass
class _ClassPlan:
    """A class an assert will use: an existing one (`id`), or one it will create."""
    id: str | None = None
    name: str = ""
    description: str = ""


@dataclass
class _EntityPlan:
    """What an assert will do for one entity, decided before anything is written:
    reuse `hit` (attaching `class_plan` if it needs a class) or create the entity
    in `class_plan`. Every judge an assert needs runs while building these (#126)."""
    name: str
    text: str
    description: str | None
    hit: ResolutionCandidate | None
    class_plan: _ClassPlan | None
    existing_class_ids: list[str] = field(default_factory=list)


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
        model: str | None = None,
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
        model: str | None = None,
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
        model: str | None = None,
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
        model: str | None = None,
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
        model: str | None = None,
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
        model: str | None = None,
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
        model: str | None = None,
    ) -> AssertResult:
        # Phase 1: resolve and run every judge; phase 2: write (#126).
        plan = await self._plan_entity(database, name, schema_creation_mode, model,
                                       class_name=class_name, description=description)
        entity_id, created, extraction_id = await self._write_entity(
            database, plan, source_id, confidence, extraction_type, {}
        )
        if plan.hit is not None:
            return AssertResult(node_id=entity_id, created=False, match_type=plan.hit.match_type,
                                created_nodes=created)
        return AssertResult(node_id=entity_id, created=True,
                            extraction_id=extraction_id, created_nodes=created)

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
        model: str | None = None,
    ) -> AssertResult:
        text = f"{head} {relation} {tail}"
        rel_hit = self._pick(await self._resolver.resolve_relation(database, relation),
                             f"relation {relation!r}")
        expectations: dict[str, set[str]] = {"head": set(), "tail": set()}
        if rel_hit is not None:
            dr = await self._schema.get_relation_domain_range(database, rel_hit.node_id)
            expectations["head"] = {c["id"] for c in dr["domain"]}
            expectations["tail"] = {c["id"] for c in dr["range"]}
        hits = {
            role: await self._pick_entity(await self._resolver.resolve_entity(database, ename),
                                          f"entity {ename!r}", database, expectations[role])
            for role, ename in (("head", head), ("tail", tail))
        }

        # Structural dedupe (decision 14): only checkable when all operands pre-exist.
        if rel_hit is not None and all(h is not None for h in hits.values()):
            fact = find_fact(self._source, database, hits["head"].node_id,
                             rel_hit.node_id, hits["tail"].node_id)
            if fact is not None:
                ref = await self._evidence(database, fact["id"], FACT, text, text,
                                           source_id, confidence, extraction_type)
                return AssertResult(node_id=fact["id"], created=False, match_type="structural",
                                    extraction_id=ref.extraction_id)

        # Phase 1, continued: every judge runs before the first write — the class for
        # each new or untyped entity, and the new relation's description. Judging the
        # relation after writing the entities left them behind when it failed (#126).
        plans = {
            role: await self._plan_entity(database, ename, schema_creation_mode, model,
                                          hit=hits[role], text=text)
            for role, ename in (("head", head), ("tail", tail))
        }
        rel_desc = (await self._describe("relation", relation, text, None, schema_creation_mode, model)
                    if rel_hit is None else None)

        # Phase 2.
        created: list[NodeRef] = []
        widened: list[NodeRef] = []
        made: dict[str, str] = {}
        entity_ids: dict[str, str] = {}
        role_class: dict[str, str] = {}
        for role in ("head", "tail"):
            entity_id, refs, _ = await self._write_entity(
                database, plans[role], source_id, confidence, extraction_type, made
            )
            created.extend(refs)
            entity_ids[role] = entity_id
            role_class[role] = entity_classes(self._source, database, entity_id)[0] \
                .get("properties", {}).get("name", "")

        if rel_hit is None:
            rel_id = await self._schema.declare_relation_type(
                database, relation, rel_desc or "", domain=role_class["head"], range=role_class["tail"]
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
        model: str | None = None,
    ) -> AssertResult:
        text = f"{entity} {property} {value}"
        # Phase 1: the entity's plan, then — if the property is new to its classes —
        # the property's description and which classes declare it (#126).
        plan = await self._plan_entity(database, entity, schema_creation_mode, model)
        known = set(plan.existing_class_ids)
        if plan.class_plan is not None and plan.class_plan.id is not None:
            known.add(plan.class_plan.id)
        prop_id = await self._owned_property_of_classes(database, known, property)
        owners: list[str] = []
        if prop_id is None:
            prop_desc = await self._describe("property", property, text, None,
                                             schema_creation_mode, model)
            pending = plan.class_plan if plan.class_plan is not None and plan.class_plan.id is None else None
            owners = await self._placement_classes(database, sorted(known), pending, property,
                                                   schema_creation_mode, model)

        # Phase 2.
        made: dict[str, str] = {}
        entity_id, created, _ = await self._write_entity(
            database, plan, source_id, confidence, extraction_type, made
        )
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
            owner_ids = [made[plan.class_plan.name] if o == _PENDING_CLASS else o for o in owners]
            prop_id = await self._schema.declare_class_property(
                database, property, prop_desc, class_id=owner_ids[0]
            )
            for class_id in owner_ids[1:]:
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
        model: str | None = None,
    ) -> AssertResult:
        text = f"{head} {relation} {tail}: {property} {value}"
        # A property new to the relation is described before assert_fact writes
        # anything, so a failing judge leaves the fact unwritten too (#126).
        rel_hit = self._pick(await self._resolver.resolve_relation(database, relation),
                             f"relation {relation!r}")
        prop_desc: str | None = None
        if rel_hit is None or self._own_property(database, rel_hit.node_id, property) is None:
            prop_desc = await self._describe("property", property, text, None,
                                             schema_creation_mode, model)
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
            prop_id = await self._schema.declare_relation_property(
                database, property, prop_desc or "", relation_id=rel_id
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
    # Bulk ingest                                                          #
    # ------------------------------------------------------------------ #

    async def ingest(
        self,
        database: str,
        payload: dict,
        *,
        source_id: str | None = None,
        confidence: float | None = None,
        extraction_type: _ExtractionType = "assertion",
        schema_creation_mode: _SchemaCreationMode = "llm",
        model: str | None = None,
    ) -> IngestReport:
        """Run every payload row through its per-item assert, collecting a report.

        Arms are processed in the payload's own order (decision 27); each row is
        the assert's keyword arguments, and may carry its own `confidence` or
        `extraction_type` to override the run-level default (decision 30). Row
        failures are collected, never raised — `KBError`s with their candidates,
        and `TypeError`s from malformed row keys; an unknown arm key raises
        before any row is processed.
        """
        arms = {
            "classes": self.assert_class,
            "relations": self.assert_relation_type,
            "subclasses": self.assert_subclass,
            "subrelations": self.assert_subrelation,
            "class_properties": self.assert_class_property,
            "relation_properties": self.assert_relation_property,
            "entities": self.assert_entity,
            "facts": self.assert_fact,
            "entity_properties": self.assert_entity_property,
            "fact_properties": self.assert_fact_property,
        }
        unknown = [k for k in payload if k not in arms]
        if unknown:
            raise IngestionError(
                f"unknown payload arm(s): {', '.join(sorted(unknown))}. "
                f"Valid arms: {', '.join(arms)}. Nothing was written."
            )
        report = IngestReport()
        for arm, rows in payload.items():
            for index, row in enumerate(rows):
                kwargs = dict(row)
                kwargs.setdefault("confidence", confidence)
                kwargs.setdefault("extraction_type", extraction_type)
                try:
                    result = await arms[arm](
                        database,
                        schema_creation_mode=schema_creation_mode, model=model,
                        source_id=source_id, **kwargs,
                    )
                except (KBError, TypeError) as exc:
                    report.failures.append(IngestFailure(
                        arm=arm, index=index, reason=str(exc),
                        candidates=list(getattr(exc, "candidates", [])),
                    ))
                    continue
                if result.created:
                    report.written += 1
                elif result.match_type == "structural":
                    report.deduped += 1
                else:
                    report.skipped += 1
                for ref in result.created_nodes:
                    report.created[ref.label] = report.created.get(ref.label, 0) + 1
                report.widened.extend(w.name for w in result.widened_relations)
        return report

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

    async def _plan_class(
        self,
        class_name: str,
        text: str,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
        class_hit: ResolutionCandidate | None,
    ) -> _ClassPlan:
        """The resolved class, or the class to create with its judge-3 description."""
        if class_hit is not None:
            return _ClassPlan(id=class_hit.node_id, name=class_hit.name)
        desc = await self._describe("class", class_name, text, None, schema_creation_mode, model)
        return _ClassPlan(name=class_name, description=desc)

    async def _plan_membership_class(
        self,
        database: str,
        entity_name: str,
        text: str,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
    ) -> _ClassPlan:
        """A class for an untyped entity (decision 29): judge 2 under "llm",
        the EVERYTHING catch-all under "none" (decision 19)."""
        if schema_creation_mode != "llm":
            existing = await self._schema.find_class(database, "EVERYTHING")
            if existing is not None:
                return _ClassPlan(id=existing["id"], name="EVERYTHING")
            return _ClassPlan(name="EVERYTHING", description=_EVERYTHING_DESC)
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
        return await self._plan_class(chosen, text, schema_creation_mode, model, hit)

    async def _write_class(
        self,
        database: str,
        plan: _ClassPlan,
        text: str,
        source_id: str | None,
        confidence: float | None,
        extraction_type: _ExtractionType,
        made: dict[str, str],
    ) -> tuple[str, list[NodeRef]]:
        """The planned class's id, creating it — once per assert, via *made* — if new."""
        if plan.id is not None:
            return plan.id, []
        if plan.name in made:
            return made[plan.name], []
        ref = await self._create_class(database, plan.name, plan.description, text,
                                       source_id, confidence, extraction_type, made)
        return ref.node_id, [ref]

    async def _plan_entity(
        self,
        database: str,
        name: str,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
        *,
        class_name: str | None = None,
        description: str | None = None,
        hit: ResolutionCandidate | None | type[_Unresolved] = _Unresolved,
        text: str | None = None,
    ) -> _EntityPlan:
        """Phase 1 for one entity: resolve it (unless *hit* is given) and judge the
        class it needs — to be created with, or attached to an existing untyped or
        wrongly typed entity (decision 29). Writes nothing."""
        text = text or (f"{name} is_a {class_name}" if class_name else name)
        expected: set[str] = set()
        class_hit = None
        if class_name is not None:
            class_hit = self._pick(await self._resolver.resolve_class(database, class_name),
                                   f"class {class_name!r}")
            if class_hit is not None:
                expected = {class_hit.node_id}
        if hit is _Unresolved:
            hit = await self._pick_entity(await self._resolver.resolve_entity(database, name),
                                          f"entity {name!r}", database, expected)
        if hit is None:
            class_plan = (await self._plan_class(class_name, text, schema_creation_mode, model, class_hit)
                          if class_name is not None else
                          await self._plan_membership_class(database, name, text, schema_creation_mode, model))
            return _EntityPlan(name=name, text=text, description=description, hit=None,
                               class_plan=class_plan)
        have = [c["id"] for c in entity_classes(self._source, database, hit.node_id)]
        overlap = ((await self._class_closure(database, set(have))) & expected
                   if expected else None)
        class_plan = None
        if class_name is not None and not overlap:
            class_plan = await self._plan_class(class_name, text, schema_creation_mode, model, class_hit)
        elif not have:
            class_plan = await self._plan_membership_class(database, name, text, schema_creation_mode, model)
        return _EntityPlan(name=name, text=text, description=description, hit=hit,
                           class_plan=class_plan, existing_class_ids=have)

    async def _write_entity(
        self,
        database: str,
        plan: _EntityPlan,
        source_id: str | None,
        confidence: float | None,
        extraction_type: _ExtractionType,
        made: dict[str, str],
    ) -> tuple[str, list[NodeRef], str | None]:
        """Phase 2 for one entity: (entity id, nodes created, the new entity's extraction id)."""
        created: list[NodeRef] = []
        class_id = None
        if plan.class_plan is not None:
            class_id, refs = await self._write_class(database, plan.class_plan, plan.text,
                                                     source_id, confidence, extraction_type, made)
            created.extend(refs)
        if plan.hit is not None:
            if class_id is not None and class_id not in plan.existing_class_ids:
                await self._knowledge.attach_entity_class(database, plan.hit.node_id, class_id)
            return plan.hit.node_id, created, None
        entity_id = await self._knowledge.add_entity(
            database, plan.name, description=plan.description, class_id=class_id
        )
        ref = await self._evidence(database, entity_id, ENTITY, plan.name, plan.text,
                                   source_id, confidence, extraction_type)
        created.append(ref)
        return entity_id, created, ref.extraction_id

    async def _placement_classes(
        self,
        database: str,
        class_ids: list[str],
        pending: _ClassPlan | None,
        property_name: str,
        schema_creation_mode: _SchemaCreationMode,
        model: str,
    ) -> list[str]:
        """Class ids that should declare a new property (decision 15): judge 1 picks
        from the entity's classes and ancestors under "llm"; under "none" the
        entity's direct classes (deterministic fallback, plan row 39d). *pending* is
        a class the same assert will create, offered as _PENDING_CLASS."""
        direct_ids = list(class_ids) + ([_PENDING_CLASS] if pending is not None else [])
        if schema_creation_mode != "llm" or direct_ids == [_PENDING_CLASS]:
            return direct_ids
        offered: dict[str, dict] = {}
        for cid in class_ids:
            node = self._source.get_node(database, cid)
            if node is not None:
                offered[cid] = node
        for cid in (await self._class_closure(database, set(class_ids))) - set(class_ids):
            node = self._source.get_node(database, cid)
            if node is not None:
                offered[cid] = node
        classes = [
            {"id": cid, "name": n.get("properties", {}).get("name", ""),
             "description": n.get("properties", {}).get("description") or ""}
            for cid, n in offered.items()
        ]
        if pending is not None:
            classes.append({"id": _PENDING_CLASS, "name": pending.name, "description": pending.description})
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

    async def _owned_property_of_classes(
        self, database: str, class_ids: set[str], name: str
    ) -> str | None:
        """A PROPERTY named *name* defined on any of these classes or their ancestors."""
        for cid in await self._class_closure(database, class_ids):
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
