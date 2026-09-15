from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, Callable, Literal

from blue_data.source.graph.graph_source import GraphSource

from blue_data_kb_source._constants import CLASS, ENTITY, FACT
from blue_data_kb_source.knowledge.helpers import entity_classes, fact_parts, node_name
from blue_data_kb_source.resolution.resolver import NodeResolver
from blue_data_kb_source.types import DataSearchResult, ExtractionError

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
            "description": "Look up an ENTITY node by name. Returns {id, name, description} or {id: null}.",
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
            "name": "find_fact",
            "description": (
                "Look up an existing FACT by its head entity, relation, and tail entity. "
                "Returns {id, name} or {id: null}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "head_entity_id": {"type": "string"},
                    "relation_id": {"type": "string"},
                    "tail_entity_id": {"type": "string"},
                },
                "required": ["head_entity_id", "relation_id", "tail_entity_id"],
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
                    "class_id": {"type": "string"},
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare name) this entity was extracted from"},
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
                    "head_entity_id": {"type": "string"},
                    "tail_entity_id": {"type": "string"},
                    "relation_id": {"type": "string"},
                    "name": {"type": "string"},
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare relation phrase) this fact was extracted from"},
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
                    "entity_id": {"type": "string"},
                    "property_id": {"type": "string"},
                    "value": {"type": "string"},
                    "literal_type": {
                        "type": "string",
                        "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                    },
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare value) this value was extracted from"},
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
                    "fact_id": {"type": "string"},
                    "property_id": {"type": "string"},
                    "value": {"type": "string"},
                    "literal_type": {
                        "type": "string",
                        "enum": ["NUMBER", "DATE", "BOOLEAN", "STRING"],
                    },
                    "text": {"type": "string", "description": "The full verbatim sentence (not just the bare value) this value was extracted from"},
                },
                "required": ["fact_id", "property_id", "value"],
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

STAGES = (
    STAGE_ENTITIES, STAGE_ENTITY_GAPS,
    STAGE_RELATIONS, STAGE_ENTITY_PROPERTIES, STAGE_FACT_PROPERTIES,
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
    STAGE_ENTITIES: ["get_schema", "search_schema", "find_class", "find_entity", "add_entity", "link_extraction"],
    STAGE_ENTITY_GAPS: ["get_schema", "search_schema", "find_class", "find_entity", "add_entity", "link_extraction"],
    STAGE_RELATIONS: ["get_schema", "search_schema", "find_relation_type", "find_fact", "add_fact", "link_extraction"],
    STAGE_ENTITY_PROPERTIES: ["get_schema", "search_schema", "add_entity_property_value", "link_extraction"],
    STAGE_FACT_PROPERTIES: ["get_schema", "search_schema", "add_fact_property_value", "link_extraction"],
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
it. For each mention: call find_entity first. If it already exists, call
link_extraction with its id and the new text span instead of add_entity.
Otherwise call add_entity, passing that class's id from the list above. Use
names exactly as they appear in the text.

You do not need search_schema to find a class on that list — it is already
complete. Use search_schema only to check some *other* concept in the text
that none of the listed classes seems to cover; if nothing matches, skip that
mention.

When you have finished, report each class on the list with the instances you
added or linked for it, or "none".\
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

For each class above, find any instance the earlier pass missed. Call
find_entity first; if it already exists, call link_extraction with its id and
the new text span, otherwise call add_entity with that class's id from the
list above. Add nothing for a class the text genuinely does not mention, and
do not revisit classes that are not on the list above.\
""",
    STAGE_RELATIONS: """\
Stage: relation extraction. Entities were already resolved in a prior stage —
never call find_entity or add_entity here, and never invent an entity that
isn't in the list below; if a relationship involves one, skip it.

Entities already resolved for this text (id, name, class):
{entities}

For each relationship the text states between two of the entities above: find
the RELATION type via search_schema. If no matching RELATION exists, skip the
relationship. Otherwise call find_fact using the ids above as
head_entity_id/tail_entity_id. If the fact already exists, call
link_extraction with its id and the new text span instead of add_fact.
Otherwise call add_fact.\
""",
    STAGE_ENTITY_PROPERTIES: """\
Stage: entity property extraction. Entities were already resolved in a prior
stage — never invent an entity that isn't in the list below.

Entities already resolved for this text (id, name, class):
{entities}

For each attribute value the text states about one of the entities above:
find the PROPERTY on its class via search_schema. If no matching PROPERTY
exists, skip this value. Otherwise call add_entity_property_value using the
entity_id from the list above.\
""",
    STAGE_FACT_PROPERTIES: """\
Stage: fact property extraction. Facts were already resolved in a prior
stage — never invent a fact that isn't in the list below.

Facts already resolved for this text (id, head, relation, tail):
{facts}

For each attribute value the text states about one of the facts above: find
the PROPERTY on its relation via search_schema. If no matching PROPERTY
exists, skip this value. Otherwise call add_fact_property_value using the
fact_id from the list above.\
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
    covered = {e["class_name"] for e in entities if e.get("class_name")}
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
        suffix = f" ({e['class_name']})" if e.get("class_name") else ""
        lines.append(f"- id={e['id']}  {e['name']}{suffix}")
    return "\n".join(lines)


def _render_resolved_facts(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return "(none resolved in this pass)"
    lines = [f"- id={f['id']}  {f['head_name']} —{f['relation_name']}→ {f['tail_name']}" for f in facts]
    return "\n".join(lines)


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


# Tools whose `text` argument claims to be a verbatim quote from the source.
_TEXT_BEARING_TOOLS = frozenset({
    "add_entity", "add_fact", "add_entity_property_value",
    "add_fact_property_value", "link_extraction",
})

_WHITESPACE = re.compile(r"\s+")


def _normalize_span(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip().lower()


def _span_is_grounded(span: str, source_text: str) -> bool:
    """Is `span` a real quote from `source_text` (case- and whitespace-insensitive)?

    The benchmark scorer already checks this after the fact (see
    `check_provenance` in tests/benchmarks/common/scoring.py); doing it at
    dispatch time turns a score into a correction the model can act on.
    """
    return _normalize_span(span) in _normalize_span(source_text)


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
        model: str = "gpt-4o",
        source_id: str | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], Any], None] | None = None,
        max_turns: int = 25,
        related_data_k: int = 10,
        effort: Literal["low", "medium", "high"] = "low",
        max_concurrency: int = 1,
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
        """
        try:
            import openai
        except ImportError as exc:
            raise ExtractionError(
                "import_text requires the 'openai' package: pip install openai"
            ) from exc
        if effort not in EFFORT_PASSES:
            raise ExtractionError(f"effort must be one of {list(EFFORT_PASSES)}, got {effort!r}")

        client = openai.AsyncOpenAI()
        total_passes = EFFORT_PASSES[effort]
        chunks = _split_into_chunks(text, max_concurrency)
        write_lock = asyncio.Lock()

        async def run_chunk(chunk_text: str) -> None:
            for pass_num in range(1, total_passes + 1):
                await self._run_pipeline_pass(
                    database, client, chunk_text, queries, model, source_id,
                    on_tool_call, on_tool_result, max_turns, related_data_k,
                    pass_num=pass_num, total_passes=total_passes, write_lock=write_lock,
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
    ) -> None:
        """One full run of the 4-stage pipeline over `text`."""
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

        def build_prompt(workflow: str) -> str:
            return _SYSTEM_PROMPT_TEMPLATE.format(
                workflow=workflow, schema=schema_str, queries=queries_str,
                source=source_str, related_data=related_data_str, pass_note=pass_note_str,
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
        entities = self._resolved_entities(database, entity_calls)

        # A class with zero instances is usually right, but it's also how a
        # class whose instances aren't proper nouns gets skipped wholesale.
        # One extra targeted pass over just those classes — but only when the
        # stage found *something*, since the failure being recovered from is
        # selective blindness (some classes resolved, others never). A stage
        # that resolved nothing at all has no partial coverage to explain, and
        # re-listing every class would just repeat the entity prompt verbatim;
        # that's what `effort`'s additional passes are for.
        missing = _missing_classes(classes, entities) if entities else []
        if missing:
            gap_calls = await self._run_stage(
                database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
                tools=_tools_for(STAGE_ENTITY_GAPS),
                system_prompt=build_prompt(
                    _STAGE_WORKFLOWS[STAGE_ENTITY_GAPS].format(
                        entities=_render_resolved_entities(entities),
                        missing_classes=_render_class_checklist(missing),
                    )
                ),
                text=text,
            )
            entities = _merge_entities(entities, self._resolved_entities(database, gap_calls))

        relation_calls = await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
            tools=_tools_for(STAGE_RELATIONS),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_RELATIONS].format(entities=_render_resolved_entities(entities))
            ),
            text=text,
        )
        facts = self._resolved_facts(database, relation_calls)

        await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
            tools=_tools_for(STAGE_ENTITY_PROPERTIES),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_ENTITY_PROPERTIES].format(entities=_render_resolved_entities(entities))
            ),
            text=text,
        )

        await self._run_stage(
            database, client, model, max_turns, source_id, on_tool_call, on_tool_result, write_lock,
            tools=_tools_for(STAGE_FACT_PROPERTIES),
            system_prompt=build_prompt(
                _STAGE_WORKFLOWS[STAGE_FACT_PROPERTIES].format(facts=_render_resolved_facts(facts))
            ),
            text=text,
        )

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
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
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
                args = json.loads(tc.function.arguments)
                if on_tool_call:
                    on_tool_call(tc.function.name, args)
                try:
                    async with write_lock:
                        result = await self._dispatch_tool(
                            database, tc.function.name, args, source_id, text
                        )
                except Exception as exc:
                    result = {"error": str(exc)}
                if on_tool_result:
                    on_tool_result(tc.function.name, args, result)
                calls.append((tc.function.name, args, result))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
        return calls

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
        resolved = []
        for eid in ids:
            node = self._source.get_node(database, eid)
            if node is None or node.get("label") != ENTITY:
                continue
            classes = entity_classes(self._source, database, eid)
            resolved.append({
                "id": eid,
                "name": node_name(node),
                "class_name": node_name(classes[0]) if classes else None,
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
                "head_name": node_name(head),
                "relation_name": node_name(relation),
                "tail_name": node_name(tail),
            })
        return resolved

    async def _dispatch_tool(
        self,
        database: str,
        fn: str,
        args: dict[str, Any],
        source_id: str | None,
        source_text: str,
    ) -> dict[str, Any]:
        # Every write claims a verbatim `text` span from the source. Refuse the
        # ones that don't quote it — a fabricated span is the signature of a
        # value the model inferred rather than read. The error goes back as the
        # tool result, so the model gets a chance to correct itself. A call that
        # omits `text` entirely is left to the existing (permissive) path.
        span = args.get("text")
        if fn in _TEXT_BEARING_TOOLS and span and not _span_is_grounded(span, source_text):
            return {"error": (
                "the `text` argument is not a verbatim span of the source text. Quote "
                "the sentence exactly as it appears, and only extract what the text "
                "actually states — never infer a value from world knowledge, from an "
                "entity's name, or from what is typical. If the text does not state "
                "it, skip it."
            )}

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
            return {"schema_elements": [
                {"id": r.id, "type": r.type, "name": r.name, "description": r.description}
                for r in results
            ]}

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
            node = await self._knowledge.find_entity(database, args["name"])
            if node is None:
                # find_entity is a deliberately frozen exact-match lookup (see
                # resolution/resolver.py); fall back here to a case/whitespace/
                # punctuation-insensitive match so "ibm" resolves to an existing
                # "IBM" instead of creating a duplicate. Exclude the search tier —
                # a semantically similar but distinct entity is not the same entity.
                candidates = await self._resolver.resolve_entity(
                    database, args["name"], k=1, include_search=False
                )
                if candidates:
                    node = self._source.get_node(database, candidates[0].node_id)
            if node:
                p = node.get("properties", {})
                return {"id": node["id"], "name": p.get("name"), "description": p.get("description")}
            return {"id": None}

        if fn == "find_fact":
            node = await self._knowledge.find_fact(
                database, args["head_entity_id"], args["relation_id"], args["tail_entity_id"]
            )
            if node:
                return {"id": node["id"], "name": node.get("properties", {}).get("name")}
            return {"id": None}

        if fn == "add_entity":
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
