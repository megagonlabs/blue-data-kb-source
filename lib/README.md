# blue-data-kb-source

Python library for building and querying knowledge-base graphs with a typed schema layer on top of `blue-data`'s `GraphSource`.

## Requirements

- Python 3.10+
- [`blue-data`](../../blue-data/lib) (local dependency)

## Installation

The package is not yet published to PyPI. Use the provided install script, which installs `blue-data` from its local path first:

```bash
cd lib
bash scripts/install.sh
```

Or manually:

```bash
pip install -e ../../blue-data/lib
pip install -e ".[dev]"
```

## Development setup

```bash
cd lib
python -m venv .venv
source .venv/bin/activate
bash scripts/install.sh
```

## Usage

`KBSource` is abstract. Pick a ready-made backend: `MemoryKBSource`, `FileKBSource` (both `from blue_data_kb_source import ...`), or `NEO4JKBSource` (`from blue_data_kb_source.neo4j.neo4j_kb_source import NEO4JKBSource` — kept out of the top-level package so plain `import blue_data_kb_source` never requires the `neo4j` driver). To wire up a new backend from scratch, subclass `KBSource` together with a concrete `GraphSource` directly.

> **All I/O-bound methods are `async def`.** Use them inside an `async` function and run with `asyncio.run(…)` or an async framework.

```python
import asyncio
from blue_data_kb_source import MemoryKBSource

async def main():
    kb = MemoryKBSource(name="my-kb")
    kb.create_database("main")

    # --- declare schema ---
    person      = await kb.declare_class("main", name="PERSON",   description="A human individual")
    person_name = await kb.declare_class_property("main", name="NAME", description="Name of the individual", class_id=person)

    company     = await kb.declare_class("main", name="COMPANY",  description="A business organization")
    employee    = await kb.declare_class("main", name="EMPLOYEE", description="A person employed by an organization")

    await kb.add_is_subclass_of("main", employee, person)

    worked_at = await kb.declare_relation_type(
        "main", name="WORKED_AT",
        description="Employment relationship between a person and a company",
        is_symmetric=False,
        domain="EMPLOYEE",   # required; wires DOMAIN edge inline
        range="COMPANY",     # required; wires RANGE edge inline
    )

    start_date_prop = await kb.declare_relation_property(
        "main", name="START_DATE",
        description="Date when employment began",
        relation_id=worked_at,
    )

    # --- add provenance source ---
    src = await kb.add_source("main", source="resume_alice", name="Alice's résumé", description="PDF résumé")

    # --- add instances (with inline provenance) ---
    alice = await kb.add_entity("main", name="Alice", entity_id="alice", class_id=employee,
                                text="Alice is a software engineer.", source_id=src)
    await kb.add_entity_property_value("main", "Alice", literal_type="STRING",
                                       entity_id=alice, property_id=person_name,
                                       text="Alice is a software engineer.", source_id=src)

    ibm  = await kb.add_entity("main", name="IBM", entity_id="ibm", class_id=company)

    # --- add a fact ---
    fact = await kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm,
                             text="Alice joined IBM in June 1998.", source_id=src)

    # --- attach a property value to the fact ---
    await kb.add_fact_property_value("main", value="1998-06-01", literal_type="DATE",
                                     fact_id=fact, property_id=start_date_prop,
                                     text="Alice joined IBM in June 1998.", source_id=src)

    # --- provenance wiring without inline text: add_extraction can link in one call ---
    extraction = await kb.add_extraction("main", text="Alice joined IBM in June 1998.",
                                         source_id=src, element_id=ibm)

asyncio.run(main())
```

### Querying the knowledge base

`answer`, `validate`, and `enrich` run an LLM agent (via `QueryEngine`) that is given the current schema and a set of read-only graph tools. The agent reasons over the graph, calls tools to gather evidence, and returns a structured result. Requires `openai` and `OPENAI_API_KEY`. Pass `on_tool_call(name, args)` to observe each tool call as it happens (e.g. to drive a progress indicator). `max_turns` (default `25`) caps the number of agent turns; if the agent hasn't finished by then, the loop stops silently and returns an empty/default result rather than raising.

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

async def query(kb):
    # Answer a natural language question — LLM agent traverses entities and facts
    answers = await kb.answer("main", "Where did Alice work?")
    for a in answers:
        print(a.entity, a.relation, a.value, a.provenance)

    # Validate a statement — LLM agent returns supporting and counter evidence
    result = await kb.validate("main", "Alice worked at IBM")
    print(result.supporting)   # list of {fact, head, tail, relation} dicts
    print(result.counter)      # list of contradicting evidence

    # Enrich — LLM agent BFS from seed entities up to depth hops
    related = await kb.enrich("main", "Alice", depth=2)
    for r in related:
        print(r.entity, r.relation, r.distance)

    # All three accept an optional model parameter (default: "gpt-4o")
    answers = await kb.answer("main", "Where did Alice work?", model="gpt-4o-mini")

    # Search the schema by keyword (no LLM — direct name/description match)
    hits = await kb.search_schema("main", "employment")
    for h in hits:
        print(h.type, h.name, h.description)

    # Fold WORKER into EMPLOYEE: instances, domain/range and property
    # definitions are rewired, and WORKER is removed from the schema
    await kb.consolidate_schema("main", operation="merge",
                                source_names=["EMPLOYEE", "WORKER"],
                                target_name="EMPLOYEE")
```

### Growing the schema and extracting knowledge from text (LLM)

Two separate LLM passes, both OpenAI agents:

- **`expand_schema`** — grows the schema (`CLASS`/`RELATION`/`PROPERTY`), grounded by a text
  passage. Never touches entities, facts, or property values.
- **`import_text`** — extracts entities, facts, and property values *against the schema as it
  currently stands*. Never declares new schema; a mention with no matching schema element is
  skipped. By default `import_text` runs `expand_schema` first (`expand_schema=True`), so a single
  call still does both — pass `expand_schema=False` to extract only, e.g. when reusing a schema
  already expanded once across many documents.

Each node `import_text` creates is automatically wired to its own `:EXTRACTION` node containing the
verbatim text span the LLM identified. If an entity or fact is found to already exist (via
`find_entity`/`find_fact`), the agent attaches the new text as additional evidence instead of
creating a duplicate, so an item can end up with multiple `EXTRACTED_FROM` edges as more evidence is
discovered across extraction runs. Pass `on_tool_call(name, args)` to observe each tool call as it
happens (e.g. to drive a progress indicator) — it fires for both the expansion and extraction pass
when both run.

```python
import os
os.environ["OPENAI_API_KEY"] = "sk-..."

async def extract(kb):
    # Create a source node first (optional but recommended for provenance)
    src = await kb.add_source("main", source="resume_alice", name="Alice's résumé")

    await kb.import_text(
        "main",
        text="Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005.",
        queries=["Where did Alice work?", "When did she start?"],
        source_id=src,   # id of an existing :SOURCE node (optional)
        model="gpt-4o",  # default
        # expand_schema=True by default — set False to extract only, against schema
        # already grown by a prior expand_schema() call.
    )
```

`import_text`'s extraction pass will:
1. Inspect the current schema (classes and relations already declared).
2. For each entity/relationship/attribute in the text, call `search_schema` to resolve it to an
   existing `CLASS`/`RELATION`/`PROPERTY` — if nothing matches, skip that item.
3. Call `add_entity`, `add_fact`, `add_entity_property_value`, etc. to populate the KB.
4. For each item, pass the verbatim source span as `text` so that individual `:EXTRACTION` nodes are
   created and linked via `EXTRACTED_FROM`.
5. When an entity or fact already exists, call `link_extraction` instead of re-creating it, adding a
   new `EXTRACTED_FROM` edge alongside any existing ones.

The agent has tools for every kind of schema change: `declare_class`/`declare_relation_type`
(new CLASS/RELATION), `declare_class_property`/`declare_relation_property` (new PROPERTY),
`add_subclass_of` (place a CLASS/RELATION under an existing parent of the same kind — the only way
to express is-a/type-of/kind-of; the model is instructed never to declare a taxonomic name like
`IS_A` or `SUBCLASS_OF` as a `RELATION`, and `declare_relation_type` rejects a small set of those
names outright), and `attach_property` (reuse an existing PROPERTY on another CLASS/RELATION instead
of declaring a same-meaning duplicate). `search_schema` is used both for de-duplication and, after
declaring something new, to find a plausible existing parent/child to link with `add_subclass_of`.

A CLASS is a *kind* that can have many members (`PERSON`, `ORGANIZATION`) — the model is instructed
never to declare a CLASS (or RELATION) named after a specific individual mentioned in the text (e.g.
"Alice" or "United Nations" should never become a `CLASS ALICE`/`CLASS UNITED_NATIONS`); it declares
the general kind instead (`PERSON`, `ORGANIZATION`) and leaves the specific individual to the
extraction pass, which creates it as an entity against that CLASS.

Declaring a new CLASS is also expected to lead to a RELATION connecting it to the rest of the
schema: the model is prompted to check, after each new CLASS, whether the text evidences a
relationship to another class (existing or new) — e.g. text about a person's skills and hobbies
should yield not just `SKILL`/`HOBBY` classes but `HAS_SKILL`/`HAS_HOBBY` relations from `PERSON`
to them. A class left with no relation to anything is treated as a likely sign one was missed.

`expand_schema` (called directly, or implicitly by `import_text`) takes:
- **`depth`** (default `1`) — how many hierarchy levels beyond what's directly evidenced in the text
  to also propose and link via `add_subclass_of` (e.g. a mentioned "poodle" also suggesting a `DOG`
  parent `CLASS`).
- **`reference`** — `"llm"` (default) proposes schema freely; `"mcp"` additionally connects the
  servers in `mcp_servers` and gives the model their tools too, to ground new names and hierarchy
  placement (e.g. WordNet hypernyms/synonyms via `platform/wordnet-mcp`) — `mcp_servers` entries are
  `{"transport": "stdio", "command": ..., "args": [...], "env": {...}}` or
  `{"transport": "sse"|"streamable_http", "url": ..., "headers": {...}}`. Each entry may also set `startup_timeout` (seconds, default 30) for its handshake and `call_timeout` (default 120) per tool call; a server that misses either fails clearly instead of hanging the run.
- **`dedup_mode`** — how it checks for an existing schema element before declaring a new one. This
  applies both to the `search_schema` tool the model can call, and — for `declare_class` /
  `declare_relation_type` specifically — as an automatic check run before the node is created,
  regardless of whether the model called `search_schema` itself (closing the gap where two
  differently-worded near-duplicates, e.g. `WORKED_AT` and `EMPLOYED_AT`, would otherwise both get
  declared since the underlying `declare_*` calls only dedup on an exact name match):
  - `"search"` (default) — the database's configured search backend (index-free keyword match ranked by shared words, BM25, or
    semantic/embedding search if registered with `--search semantic`).
  - `"llm"` — asks the same model to judge whether any existing element means the same thing as the
    proposed one, even under different wording (e.g. `JOIN_DATE` vs `START_DATE`, `WORKED_AT` vs
    `EMPLOYED_AT`). More reliable for synonyms than keyword/semantic search over short names, at the
    cost of one extra model call per check.
  - `"none"` — the `search_schema` tool isn't offered, and the automatic pre-create check is skipped.

```python
await kb.expand_schema(
    "main",
    "Alice joined IBM as a software engineer in June 1998.",
    depth=1,
    reference="mcp",
    mcp_servers=[{"transport": "stdio", "command": "blue-data-wordnet-mcp"}],
)
await kb.import_text("main", text="...", expand_schema=False)  # reuse the schema above
```

`import_text`'s own schema-expansion params (`schema_expansion_target`, `schema_expansion_depth`,
`schema_expansion_mode`, `schema_expansion_mcp_servers`, `schema_expansion_dedup_mode`) mirror
`expand_schema`'s `text`/`depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one —
`schema_expansion_target` (any combination of `"text"`/`"queries"`, default both) selects what gets
concatenated into the `text` passed to the underlying `expand_schema` call.

Install the optional OpenAI extra first (add `mcp` too for `reference="mcp"`):

```bash
pip install "blue-data-kb-source[openai]"
pip install "blue-data-kb-source[openai,mcp]"  # for reference="mcp"
```

### Change hooks

Register a callback on the KB instance to be notified whenever the schema or instance data changes in a specific database. Hooks cover both schema declarations and instance writes. Multiple hooks can be registered for the same database; all fire in registration order. Hooks are synchronous (`def`, not `async def`).

Schema events fire only when something is actually created — a repeated call that creates nothing (an idempotent re-declaration, a deduplicated attach) does not re-fire.

```python
def on_kb_change(event: dict) -> None:
    t = event["type"]
    # --- schema events ---
    if t == "class_declared":
        print(f"class      {event['name']!r}")
    elif t == "relation_declared":
        print(f"relation   {event['name']!r}  ({event['domain']} → {event['range']})")
    elif t == "class_property_declared":
        print(f"property   {event['name']!r}  on class {event['class_name']!r}")
    elif t == "relation_property_declared":
        print(f"property   {event['name']!r}  on relation {event['relation_name']!r}")
    elif t == "class_subclass":
        print(f"subclass   {event['child_name']} IS_SUBCLASS_OF {event['parent_name']}")
    elif t == "relation_subclass":
        print(f"subrelation {event['child_name']} IS_SUBCLASS_OF {event['parent_name']}")
    elif t == "property_attached":
        print(f"attach     property {event['property_id']}  to {event['target_label']} {event['target_id']}")
    elif t == "relation_domain_attached":
        print(f"domain     {event['relation_name']} now accepts {event['class_name']}")
    elif t == "relation_range_attached":
        print(f"range      {event['relation_name']} now accepts {event['class_name']}")
    # --- instance events ---
    elif t == "entity_added":
        print(f"entity     {event['name']!r}  (class: {event['class_name']})")
    elif t == "fact_added":
        print(f"fact       {event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']}")
    elif t == "entity_property_added":
        print(f"entity property  {event['entity_name']}.{event['property_name']} = {event['value']!r}")
    elif t == "fact_property_added":
        print(f"fact property    {event['fact_name']}  {event['property_name']} = {event['value']!r}"
              f"  ({event['head_entity_name']} —{event['relation_name']}→ {event['tail_entity_name']})")

kb.add_change_hook("main", on_kb_change)

# Schema declarations fire the hook:
person   = await kb.declare_class("main", "PERSON", "A human individual")
# → class      'PERSON'

worked_at = await kb.declare_relation_type("main", "WORKED_AT", "Employment", domain="PERSON", range="COMPANY")
# → relation   'WORKED_AT'  (['PERSON'] → ['COMPANY'])

await kb.declare_class_property("main", "AGE", "Age", class_id=person)
# → property   'AGE'  on class 'PERSON'

employee = await kb.declare_class("main", "EMPLOYEE", "")
await kb.add_is_subclass_of("main", employee, person)
# → subclass   EMPLOYEE IS_SUBCLASS_OF PERSON

# Repeated declarations are idempotent and do NOT re-fire:
await kb.declare_class("main", "PERSON", "ignored")   # hook does not fire

# Instance writes fire the hook:
alice = await kb.add_entity("main", name="Alice", class_id=person)
# → entity     'Alice'  (class: PERSON)

fact = await kb.add_fact("main", relation_id=worked_at, head_entity_id=alice, tail_entity_id=ibm)
# → fact       Alice —WORKED_AT→ IBM

# Writes to other databases are not observed:
await kb.add_entity("other", name="Bob", class_id=person)   # hook does not fire

# Remove when no longer needed:
kb.remove_change_hook("main", on_kb_change)
```

Each event dict always contains `type`, `database`, and `id`. Extra fields by type:

**Schema events**

| `type` | extra fields |
|---|---|
| `class_declared` | `name`, `description` |
| `relation_declared` | `name`, `description`, `is_symmetric`, `domain` (list), `range` (list) |
| `class_property_declared` | `name`, `description`, `class_id`, `class_name` |
| `relation_property_declared` | `name`, `description`, `relation_id`, `relation_name` |
| `class_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |
| `relation_subclass` | `child_id`, `child_name`, `parent_id`, `parent_name` |
| `property_attached` | `target_id`, `target_label` (`"CLASS"`\|`"RELATION"`), `property_id` |
| `relation_domain_attached` | `relation_id`, `relation_name`, `class_id`, `class_name` |
| `relation_range_attached` | `relation_id`, `relation_name`, `class_id`, `class_name` |

**Instance events**

| `type` | extra fields |
|---|---|
| `entity_added` | `name`, `class_id`, `class_name` |
| `fact_added` | `name`, `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_property_added` | `entity_id`, `entity_name`, `property_id`, `property_name`, `value` |
| `fact_property_added` | `fact_id`, `fact_name`, `property_id`, `property_name`, `value`, `head_entity_id`, `head_entity_name`, `tail_entity_id`, `tail_entity_name`, `relation_id`, `relation_name` |
| `entity_class_attached` | `entity_id`, `entity_name`, `class_id`, `class_name` |
| `extraction_attached` | `element_id`, `extraction_id` |

Name and id fields are `None` when the corresponding node was not provided.

### Reading and importing a schema and its data

Export the schema of an existing database to JSON and reload it into a fresh KB. `export_database_schema` is a thin `fetch_database_schema(...).to_json()` wrapper — the symmetric counterpart to `import_database_schema`, which takes that same JSON shape back in:

```python
# --- export ---
main_schema_json = await kb.export_database_schema("main")

# --- import into a new database ---
kb.create_database("copy")
await kb.import_database_schema("copy", main_schema_json)
```

`export_database_data`/`import_database_data` do the same for instance data (entities, facts, property values) — schema first, then data, since data import resolves classes/relations/properties **by name** against whatever schema is already in the target database (schema import mints new node ids rather than preserving the originals, so the data export can't rely on the old ones):

```python
main_data_json = await kb.export_database_data("main", include_provenance=True)
await kb.import_database_data("copy", main_data_json)
```

Entities/facts in the export carry their original id as a `ref` purely as an in-payload join key (entity names aren't guaranteed unique — `add_entity` never dedups — so facts/property values reference their owner by `ref`, not by name, to avoid mis-linking when two entities share a name).

The CLI exposes the same round trip: `blue-kb schema export -o schema.json` / `blue-kb schema import schema.json`, then `blue-kb data export -o data.json` / `blue-kb data import data.json` (see [CLI usage](../platform/cli/README.md)).

For runnable versions of the patterns above with real captured output, see
[examples/backends/](../examples/backends/) (one script per `GraphSource`/`VectorSource`
combination) and [examples/feature-demos/](../examples/feature-demos/) (one script per
feature — schema declaration, provenance, change hooks, export/import, LLM schema
expansion, LLM extraction and querying).

## API

### `KBSource`

Abstract base class. Extend it alongside a concrete `GraphSource` implementation. Inherits all `GraphSource` primitives (`add_node`, `add_edge`, `get_node`, `get_neighbors`, …).

All I/O-bound methods are `async def`. `add_change_hook` and `remove_change_hook` are synchronous (hooks themselves must also be synchronous `def` functions).

Every node (`CLASS`, `RELATION`, `PROPERTY`, `ENTITY`, `FACT`, `LITERAL`, `SOURCE`, `EXTRACTION`) carries `created` and `modified` timestamps (ISO-8601 UTC) alongside its other attributes: `created` is set once when the node is created, `modified` is refreshed whenever one of the `update_*` methods changes the node's own attributes (wiring an edge to/from the node does not count as a modification). Every edge also carries a `created` timestamp (no `modified` — edges aren't updated in place). `SOURCE` nodes additionally support an `original` attribute (see `add_source` below) for a caller-supplied original date, distinct from `created`.

---

#### Schema operations (→ `SchemaManager`)

All `declare_*` methods are **idempotent**: if a node with the same name already exists they return its id without inserting a duplicate.

| Method | Returns | Description |
|---|---|---|
| `await declare_class(database, name, description, class_id?)` | `str` | Add a `:CLASS` node; `name` is canonicalized to `UPPER_SNAKE_CASE` first, so `"Person"` and `"PERSON"` resolve to the same node |
| `await declare_relation_type(database, name, description, is_symmetric?, relation_id?, *, domain, range)` | `str` | Add a `:RELATION` node; `domain`/`range` are required (keyword-only), accept a class name or list of class names, and wire `DOMAIN`/`RANGE` edges; `name` and every `domain`/`range` name are canonicalized to `UPPER_SNAKE_CASE` first. `IS_SUBCLASS_OF`/`IS_INSTANCE_OF` are reserved for the KB's own structural edges and raise `SchemaError` |
| `await declare_class_property(database, name, description, property_id?, class_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a class; `name` is canonicalized to `UPPER_SNAKE_CASE` first |
| `await declare_relation_property(database, name, description, property_id?, relation_id?)` | `str` | Add a `:PROPERTY` node; optionally wires `DEFINE_PROPERTY` from a relation; `name` is canonicalized to `UPPER_SNAKE_CASE` first |
| `await add_is_subclass_of(database, child_id, parent_id)` | `None` | Wire `IS_SUBCLASS_OF` between two CLASS or RELATION nodes (both must be the same kind). Idempotent — a repeat call is a no-op and fires no event. Raises `SchemaError` on a self-loop or when the edge would create a cycle |
| `await attach_property(database, target_id, property_id)` | `None` | Wire `DEFINE_PROPERTY` from an existing CLASS or RELATION to an existing PROPERTY (idempotent) — reuse a property across classes/relations instead of declaring a same-meaning duplicate under a new name |
| `await attach_relation_domain(database, relation_id, class_id)` | `None` | Attach a CLASS to an existing relation's `DOMAIN` — deduplicated, and add-only (no detach counterpart; domains only widen) |
| `await attach_relation_range(database, relation_id, class_id)` | `None` | Same, for `RANGE` |
| `await get_class_ancestors(database, class_id, max_hops?)` | `list[dict]` | Ancestor CLASS nodes via `IS_SUBCLASS_OF`, breadth-first with cycle protection, ordered nearest-first; `max_hops` defaults to `5`, `None` = unbounded |
| `await get_class_descendants(database, class_id, max_hops?)` | `list[dict]` | Descendant CLASS nodes, same walk downward |
| `await get_relation_ancestors(database, relation_id, max_hops?)` | `list[dict]` | Ancestor RELATION nodes, same walk over the relation hierarchy |
| `await get_relation_descendants(database, relation_id, max_hops?)` | `list[dict]` | Descendant RELATION nodes |
| `await get_class_entities(database, class_id, limit?)` | `list[dict]` | ENTITY nodes that are instances of the class; `limit` defaults to `10`, `None` = all |
| `await get_relation_domain_range(database, relation_id)` | `dict` | The relation's DOMAIN and RANGE CLASS nodes, as `{"domain": [...], "range": [...]}` |
| `await get_allowed_relations_between_classes(database, head_class_id, tail_class_id, expand?, max_hops?)` | `list[dict]` | RELATION nodes whose domain/range accept the two classes; `expand` defaults to `True` and lets an ancestor class satisfy the constraint |
| `await find_class(database, name)` | `dict \| None` | CLASS node by exact, case-sensitive name |
| `await find_relation_type(database, name)` | `dict \| None` | RELATION node by exact name |
| `await find_property(database, name)` | `dict \| None` | PROPERTY node by exact name |
| `await fetch_database_schema(database)` | `DataSchema` | Build a `DataSchema` from the `:CLASS` and `:RELATION` nodes declared in *database* |
| `await fetch_meta_schema()` | `DataSchema` | Return the fixed `DataSchema` describing the KB graph schema itself (node and edge type taxonomy) |
| `await export_database_schema(database)` | `dict` | `fetch_database_schema(database).to_json()` — portable JSON, symmetric with `import_database_schema` |
| `await import_database_schema(database, schema_json)` | `None` | Populate *database* from a `DataSchema.to_json()`/`export_database_schema` dict; declares classes, properties, relations, and domain/range edges; `IS_SUBCLASS_OF` entries (class and relation hierarchy) are re-wired with `add_is_subclass_of` after everything else is declared, so export → import → export round-trips unchanged |
| `await search_schema(database, query)` | `list[SchemaSearchResult]` | CLASS/RELATION/PROPERTY nodes matching *query*: via the search index when configured, else by keyword (whole query as a substring first, then shared words, names split on `_` so "is proficient in" finds `PROFICIENT_IN`) |
| `await consolidate_schema(database, operation, source_names, target_name, predicate?)` | `None` | Fold source elements into the target: rewires every reference, deletes the sources, updates the search index. `operation="merge"` only. Refused with `SchemaError`, before anything is written, if the merged hierarchy would contain a cycle or a relation target name is reserved |
| `await expand_schema(database, text, depth?, reference?, mcp_servers?, dedup_mode?, model?, on_tool_call?, max_turns?, timeout?)` | `None` | OpenAI LLM agent that grows the schema (CLASS/RELATION/PROPERTY) grounded by *text*; never touches entities/facts/property values. `depth` (default `1`) — hierarchy levels beyond what's directly evidenced to also propose, linked via `add_subclass_of` — each such link is first checked by a short LLM judge call ("is every CHILD necessarily a PARENT?"), and an implausible one (TEAM under COMPANY, COMPANY under ENGINEER) is rejected back to the agent with the reason. Each `declare_class_property`/`declare_relation_property` likewise gets one owner-scoped LLM check: a property that doesn't describe its owner (DEGREE on an employment relation) is rejected, and one meaning the same as a property the owner already has (JOIN_DATE next to START_DATE) returns that property as `reused_existing` — the synonym half is skipped when `dedup_mode="none"`. `reference` — `"llm"` (default) or `"mcp"` (also gives the model tools from connected `mcp_servers`, e.g. WordNet). `dedup_mode` (`"search"` default, `"llm"`, or `"none"`) controls how `search_schema` checks for existing elements *and* is auto-applied as a safety-net check before every `declare_class`/`declare_relation_type` call, even if the model skips `search_schema` — pass `"none"` to disable both. The automatic check reuses an existing element only on strong evidence (the `"llm"` judge's verdict, or a semantic hit at similarity ≥ 0.9 — keyword/BM25 overlap alone never merges), and for a relation only when its domain/range share a class lineage (self, ancestor, or descendant) with the requested ones; a redirected declaration's tool result carries `reused_existing` naming the element reused. Never declares a taxonomic (is-a/type-of/kind-of/subclass-of) name as a `RELATION`; those are rejected in favor of `add_subclass_of`. `timeout` (seconds, default `None` = no limit) caps the whole run — every model turn, tool call and judge call — raising `SchemaError` on expiry while keeping what was declared before it; without one, a stalled request waits out the OpenAI client's 600 s timeout and silent retries. Requires `openai` (and `mcp` for `reference="mcp"`). |
| `await update_class(database, class_id, name?, description?)` | `None` | Rename and/or redescribe a CLASS; refreshes `modified`. At least one of `name`/`description` is required. A new name follows `declare_*`'s rules: canonicalized to UPPER_SNAKE_CASE, and `SchemaError` if it's taken by another node of the same kind (for a property, by another property of the same owner) or, for a relation, reserved (`IS_SUBCLASS_OF`, `IS_INSTANCE_OF`) |
| `await update_relation(database, relation_id, name?, description?)` | `None` | As `update_class`, for a RELATION |
| `await update_property(database, property_id, name?, description?)` | `None` | As `update_class`, for a PROPERTY |
| `await delete_class(database, class_id)` | `None` | Remove a CLASS. Raises `SchemaError` if it still has entity instances or child classes — delete or re-parent those first |
| `await delete_relation(database, relation_id)` | `None` | Remove a RELATION. Raises `SchemaError` if it still has fact instances or sub-relations |
| `await delete_property(database, property_id)` | `None` | Remove a PROPERTY. Raises `SchemaError` if any LITERAL still holds a value for it |
| `await remove_is_subclass_of(database, child_id, parent_id)` | `None` | Drop one `IS_SUBCLASS_OF` edge; the inverse of `add_is_subclass_of`, and a no-op if the edge is absent |

---

#### Knowledge operations (→ `KnowledgeManager`)

| Method | Returns | Description |
|---|---|---|
| `await add_entity(database, name, description?, entity_id?, class_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Add an `:ENTITY` node; optionally wires `IS_INSTANCE_OF`; when `text` is given creates a per-item `:EXTRACTION` (linked to `source_id`) and wires `EXTRACTED_FROM` |
| `await add_fact(database, name?, description?, fact_id?, head_entity_id?, tail_entity_id?, relation_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Add a `:FACT` node; auto-names from head/relation/tail if `name` is omitted; optionally wires `HEAD`, `TAIL`, `REPRESENTS`; provenance via `text`/`source_id`. When head/tail/relation are all given, raises `KnowledgeError` unless `is_fact_allowed` reports `ALLOWED` — it never widens the relation's domain/range the way `assert_fact` does |
| `await add_entity_property_value(database, value, name?, description?, literal_type?, entity_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a `:LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from an entity; provenance via `text`/`source_id`. When both `entity_id` and `property_id` are given, raises `KnowledgeError` unless the property is actually defined for the entity's class (or an ancestor) |
| `await add_fact_property_value(database, value, name?, description?, literal_type?, fact_id?, property_id?, text?, source_id?, confidence?, extraction_type?)` | `str` | Create a `:LITERAL`, wire `IS_VALUE_OF` and `HAS_PROPERTY` from a fact; provenance via `text`/`source_id`. When both `fact_id` and `property_id` are given, raises `KnowledgeError` unless the property is actually defined for the fact's relation (or an ancestor) |
| `await add_entity_extraction(database, entity_id, text, source_id?, confidence?, extraction_type?)` | `str` | Link a new text span to an *existing* entity — adds a new `EXTRACTED_FROM` edge without touching the entity's node, so multiple text spans can accumulate over time |
| `await add_fact_extraction(database, fact_id, text, source_id?, confidence?, extraction_type?)` | `str` | Link a new text span to an *existing* fact, same as above |
| `await add_entity_property_value_extraction(database, entity_id, property_id, text, source_id?, confidence?, extraction_type?)` | `str` | Link a new text span to an entity's existing property value (resolved via `entity_id`+`property_id`); raises if no such value exists |
| `await add_fact_property_value_extraction(database, fact_id, property_id, text, source_id?, confidence?, extraction_type?)` | `str` | Link a new text span to a fact's existing property value (resolved via `fact_id`+`property_id`); raises if no such value exists |
| `await attach_entity_class(database, entity_id, class_id)` | `None` | Attach an existing CLASS to an existing ENTITY — deduplicated, add-only (no detach counterpart). An entity's *additional* class; `add_entity(class_id=…)` wires the first |
| `await fetch_database_data(database, include_provenance?, compact?, detailed?)` | `dict \| list` | `compact=False` (default): raw `{nodes, edges}` graph form (already includes each node's full `properties`, so `detailed` has no effect here). `compact=True`: flat denormalized rows (entity/class, property/value, fact head/relation/tail), the shape `blue-kb data list`/`blue-kb data list --format json` uses; `include_provenance=True` adds each row's `evidence`, one record per EXTRACTION on it (via its EXTRACTED_FROM chain); `detailed=True` adds each row's own `description`/`created`/`modified` (the entity for entity rows, the literal for property rows, the fact for fact rows), plus a FACT's derived `confidence` |
| `await export_database_data(database, include_provenance?)` | `dict` | Portable, name-based export of entities/facts/property values (schema not included — export/import that separately first); symmetric with `import_database_data`. An entity carries every class it belongs to (`classes`); with `include_provenance`, every item carries its evidence records (`evidence`: text, confidence, extraction type, source), which import recreates one by one, creating any source the target lacks |
| `await import_database_data(database, data)` | `None` | Populate *database* from an `export_database_data` dict; resolves classes/relations/properties **by name** against the target database's current schema, raising `KnowledgeError` if a reference can't be resolved. A property value's property is resolved among the properties declared for its owner's classes (or its fact's relation), so the same property name on two classes imports correctly. All or nothing: if any item fails (e.g. a fact that doesn't conform to domain/range), everything the call created — entities, their facts and values, provenance and sources — is removed before the error propagates |
| `await get_metadata(database, node_id)` | `dict` | Metadata for a single entity, fact, or property-value (literal) id: created/modified, `confidence` (a FACT's derived score, `None` on the other two), and an `evidence` record per EXTRACTION — raises `KnowledgeError` if `node_id` isn't one of those three types |
| `await get_entity_classes(database, entity_id)` | `list[dict]` | CLASS nodes the entity is an instance of, via `IS_INSTANCE_OF` |
| `await get_fact_parts(database, fact_id)` | `dict` | The fact's first HEAD / RELATION / TAIL nodes, as `{"head": ..., "relation": ..., "tail": ...}` with `None` for unwired parts |
| `await get_property_values(database, owner_id)` | `list[dict]` | LITERAL values on an ENTITY or FACT with each value's PROPERTY, as `[{"literal": ..., "property": ...}]` |
| `await is_fact_allowed(database, head_entity_id, relation_id, tail_entity_id, max_hops?)` | `dict` | Whether the relation's domain/range accept the two entities' classes, subclass-aware via ancestors; returns `{ok, reason, details}` with reasons `ALLOWED` \| `UNTYPED_ENTITY` \| `DOMAIN_RANGE_MISMATCH`. Direction-exact — `is_symmetric` is not consulted |
| `await is_property_allowed(database, owner_id, property_id, max_hops?)` | `dict` | Whether the property is `DEFINE_PROPERTY`-owned by the entity's classes or their ancestors (entity owner), or by the fact's relation or its ancestors (fact owner); adds reasons `FACT_HAS_NO_RELATION` \| `NOT_DEFINED_FOR_OWNER` |
| `await find_entity(database, name)` | `dict \| None` | First ENTITY with exactly *name* — entity names are not unique |
| `await find_fact(database, head_entity_id, relation_id, tail_entity_id)` | `dict \| None` | First FACT matching the exact directed (head, relation, tail) triple |
| `await find_literal(database, owner_id, property_id)` | `dict \| None` | The ENTITY/FACT owner's existing LITERAL for *property_id* — the property-value counterpart of the two lookups above, so a caller can check before writing. Returns the first value when the owner holds several |
| `await answer(database, question, model?, natural_language?, include_metadata?, on_tool_call?, max_turns?)` | `list[dict] \| str \| tuple[..., dict]` | LLM agent answers a natural language question by traversing the instance graph. `include_metadata=True` has the agent call `get_metadata` for entities/facts/properties it cites and returns `(result, metadata)` instead of just `result` — `metadata` is a `dict[node_id, dict]`. With `natural_language=True` too, the prose answer gets a natural-language provenance section appended after the main answer (still paired with the raw `metadata` dict as element 1). `max_turns` (default `25`) caps agent turns; hitting the limit stops the loop silently and returns an empty/default result |
| `await validate(database, statement, model?, natural_language?, include_metadata?, on_tool_call?, max_turns?)` | `ValidationResult \| str \| tuple[..., dict]` | LLM agent returns supporting and counter evidence for a statement. Every `supporting`/`counter` item is a FACT that exists in the KB — resolved by the `fact_id` the agent cites, or by its exact head/relation/tail, and reported as stored (`fact_id`, `fact`, `head`, `relation`, `tail`); an item that resolves to none goes to `unverified`, tagged `cited_as`. `include_metadata`/`natural_language`/`max_turns` behave as in `answer` |
| `await enrich(database, query, depth?, model?, on_tool_call?, max_turns?)` | `list[EnrichmentResult]` | LLM agent traverses the graph up to *depth* hops from seed entities and returns related knowledge; `max_turns` (default `25`) behaves as in `answer` |
| `await update_entity(database, entity_id, name?, description?)` | `None` | Rename and/or redescribe an ENTITY; refreshes `modified`. At least one of `name`/`description` is required |
| `await update_fact(database, fact_id, name?, description?)` | `None` | As `update_entity`, for a FACT |
| `await update_entity_property_value(database, entity_id, property_id, value?, name?, description?)` | `None` | Change an existing value in place. Raises `KnowledgeError` when the entity holds no value for that property |
| `await update_fact_property_value(database, fact_id, property_id, value?, name?, description?)` | `None` | As above, for a fact's property value |
| `await delete_entity(database, entity_id, cascade?)` | `None` | Remove an ENTITY and its property values. `cascade=True` also removes the facts it participates in; without it, an entity that still has facts raises `KnowledgeError` |
| `await delete_fact(database, fact_id)` | `None` | Remove a FACT and its property values |
| `await delete_entity_property_value(database, entity_id, property_id)` | `None` | Remove the entity's value for that property; raises `KnowledgeError` if there is none |
| `await delete_fact_property_value(database, fact_id, property_id)` | `None` | As above, for a fact's property value |
| `await search_data(database, query, k?, mode?, type_filter?)` | `list[DataSearchResult]` | Search ENTITY/FACT nodes by keyword (BM25) or `mode="semantic"` (embeddings). Falls back to a substring scan when the database was registered without search |
| `await execute_query(query, database?, collection?, optional_properties?)` | `Any` | Run one KB operation described as a structured query dict — the generic `DataSource` entry point, used by the query layer |
| `await execute_query_nl(query, database?, collection?, schema?, llm_fn?, optional_properties?)` | `Any` | Answer a natural-language question by translating it to a structured query first |

---

#### Provenance operations (→ `ProvenanceTracker`)

| Method | Returns | Description |
|---|---|---|
| `await add_source(database, source, name?, description?, node_id?, original?)` | `str` | Add a `:SOURCE` node; `name` defaults to `source` if omitted; `original` records the source's original date (e.g. publication date), separate from `created` (when it was added to the KB) |
| `await add_extraction(database, text, name?, description?, node_id?, source_id?, confidence?, extraction_type?, element_id?)` | `str` | Add an `:EXTRACTION` node; optionally wires `EXTRACTED_FROM` to a source (`source_id`) and/or links it to an existing entity/fact/literal (`element_id`) in the same call |
| `await attach_extraction(database, element_id, extraction_id)` | `None` | Wire `EXTRACTED_FROM` from an `ENTITY`/`FACT`/`LITERAL` to an EXTRACTION that already exists — created up front by `add_extraction` without an `element_id`, or re-wired on snapshot restore. Deduplicated, add-only. Each item still keeps its own EXTRACTION node; this does not share evidence between items |
| `await update_source(database, source_id, name?, description?, original?)` | `None` | Change a SOURCE's name, description and/or original date |
| `await delete_source(database, source_id)` | `None` | Remove a SOURCE and the extraction records that point at it |

---

#### Change hooks (→ `ChangeNotifier`)

| Method | Description |
|---|---|
| `add_change_hook(database, fn)` | Register *fn* (a sync `def`) to be called after every schema or instance-level write to *database*, after the search index is updated; *fn* receives a single event `dict`. An exception *fn* raises is logged, not raised to the writer, and later hooks still run |
| `remove_change_hook(database, fn)` | Deregister a previously added hook for *database* (no-op if not found) |
| `reindex(database)` | Rebuild the search index for *database* from the graph; synchronous. Raises `KBError` when the database was registered without search |

---

#### Ingest operations (→ `IngestionManager`)

Name-based, resolve-or-create counterparts to the knowledge write operations: resolution happens first (ambiguity raises `IngestionError` carrying the candidates, nothing written), and anything unresolved is created — schema included, with LLM judges naming and describing. All eleven take trailing `source_id?`/`confidence?`/`extraction_type?`/`schema_creation_mode?`/`model?` options.

| Method | Returns | Description |
|---|---|---|
| `await assert_class(database, name, description?)` | `AssertResult` | Reuse a class when one resolves, create it otherwise |
| `await assert_relation_type(database, name, domain, range, description?, is_symmetric?)` | `AssertResult` | Missing domain/range classes are created; an existing relation is widened, not rejected |
| `await assert_class_property(database, name, class_name, description?)` | `AssertResult` | The owning class is created when missing |
| `await assert_relation_property(database, name, relation_name, description?)` | `AssertResult` | The relation must already exist |
| `await assert_subclass(database, child, parent)` | `AssertResult` | Missing classes are created; the edge is idempotent |
| `await assert_subrelation(database, child, parent)` | `AssertResult` | Both relations must already exist |
| `await assert_entity(database, name, class_name?, description?)` | `AssertResult` | Resolves by name; a new or classless entity gets a judged class (`EVERYTHING` under `"none"`) |
| `await assert_fact(database, head, relation, tail, name?, description?)` | `AssertResult` | Operands resolved or created; an existing triple dedupes with new evidence; non-conforming domain/range widens |
| `await assert_entity_property(database, entity, property, value, literal_type?)` | `AssertResult` | Equal values dedupe; placement of a new property is judged over the entity's classes |
| `await assert_fact_property(database, head, relation, tail, property, value, literal_type?)` | `AssertResult` | The fact is identified structurally by its triple, and created when missing |
| `await ingest(database, payload)` | `IngestReport` | Runs a ten-arm payload of rows through the asserts in file order; rows may override `confidence`/`extraction_type`; failures collect into the report |

#### Extraction (→ `ExtractionEngine`)

| Method | Returns | Description |
|---|---|---|
| `await import_text(database, text, queries?, model?, source_id?, expand_schema?, schema_expansion_target?, schema_expansion_depth?, schema_expansion_mode?, schema_expansion_mcp_servers?, schema_expansion_dedup_mode?, schema_expansion_timeout?, on_tool_call?, max_turns?, related_data_k?, effort?, max_concurrency?, on_stage?)` | `None` | Use an OpenAI LLM to extract entities, facts, and properties from *text* into *database* via a sequence of agentic stages — entities, an optional entity-gap recovery sweep, then relations, fact properties, and entity properties — each over a narrower tool set than the last and told what every prior stage already resolved (entities as `id=… Name (CLASS)`, facts as `id=… Head (CLASS, id=…) —RELATION→ Tail (CLASS, id=…)`, property values as `Owner.PROPERTY = value`) rather than rediscovering it, against the schema as it currently stands (never declares new schema — a mention with no matching element is skipped, never coerced into the nearest element of a different kind); each created node gets its own `:EXTRACTION` wired to `source_id`. The entity stage is handed the full CLASS list (name-sorted, with ids and descriptions) and works through it class by class instead of scanning the text for names — the older text-first framing found only proper nouns and skipped classes like `SKILL` entirely. Any class that ends the entity stage with no instances is re-swept once by the gap stage; that stage is skipped when every class is covered, and when the entity stage resolved nothing at all (no partial coverage to explain — that is `effort`'s job). Entities the gap stage recovers are merged into the list every later stage receives, so they can still take part in facts and carry property values. Across all stages, a creation call whose `text` argument is not a quote from the source is refused with an explanatory tool result rather than written, so a value the model inferred rather than read cannot be attached to a sentence that doesn't exist; a call that omits `text` altogether still takes the older permissive path. A quote is a contiguous case/whitespace-insensitive match, or the span's words appearing in order within a bounded window — the window admits how models actually quote list-structured text (a heading spliced onto a real fragment, or an elided middle) while still rejecting a sentence assembled from scattered words. Separately, every creation call is deduped at dispatch: `add_entity` collapses onto an existing entity of the same normalized name, `add_fact` onto the same (head, relation, tail) triple, and `add_entity_property_value`/`add_fact_property_value` onto an existing value that is identical for that property (keyed on the value, so an owner can still hold several — two phone numbers). The surviving item gets the new text span and the tool returns `{id, deduped: true}`; values are compared in the form the write path stores them, so a date re-extracted as "March 2019" does not become a second copy of the "2019-03" already recorded. Separately again, every id-typed argument (`class_id`, `relation_id`, `entity_id`, `head_entity_id`, `tail_entity_id`, `fact_id`, `property_id`) is resolved before the call runs: the agent routinely passes a name where an id belongs, and an argument already holding a node of the right label passes through while a name is looked up exactly (never fuzzily — a near-match would invent schema membership). `property_id` is resolved against the properties declared for the owner named in the same call before falling back to a global lookup, because property names are not unique across classes. The rewrite comes back as `coerced: {arg: "name -> id"}`, and an unresolvable name still errors. This covers the read tools too, where a name used to produce `{id: null}` rather than an error — `find_fact` reporting "no such fact" is how duplicates got created in the first place. The stages are told to call `find_entity`/`find_fact`/`find_literal` first, but that is advisory and the model skips it precisely when re-reading text a prior pass already covered. When `source_id` resolves to an existing `:SOURCE` node, its `name`/`description` are included in every stage's system prompt as context (e.g. "Alice's résumé — submitted for her job application"), not just used for provenance wiring. `expand_schema` (default `True`) runs `expand_schema()` first — its `schema_expansion_*` params mirror `expand_schema`'s `text`/`depth`/`reference`/`mcp_servers`/`dedup_mode` one-to-one, with `schema_expansion_target` (any combination of `"text"`/`"queries"`, default both) selecting what gets concatenated into that call's `text`. `related_data_k` (default `10`) additionally surfaces that many existing ENTITY/FACT search hits (via the database's search indexer, when configured) in every stage's system prompt as unconfirmed leads the agent should verify via `find_entity`/`find_fact` rather than reuse blindly; `0` disables the lookup. Fact properties run before entity properties: both stages re-scan the same text for attribute values and `is_property_allowed` only blocks a property that isn't declared for that owner at all, so where one is declared on both a class and a relation the first stage to claim it keeps it — the fact is the more specific owner, and the entity stage then runs with the fact stage's values in its prompt and is told not to re-attach them. `effort` (`"low"` default \| `"medium"` \| `"high"` → 1/2/3 passes) repeats the whole pipeline that many times over the same text — each pass after the first is told a prior pass already ran and is shown everything those passes resolved, so it can hunt for what they missed rather than re-deriving what is there; the block is omitted when nothing has been carried, so a single-pass run's prompts are unchanged. That carried context belongs to its chunk and is never shared with a concurrently running one — a chunk must not be told an entity exists because another chunk is midway through creating it. Safe to repeat since `find_entity`/`find_fact`/`link_extraction` already dedupe, it just costs more LLM calls. `max_concurrency` (default `1`, unchanged behavior) splits `text` into up to that many paragraph-aligned chunks and runs a full pipeline (all `effort` passes) per chunk *concurrently*, sharing one client — the LLM round-trips overlap, which is where the wall-clock win comes from; every tool dispatch is serialized by a lock, but that lock cannot reach across a chunk's separate `find_*`-then-`add_*` LLM turns, so two chunks that both mention the same entity/fact can still each independently create it — best suited to long text whose chunks cover mostly distinct content, and a single unbroken block of text always runs as one chunk regardless of this value. `on_tool_call(name, args)`, if given, is invoked synchronously before each tool dispatch, across every stage, pass, and chunk. `max_turns` (default `25`) caps agent turns *per stage* (not per pass or chunk); hitting the limit stops that stage's loop silently and the pipeline moves on. Requires `openai`. `on_stage(stage, pass_num)`, if given, is called after each extraction stage finishes (the extraction benchmark snapshots the graph there). |

---

### Result types

| Type | Fields |
|---|---|
| `SchemaSearchResult` | `id`, `type`, `name`, `description` |
| `AnswerResult` | `entity`, `relation`, `value`, `provenance` |
| `ValidationResult` | `supporting: list[dict]`, `counter: list[dict]` |
| `EnrichmentResult` | `entity`, `relation`, `value`, `distance` |
| `ResolutionCandidate` | `node_id`, `name`, `label`, `match_type`, `score` |
| `NodeRef` | `node_id`, `label`, `name`, `extraction_id` |
| `AssertResult` | `node_id`, `created`, `match_type`, `extraction_id`, `created_nodes`, `widened_relations` |
| `IngestFailure` | `arm`, `index`, `reason`, `candidates` |
| `IngestReport` | `written`, `deduped`, `skipped`, `failures`, `created`, `widened` |

### Exceptions

| Exception | When raised |
|---|---|
| `KBError` | Base class for all blue-kb errors |
| `SchemaError` | Schema validation or consistency failures (e.g. missing domain class, a `class_id` that isn't a CLASS node) |
| `KnowledgeError` | Knowledge-layer write or query failures (e.g. an `entity_id` that doesn't exist, or refers to a FACT instead) |
| `ExtractionError` | LLM extraction failures (e.g. missing `openai` package) |
| `ProvenanceError` | Provenance-tracking failures |
| `IngestionError` | An assert's resolve phase failed — ambiguity (candidates attached), a judge crash; nothing was written |

`SchemaManager`/`KnowledgeManager` methods that take an id parameter referencing an existing node
(`class_id`, `relation_id`, `property_id`, `entity_id`, `fact_id`, `source_id`, ...) validate that the
node exists and is the expected type before doing anything, raising `SchemaError`/`KnowledgeError` with
the offending id and expected type otherwise. Ids that name a *new* node being created (e.g. `class_id`
passed to `declare_class` to force a specific id) are not checked this way.

### Literal types

`literal_type` must be one of: `"NUMBER"`, `"DATE"`, `"BOOLEAN"`, `"STRING"`. When omitted, it is inferred from the Python type of `value` (`bool` → `BOOLEAN`, `int`/`float` → `NUMBER`, `datetime.date`/`datetime` → `DATE`, stored as an ISO string; everything else → `STRING`, stored as `str(value)`), so every stored value is a JSON scalar. When given explicitly, `value` is cast to match — a string `"42"` with `literal_type="NUMBER"` is stored as the `int` `42`, not the string — so callers that only produce text (CLI, MCP, the extraction agent) still get a properly typed `LITERAL`. An unparsable `NUMBER`/`BOOLEAN` string raises `KnowledgeError`. A `DATE` value in an unambiguous textual-month format ("June 1998", "June 1, 1998") is normalized to ISO-8601 (`YYYY`/`YYYY-MM`/`YYYY-MM-DD`); anything already ISO or genuinely ambiguous (`"06/07/1998"`, `"circa 1998"`) is left as-is rather than guessed.

## Running tests

Tests require `pytest-asyncio` (configured with `asyncio_mode = "auto"`). LLM calls in `answer`/`validate`/`enrich` tests are mocked via `unittest.mock.patch` — no API key needed to run the suite.

```bash
cd lib
pytest
```

### Extraction benchmark suite

`tests/benchmarks/extraction/` scores extraction quality — schema expansion (`expand_schema`) and extraction (`import_text`) — against a set of gold-standard cases in `tests/benchmarks/extraction/cases/*.yaml`, spanning several domains (business, healthcare, finance, legal, HR, cooking, software). Each case declares a `category:` — `basic`, `subject_self` (the person a profile/résumé/first-person text is about is an entity too), `typing_fit` (an instance only goes into a class it actually fits: a university is not a `COMPANY`, a job title is not an entity), `same_name` (one name, two different things), `relation_extraction` (direction, study vs. work, implicit relations), `coreference`, `negation_hedging` (ruled-out, hypothetical, former and absent facts), `properties_normalization` (money, percentages, units, dates), `schema_expansion_quality` (no instance-named classes, coverage of the text, properties placed on the right owner), `schema_reuse`, `rules_policy` (requirements and rules are not instances), `lists_common_nouns`, `long_text` and `cross_document` (aliases, updates across passages) — and the report averages F1 per category. It is **not** part of the default `pytest` run (`testpaths = ["tests/unit"]`) and is **report-only** — it never fails the build on a metric regression, only on a harness error. Requires the `openai` extra installed alongside `dev` (`pip install -e '.[dev,openai]'`).

Scoring combines two techniques: entity/fact/property recall & precision use an LLM-judge alignment (semantic, tolerant of wording differences), while schema-reuse/expansion, value **normalization** (does a matched property hold the correctly normalized value/type, not just something an LLM judge calls "close enough"), **provenance** (does every created item's text span actually appear in the source, catching fabricated evidence), and **domain/range compliance** (does every fact's head/tail entity class actually satisfy the relation's domain/range, directly or through a superclass) are checked deterministically by reading the resulting graph directly. Each step runs the two passes `import_text` runs by default, separately: `expand_schema` over the text and queries (under each of the case's `dedup_modes`), then `import_text(expand_schema=False)`; a case with `expand_schema: false` skips the first pass and extracts against its seed schema only. Each case file is a single `input_text`/`queries`, or a `steps: [{input_text, queries}, ...]` list for cases that run several `import_text` passages in sequence against the same database (e.g. cross-document entity fusion) — `expected` describes the cumulative graph after all steps. `expected.optional.{entities,facts,properties}` lists items that are acceptable but not required: extracting one is neither a miss nor a false positive (it counts toward neither recall nor precision), so a reasonable extra such as `Alice : SOFTWARE_ENGINEER` doesn't cost precision while still not being demanded. `seed_schema` classes may name a `parent:` (declared as a subclass), and `seed_data: {entities: [{name, class}], facts: [{head, relation, tail}]}` pre-populates the graph before the first step, for cases that test linking to what is already there.

```bash
cd lib
pytest tests/benchmarks -s          # replays committed VCR cassettes; prints a score table
python -m tests.benchmarks.extraction.run --model gpt-4o --model gpt-4o-mini   # live, real API calls
python -m tests.benchmarks.extraction.run --record   # refresh the cassettes the pytest suite replays
python -m tests.benchmarks.extraction.report .reports/live-<stamp>.json   # re-render a past run's .md report
python -m tests.benchmarks.extraction.history add .reports/live-<stamp>.json --note "..."   # record a past run in the history
```

**Run history.** Every full-suite live run of `run.py` appends a one-line summary — mean F1 per kind, per category and per case, failed checks, tokens, commit, and an optional `--note` — to `tests/benchmarks/extraction/results/history.jsonl` and regenerates `results/README.md` from it: the latest run with ▲/▼ change since the previous one, sparkline trends per category and per case, and Mermaid charts of mean F1 over runs and per category (both render on GitHub). Both files are committed to track progress over time; partial runs (`--case`, `--mode`) and `--no-history` runs are not recorded. The live paths require `OPENAI_API_KEY`. Without it and without a cassette already recorded for a given (case, `dedup_mode`) pair, `pytest tests/benchmarks` skips that case rather than failing.

Each run also writes a readable **per-stage report** (`latest.md` / `live-<timestamp>.md`): a summary table, mean F1 per category, then per case its category, the input text, the seed schema, what schema expansion added, what each extraction stage added in order (entities, entity gaps, relations, fact properties, entity properties — via `import_text`'s `on_stage` hook), and the outcome against the gold as ✓ matched (expected → extracted), ≈ acceptable (optional gold that was extracted), ✗ missing and + extra items per kind, plus any failed deterministic check. Each run writes two files under `.reports/` (gitignored): a JSON score report (`latest.json` for the pytest suite; `live-<timestamp>.json` for `run.py`, timestamped so successive live runs don't overwrite each other) and a companion **detailed log** (`latest.detail.jsonl` / `live-<timestamp>.detail.jsonl`) with one JSON object per (case, model, mode) run — the task spec (case description, steps, seed schema, expected results), every LLM call's full prompt messages and response (including tool calls), the raw tool-call trace (inputs and outputs), and pre-/post-run KB snapshots (schema and data, captured separately), and per step what each pass added — `steps[i].expansion_added` (classes, relations, properties) and `steps[i].extraction_added` (entities, facts, property values), as readable lines like `PERSON -WORKED_AT-> COMPANY` or `Alice -WORKED_AT-> IBM` — plus the `final_state` of the whole KB — everything the summary table/score JSON leaves out, for debugging a specific run.

### Querying benchmark suite

`tests/benchmarks/querying/` scores `answer()`/`validate()`/`enrich()` — the read-only query agents, as opposed to extraction's write agent — against gold-standard cases in `tests/benchmarks/querying/cases/*.yaml` that seed a fixed graph (`seed_schema` + `seed_data`) and then ask one question/statement/traversal query of it. Same report-only philosophy as the extraction suite (never fails the build, only errors on a harness bug). Requires the `openai` **and** `search` extras alongside `dev` (`pip install -e '.[dev,openai,search]'`) — `search` (`rank-bm25`) is needed because cases run with `properties={"search": {"mode": "bm25"}}` (real in-memory BM25), not the index-free keyword fallback used when no search index is configured at all.

Cases span business, healthcare, finance, legal, and supply-chain domains, and cover categories chosen because `answer`/`validate`/`enrich` are agentic tool-callers (`search_entities`, `get_entity_facts`, `get_neighbors`, ...), not a text-to-query-language (Cypher/SPARQL) system — so there's no filter/aggregate/sort *tool*, and these categories test whether the agent does that work correctly itself over raw retrieved data: aggregation (COUNT/AVG via multi-call reasoning), constraint interpretation (temporal/numeric/categorical filters), ranking/top-k, multi-hop reasoning through `answer` itself (not just `enrich`'s BFS), complex logical composition (AND/OR/NOT), business-vocabulary grounding ("premium" → `tier=Gold`), boolean questions, and ambiguous-entity handling (scored by rewarding the agent for surfacing all plausible candidates, since there's no clarification tool in the product to fall back on — see `answer_ambiguous_entity_reference.yaml`). Graph analytics, access-control-aware querying, temporal validity intervals, confidence/data-quality-aware querying, multi-source federation, and conversational/multi-turn refinement are **not** covered — the product doesn't support them today (or, for multi-turn, `answer`/`validate`/`enrich` are single-shot with no session state), not a benchmark-design gap.

Each case runs under one or more `search_modes` (`keyword` | `semantic`, defaulting to `keyword` — a case-level knob analogous to extraction's `dedup_modes`, since `search_schema`/`search_entities`/`search_facts` all take a `mode` the LLM chooses per call). `semantic` mode wires up an in-memory vector backend (`blue_data.source.vector.memory_vector_source.MemoryVectorSource`) with a real OpenAI embeddings call for `embedding_fn` — a second, separate kind of API call from the chat-completions agent loop, not wrapped by the token/cost instrumentation the way chat completions are, though VCR cassette replay covers it transparently since interception happens at the HTTP layer regardless of which client made the call.

Scoring reuses the same LLM-judge alignment (`judge_alignment`/`PRF1`) the extraction suite uses — it's already generic — plus two new checks specific to free-form query output: **schema match** (for `answer`, whose result rows have LLM-chosen column names — "choose natural column names based on the question" — a deterministic key-set-overlap check over the alignment's matched pairs catches "right values, oddly named columns" independently of value correctness; doesn't apply to `validate`/`enrich`, whose dict shapes are prompt-specified, not free-form) and **NL fidelity** (cases can set `test_natural_language: true` to also exercise `answer`/`validate`'s `natural_language=True` prose-output path — costing roughly 2x the LLM calls for that case, since the structured result is always re-fetched first regardless of the flag — scored by an LLM judge checking the prose doesn't drop or contradict the gold-standard facts).

```bash
cd lib
pytest tests/benchmarks -s
python -m tests.benchmarks.querying.run --model gpt-4o --model gpt-4o-mini
python -m tests.benchmarks.querying.run --record
```

## Project structure

```
lib/
├── pyproject.toml
├── scripts/
│   ├── build.sh       # python -m build
│   ├── install.sh     # installs blue-data (local) then this package
│   └── publish.sh     # twine upload dist/*
├── src/
│   └── blue_data_kb_source/
│       ├── __init__.py         # public exports
│       ├── _constants.py       # node label and edge type string constants
│       ├── types.py            # result types and exception hierarchy
│       ├── kb_source.py        # KBSource abstract class (coordinator)
│       ├── schema/             # SchemaManager
│       ├── knowledge/          # KnowledgeManager + QueryEngine (LLM agent)
│       ├── provenance/         # ProvenanceTracker
│       ├── notification/       # ChangeNotifier
│       ├── resolution/         # NodeResolver — ranked name resolution
│       ├── ingestion/          # IngestionManager + LLM judges (assert_*, ingest)
│       └── extraction/         # ExtractionEngine (LLM agent)
└── tests/
    ├── conftest.py           # kb / populated_kb fixtures shared by unit/ and benchmarks/
    ├── unit/
    └── benchmarks/
        ├── common/            # shared harness: capture + cases/runner/scoring/reporting per family
        ├── extraction/        # gold-standard cases + pytest suite + live run.py CLI (import_text)
        ├── querying/          # gold-standard cases + pytest suite + live run.py CLI (answer/validate/enrich)
        └── judges/            # gold-standard cases + pytest suite + live run.py CLI (ingestion judges)
```
