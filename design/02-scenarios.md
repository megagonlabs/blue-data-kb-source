# KBDB — Scenarios

## Scenario 1: Defining a Domain Schema

A developer is building an agent that reasons over a specific domain. Before any data is ingested, the developer defines the vocabulary the knowledge base will use: entity classes, relation types between them, and properties that describe those entities and relations. The schema is stored persistently in the knowledge base and serves as the structured vocabulary for all subsequent ingestion and querying.

**Example:**

A developer building an employment-history agent defines the following schema:
- Classes: `PERSON`, `EMPLOYEE` (subtype of `PERSON`), `COMPANY`
- Relation: `WORKED_AT` — from `EMPLOYEE` to `COMPANY`
- Properties: `NAME` on `PERSON`, `NAME` on `COMPANY`, `START_DATE` on `WORKED_AT`

The schema is now in place and all subsequent ingestion will use these types.

---

## Scenario 2: Ingesting a Structured Document

A developer populates the knowledge base from a document whose content is already known and structured. Entity instances are created for each subject in the document, facts are wired between them according to the schema, and property values are attached. Each item is linked to a source node representing the originating document, so any fact can later be traced back to where it came from.

**Example:**

A developer ingests Alice's résumé:
- Source: `Alice's résumé (PDF)`
- Entity: `Alice` of class `EMPLOYEE`, name property `"Alice"` — both linked to the résumé
- Entity: `IBM` of class `COMPANY` — added without a `text`/`source_id`, so it carries no provenance link
- Fact: `Alice —WORKED_AT→ IBM`, start date `1998-06-01` — both linked to the résumé

Provenance linking is opt-in per write, not automatic — only items created with `text=`/`source_id=` get an EXTRACTED_FROM link back to a source; `IBM` above shows what an item without one looks like. Every node, linked or not, also carries `created` and `modified` timestamps set automatically on write — distinct from a SOURCE node's own `original` field (e.g. the résumé's own date, if supplied when the source was added), which is about when the *source document* is from, not when the KB learned about it.

---

## Scenario 3: Extracting Knowledge from Free Text

A document arrives as unstructured prose. Rather than parsing it manually, the developer provides the text and a set of guiding queries to the extraction function. The system invokes an LLM that reads the current schema, identifies the relevant entities and facts in the text, and populates the knowledge base. For each item it creates, the LLM records the exact text span it drew from, preserving fine-grained provenance back to the source passage.

**Example:**

> Input text: "Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005."
> Queries: "Where did Alice work?", "When did each role start?"

Extracted and stored:
```
Entity:  Alice       (EMPLOYEE)   ← "Alice joined IBM as a software engineer"
Entity:  IBM         (COMPANY)    ← "Alice joined IBM as a software engineer in June 1998"
Entity:  Google      (COMPANY)    ← "She later moved to Google in 2005"
Fact:    Alice —WORKED_AT→ IBM    ← same span
  START_DATE: 1998-06-01          ← "joined IBM ... in June 1998"
Fact:    Alice —WORKED_AT→ Google ← "She later moved to Google in 2005"
  START_DATE: 2005-01-01          ← "moved to Google in 2005"
```

Each item is stored alongside the verbatim span the LLM identified.

---

## Scenario 4: Schema Evolution During Ingestion

A new document introduces concepts not present in the existing schema. During extraction the LLM first checks — by default, via a `search_schema` tool over the existing schema (`import_text`'s `schema_discovery_mode="search"`, the default) — whether something close enough already exists, to avoid declaring a near-duplicate (e.g. `JOIN_DATE` when `START_DATE` already covers it). Finding no suitable class or relation, it declares the new ones and then populates the corresponding instances. The schema has grown to accommodate the new domain, and all subsequent documents benefit from the expanded vocabulary without any manual intervention from the developer.

**Example:**

A second résumé mentions: "Alice studied Computer Science at MIT from 1994 to 1998."

The current schema has no `UNIVERSITY` class and no `STUDIED_AT` relation. The LLM:
1. Declares class `UNIVERSITY`
2. Declares relation `STUDIED_AT` — from `EMPLOYEE` to `UNIVERSITY`
3. Declares properties `FIELD_OF_STUDY` and `END_DATE` on `STUDIED_AT`
4. Adds entity `MIT` (class `UNIVERSITY`)
5. Adds fact `Alice —STUDIED_AT→ MIT` with field `Computer Science`, end date `1998`

The schema now covers both employment and education, and future documents can reference these types without triggering another declaration.

---

## Scenario 5: Multi-source Knowledge Accumulation

A knowledge base is built up from a series of documents over time. Each ingestion pass creates a distinct source node, and every entity, fact, and property value added during that pass is linked to its originating document. When the same entity appears in multiple documents, it is reused and new facts are appended alongside their respective provenance links. At any point, an agent can ask not only what is known about an entity but also which source established each piece of that knowledge.

**Example:**

Three documents are ingested in sequence:

1. **Alice's résumé** → `Alice —WORKED_AT→ IBM` (start: 1998), `Alice —WORKED_AT→ Google` (start: 2005)
2. **TechCrunch article** → `Alice —FOUNDED→ Acme AI` (year: 2018)
3. **LinkedIn export** → `Alice —WORKED_AT→ Google` (already known — entity reused, no duplicate created)

After all three passes, `Alice` is a single entity with three outgoing facts, each carrying a distinct provenance link. A plain `answer("main", "what do we know about Alice?")` call returns the facts with their extraction text (the verbatim span each was drawn from) — to also get *which document* each came from, the caller passes `include_metadata=True`, which adds a `get_metadata` tool to the agent's loop and returns a `metadata` dict alongside the result, keyed by node id, giving the source name and its `original` date for each cited item (see Scenario 7).

---

## Scenario 6: Real-time Knowledge Monitoring

A pipeline component needs to react immediately whenever new facts are added to the knowledge base — for example, to trigger an enrichment step or push a notification downstream. The component registers a change hook on the specific database it cares about. From that point on, every time an entity, fact, or property value is written, the hook receives a structured event describing the change. The hook fires synchronously before control returns to the caller, ensuring no events are missed. When the component shuts down it deregisters the hook.

**Example:**

A downstream enrichment service registers a hook on the `"main"` database. As an ingestion run proceeds:

```
entity added:    "Alice"  (class: EMPLOYEE)
entity added:    "IBM"    (class: COMPANY)
fact added:      Alice —WORKED_AT→ IBM
fact property:   Alice —WORKED_AT→ IBM   START_DATE = "1998-06-01"
```

The service receives each event in order and, upon seeing a new `WORKED_AT` fact, immediately kicks off a background lookup for additional public data about that company. Writes to any other database do not trigger the hook.

---

## Scenario 7: LLM-Powered Querying

A knowledge base has been populated from multiple résumés and news articles. A developer wants to query it in natural language without writing graph traversal code. They call `answer`, `validate`, and `enrich` on the knowledge base. Internally, each call runs an LLM agent that is given the current schema and a set of read-only graph tools. The agent reasons over the graph, calls the appropriate tools to gather evidence, and returns a structured result.

**Example:**

The knowledge base contains Alice's résumé (IBM, Google) and a TechCrunch article (Acme AI, 2018).

```python
# Question answering
answers = await kb.answer("main", "Where has Alice worked?")
# → [{"entity": "Alice → IBM",    "relation": "WORKED_AT", "provenance": "Alice joined IBM in June 1998."},
#    {"entity": "Alice → Google", "relation": "WORKED_AT", "provenance": "She later moved to Google in 2005."},
#    {"entity": "Alice → Acme AI", "relation": "FOUNDED",  "provenance": "Alice founded Acme AI in 2018."}]

# Statement validation
result = await kb.validate("main", "Alice founded a company in 2018.")
# → ValidationResult(
#       supporting=[{"fact": "Alice_FOUNDED_Acme AI", "head": "Alice", "tail": "Acme AI", "relation": "FOUNDED"}],
#       counter=[]
#   )

# Graph enrichment
context = await kb.enrich("main", "Alice's career", depth=2)
# → [EnrichmentResult(entity="Alice → IBM",    relation="WORKED_AT", distance=1),
#    EnrichmentResult(entity="Alice → Google", relation="WORKED_AT", distance=1),
#    EnrichmentResult(entity="Alice → Acme AI", relation="FOUNDED",   distance=1)]
```

The agent loop is invisible to the caller — only the final structured result is returned. A custom model can be chosen by passing `model=` to any of the three methods.

`answer` and `validate` additionally take `include_metadata=True`, which returns a `(result, metadata)` tuple instead of the bare result — `metadata` is a dict keyed by node id, giving each cited item's `created`/`modified` timestamps, extraction text, and source name/`original` date:

```python
answers, metadata = await kb.answer("main", "Where has Alice worked?", include_metadata=True)
# answers is the same list[dict] as above; metadata might look like:
# {"fact-abc123": {"source": "Alice's résumé", "source_original": "2020-01-15", "created": "...", ...}, ...}
```

Both `answer` and `validate` also take `natural_language=True`, returning prose instead of the structured result (composed with `include_metadata=True`, the prose gets a metadata section appended, and `metadata` is still returned alongside it). `enrich` has neither `include_metadata` nor `natural_language` — it always returns structured `EnrichmentResult` objects.

---

## Scenario 8: Schema Consolidation

During ingestion from several sources, redundant or overlapping schema elements accumulate. Two separate extraction runs have produced both a `COMPANY` class and a `CORPORATION` class, and instances of `CORPORATION` need to be reachable as `COMPANY` too. A developer calls `consolidate_schema` to merge them. The Schema Manager identifies every instance whose primary instance edge points at `CORPORATION` and adds a **new** instance edge from each one to `COMPANY` — it does not remove the old edge, and it does not delete `CORPORATION` from the schema. `operation="split"` is not implemented — `consolidate_schema` only supports `"merge"` today.

**Example:**

Before consolidation, the schema has two overlapping classes:
```
COMPANY     CLASS  "A business organization"
CORPORATION CLASS  "A commercial entity"
```

And two EMPLOYEE entities referencing different classes:
```
Alice —IS_INSTANCE_OF→ EMPLOYEE
IBM   —IS_INSTANCE_OF→ COMPANY
Acme  —IS_INSTANCE_OF→ CORPORATION
```

After merging `CORPORATION` into `COMPANY`:

```python
await kb.consolidate_schema(
    "main",
    operation="merge",
    source_names=["CORPORATION"],
    target_name="COMPANY",
)
```

Result:
```
COMPANY     CLASS  "A business organization"
CORPORATION CLASS  "A commercial entity"   # still in the schema — merge doesn't delete it

Acme —IS_INSTANCE_OF→ COMPANY       # new edge, added by the merge
Acme —IS_INSTANCE_OF→ CORPORATION   # old edge, still there
IBM  —IS_INSTANCE_OF→ COMPANY       # unchanged
```

A subsequent call to `fetch_database_schema` still lists both `COMPANY` and `CORPORATION`. `answer("main", "What companies are in the KB?")` returns both IBM and Acme, since the agent can reach Acme via either class edge — but code that assumes one class per entity (like `get_metadata`'s `class_name`) reports whichever edge it finds first, which is the original `CORPORATION` edge, not the new `COMPANY` one, since it was created first. If a clean single-class result matters, follow the merge with `delete_class("main", corporation_id)` to remove the source class and its now-orphaned edges explicitly.

---

## Scenario 9: Schema Export and Reuse

A team has invested effort in defining a rich domain schema in one knowledge base. A second team building a related agent wants to start from the same vocabulary rather than redefine it from scratch. The first team exports the schema to a portable representation and shares it. The second team imports it into a fresh knowledge base, which now contains all the same classes, relations, and property definitions. Each team then extends the schema independently as their ingestion needs diverge.

**Example:**

Team A exports the employment schema:
```
Classes:    PERSON, EMPLOYEE, COMPANY, UNIVERSITY
Relations:  WORKED_AT, STUDIED_AT, FOUNDED
Properties: NAME, START_DATE, END_DATE, FIELD_OF_STUDY
```

Team B imports it into a new knowledge base for a recruiting agent. They then extend it with:
- Class `JOB_POSTING`
- Relation `APPLIED_TO` — from `PERSON` to `JOB_POSTING`

Team A's schema remains unchanged. Both teams share the common vocabulary while evolving their own additions independently.

---

## Scenario 10: Searching Schema and Data

A developer wants to look up schema elements or instance data directly by keyword or meaning, without invoking an LLM. When a database is registered, it can be configured with a search mode — `bm25` for keyword search or `semantic` for embedding-based similarity search. As entities, facts, classes, relations, and properties are written, the search indexer keeps a corpus up to date automatically. The developer can then search schema elements and data independently, optionally restricting results to a specific node type, and can force a full reindex if needed.

**Example:**

A team registers a database with semantic search enabled:
```bash
kb database register main --search semantic --embedding-model text-embedding-3-small
```

After ingesting the employment schema and Alice's résumé, they search the schema for a concept without knowing the exact class name:
```bash
kb --database main schema search "place of employment" --mode semantic --type relation
# → WORKED_AT   RELATION   "Employment relationship"
```

And search the data for matching entities or facts:
```bash
kb --database main data search "Alice IBM" --mode keyword --type fact --top-k 5
# → Alice —WORKED_AT→ IBM   FACT   "Alice joined IBM in June 1998."
```

If the corpus falls out of sync — for example after a bulk import — the developer forces a rebuild:
```python
kb.reindex("main")
```

Search results are returned directly from the index, with no LLM call involved.
