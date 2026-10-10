"""Scoring a benchmark run against a case's gold-standard `expected` block.

Two different techniques, on purpose:
  * entities/facts/properties are matched to the gold list via an LLM judge
    (`judge_alignment`) — semantic equivalence, not string equality, since
    extracted names can legitimately vary in wording from the gold label.
  * schema reuse/expansion, normalization, provenance, and domain/range
    compliance are checked deterministically, by reading the live graph
    directly, since these are exactly the failure modes an LLM judge is too
    lenient to catch — e.g. it will happily call "March 2005" and "2005-03"
    equivalent even if the agent never normalized the value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from blue_data_kb_source._constants import DOMAIN, EXTRACTED_FROM, IS_SUBCLASS_OF, RANGE
# The engine's own dispatch-time grounding predicate, reused verbatim rather
# than reimplemented: a scorer with its own idea of "verbatim" reports spans
# the engine accepted as fabricated. It did — a strict substring test flagged
# every entity quoted out of a newline-separated list block.
from blue_data_kb_source.extraction.engine import _span_is_grounded
from blue_data_kb_source.knowledge.helpers import entity_classes

from .cases import Case, SchemaReuseSpec
from .capture import CreationCollector, ToolCallRecord

_ALIGNMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "record_alignment",
        "description": (
            "Record which extracted items correspond to which expected (gold) items, "
            "matching by meaning rather than exact wording. Not every item on either "
            "side needs a match."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "matches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "expected_index": {"type": "integer"},
                            "actual_index": {"type": "integer"},
                        },
                        "required": ["expected_index", "actual_index"],
                    },
                    "description": "One entry per matched pair.",
                },
            },
            "required": ["matches"],
        },
    },
}

_JUDGE_SYSTEM_PROMPT = (
    "You score an information-extraction benchmark. You are given a gold-standard "
    "list of expected {kind} items and the list actually produced by the system "
    "under test. Match items that refer to the same real-world {kind} even if "
    "wording differs (e.g. different phrasing of a date, or a paraphrased fact). "
    "Do not force a match when items clearly differ. Call record_alignment once."
)


@dataclass
class Match:
    expected_index: int
    actual_index: int


async def judge_alignment(
    expected: list[dict], actual: list[dict], kind: str, client: Any, model: str
) -> list[Match]:
    """Ask an LLM to align *actual* items to *expected* (gold) items by meaning."""
    if not expected or not actual:
        return []

    expected_str = "\n".join(f"{i}: {json.dumps(e)}" for i, e in enumerate(expected))
    actual_str = "\n".join(f"{i}: {json.dumps(a)}" for i, a in enumerate(actual))
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT.format(kind=kind)},
            {"role": "user", "content": f"Expected:\n{expected_str}\n\nActual:\n{actual_str}"},
        ],
        tools=[_ALIGNMENT_TOOL],
        tool_choice={"type": "function", "function": {"name": "record_alignment"}},
    )
    tc = response.choices[0].message.tool_calls[0]
    raw_matches = json.loads(tc.function.arguments).get("matches", [])
    seen_expected: set[int] = set()
    seen_actual: set[int] = set()
    matches: list[Match] = []
    for m in raw_matches:
        ei, ai = m.get("expected_index"), m.get("actual_index")
        # The model isn't always well-behaved: `required` in the tool schema doesn't
        # stop it from emitting null or omitting a field, so treat anything other
        # than a plain in-range int as a malformed (skip) match rather than crashing.
        if not isinstance(ei, int) or not isinstance(ai, int):
            continue
        if ei in seen_expected or ai in seen_actual:
            continue
        if not (0 <= ei < len(expected)) or not (0 <= ai < len(actual)):
            continue
        seen_expected.add(ei)
        seen_actual.add(ai)
        matches.append(Match(expected_index=ei, actual_index=ai))
    return matches


@dataclass
class PRF1:
    precision: float
    recall: float
    f1: float
    matched: int
    expected_count: int
    actual_count: int


def _prf1(expected_count: int, actual_count: int, matched: int) -> PRF1:
    precision = matched / actual_count if actual_count else (1.0 if expected_count == 0 else 0.0)
    recall = matched / expected_count if expected_count else (1.0 if actual_count == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return PRF1(precision, recall, f1, matched, expected_count, actual_count)


@dataclass
class SchemaReuseResult:
    reused_ok: list[str] = field(default_factory=list)
    reused_missing: list[str] = field(default_factory=list)
    wrongly_declared: list[str] = field(default_factory=list)
    expanded_ok: list[str] = field(default_factory=list)
    expansion_missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reused_missing and not self.wrongly_declared and not self.expansion_missing


def _declared_names(collector: CreationCollector) -> set[str]:
    names: set[str] = set()
    for bucket in (
        collector.classes_declared,
        collector.relations_declared,
        collector.class_properties_declared,
        collector.relation_properties_declared,
    ):
        names.update(e.get("name", "") for e in bucket)
    return names


def check_schema_reuse(spec: SchemaReuseSpec, collector: CreationCollector) -> SchemaReuseResult:
    """Pure set comparison of newly-declared schema element names, no LLM."""
    declared = _declared_names(collector)
    result = SchemaReuseResult()
    for name in spec.must_reuse:
        (result.reused_missing if name in declared else result.reused_ok).append(name)
    for name in spec.must_not_declare:
        if name in declared:
            result.wrongly_declared.append(name)
    for name in spec.must_expand:
        (result.expanded_ok if name in declared else result.expansion_missing).append(name)
    return result


@dataclass
class NormalizationResult:
    ok_items: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def check_normalization(
    expected_properties: list[dict],
    actual_properties: list[dict],
    matches: list[Match],
    kb: Any,
    database: str,
) -> NormalizationResult:
    """For matched property pairs where the gold entry specifies a `literal_type`,
    verify the actual LITERAL node's stored `value`/`type` exactly match — the
    LLM judge's semantic alignment can't catch "extracted the right fact but
    never normalized the value" (e.g. stored "March 2005" instead of "2005-03").
    Properties without a `literal_type` in the gold entry are skipped (opt-in).
    """
    result = NormalizationResult()
    for m in matches:
        expected_entry = expected_properties[m.expected_index]
        if "literal_type" not in expected_entry:
            continue
        actual_entry = actual_properties[m.actual_index]
        node = kb.get_node(database, actual_entry["id"]) or {}
        props = node.get("properties", {})
        label = expected_entry.get("name", f"expected[{m.expected_index}]")
        value_ok = "value" not in expected_entry or props.get("value") == expected_entry["value"]
        type_ok = props.get("type") == expected_entry["literal_type"]
        if value_ok and type_ok:
            result.ok_items.append(label)
        else:
            result.mismatches.append(
                f"{label}: expected value={expected_entry.get('value')!r} "
                f"type={expected_entry['literal_type']!r}, got value={props.get('value')!r} "
                f"type={props.get('type')!r}"
            )
    return result


@dataclass
class ProvenanceResult:
    ok_items: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    fabricated: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.fabricated


def check_provenance(collector: CreationCollector, kb: Any, database: str, source_text: str) -> ProvenanceResult:
    """Every created entity/fact/property value should carry a verbatim source
    text span. This walks each item's EXTRACTED_FROM edges directly and checks
    the text really is quoted from the case's source material — catching
    fabricated/hallucinated provenance, which the LLM judge (which never sees
    raw graph state, only the extracted item lists) has no way to detect.

    "Quoted" means whatever `_span_is_grounded` means, since that is the rule
    the engine enforced when it accepted the write; scoring against a stricter
    rule measures the gap between two implementations, not the model.
    """
    result = ProvenanceResult()
    items: list[tuple[str, str]] = (
        [(e.get("name", e.get("id", "?")), e["id"]) for e in collector.entities]
        + [(f.get("name", f.get("id", "?")), f["id"]) for f in collector.facts]
        + [
            (p.get("property_name", p.get("id", "?")), p["id"])
            for p in collector.entity_properties + collector.fact_properties
        ]
    )
    for label, node_id in items:
        nbs = kb.get_neighbors(database, node_id, edge_type=EXTRACTED_FROM, direction="out")
        texts = [t for t in (nb["node"].get("properties", {}).get("text") for nb in nbs) if t]
        if not texts:
            result.missing.append(label)
        elif not any(_span_is_grounded(t, source_text) for t in texts):
            result.fabricated.append(label)
        else:
            result.ok_items.append(label)
    return result


@dataclass
class ComplianceResult:
    ok_items: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _class_names(kb: Any, database: str, entity_id: str) -> set[str]:
    """Names of the entity's classes and all their superclasses."""
    names: set[str] = set()
    seen: set[str] = set()
    todo = list(entity_classes(kb, database, entity_id))
    while todo:
        c = todo.pop()
        if c.get("id") in seen:
            continue
        seen.add(c.get("id"))
        names.add(c.get("properties", {}).get("name", ""))
        todo += [nb["node"] for nb in kb.get_neighbors(database, c["id"], edge_type=IS_SUBCLASS_OF, direction="out")]
    return names


def _domain_range_names(kb: Any, database: str, relation_id: str, edge_type: str) -> set[str]:
    return {
        nb["node"].get("properties", {}).get("name", "")
        for nb in kb.get_neighbors(database, relation_id, edge_type=edge_type, direction="out")
    }


def check_domain_range_compliance(collector: CreationCollector, kb: Any, database: str) -> ComplianceResult:
    """For every fact the agent created, verify the head/tail entity's class is
    actually within the relation's DOMAIN/RANGE — directly or through a
    superclass (a STARTUP ⊂ COMPANY head satisfies a COMPANY domain).
    """
    result = ComplianceResult()
    for f in collector.facts:
        label = f.get("name", f.get("id", "?"))
        head_id, tail_id, relation_id = f.get("head_entity_id"), f.get("tail_entity_id"), f.get("relation_id")
        if not (head_id and tail_id and relation_id):
            continue
        head_classes = _class_names(kb, database, head_id)
        tail_classes = _class_names(kb, database, tail_id)
        domain = _domain_range_names(kb, database, relation_id, DOMAIN)
        range_ = _domain_range_names(kb, database, relation_id, RANGE)
        violations = []
        if domain and not (head_classes & domain):
            violations.append(f"head class {sorted(head_classes)} not in domain {sorted(domain)}")
        if range_ and not (tail_classes & range_):
            violations.append(f"tail class {sorted(tail_classes)} not in range {sorted(range_)}")
        if violations:
            result.violations.append(f"{label}: " + "; ".join(violations))
        else:
            result.ok_items.append(label)
    return result


def _expected_label(kind: str, item: dict) -> str:
    if kind == "entity":
        return f"{item.get('name')} : {item.get('class', '?')}"
    if kind == "fact":
        return f"{item.get('head')} -{item.get('relation')}-> {item.get('tail')}"
    return f"{item.get('owner')}.{item.get('name')} = {item.get('value')!r}"


def _actual_label(kind: str, event: dict) -> str:
    if kind == "entity":
        return f"{event.get('name')} : {event.get('class_name') or '(untyped)'}"
    if kind == "fact":
        return f"{event.get('head_entity_name')} -{event.get('relation_name')}-> {event.get('tail_entity_name')}"
    if event.get("fact_id") and event.get("head_entity_name"):
        owner = f"({event['head_entity_name']} -{event.get('relation_name')}-> {event.get('tail_entity_name')})"
    else:
        owner = event.get("entity_name") or event.get("fact_name") or event.get("name") or "?"
    return f"{owner}.{event.get('property_name')} = {event.get('value')!r}"


@dataclass
class Alignment:
    """Which expected items were matched (and to what), which are missing, which produced
    items are extra — the per-item view behind one PRF1, for analysing a run (#134)."""

    matched: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    acceptable: list[tuple[str, str]] = field(default_factory=list)  # optional gold → extracted


def _alignment(
    kind: str, expected: list[dict], optional: list[dict], actual: list[dict], matches: list[Match]
) -> Alignment:
    """*matches* index into `expected + optional`; indices past `expected` are optional."""
    gold = expected + optional
    req = {m.expected_index: m.actual_index for m in matches if m.expected_index < len(expected)}
    opt = {m.expected_index: m.actual_index for m in matches if m.expected_index >= len(expected)}
    used = set(req.values()) | set(opt.values())
    return Alignment(
        matched=[(_expected_label(kind, gold[e]), _actual_label(kind, actual[a])) for e, a in sorted(req.items())],
        missing=[_expected_label(kind, e) for i, e in enumerate(expected) if i not in req],
        extra=[_actual_label(kind, a) for i, a in enumerate(actual) if i not in used],
        acceptable=[(_expected_label(kind, gold[e]), _actual_label(kind, actual[a])) for e, a in sorted(opt.items())],
    )


def _scored(kind: str, expected: list[dict], optional: list[dict], actual: list[dict], matches: list[Match]):
    """PRF1 and alignment, with optional matches dropped from both sides: they don't
    count toward recall, and the extracted items they matched aren't false positives."""
    al = _alignment(kind, expected, optional, actual, matches)
    return _prf1(len(expected), len(actual) - len(al.acceptable), len(al.matched)), al


@dataclass
class CaseScore:
    case_id: str
    model: str
    dedup_mode: str
    entities: PRF1
    facts: PRF1
    properties: PRF1
    schema_reuse: SchemaReuseResult
    normalization: NormalizationResult
    provenance: ProvenanceResult
    domain_range_compliance: ComplianceResult
    tool_call_count: int
    error_count: int
    prompt_tokens: int
    completion_tokens: int
    latency_seconds: float
    alignment: dict[str, Alignment] = field(default_factory=dict)
    category: str = "uncategorized"


async def score_case(
    case: Case,
    model: str,
    dedup_mode: str,
    collector: CreationCollector,
    trace: list[ToolCallRecord],
    kb: Any,
    database: str,
    judge_client: Any,
    judge_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_seconds: float,
) -> CaseScore:
    ex = case.expected
    entity_matches = await judge_alignment(ex.entities + ex.optional_entities, collector.entities, "entity", judge_client, judge_model)
    fact_matches = await judge_alignment(ex.facts + ex.optional_facts, collector.facts, "fact", judge_client, judge_model)
    actual_properties = collector.entity_properties + collector.fact_properties
    prop_matches = await judge_alignment(
        ex.properties + ex.optional_properties,
        actual_properties,
        "property value",
        judge_client,
        judge_model,
    )
    entities_prf1, entities_al = _scored("entity", ex.entities, ex.optional_entities, collector.entities, entity_matches)
    facts_prf1, facts_al = _scored("fact", ex.facts, ex.optional_facts, collector.facts, fact_matches)
    props_prf1, props_al = _scored("property", ex.properties, ex.optional_properties, actual_properties, prop_matches)
    # Normalization only judges required property matches.
    prop_matches = [m for m in prop_matches if m.expected_index < len(ex.properties)]
    source_text = "\n".join(s.input_text for s in case.steps)
    return CaseScore(
        case_id=case.id,
        model=model,
        dedup_mode=dedup_mode,
        entities=entities_prf1,
        facts=facts_prf1,
        properties=props_prf1,
        schema_reuse=check_schema_reuse(case.expected.schema_reuse, collector),
        normalization=check_normalization(case.expected.properties, actual_properties, prop_matches, kb, database),
        provenance=check_provenance(collector, kb, database, source_text),
        domain_range_compliance=check_domain_range_compliance(collector, kb, database),
        tool_call_count=len(trace),
        error_count=sum(1 for t in trace if t.is_error),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_seconds=latency_seconds,
        category=case.category,
        alignment={"entities": entities_al, "facts": facts_al, "properties": props_al},
    )
