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
- Entity: `IBM` of class `COMPANY`
- Fact: `Alice —WORKED_AT→ IBM`, start date `1998-06-01` — both linked to the résumé

Every node carries a provenance link back to the résumé source.

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

A new document introduces concepts not present in the existing schema. During extraction the LLM detects that no suitable class or relation exists for the new concepts, declares them, and then populates the corresponding instances. The schema has grown to accommodate the new domain, and all subsequent documents benefit from the expanded vocabulary without any manual intervention from the developer.

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

After all three passes, `Alice` is a single entity with three outgoing facts, each carrying a distinct provenance link. A query for "what do we know about Alice and where does it come from?" yields all three facts with their respective source documents.

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

## Scenario 7: Schema Export and Reuse

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
