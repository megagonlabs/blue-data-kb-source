from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._agent_tools import parse_tool_arguments, tool_error, tool_message_content
from blue_data_kb_source._constants import (
    CLASS,
    DEFINE_PROPERTY,
    ENTITY,
    FACT,
    HAS_PROPERTY,
    IS_VALUE_OF,
    LITERAL,
    PROPERTY,
    RELATION,
)
from blue_data_kb_source.knowledge.helpers import (
    cast_literal_value,
    entity_classes,
    normalize_date,
    fact_parts,
    node_name,
    property_values,
)
from blue_data_kb_source.extraction.handles import Handles
from blue_data_kb_source.resolution.resolver import NodeResolver
from blue_data_kb_source.types import DataSearchResult, ExtractionError
from blue_data_kb_source import llm

if TYPE_CHECKING:
    from blue_data_kb_source.knowledge.manager import KnowledgeManager
    from blue_data_kb_source.provenance.tracker import ProvenanceTracker
    from blue_data_kb_source.schema.manager import SchemaManager
    from blue_data_kb_source.search.indexer import KBSearchIndexer

_TOOLS: list[dict[str, Any]] = [
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
                "properties": {"name": {"type": "string"}},
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
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_entity",
            "description": (
                "Look up an ENTITY node by name. Names aren't unique — \"Apple\" the company and "
                "\"Apple\" the fruit are different entities — so pass class_id when you know which "
                "kind you mean; only an entity of that class (or a parent/child class) matches. "
                "Returns {id, name, description, classes, other_matches} or {id: null}; "
                "other_matches lists same-named entities of other classes, which are NOT this one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "class_id": {"type": "string", "description": "id of the CLASS the mention belongs to, as returned by find_class or search_schema — never a name (optional)"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_fact",
            "description": (
                "Look up an existing FACT by its head entity, relation, and tail entity. "
                "Returns {id, name} or {id: null}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "head_entity_id": {"type": "string", "description": "id of an existing ENTITY node, as returned by find_entity — never a name"},
                    "relation_id": {"type": "string", "description": "id of an existing RELATION node, as returned by find_relation_type or search_schema — never a name"},
                    "tail_entity_id": {"type": "string", "description": "id of an existing ENTITY node, as returned by find_entity — never a name"},
                },
                "required": ["head_entity_id", "relation_id", "tail_entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_literal",
            "description": (
                "Look up the property value an ENTITY or FACT already carries for a "
                "given PROPERTY. Call this before add_entity_property_value / "
                "add_fact_property_value so an existing value is re-linked rather "
                "than duplicated. Returns {id, value} or {id: null}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_id": {"type": "string", "description": "id of the ENTITY or FACT node the value belongs to — never a name"},
                    "property_id": {"type": "string", "description": "id of an existing PROPERTY node, as returned by search_schema — never a name"},
                },
                "required": ["owner_id", "property_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_schema",
            "description": (
                "Search existing schema elements (classes, relations, properties) for one that "
                "matches a given concept — even under a different name. Use this to find the "
                "CLASS/RELATION/PROPERTY that a mention in the text corresponds to. Returns "
                "{schema_elements: [{id, type, name, description}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Short description of the concept to look for"},
                    "mode":  {"type": "string", "enum": ["keyword", "semantic"], "description": "Search mode (default: keyword)"},
                    "type":  {"type": "string", "enum": ["class", "relation", "property"], "description": "Restrict to a schema element type (optional)"},
                },
                "required": ["query"],
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
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "class_id": {"type": "string", "description": "id of an existing CLASS node, as returned by find_class or search_schema — never a name"},
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare name) this entity was extracted from"},
                },
                "required": ["name", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach_entity_class",
            "description": (
                "Record that an ENTITY that already exists is also an instance of another "
                "CLASS (idempotent) — e.g. \"Alice ... as a staff engineer\" makes the existing "
                "PERSON Alice an ENGINEER too. Use this instead of add_entity for a descriptive "
                "phrase that refers to an entity already found. Returns {}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "id of an existing ENTITY node, as returned by find_entity or add_entity — never a name"},
                    "class_id": {"type": "string", "description": "id of an existing CLASS node, as returned by find_class or search_schema — never a name"},
                },
                "required": ["entity_id", "class_id"],
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
                    "head_entity_id": {"type": "string", "description": "id of an existing ENTITY node, as returned by find_entity — never a name"},
                    "tail_entity_id": {"type": "string", "description": "id of an existing ENTITY node, as returned by find_entity — never a name"},
                    "relation_id": {"type": "string", "description": "id of an existing RELATION node, as returned by find_relation_type or search_schema — never a name"},
                    "name": {"type": "string"},
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare relation phrase) this fact was extracted from"},
                },
                "required": ["head_entity_id", "tail_entity_id", "relation_id", "text"],
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
                    "entity_id": {"type": "string", "description": "id of an existing ENTITY node, as returned by find_entity — never a name"},
                    "property_id": {"type": "string", "description": "id of an existing PROPERTY node, as returned by search_schema — never a name"},
                    "value": {"type": "string"},
                    "literal_type": {
                        "type": "string",
                        "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                    },
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare value) this value was extracted from"},
                },
                "required": ["entity_id", "property_id", "value", "text"],
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
                    "fact_id": {"type": "string", "description": "id of an existing FACT node, as returned by find_fact or add_fact — never a name"},
                    "property_id": {"type": "string", "description": "id of an existing PROPERTY node, as returned by search_schema — never a name"},
                    "value": {"type": "string"},
                    "literal_type": {
                        "type": "string",
                        "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                    },
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare value) this value was extracted from"},
                },
                "required": ["fact_id", "property_id", "value", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_extraction",
            "description": (
                "Link a new text span to an ENTITY, FACT, or LITERAL that already "
                "exists (found via find_entity/find_fact). Use this instead of "
                "add_entity/add_fact/add_*_property_value when the item was already "
                "found to exist, so new text spans accumulate as additional "
                "EXTRACTED_FROM links rather than creating a duplicate. Returns {id} "
                "of the new extraction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "string", "description": "id of the existing entity, fact, or literal"},
                    "text": {"type": "string", "description": "The full verbatim sentence this was found in"},
                },
                "required": ["element_id", "text"],
            },
        },
    },
]

_SYSTEM_PROMPT_TEMPLATE = """\
You are a knowledge base (KB) extraction agent.

The KB stores:
  CLASS     — category of entities (e.g. PERSON, COMPANY, SKILL)
  RELATION  — type of relationship (e.g. WORKED_AT, LIVES_IN)
  PROPERTY  — attribute of a class or relation (e.g. AGE, START_DATE)
  ENTITY    — a specific instance of a class (e.g. Alice, IBM, Python)
  FACT      — a specific relation between two entities
  LITERAL   — a concrete property value (e.g. "28", "1998-06-01")

An ENTITY is an instance of *any* class in the schema, not just a named thing.
Plenty of classes have instances that are ordinary nouns or noun phrases —
`Python` is an instance of `SKILL` exactly as `IBM` is an instance of
`COMPANY`. Never limit yourself to proper nouns.

This pass only extracts against the schema as it currently stands — it never
declares new CLASSes, RELATIONs, or PROPERTIES. If nothing in the current schema
covers something in the text (use search_schema to check), skip that item rather
than inventing a schema element for it.

Fit, not nearest. A mention belongs to a CLASS only if it genuinely is a member of
that class as the class's name and description define it, and a relationship
belongs to a RELATION only if the text states that same relationship. When the
closest element in the schema is a *different* kind of thing, it does not cover
the mention — skip it rather than coerce it into the nearest fit:
  - a university, school, agency, or team is not a COMPANY described as a business
  - "completed a PhD at", "studied at", or "holds a degree from" is not WORKED_AT
  - a job title or role ("staff engineer", "director") is not itself a PERSON or
    an instance of a class of people — the person holding it is
A skipped mention costs nothing; a coerced one writes a false statement into the
KB that its provenance makes look sourced.
{pass_note}
{workflow}

When calling add_entity, add_fact, add_entity_property_value,
add_fact_property_value, or link_extraction, always pass the full sentence
(verbatim, exactly as written) that the item was extracted from as the
`text` argument — never just the bare name, relation phrase, or value on its
own. A short span like "Alice" or "1998-06" gives a reader no way to verify
the extraction later; the surrounding sentence does. If the item depends on
two adjacent sentences, include the verbatim span covering both. Reusing an
existing item via link_extraction records the new text as an additional link
without creating a duplicate.

Source of this text: {source}

Current schema:
{schema}

Potentially related existing data (found by searching the KB with this text;
these are hints from a keyword/semantic match, not confirmed — always verify
with find_entity/find_fact before reusing one, exactly as you would for any
other candidate):
{related_data}
{prior_extraction}
Extraction queries:
{queries}

Work through the text systematically and stop calling tools when you are done.\
"""

# ------------------------------------------------------------------ #
# Staged extraction — entities, then relations, then property values.  #
# Each stage is its own agentic pass over a narrower tool set, told   #
# what the prior stage(s) already resolved rather than rediscovering  #
# it — mirroring the existing schema-expansion-then-extraction split. #
# ------------------------------------------------------------------ #

STAGE_ENTITIES = "entities"
# Recovery sweep after STAGE_ENTITIES, run only for classes that came back with
# no instances at all. Same tools as the entity stage; a narrower prompt.
STAGE_ENTITY_GAPS = "entity_gaps"
STAGE_RELATIONS = "relations"
STAGE_ENTITY_PROPERTIES = "entity_properties"
STAGE_FACT_PROPERTIES = "fact_properties"

# Fact properties run *before* entity properties: both stages re-scan the same
# text for attribute values, and where a PROPERTY is declared on both a class
# and a relation the first stage to claim a value keeps it. The fact is the
# more specific owner (a START_DATE belongs to the employment, not the person),
# so it gets first claim, and the entity stage is shown what it took.
STAGES = (
    STAGE_ENTITIES, STAGE_ENTITY_GAPS,
    STAGE_RELATIONS, STAGE_FACT_PROPERTIES, STAGE_ENTITY_PROPERTIES,
)

# ------------------------------------------------------------------ #
# Effort — how many times the whole staged pipeline runs over the     #
# same text. Later passes are told a prior pass already ran, so they  #
# hunt for what got missed rather than re-deriving what's there —     #
# re-finding the same items is harmless (find_entity/find_fact/       #
# link_extraction already dedupe), just wasted turns.                 #
# ------------------------------------------------------------------ #

EFFORT_PASSES: dict[str, int] = {"low": 1, "medium": 2, "high": 3}

_FIRST_PASS_NOTE_TEMPLATE = (
    "This is extraction pass {pass_num} of {total_passes} over the same text — "
    "further passes will follow to catch anything this one misses, so extract "
    "thoroughly but don't worry about being exhaustive.\n"
)
_LATER_PASS_NOTE_TEMPLATE = (
    "This is extraction pass {pass_num} of {total_passes} over the same text — "
    "prior passes already extracted what they could from it. Focus on anything "
    "subtle, indirect, or easy to miss that hasn't been captured yet, rather "
    "than re-deriving what's already there.\n"
)

_STAGE_TOOL_NAMES: dict[str, list[str]] = {
    STAGE_ENTITIES: ["get_schema", "search_schema", "find_class", "find_entity", "add_entity", "attach_entity_class", "link_extraction"],
    STAGE_ENTITY_GAPS: ["get_schema", "search_schema", "find_class", "find_entity", "add_entity", "attach_entity_class", "link_extraction"],
    STAGE_RELATIONS: ["get_schema", "search_schema", "find_relation_type", "find_fact", "add_fact", "link_extraction"],
    STAGE_ENTITY_PROPERTIES: ["get_schema", "search_schema", "find_literal", "add_entity_property_value", "link_extraction"],
    STAGE_FACT_PROPERTIES: ["get_schema", "search_schema", "find_literal", "add_fact_property_value", "link_extraction"],
}

_STAGE_WORKFLOWS: dict[str, str] = {
    STAGE_ENTITIES: """\
Stage: entity extraction. Only entities exist yet — relations and property
values are handled in later stages, so ignore them here.

Work through the schema class by class, not through the text name by name.
Scanning for names finds only proper nouns and silently drops every class
whose instances are ordinary words — skills, languages, tools, materials,
conditions. A bare list item, a comma-separated run, or a noun buried in a
sentence is an entity whenever a class covers it.

This is the complete list of classes; account for every one of them:

{classes}

For each class above, find every mention in the text that is an instance of
it — one that really is a member of the class as its description defines it
(see "Fit, not nearest" above). Being the closest class on the list is not
enough: a mention whose kind no class covers is skipped, not filed under the
nearest one. For each mention: call find_entity first. If it already exists, call
link_extraction with its id and the new text span instead of add_entity.
Otherwise call add_entity, passing that class's id from the list above. Use
names exactly as they appear in the text.

A descriptive phrase that refers back to an entity already named in the text —
"Alice ... as a staff engineer", "Bob Chen, a director" — is not a separate
entity: it describes that one. Never add the phrase itself ("staff engineer",
"director") as an instance of its own. If the phrase puts that entity in
another class on the list (a staff engineer is an ENGINEER), call
attach_entity_class with the entity's id and that class's id instead.

You do not need search_schema to find a class on that list — it is already
complete. Use search_schema only to check some *other* concept in the text
that none of the listed classes seems to cover; if nothing matches, skip that
mention.

When you have finished, report each class on the list with the instances you
added or linked for it, or "none". Then list the mentions you skipped because
no class on the list covers their kind (e.g. "Stanford University — a
university; no class for educational institutions"), or "none".\
""",
    STAGE_ENTITY_GAPS: """\
Stage: entity gap recovery. An entity pass over this text already ran and
resolved these entities:

{entities}

It produced no instances at all for the classes below. Often that is correct —
the text may genuinely contain none of them. But it is also exactly how a
class whose instances are ordinary nouns rather than names gets skipped, so
re-read the text once more for these classes and nothing else:

{missing_classes}

For each class above, find any instance the earlier pass missed — a genuine
member of the class as its description defines it, never a mention of some
other kind that merely resembles it (see "Fit, not nearest" above). Call
find_entity first; if it already exists, call link_extraction with its id and
the new text span, otherwise call add_entity with that class's id from the
list above. When the instance is an entity listed above under another class
(the text calls Alice a staff engineer, and ENGINEER is missing), call
attach_entity_class for it rather than adding a new entity. Add nothing for a class the text genuinely does not mention, and
do not revisit classes that are not on the list above.\
""",
    STAGE_RELATIONS: """\
Stage: relation extraction. Entities were already resolved in a prior stage —
never call find_entity or add_entity here, and never invent an entity that
isn't in the list below; if a relationship involves one, skip it.

Entities already resolved for this text (id, name, class):
{entities}

For each relationship the text states between two of the entities above: find
the RELATION type via search_schema. It matches only if its name and
description state the same relationship the text does — not a neighbouring one
(studying or earning a degree somewhere is not working there; being managed by
someone is not working at them). If no RELATION states it, skip the
relationship; never reuse the nearest available one. Otherwise call find_fact using the ids above as
head_entity_id/tail_entity_id. If the fact already exists, call
link_extraction with its id and the new text span instead of add_fact.
Otherwise call add_fact.

This stage records relationships between two of the entities above, nothing
else. A value — a date, an amount, a title, a percentage — is not a
relationship, even when the schema has a PROPERTY for it: never pass a PROPERTY
(P...) as relation_id. The property stages that follow record values; leave them.\
""",
    STAGE_ENTITY_PROPERTIES: """\
Stage: entity property extraction. Entities and facts were both already
resolved in prior stages — never invent an entity that isn't in the list
below.

Entities already resolved for this text (id, name, class):
{entities}

Facts already resolved for this text (id, head, relation, tail):
{facts}

Property values the fact stage already attached to those facts:
{fact_properties}

This stage records values about the *entities* above, nothing else: entity_id
is always an entity (E...), never a fact (F...). Values about the facts were
recorded in the previous stage.

For each attribute value the text states about one of the *entities* above:
find the PROPERTY on its class via search_schema. If no matching PROPERTY
exists, skip this value. Otherwise call find_literal with that entity_id and
property_id — if the entity already carries a value for that property, call
link_extraction with the returned id and the new text span instead of adding
a second one. Only call add_entity_property_value when find_literal returns
{{id: null}}.

Two limits on what counts. First, a value that describes a relationship
rather than an entity has already been handled — the list above shows what
the fact stage took, and none of it should be re-attached here. Second, only
extract a value the text actually states about the entity itself: never infer
one from world knowledge, from the entity's name, or from what is typical. If
the text does not state it, skip it.

A PROPERTY holds one kind of value: its description says what, and a typed
property says which type (literal_type NUMBER, DATE, BOOLEAN or STRING; get_schema
shows it as number/date/boolean/string). Attach a value only when it is that kind
of thing — a job title is not an AGE, a role is not an INDUSTRY. If the text gives
no value of that kind, skip the property rather than fill it with the nearest thing.
Give a NUMBER as a plain number ("twelve hundred" → 1200, "$42.5 million" →
42500000) and a DATE as an ISO date ("June 1998" → 1998-06); a value that doesn't
fit its property's type is refused.\
""",
    STAGE_FACT_PROPERTIES: """\
Stage: fact property extraction. Entities and facts were both already
resolved in prior stages — never invent one that isn't in the lists below.

Entities already resolved for this text (id, name, class):
{entities}

Facts already resolved for this text (id, head, relation, tail):
{facts}

This stage records values about the *facts* above, nothing else: fact_id is
always one of those facts (F...), never an entity (E...). A value about an
entity on its own is recorded in the next stage — skip it here.

For each attribute value the text states about one of the *facts* above:
find the PROPERTY on its relation via search_schema. If no matching PROPERTY
exists, skip this value. Otherwise call find_literal with that fact_id and
property_id — if the fact already carries a value for that property, call
link_extraction with the returned id and the new text span instead of adding
a second one. Only call add_fact_property_value when find_literal returns
{{id: null}}.

A value that describes the relationship belongs here — when it began, how
long it ran, what it was worth, what role it was held under. A value that
describes one of the entities on its own — an age, a nationality, a founding
year — does not; leave it, the next stage attaches those. The entity list is
here to help you tell the two apart, not to attach values to.

A PROPERTY holds one kind of value: its description says what, and a typed
property says which type (literal_type NUMBER, DATE, BOOLEAN or STRING; get_schema
shows it as number/date/boolean/string). Attach a value only when it is that kind
of thing — a job title is not an AGE, a role is not an INDUSTRY. If the text gives
no value of that kind, skip the property rather than fill it with the nearest thing.
Give a NUMBER as a plain number ("twelve hundred" → 1200, "$42.5 million" →
42500000) and a DATE as an ISO date ("June 1998" → 1998-06); a value that doesn't
fit its property's type is refused.\
""",
}


def _tools_for(stage: str) -> list[dict[str, Any]]:
    names = set(_STAGE_TOOL_NAMES[stage])
    return [t for t in _TOOLS if t["function"]["name"] in names]


def _render_class_checklist(classes: list[dict[str, Any]]) -> str:
    if not classes:
        return "(no classes in the schema — there is nothing to extract in this stage)"
    lines = []
    for c in classes:
        suffix = f" — {c['description']}" if c.get("description") else ""
        lines.append(f"- id={c['id']}  {c['name']}{suffix}")
    return "\n".join(lines)


def _missing_classes(
    classes: list[dict[str, Any]], entities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Classes the entity stage produced no instance for — the gap stage's worklist."""
    covered = {name for e in entities for name in e.get("class_names") or ()}
    return [c for c in classes if c["name"] not in covered]


def _merge_entities(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """`first` plus whatever `second` added that isn't already in it, by id."""
    seen = {e["id"] for e in first}
    return first + [e for e in second if e["id"] not in seen]


def _render_resolved_entities(entities: list[dict[str, Any]]) -> str:
    if not entities:
        return "(none resolved in this pass)"
    lines = []
    for e in entities:
        names = e.get("class_names") or ()
        suffix = f" ({', '.join(names)})" if names else ""
        lines.append(f"- id={e['id']}  {e['name']}{suffix}")
    return "\n".join(lines)


def _endpoint(name: str, class_name: str | None, node_id: str) -> str:
    qualifier = f"{class_name}, id={node_id}" if class_name else f"id={node_id}"
    return f"{name} ({qualifier})"


def _render_resolved_facts(facts: list[dict[str, Any]]) -> str:
    """Facts with each endpoint's class and id, so a stage given both this and
    the entity list can line the two up without matching on bare names."""
    if not facts:
        return "(none resolved in this pass)"
    lines = []
    for f in facts:
        head = _endpoint(f["head_name"], f.get("head_class"), f["head_id"])
        tail = _endpoint(f["tail_name"], f.get("tail_class"), f["tail_id"])
        lines.append(f"- id={f['id']}  {head} —{f['relation_name']}→ {tail}")
    return "\n".join(lines)


def _render_property_values(values: list[dict[str, Any]]) -> str:
    if not values:
        return "(none attached in this pass)"
    return "\n".join(
        f"- {v['owner_label']}.{v['property_name']} = {v['value']}" for v in values
    )


@dataclass
class _ExtractionContext:
    """What the pipeline has resolved so far — accumulated across the stages of
    a pass and, when `effort` runs more than one, across the passes of a chunk.

    Each chunk owns its own instance; chunks deliberately do not share one, for
    the same reason the dispatch lock can't make them race-free (see
    `import_text`) — a chunk must not be told an entity exists because a
    concurrently-running chunk is halfway through creating it.
    """

    entities: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    entity_properties: list[dict[str, Any]] = field(default_factory=list)
    fact_properties: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.entities or self.facts or self.entity_properties or self.fact_properties)

    def snapshot(self) -> _ExtractionContext:
        """A copy, so a pass can render what it inherited while still adding to
        the live context as its own stages resolve things."""
        return _ExtractionContext(
            entities=list(self.entities), facts=list(self.facts),
            entity_properties=list(self.entity_properties),
            fact_properties=list(self.fact_properties),
        )

    def extend(self, field_name: str, items: list[dict[str, Any]]) -> None:
        # A later pass's entry replaces an earlier one with the same id — it is
        # newer (e.g. an entity that since gained a class via attach_entity_class).
        current = getattr(self, field_name)
        newer = {i["id"]: i for i in items}
        current[:] = [newer.pop(i["id"], i) for i in current] + list(newer.values())


def _render_prior_extraction(ctx: _ExtractionContext) -> str:
    """The cross-pass carry block. Empty (and absent from the prompt entirely)
    on the first pass, so a single-pass run reads exactly as it did before."""
    if ctx.is_empty():
        return ""
    sections = []
    if ctx.entities:
        sections.append("Entities:\n" + _render_resolved_entities(ctx.entities))
    if ctx.facts:
        sections.append("Facts:\n" + _render_resolved_facts(ctx.facts))
    if ctx.fact_properties:
        sections.append("Fact property values:\n" + _render_property_values(ctx.fact_properties))
    if ctx.entity_properties:
        sections.append("Entity property values:\n" + _render_property_values(ctx.entity_properties))
    body = "\n\n".join(sections)
    return (
        "\nAlready extracted from this text by earlier passes — this is what is\n"
        "already in the KB for this text, so do not re-extract it. Look for what\n"
        "these passes missed instead:\n\n"
        f"{body}\n"
    )


def _describe_source(node: dict[str, Any] | None) -> str:
    """Human-readable name/description of a SOURCE node, for the system prompt."""
    if node is None:
        return "(not specified)"
    props = node.get("properties", {})
    name = props.get("name")
    description = props.get("description")
    if name and description:
        return f"{name} — {description}"
    return name or description or "(not specified)"


def _describe_related_data(results: list[DataSearchResult]) -> str:
    """Render search_data hits (existing ENTITY/FACT nodes) for the system prompt."""
    if not results:
        return "(none found)"
    lines: list[str] = []
    for r in results:
        if r.type == ENTITY:
            suffix = f" ({r.class_name})" if r.class_name else ""
            lines.append(f"- {r.name}{suffix}")
        else:  # FACT
            lines.append(f"- {r.head_name} —{r.relation_name}→ {r.tail_name}")
    return "\n".join(lines)


# Arguments that take a node id, and the node label each one must name. The
# extraction agent regularly passes the *name* instead ("AGE", "WORKED_AT",
# "COMPANY"); `_coerce_id_args` looks each one up by name before the call is
# allowed to fail. Order matters: the owner args resolve before `property_id`,
# because a property is resolved against its owner's class/relation first.
_ID_ARGS: tuple[tuple[str, str], ...] = (
    ("class_id", CLASS),
    ("relation_id", RELATION),
    ("entity_id", ENTITY),
    ("head_entity_id", ENTITY),
    ("tail_entity_id", ENTITY),
    ("fact_id", FACT),
    ("property_id", PROPERTY),
)

# A uuid as the model copies it, give or take one character (#96).
_UUID_LIKE = re.compile(r"[0-9a-fA-F-]{35,37}")


def _within_one_edit(a: str, b: str) -> bool:
    """True if *a* becomes *b* by inserting, deleting or substituting one character."""
    if a == b or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:]
    return a[i:] == b[i + 1:]


# Tools whose `text` argument claims to be a verbatim quote from the source.
_TEXT_BEARING_TOOLS = frozenset({
    "add_entity", "add_fact", "add_entity_property_value",
    "add_fact_property_value", "link_extraction",
})

_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"[0-9a-z]+")

# How far the source may sprawl beyond the span's own length and still count as
# a quote of it: a real quote with list markers or an elided middle stays tight,
# whereas a sentence assembled from words scattered across the text does not.
_SPAN_WINDOW_SLACK = 3
_SPAN_WINDOW_FLOOR = 5


def _comparable_value(value: Any, literal_type: str | None) -> str:
    """A literal's value in the form it is stored in, as text.

    Mirrors `add_literal`: a typed value is cast on write, so an incoming
    "March 2019" has to become "2019-03" before it can be compared with what
    is already on the graph.
    """
    if literal_type:
        try:
            value = cast_literal_value(value, literal_type)
        except Exception:
            pass          # un-castable: compare the raw text, as the writer will
    return str(value)


def _same_value(left: tuple[Any, str | None], right: tuple[Any, str | None]) -> bool:
    """Do two (value, literal_type) pairs denote the same literal?

    Casting each the way the write path would catches the ordinary case. The
    second comparison catches the same date sent twice under different types —
    the model calls one "March 2019" as a STRING and the next "2019-03" as a
    DATE, and only the DATE one gets normalized on write. `normalize_date` is
    an identity on anything that isn't a date, so this cannot collapse two
    genuinely different values.
    """
    if _comparable_value(*left) == _comparable_value(*right):
        return True
    return normalize_date(str(left[0])) == normalize_date(str(right[0]))


def _normalize_span(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().lower()


def _span_tokens(value: str) -> list[str]:
    return _WORD.findall(value.lower())


def _minimal_window(span: list[str], source: list[str]) -> int | None:
    """Length of the shortest run of `source` containing `span` in order, or
    None if `span` isn't a subsequence of it at all."""
    if not span:
        return None
    best: int | None = None
    positions = [i for i, tok in enumerate(source) if tok == span[0]]
    for start in positions:
        i = start
        matched = 0
        while i < len(source) and matched < len(span):
            if source[i] == span[matched]:
                matched += 1
            i += 1
        if matched == len(span):
            window = i - start
            if best is None or window < best:
                best = window
                if best == len(span):      # exact contiguous run; can't do better
                    break
    return best


def _span_is_grounded(span: str, source_text: str) -> bool:
    """Is `span` a real quote from `source_text`?

    The benchmark scorer makes the same judgement after the fact (see
    `check_provenance` in tests/benchmarks/common/scoring.py); doing it at
    dispatch time turns a score into a correction the model can act on.

    A contiguous case/whitespace-insensitive match is the fast path. Beyond
    that, the span's words must appear in the source *in order and close
    together*. Models quoting list-structured text reliably splice a heading
    onto a genuine fragment ("Skills: Tools: JIRA, Trello") or elide a middle
    ("* Technical: Basic Python" from "* Technical: SQL, Basic Python"); those
    are real quotes and a strict substring test rejects every one of them,
    which costs the stage its whole turn budget. The window bound is what keeps
    this from waving through a sentence stitched out of scattered words — those
    need most of the document to match, and get rejected.
    """
    if not span.strip():
        return False
    if _normalize_span(span) in _normalize_span(source_text):
        return True
    span_tokens = _span_tokens(span)
    if not span_tokens:
        return False
    window = _minimal_window(span_tokens, _span_tokens(source_text))
    if window is None:
        return False
    return window <= len(span_tokens) * _SPAN_WINDOW_SLACK + _SPAN_WINDOW_FLOOR


_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _split_into_chunks(text: str, max_chunks: int) -> list[str]:
    """Split *text* into up to `max_chunks` contiguous, paragraph-aligned chunks.

    Splits on blank lines; a text with `max_chunks <= 1` or no paragraph
    breaks (a single unbroken block) is returned as one chunk — this is a
    simple heuristic, not a guarantee of exactly `max_chunks` pieces.
    """
    if max_chunks <= 1:
        return [text]
    paragraphs = [p for p in _PARAGRAPH_BREAK.split(text) if p.strip()]
    if len(paragraphs) <= 1:
        return [text]
    n = min(max_chunks, len(paragraphs))
    avg = len(paragraphs) / n
    chunks: list[str] = []
    start = 0.0
    for i in range(n):
        end = start + avg
        group = paragraphs[round(start):round(end)]
        if group:
            chunks.append("\n\n".join(group))
        start = end
    return chunks or [text]


class ExtractionEngine:
    """LLM-powered extraction engine that populates the KB from raw text."""

    def __init__(
        self,
        source: GraphSource,
        schema_manager: SchemaManager,
        knowledge_manager: KnowledgeManager,
        provenance_tracker: ProvenanceTracker,
        search_indexer: KBSearchIndexer | None = None,
    ) -> None:
        self._source = source
        self._schema = schema_manager
        self._knowledge = knowledge_manager
        self._provenance = provenance_tracker
        self._search_indexer = search_indexer
        self._resolver = NodeResolver(source, search_indexer)

    async def import_text(
        self,
        database: str,
        text: str,
        queries: list[str] | None = None,
        model: str | None = None,
        source_id: str | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
        related_data_k: int = 10,
        effort: Literal["low", "medium", "high"] = "low",
        max_concurrency: int = 1,
        on_stage: Callable[[str, int], None] | None = None,
    ) -> None:
        """Run the staged extraction pipeline: entities, then relations, then
        entity property values, then fact property values. Each stage is its
        own agentic pass over a narrower tool set than the last, told what the
        prior stage(s) resolved (by id) instead of rediscovering it.

        `effort` runs the whole pipeline multiple times over the same text —
        low/medium/high = 1/2/3 passes — each later pass told a prior pass
        already ran, so it hunts for what got missed rather than re-deriving
        what's there.

        `max_concurrency` (default 1: today's behavior, unchanged) splits
        `text` into up to that many paragraph-aligned chunks and runs a full
        pipeline (all `effort` passes) per chunk *concurrently* — the LLM
        round-trips overlap, which is where the wall-clock time actually
        goes. Each tool dispatch is still serialized by a lock, so two
        chunks can never corrupt shared graph state by writing at the exact
        same instant — but the lock cannot see across a chunk's separate
        find_*-then-add_* turns, so it does NOT prevent two chunks that both
        mention the same entity/fact from each independently deciding it
        doesn't exist yet and creating it twice; that race is only impossible
        today because everything runs sequentially. Best suited to long text
        whose chunks cover mostly distinct content (independent sections,
        different topics) rather than one narrative that repeats the same
        entities throughout — a single unbroken block of text can't be split
        at all and always runs as one chunk regardless of `max_concurrency`.

        `on_stage(stage, pass_num)`, if given, is called after each stage finishes
        (stage is one of STAGES; the gap stage only when it runs), so a caller can see
        what each stage added — the extraction benchmark snapshots the graph there. With
        max_concurrency > 1 the chunks' stages interleave.
        """
        if effort not in EFFORT_PASSES:
            raise ExtractionError(f"effort must be one of {list(EFFORT_PASSES)}, got {effort!r}")

        client = llm.AsyncClient()
        total_passes = EFFORT_PASSES[effort]
        chunks = _split_into_chunks(text, max_concurrency)
        write_lock = asyncio.Lock()

        async def run_chunk(chunk_text: str) -> None:
            # One context per chunk, carried from pass to pass: pass 2 is shown
            # what pass 1 resolved instead of only being told that it ran.
            carried = _ExtractionContext()
            for pass_num in range(1, total_passes + 1):
                await self._run_pipeline_pass(
                    database, client, chunk_text, queries, model, source_id,
                    on_tool_call, on_tool_result, max_turns, related_data_k,
                    pass_num=pass_num, total_passes=total_passes, write_lock=write_lock,
                    carried=carried, on_stage=on_stage,
                )

        await asyncio.gather(*(run_chunk(c) for c in chunks))

    async def _run_pipeline_pass(
        self,
        database: str,
        client: Any,
        text: str,
        queries: list[str] | None,
        model: str,
        source_id: str | None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None,
        max_turns: int,
        related_data_k: int,
        pass_num: int,
        total_passes: int,
        write_lock: asyncio.Lock,
        carried: _ExtractionContext,
        on_stage: Callable[[str, int], None] | None = None,
    ) -> None:
        """One full run of the staged pipeline over `text`.

        `carried` accumulates what every stage resolves and is shared across the
        passes of this chunk, so a later pass inherits it as prompt context.
        """
        schema_str = (await self._schema.fetch_database_schema(database)).to_str(simplified=True)
        queries_str = (
            "\n".join(f"- {q}" for q in queries)
            if queries
            else "Extract all notable entities, relations, and property values."
        )
        source_node = self._source.get_node(database, source_id) if source_id is not None else None
        source_str = _describe_source(source_node)
        # Recomputed fresh (not hoisted to import_text) so a later pass sees
        # what an earlier pass in this same call just wrote.
        if self._search_indexer is not None and related_data_k > 0:
            related_data_str = _describe_related_data(
                self._search_indexer.search_data(database, text, k=related_data_k)
            )
        else:
            related_data_str = "(search not configured for this database)"
        if total_passes == 1:
            pass_note_str = ""
        elif pass_num == 1:
            pass_note_str = _FIRST_PASS_NOTE_TEMPLATE.format(pass_num=pass_num, total_passes=total_passes)
        else:
            pass_note_str = _LATER_PASS_NOTE_TEMPLATE.format(pass_num=pass_num, total_passes=total_passes)

        # Snapshotted before any stage runs, so every stage of this pass sees the
        # same inherited context while `carried` keeps growing underneath it.
        inherited_str = _render_prior_extraction(carried.snapshot())

        def build_prompt(workflow: str) -> str:
            return _SYSTEM_PROMPT_TEMPLATE.format(
                workflow=workflow, schema=schema_str, queries=queries_str,
                source=source_str, related_data=related_data_str, pass_note=pass_note_str,
                prior_extraction=inherited_str,
            )

        classes = self._schema_classes(database)
        entity_calls = await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
            tools=_tools_for(STAGE_ENTITIES),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_ENTITIES].format(classes=_render_class_checklist(classes))
            ),
            text=text,
        )
        if on_stage is not None:
            on_stage(STAGE_ENTITIES, pass_num)
        entities = self._resolved_entities(database, entity_calls)

        # A class with zero instances is usually right, but it's also how a
        # class whose instances aren't proper nouns gets skipped wholesale.
        # One extra targeted pass over just those classes — but only when
        # *something* is known, since the failure being recovered from is
        # selective blindness (some classes resolved, others never). Nothing
        # known at all means there's no partial coverage to explain, and
        # re-listing every class would just repeat the entity prompt verbatim;
        # that's what `effort`'s additional passes are for.
        #
        # Coverage is judged against everything known for this text, not just
        # what this pass touched: a later pass only re-reads what it re-reads,
        # so a class an earlier pass already filled would otherwise look empty
        # and send the gap stage hunting for entities that already exist.
        known_entities = _merge_entities(entities, carried.entities)
        missing = _missing_classes(classes, known_entities) if known_entities else []
        if missing:
            gap_calls = await self._run_stage(
                database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
                tools=_tools_for(STAGE_ENTITY_GAPS),
                system_prompt=build_prompt(
                    _STAGE_WORKFLOWS[STAGE_ENTITY_GAPS].format(
                        entities=_render_resolved_entities(known_entities),
                        missing_classes=_render_class_checklist(missing),
                    )
                ),
                text=text,
            )
            if on_stage is not None:
                on_stage(STAGE_ENTITY_GAPS, pass_num)
            # Re-resolved from both stages' calls rather than merged, so an entity
            # the gap stage attached another class to carries its current classes.
            entities = self._resolved_entities(database, entity_calls + gap_calls)
        carried.extend("entities", entities)

        relation_calls = await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
            tools=_tools_for(STAGE_RELATIONS),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_RELATIONS].format(entities=_render_resolved_entities(entities))
            ),
            text=text,
        )
        if on_stage is not None:
            on_stage(STAGE_RELATIONS, pass_num)
        facts = self._resolved_facts(database, relation_calls)
        carried.extend("facts", facts)

        entities_str = _render_resolved_entities(entities)
        facts_str = _render_resolved_facts(facts)

        # Fact properties before entity properties — see the STAGES comment.
        fact_property_calls = await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
            tools=_tools_for(STAGE_FACT_PROPERTIES),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_FACT_PROPERTIES].format(
                    entities=entities_str, facts=facts_str,
                )
            ),
            text=text,
        )
        if on_stage is not None:
            on_stage(STAGE_FACT_PROPERTIES, pass_num)
        fact_properties = self._resolved_property_values(
            database, fact_property_calls, "add_fact_property_value"
        )
        carried.extend("fact_properties", fact_properties)

        entity_property_calls = await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
            tools=_tools_for(STAGE_ENTITY_PROPERTIES),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_ENTITY_PROPERTIES].format(
                    entities=entities_str, facts=facts_str,
                    fact_properties=_render_property_values(fact_properties),
                )
            ),
            text=text,
        )
        if on_stage is not None:
            on_stage(STAGE_ENTITY_PROPERTIES, pass_num)
        carried.extend("entity_properties", self._resolved_property_values(
            database, entity_property_calls, "add_entity_property_value"
        ))

    async def _run_stage(
        self,
        database: str,
        client: Any,
        model: str,
        max_turns: int,
        source_id: str | None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None,
        write_lock: asyncio.Lock,
        *,
        tools: list[dict[str, Any]],
        system_prompt: str,
        text: str,
    ) -> list[tuple[str, dict[str, Any], Any]]:
        """Run one agentic tool-calling pass to convergence (or max_turns).

        Returns every (tool_name, args, result) triple from this pass, so the
        caller can harvest which entities/facts it resolved for the next stage.
        `write_lock` is held for the duration of each tool dispatch, so a
        concurrent chunk (see import_text's max_concurrency) can't interleave
        a single dispatch's own reads/writes with this one's — only the LLM
        round-trip itself runs unlocked, which is the actual point of running
        chunks concurrently. This does not make cross-chunk extraction fully
        race-free: two chunks can still each independently call find_entity,
        both see "not found", and both call add_entity, since that decision
        spans two separate (unlocked) LLM turns the lock can't reach across.
        """
        # The model sees short handles (E3, R2, ...) instead of uuids it would have to
        # copy; the source text itself is never rewritten, since spans are checked
        # against it.
        handles = self._handles_for(database)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": handles.shorten(system_prompt)},
            {"role": "user", "content": text},
        ]
        calls: list[tuple[str, dict[str, Any], Any]] = []
        turns = 0

        while True:
            if turns >= max_turns:
                break
            turns += 1
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                tool_choice="auto",
            )
            msg = response.choices[0].message
            msg_dict: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(msg_dict)

            if not msg.tool_calls:
                break

            for tc in msg.tool_calls:
                args, bad_args = parse_tool_arguments(tc.function.arguments)
                args = handles.expand_args(args)
                if on_tool_call:
                    on_tool_call(tc.function.name, args)
                if bad_args is not None:
                    result: Any = {"error": bad_args}  # back to the model, not a crash (#130)
                else:
                    try:
                        async with write_lock:
                            result = await self._dispatch_tool(
                                database, tc.function.name, args, source_id, text
                            )
                    except Exception as exc:
                        result = tool_error(exc)
                if on_tool_result:
                    on_tool_result(tc.function.name, args, result)
                calls.append((tc.function.name, args, result))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": handles.shorten(tool_message_content(result)),
                })
        return calls

    def _handles_for(self, database: str) -> Handles:
        if not hasattr(self, "_handles"):
            self._handles: dict[str, Handles] = {}
        if database not in self._handles:
            self._handles[database] = Handles(self._source, database)
        return self._handles[database]

    def _schema_classes(self, database: str) -> list[dict[str, Any]]:
        """Every CLASS as {id, name, description}, name-sorted for a stable prompt."""
        classes = [
            {
                "id": node["id"],
                "name": node_name(node),
                "description": node.get("properties", {}).get("description") or "",
            }
            for node in self._source.match_nodes(database, label=CLASS)
        ]
        return sorted(classes, key=lambda c: c["name"])

    def _resolved_entities(
        self, database: str, calls: list[tuple[str, dict[str, Any], Any]]
    ) -> list[dict[str, Any]]:
        """Entities the entity stage touched: created, found, or re-linked."""
        ids: set[str] = set()
        for name, args, result in calls:
            if name in ("add_entity", "find_entity") and isinstance(result, dict) and result.get("id"):
                ids.add(result["id"])
            elif name == "link_extraction" and args.get("element_id"):
                ids.add(args["element_id"])
            elif name == "attach_entity_class" and not (isinstance(result, dict) and "error" in result):
                ids.add(args["entity_id"])
        resolved = []
        for eid in ids:
            node = self._source.get_node(database, eid)
            if node is None or node.get("label") != ENTITY:
                continue
            classes = entity_classes(self._source, database, eid)
            resolved.append({
                "id": eid,
                "name": node_name(node),
                "class_names": [node_name(c) for c in classes],
            })
        return resolved

    def _resolved_facts(
        self, database: str, calls: list[tuple[str, dict[str, Any], Any]]
    ) -> list[dict[str, Any]]:
        """Facts the relation stage touched: created, found, or re-linked."""
        ids: set[str] = set()
        for name, args, result in calls:
            if name in ("add_fact", "find_fact") and isinstance(result, dict) and result.get("id"):
                ids.add(result["id"])
            elif name == "link_extraction" and args.get("element_id"):
                ids.add(args["element_id"])
        resolved = []
        for fid in ids:
            node = self._source.get_node(database, fid)
            if node is None or node.get("label") != FACT:
                continue
            parts = fact_parts(self._source, database, fid)
            head, relation, tail = parts["head"], parts["relation"], parts["tail"]
            if head is None or relation is None or tail is None:
                continue
            resolved.append({
                "id": fid,
                "head_id": head["id"],
                "head_name": node_name(head),
                "head_class": self._first_class_name(database, head["id"]),
                "relation_name": node_name(relation),
                "tail_id": tail["id"],
                "tail_name": node_name(tail),
                "tail_class": self._first_class_name(database, tail["id"]),
            })
        return resolved

    def _first_class_name(self, database: str, entity_id: str) -> str | None:
        classes = entity_classes(self._source, database, entity_id)
        return node_name(classes[0]) if classes else None

    def _owner_label(self, database: str, node: dict[str, Any]) -> str:
        """How a property value's owner is named in a prompt: a bare entity
        name, or a fact spelled out as (head —relation→ tail)."""
        if node.get("label") != FACT:
            return node_name(node)
        parts = fact_parts(self._source, database, node["id"])
        head, relation, tail = parts["head"], parts["relation"], parts["tail"]
        if head is None or relation is None or tail is None:
            return node_name(node)
        return f"({node_name(head)} —{node_name(relation)}→ {node_name(tail)})"

    def _resolved_property_values(
        self, database: str, calls: list[tuple[str, dict[str, Any], Any]], add_tool: str
    ) -> list[dict[str, Any]]:
        """Property values the stage touched: created via *add_tool*, or
        re-linked via link_extraction. Mirrors _resolved_entities/_resolved_facts,
        except the owner and property have to be walked back from the LITERAL."""
        ids: set[str] = set()
        for name, args, result in calls:
            if name == add_tool and isinstance(result, dict) and result.get("id"):
                ids.add(result["id"])
            elif name == "link_extraction" and args.get("element_id"):
                ids.add(args["element_id"])
        resolved = []
        for lid in ids:
            node = self._source.get_node(database, lid)
            # link_extraction also re-links entities and facts; only literals here.
            if node is None or node.get("label") != LITERAL:
                continue
            owners = self._source.get_neighbors(database, lid, edge_type=HAS_PROPERTY, direction="in")
            props = self._source.get_neighbors(database, lid, edge_type=IS_VALUE_OF, direction="out")
            if not owners or not props:
                continue
            resolved.append({
                "id": lid,
                "owner_id": owners[0]["node"]["id"],
                "owner_label": self._owner_label(database, owners[0]["node"]),
                "property_name": node_name(props[0]["node"]),
                "value": node.get("properties", {}).get("value"),
            })
        return resolved

    async def _resolve_id_by_name(
        self, database: str, arg: str, label: str, value: str, args: dict[str, Any]
    ) -> str | None:
        """Id of the *label* node named *value*, or None if the name doesn't resolve.

        Exact-match lookups only (plus the case/whitespace tier entities already
        have). Nothing fuzzy or semantic: this pass extracts against the schema
        as it stands, and a near-match coercion would quietly invent membership
        the schema never granted.
        """
        if label == CLASS:
            node = await self._schema.find_class(database, value)
            return node["id"] if node else None
        if label == RELATION:
            node = await self._schema.find_relation_type(database, value)
            return node["id"] if node else None
        if label == ENTITY:
            return await self._existing_entity_id(database, value)
        if label == PROPERTY:
            return await self._resolve_property_id(database, value, args)
        # FACT has no name worth looking up — a fact is identified by its triple.
        return None

    async def _resolve_property_id(
        self, database: str, name: str, args: dict[str, Any]
    ) -> str | None:
        """Id of the PROPERTY named *name*, preferring one declared for the
        owner named in the same call.

        Property names are not unique — AGE can be declared on PERSON and on
        ANIMAL — so a bare `find_property` returns a coin-flip. The call that
        needs the id already carries its owner (`entity_id` or `fact_id`), and
        scoping to that owner's class or relation turns the guess into a lookup.
        `find_property` remains the fallback for an owner we can't pin down.
        """
        owner_id = args.get("entity_id") or args.get("fact_id")
        if owner_id:
            for node in self._owner_properties(database, owner_id):
                if node_name(node) == name:
                    return node["id"]
        node = await self._schema.find_property(database, name)
        return node["id"] if node else None

    def _schema_element(self, database: str, r: Any) -> dict[str, Any]:
        """A search hit as the agent sees it; a PROPERTY also shows the type of value
        it holds, when it declares one (#95)."""
        element = {"id": r.id, "type": r.type, "name": r.name, "description": r.description}
        if r.type == PROPERTY:
            literal_type = ((self._source.get_node(database, r.id) or {}).get("properties", {})
                            .get("literal_type"))
            if literal_type:
                element["literal_type"] = literal_type
        return element

    def _owner_properties(self, database: str, owner_id: str) -> list[dict[str, Any]]:
        """PROPERTY nodes declared for an ENTITY's classes or a FACT's relation."""
        owner = self._source.get_node(database, owner_id)
        if owner is None:
            return []
        if owner.get("label") == FACT:
            relation = fact_parts(self._source, database, owner_id)["relation"]
            owners = [relation] if relation is not None else []
        else:
            owners = entity_classes(self._source, database, owner_id)
        props: list[dict[str, Any]] = []
        for node in owners:
            props.extend(
                nb["node"] for nb in self._source.get_neighbors(
                    database, node["id"], edge_type=DEFINE_PROPERTY, direction="out"
                )
            )
        return props

    async def _coerce_id_args(
        self, database: str, args: dict[str, Any]
    ) -> tuple[dict[str, str], list[str]]:
        """Replace any id-typed argument holding a *name* with the real id.

        Returns (coercions applied, arguments that couldn't be resolved). Args
        already holding a valid node of the right label are left untouched, so
        the common case costs one `get_node` per id argument.

        This runs for reads as well as writes, and the reads are the reason it
        exists at all: `find_fact` given a relation *name* doesn't fail, it
        returns {id: null}, and the model concludes the fact doesn't exist and
        creates a second one. A hard failure at least says something.
        """
        coerced: dict[str, str] = {}
        unresolved: list[str] = []
        for arg, label in _ID_ARGS:
            value = args.get(arg)
            if not value or not isinstance(value, str):
                continue
            node = self._source.get_node(database, value)
            if node is not None and node.get("label") == label:
                continue
            resolved = await self._resolve_id_by_name(database, arg, label, value, args)
            if resolved is None:
                resolved = self._resolve_mistyped_id(database, label, value)
            if resolved is None:
                unresolved.append(arg)
                continue
            args[arg] = resolved
            coerced[arg] = f"{value} -> {resolved}"
        return coerced, unresolved

    def _unresolved_message(self, database: str, fn: str, arg: str, value: Any) -> str:
        """Why *arg* didn't resolve, and what to do instead.

        Most failures name a real node of the wrong kind — a PROPERTY as relation_id
        when the model reaches for add_fact to record a value, an ENTITY as fact_id —
        and used to be told the value was "neither the id of an existing node nor the
        name of one", which was false and gave nothing to correct (#134).
        """
        expected = dict(_ID_ARGS).get(arg, "node")
        a = "an" if expected[:1] in "AEIOU" else "a"
        node = self._source.get_node(database, value) if isinstance(value, str) else None
        if node is None:
            return (
                f"{arg}={value!r} is not the id of any node, nor the name of {a} {expected}. "
                f"{arg} takes the id (E3, R2, P4, ...) that find_* / search_schema / an "
                "earlier add_* returned; if the schema has no such element, skip the item "
                "rather than inventing one."
            )
        label = node.get("label")
        what = f"{value} is the {label} {node_name(node)!r}, not {a} {expected}"
        # Keyed by the tool too: each stage only has its own creation tool, so pointing
        # at another stage's tool sent the model round in circles. When the item belongs
        # to another stage, say so and to skip it here.
        hint = {
            ("add_fact", RELATION, PROPERTY): " — a property's value isn't a fact; the property stages "
                                              "that follow record it, so skip it here",
            ("add_fact_property_value", FACT, ENTITY): " — this stage records values about facts only; a "
                                                       "value about an entity is recorded in the next stage, "
                                                       "so skip it here",
            ("add_entity_property_value", ENTITY, FACT): " — this stage records values about entities only; "
                                                         "values about facts were recorded in the previous "
                                                         "stage, so skip it here",
        }.get((fn, expected, label)) or {
            (FACT, RELATION): " — fact_id takes one specific fact (F...) from add_fact / find_fact, "
                              "not the relation type it instantiates",
            (ENTITY, CLASS): " — pass the entity itself (E...), from find_entity / add_entity, "
                             "not its class",
            (ENTITY, FACT): " — pass an entity (E...); a fact is never the head or tail of another fact",
            (PROPERTY, CLASS): " — property_id takes a PROPERTY (P...) declared on the owner's "
                               "class; find it with search_schema",
            (PROPERTY, RELATION): " — property_id takes a PROPERTY (P...) declared on the fact's "
                                  "relation; find it with search_schema",
        }.get((expected, label), f" — {arg} takes {a} {expected}")
        return f"{arg}: {what}{hint}."

    def _resolve_mistyped_id(self, database: str, label: str, value: str) -> str | None:
        """The one *label* node whose id is a single edit away from *value*, or None.

        The model copying an id verbatim sometimes drops, adds or changes one
        character — a uuid 37 characters long — and the item was lost (#96). Unlike
        a fuzzy *name* match (which ADR-029 keeps exact, because a near name can be
        a different thing), an id carries no meaning: a uuid-shaped value one edit
        from exactly one id of the expected kind has a single possible intent.
        With two or more such ids it stays unresolved.
        """
        if not _UUID_LIKE.fullmatch(value):
            return None
        matches = [n["id"] for n in self._source.match_nodes(database, label=label)
                   if _within_one_edit(value, n["id"])]
        return matches[0] if len(matches) == 1 else None

    async def _same_named_entities(self, database: str, name: str) -> list[str]:
        """Ids of entities standing for *name*: exact matches first, then the
        case/whitespace/punctuation-insensitive tier, so "ibm" finds an existing "IBM".
        The search tier is excluded — a semantically similar but distinct entity is not
        the same entity."""
        ids = [n["id"] for n in self._source.match_nodes(database, label=ENTITY, properties={"name": name})]
        for c in await self._resolver.resolve_entity(database, name, k=10, include_search=False):
            if c.node_id not in ids:
                ids.append(c.node_id)
        return ids

    async def _class_lineage(self, database: str, class_id: str) -> set[str]:
        lineage = {class_id}
        for walk in (self._schema.get_class_ancestors, self._schema.get_class_descendants):
            try:
                lineage |= {n["id"] for n in await walk(database, class_id, max_hops=None)}
            except Exception:
                pass  # not a CLASS id: only an exact class match will count
        return lineage

    async def _existing_entity_id(
        self, database: str, name: str, class_id: str | None = None
    ) -> str | None:
        """Id of an entity already standing for *name*, or None.

        Names aren't unique, so with *class_id* a same-named entity only counts when
        it is untyped or one of its classes is *class_id*, an ancestor or a descendant
        of it: "Apple" as a FRUIT must not collapse onto "Apple" the COMPANY (#115).
        Without *class_id* the first same-named entity is returned, as before.
        """
        ids = await self._same_named_entities(database, name)
        if class_id is None or not ids:
            return ids[0] if ids else None
        lineage = await self._class_lineage(database, class_id)
        for eid in ids:
            classes = {c["id"] for c in entity_classes(self._source, database, eid)}
            if not classes or classes & lineage:
                return eid
        return None

    def _existing_literal_id(
        self,
        database: str,
        owner_id: str,
        property_id: str,
        value: Any,
        literal_type: str | None,
    ) -> str | None:
        """Id of this owner's existing LITERAL holding exactly *value* for
        *property_id*, or None.

        Keyed on the value, not just the property: an owner is allowed more
        than one value for the same property (two phone numbers), so only an
        identical value is a duplicate.

        The incoming value is cast the same way the write path casts it before
        comparing. Writes normalize ("March 2019" is stored as "2019-03"), so
        comparing the raw argument against the stored value reports every
        re-extraction of a date as new and duplicates it.
        """
        for pv in property_values(self._source, database, owner_id):
            prop = pv["property"]
            if prop is None or prop["id"] != property_id:
                continue
            props = pv["literal"].get("properties", {})
            if _same_value((value, literal_type), (props.get("value"), props.get("type"))):
                return pv["literal"]["id"]
        return None

    async def _link_existing(
        self, database: str, element_id: str, span: str | None, source_id: str | None
    ) -> dict[str, Any]:
        """Record *span* against an item that already exists and report it as
        deduped, which is what add_* collapses to on a second sighting."""
        if span:
            await self._provenance.add_extraction(
                database, text=span, source_id=source_id, element_id=element_id
            )
        return {"id": element_id, "deduped": True}

    async def _dispatch_tool(
        self,
        database: str,
        fn: str,
        args: dict[str, Any],
        source_id: str | None,
        source_text: str,
    ) -> dict[str, Any]:
        # The agent routinely passes a name where an id belongs ("AGE",
        # "WORKED_AT", "COMPANY"). Resolve those to real ids before the call is
        # allowed to fail — and before the grounding check, so an error is about
        # the argument that is actually wrong.
        coerced, unresolved = await self._coerce_id_args(database, args)
        if unresolved:
            return {"error": " ".join(self._unresolved_message(database, fn, arg, args[arg])
                                      for arg in unresolved if args.get(arg) is not None)}

        # Every write claims a verbatim `text` span from the source. Refuse the
        # ones that don't quote it — a fabricated span is the signature of a
        # value the model inferred rather than read — and the ones with no span
        # at all, which would be stored with no evidence (#128). The error goes
        # back as the tool result, so the model gets a chance to correct itself.
        # A fact needs all three ends. The library allows a partial fact, but here a
        # null id is the model's mistake — it once stored a fact relating two
        # entities by nothing (#139).
        if fn == "add_fact":
            missing = [arg for arg in ("head_entity_id", "relation_id", "tail_entity_id") if not args.get(arg)]
            if missing:
                return {"error": (
                    f"add_fact requires {', '.join(missing)}: find the relation with "
                    "find_relation_type / search_schema and pass its id. If no relation "
                    "in the schema states what the text says, skip the fact."
                )}

        span = args.get("text")
        if fn in _TEXT_BEARING_TOOLS and not (isinstance(span, str) and span.strip()):
            return {"error": (
                "`text` is required: quote the sentence from the source text this "
                "item was extracted from, character for character. If the text does "
                "not state it, skip the item."
            )}
        if fn in _TEXT_BEARING_TOOLS and not _span_is_grounded(span, source_text):
            return {"error": (
                "the `text` argument is not a quote from the source text — copy the "
                "line or sentence across character for character rather than "
                "rewriting or summarising it, and do not prepend a heading that "
                "isn't there. Only extract what the text actually states: never "
                "infer a value from world knowledge, from an entity's name, or from "
                "what is typical. If the text does not state it, skip it."
            )}

        result = await self._dispatch_resolved(database, fn, args, source_id, span)
        # Report what was rewritten, so a coercion shows up in on_tool_result and
        # in traces rather than silently papering over the model's mistake.
        if coerced and isinstance(result, dict):
            result = {**result, "coerced": coerced}
        return result

    async def _dispatch_resolved(
        self,
        database: str,
        fn: str,
        args: dict[str, Any],
        source_id: str | None,
        span: str | None,
    ) -> dict[str, Any]:
        """The tool bodies proper — every id argument here is already a real id."""
        if fn == "get_schema":
            schema = await self._schema.fetch_database_schema(database)
            return {"schema": schema.to_str(simplified=True)}

        if fn == "search_schema":
            type_filter = args.get("type")
            if type_filter:
                type_filter = type_filter.upper()
            mode = args.get("mode", "keyword")
            if self._search_indexer is not None:
                results = self._search_indexer.search_schema(
                    database, args["query"], k=20, mode=mode, type_filter=type_filter
                )
            else:
                results = await self._schema.find_schema(database, args["query"], type_filter=type_filter)
            return {"schema_elements": [self._schema_element(database, r) for r in results]}

        if fn == "find_class":
            node = await self._schema.find_class(database, args["name"])
            if node:
                p = node.get("properties", {})
                return {"id": node["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_relation_type":
            node = await self._schema.find_relation_type(database, args["name"])
            if node:
                p = node.get("properties", {})
                return {"id": node["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_entity":
            eid = await self._existing_entity_id(database, args["name"], args.get("class_id"))
            others = [
                {"id": oid, "classes": [node_name(c) for c in entity_classes(self._source, database, oid)]}
                for oid in await self._same_named_entities(database, args["name"]) if oid != eid
            ]
            node = self._source.get_node(database, eid) if eid else None
            if node:
                p = node.get("properties", {})
                result = {
                    "id": node["id"], "name": p.get("name"), "description": p.get("description"),
                    "classes": [node_name(c) for c in entity_classes(self._source, database, eid)],
                }
            else:
                result = {"id": None}
            if others:
                result["other_matches"] = others
            return result

        if fn == "find_fact":
            node = await self._knowledge.find_fact(
                database, args["head_entity_id"], args["relation_id"], args["tail_entity_id"]
            )
            if node:
                return {"id": node["id"], "name": node.get("properties", {}).get("name")}
            return {"id": None}

        if fn == "find_literal":
            node = await self._knowledge.find_literal(
                database, args["owner_id"], args["property_id"]
            )
            if node:
                return {"id": node["id"], "value": node.get("properties", {}).get("value")}
            return {"id": None}

        # Each stage is *told* to look before it writes (find_entity / find_fact /
        # find_literal), but that is advisory: a skipped check silently forks the
        # item, and a later pass that re-reads the same text is exactly when the
        # model skips it. Enforce the same resolve-or-link here, at the one point
        # every write goes through. KnowledgeManager's write semantics are
        # untouched — this is the extraction agent declining to create a second
        # copy of something it can already see.
        if fn == "attach_entity_class":
            await self._knowledge.attach_entity_class(database, args["entity_id"], args["class_id"])
            return {}

        if fn == "add_entity":
            existing = await self._existing_entity_id(database, args["name"], args.get("class_id"))
            if existing is not None:
                return await self._link_existing(database, existing, span, source_id)
            eid = await self._knowledge.add_entity(
                database,
                name=args["name"],
                description=args.get("description"),
                class_id=args.get("class_id"),
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": eid}

        if fn == "add_fact":
            existing_fact = await self._knowledge.find_fact(
                database, args["head_entity_id"], args["relation_id"], args["tail_entity_id"]
            )
            if existing_fact:
                return await self._link_existing(database, existing_fact["id"], span, source_id)
            fid = await self._knowledge.add_fact(
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
            existing_lit = self._existing_literal_id(
                database, args["entity_id"], args["property_id"],
                args["value"], args.get("literal_type"),
            )
            if existing_lit is not None:
                return await self._link_existing(database, existing_lit, span, source_id)
            lit_id = await self._knowledge.add_entity_property_value(
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
            existing_lit = self._existing_literal_id(
                database, args["fact_id"], args["property_id"],
                args["value"], args.get("literal_type"),
            )
            if existing_lit is not None:
                return await self._link_existing(database, existing_lit, span, source_id)
            lit_id = await self._knowledge.add_fact_property_value(
                database,
                value=args["value"],
                literal_type=args.get("literal_type"),
                fact_id=args["fact_id"],
                property_id=args["property_id"],
                text=args.get("text"),
                source_id=source_id,
            )
            return {"id": lit_id}

        if fn == "link_extraction":
            extraction_id = await self._provenance.add_extraction(
                database,
                text=args["text"],
                source_id=source_id,
                element_id=args["element_id"],
            )
            return {"id": extraction_id}

        return {"error": f"unknown tool: {fn}"}
