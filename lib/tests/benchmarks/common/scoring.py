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

from blue_data_kb_source._constants import DOMAIN, EXTRACTED_FROM, IS_INSTANCE_OF, RANGE

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
    the text is a real substring of the case's source material — catching
    fabricated/hallucinated provenance, which the LLM judge (which never sees
    raw graph state, only the extracted item lists) has no way to detect.
    """
    result = ProvenanceResult()
    normalized_source = source_text.lower()
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
        elif not any(t.lower() in normalized_source for t in texts):
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
    return {
        nb["node"].get("properties", {}).get("name", "")
        for nb in kb.get_neighbors(database, entity_id, edge_type=IS_INSTANCE_OF, direction="out")
    }


def _domain_range_names(kb: Any, database: str, relation_id: str, edge_type: str) -> set[str]:
    return {
        nb["node"].get("properties", {}).get("name", "")
        for nb in kb.get_neighbors(database, relation_id, edge_type=edge_type, direction="out")
    }


def check_domain_range_compliance(collector: CreationCollector, kb: Any, database: str) -> ComplianceResult:
    """For every fact the agent created, verify the head/tail entity's class is
    actually within the relation's DOMAIN/RANGE.

    v1 simplification, called out explicitly: exact class match only, not
    subclass-aware — a head entity whose class is a *subclass* of the
    relation's domain class would be flagged here even though it should
    satisfy the constraint. Left as a known limitation rather than
    implementing an IS_SUBCLASS_OF walk in this pass.
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


@dataclass
class CaseScore:
    case_id: str
    model: str
    schema_discovery_mode: str
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


async def score_case(
    case: Case,
    model: str,
    schema_discovery_mode: str,
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
    entity_matches = await judge_alignment(case.expected.entities, collector.entities, "entity", judge_client, judge_model)
    fact_matches = await judge_alignment(case.expected.facts, collector.facts, "fact", judge_client, judge_model)
    actual_properties = collector.entity_properties + collector.fact_properties
    prop_matches = await judge_alignment(
        case.expected.properties,
        actual_properties,
        "property value",
        judge_client,
        judge_model,
    )
    source_text = "\n".join(s.input_text for s in case.steps)
    return CaseScore(
        case_id=case.id,
        model=model,
        schema_discovery_mode=schema_discovery_mode,
        entities=_prf1(len(case.expected.entities), len(collector.entities), len(entity_matches)),
        facts=_prf1(len(case.expected.facts), len(collector.facts), len(fact_matches)),
        properties=_prf1(len(case.expected.properties), len(actual_properties), len(prop_matches)),
        schema_reuse=check_schema_reuse(case.expected.schema_reuse, collector),
        normalization=check_normalization(case.expected.properties, actual_properties, prop_matches, kb, database),
        provenance=check_provenance(collector, kb, database, source_text),
        domain_range_compliance=check_domain_range_compliance(collector, kb, database),
        tool_call_count=len(trace),
        error_count=sum(1 for t in trace if t.is_error),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_seconds=latency_seconds,
    )
