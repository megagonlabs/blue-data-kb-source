# KBDB — Data Model

## Overview

KBDB stores all data as a typed property graph. Every piece of knowledge — schema elements and instance data alike — is represented as a node or an edge, each carrying a label that identifies its role. The graph is divided into named databases; a single `KBSource` instance may hold multiple independent databases.

The graph has two distinct layers that are kept strictly separate:

- **Schema layer** — CLASS, RELATION, and PROPERTY nodes that define the vocabulary of the domain
- **Instance layer** — ENTITY, FACT, and LITERAL nodes that hold concrete knowledge grounded in the schema

A third layer records where knowledge came from:

- **Provenance layer** — SOURCE and EXTRACTION nodes that link every instance item back to the assertion it was drawn from and the origin of that assertion

---

## 1. Databases

Each database is an independent named graph within a `KBSource`. Node IDs are unique within a database but not across databases. All operations are scoped to a single database; there is no cross-database querying.

---

## 2. Node Types

Every node carries:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier within the database; auto-generated if not provided |
| `name` | string | Human-readable name; used for display and idempotency checks |
| `description` | string \| null | Optional longer description |
| `created` | string (ISO-8601 UTC) | Set automatically when the node is created; never changes afterward |
| `modified` | string (ISO-8601 UTC) | Set to the same value as `created` on creation; refreshed automatically on every subsequent update to the node |

`created`/`modified` are stamped on every node of every label below — CLASS, RELATION, PROPERTY, ENTITY, FACT, LITERAL, SOURCE, and EXTRACTION alike — with no separate parameter to set them; callers never pass `created`/`modified` directly. Edges carry a `created` timestamp only (no `modified`). Additional fields beyond this common set are specific to each label.

---

### 2.1 Schema Nodes

#### CLASS

Represents a category of entity. Used as the type of ENTITY instances.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | Uppercase by convention (e.g., `PERSON`, `COMPANY`) |
| `description` | string \| null | |
| `created` / `modified` | string (ISO-8601) | See §2 above |

**Example:**
```
label:       CLASS
id:          "cls-001"
name:        "EMPLOYEE"
description: "A person employed by an organization"
created:     "2026-06-01T09:00:00+00:00"
modified:    "2026-06-01T09:00:00+00:00"
```

---

#### RELATION

Represents a typed relationship between entities. Used as the type of FACT instances.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | Uppercase by convention (e.g., `WORKED_AT`, `FOUNDED`) |
| `description` | string \| null | |
| `is_symmetric` | boolean | Whether the relation holds in both directions |
| `created` / `modified` | string (ISO-8601) | See §2 above |

**Example:**
```
label:        RELATION
id:           "rel-001"
name:         "WORKED_AT"
description:  "Employment relationship between a person and a company"
is_symmetric: false
created:      "2026-06-01T09:00:00+00:00"
modified:     "2026-06-01T09:00:00+00:00"
```

---

#### PROPERTY

Represents an attribute that can be attached to a CLASS or RELATION. Used to type LITERAL values.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | Uppercase by convention (e.g., `NAME`, `START_DATE`) |
| `description` | string \| null | |
| `created` / `modified` | string (ISO-8601) | See §2 above |

**Example:**
```
label:       PROPERTY
id:          "prop-001"
name:        "START_DATE"
description: "Date when employment began"
created:     "2026-06-01T09:00:00+00:00"
modified:    "2026-06-01T09:00:00+00:00"
```

---

### 2.2 Instance Nodes

#### ENTITY

A concrete instance of a CLASS.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | The entity's display name (e.g., `"Alice"`, `"IBM"`) |
| `description` | string \| null | |
| `created` / `modified` | string (ISO-8601) | See §2 above |

**Example:**
```
label:       ENTITY
id:          "ent-001"
name:        "Alice"
description: null
created:     "2026-06-01T09:00:00+00:00"
modified:    "2026-06-01T09:00:00+00:00"
```

---

#### FACT

A concrete instance of a RELATION, connecting two ENTITY nodes.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string \| null | Label for the fact. If not supplied, `add_fact` auto-synthesizes one as `{head_name}_{relation_name}_{tail_name}` — in practice a FACT's `name` is essentially never `null` |
| `description` | string \| null | |
| `confidence` | number \| null | Derived score in `[0.0, 1.0]`: the highest `confidence` among this fact's supporting EXTRACTION nodes, ignoring any whose confidence is `null`. `null` when no supporting extraction states one. Recomputed whenever an `EXTRACTED_FROM` edge is added to the fact; not set directly. A recomputation that changes the value refreshes `modified`; one that does not changes nothing |
| `created` / `modified` | string (ISO-8601) | See §2 above |

**Example:**
```
label:      FACT
id:         "fact-001"
name:       "Alice_WORKED_AT_IBM"
confidence: 0.9
created:    "2026-06-01T09:00:00+00:00"
modified:   "2026-06-01T09:00:00+00:00"
```

---

#### LITERAL

A typed scalar value attached to an ENTITY or FACT via a PROPERTY.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string \| null | |
| `value` | string \| number \| boolean | The stored value |
| `literal_type` | string | One of `STRING`, `NUMBER`, `DATE`, `BOOLEAN` |
| `created` / `modified` | string (ISO-8601) | See §2 above |

`literal_type` is inferred from the Python value type when not supplied: `int`/`float` → `NUMBER`, `bool` → `BOOLEAN`, everything else → `STRING`. When supplied explicitly, `value` is cast to match it — callers that only ever produce text (the CLI, the MCP server, the extraction agent's tool calls) can pass a numeric/boolean value as a string alongside `literal_type="NUMBER"`/`"BOOLEAN"` and have it stored as an actual `int`/`float`/`bool`, not the literal string. An unparsable NUMBER/BOOLEAN string raises `KnowledgeError`. STRING and DATE values are normalized to `str`.

**Example:**
```
label:        LITERAL
id:           "lit-001"
value:        "1998-06-01"
literal_type: "DATE"
created:      "2026-06-01T09:00:00+00:00"
modified:     "2026-06-01T09:00:00+00:00"
```

---

### 2.3 Provenance Nodes

#### SOURCE

Represents an originating document or data source. Serves as the top-level anchor for provenance chains.

A SOURCE stands for one citable origin fixed at a point in time. For a document, that is the document itself. For a dataset or knowledge base ingested as a whole, it is one snapshot of that dataset: `source` names the dataset and `original` identifies the snapshot. Different snapshots of the same dataset are therefore distinct SOURCE nodes.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | Human-readable name for the source (e.g., `"Alice's résumé"`) |
| `source` | string | Opaque source identifier (e.g., filename, URL, session ID) |
| `description` | string \| null | |
| `original` | string \| null | Caller-supplied date or snapshot identifier for the source itself (e.g. a document's publication date, or `"2026-08-01 dump"` for a dataset snapshot) — distinct from `created` below, which is when the SOURCE node was added to the KB. Free text, not parsed or validated. Settable via `add_source`/`update_source` |
| `created` / `modified` | string (ISO-8601) | See §2 above |

**Example:**
```
label:       SOURCE
id:          "src-001"
name:        "Alice's résumé"
source:      "resume_alice.pdf"
description: "PDF résumé uploaded 2026-06-01"
original:    "2020-01-15"
created:     "2026-06-01T09:00:00+00:00"
modified:    "2026-06-01T09:00:00+00:00"
```

---

#### EXTRACTION

Captures one assertion that an item was drawn from, along with how confident that assertion is. Any node an assertion creates can carry one: instance items (ENTITY, FACT, LITERAL) and schema elements (CLASS, RELATION, PROPERTY) alike, so a class or relation created inline by an assert records the submitted statement that forced it into existence. Each item gets its own EXTRACTION node, even when several items come from the same assertion.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `extraction_type` | string | One of the values in **Extraction types** below. Absent on nodes written before this field existed; absent reads as `text_span` |
| `text` | string | What the source asserted, before any identity resolution was applied. A verbatim span when `extraction_type` is `text_span`; otherwise the submitted statement serialized to a single string |
| `confidence` | number \| null | How confident this assertion is, in `[0.0, 1.0]`. `null` means unstated, which is not the same as `0.0` |
| `name` | string \| null | |
| `description` | string \| null | Free text. Also the current home for an external record identifier, such as a Wikidata statement id, until a dedicated field exists |
| `created` / `modified` | string (ISO-8601) | See §2 above |

**Extraction types:**

`extraction_type` describes the form of the evidence, not who supplied it. The supplier is recorded on the SOURCE, whose `source` field is an opaque identifier that holds `"wikidata"`, `"human:h-42"`, or `"agent:task-7"` equally well.

| Value | Evidence is |
|-------|-------------|
| `text_span` | A verbatim quote from a document |
| `external_record` | A record in another structured knowledge base or dataset |
| `assertion` | A statement made directly, with no underlying artifact |

`inference`, for facts derived from other facts already in the KB, is reserved but not implemented.

**Example:**
```
label:           EXTRACTION
id:              "ext-001"
extraction_type: "text_span"
text:            "Alice joined IBM as a software engineer in June 1998."
confidence:      0.9
created:         "2026-06-01T09:00:00+00:00"
modified:        "2026-06-01T09:00:00+00:00"
```

---

## 3. Edge Types

All edges are directed. Source and target node labels constrain which connections are valid.

### 3.1 Schema Edges

| Edge type | Source label | Target label | Meaning |
|-----------|-------------|-------------|---------|
| `IS_SUBCLASS_OF` | CLASS | CLASS | Child class inherits from parent class |
| `DEFINE_PROPERTY` | CLASS or RELATION | PROPERTY | The class or relation owns this property |
| `DOMAIN` | RELATION | CLASS | Valid head entity type for the relation |
| `RANGE` | RELATION | CLASS | Valid tail entity type for the relation |

**Example — schema fragment:**
```
(EMPLOYEE) --[IS_SUBCLASS_OF]--> (PERSON)
(WORKED_AT) --[DOMAIN]--> (EMPLOYEE)
(WORKED_AT) --[RANGE]---> (COMPANY)
(WORKED_AT) --[DEFINE_PROPERTY]--> (START_DATE)
```

---

### 3.2 Instance Edges

| Edge type | Source label | Target label | Meaning |
|-----------|-------------|-------------|---------|
| `IS_INSTANCE_OF` | ENTITY | CLASS | Entity belongs to this class |
| `REPRESENTS` | FACT | RELATION | Fact is an instance of this relation type |
| `HEAD` | FACT | ENTITY | The head (subject) entity of the fact |
| `TAIL` | FACT | ENTITY | The tail (object) entity of the fact |
| `HAS_PROPERTY` | ENTITY or FACT | LITERAL | The entity or fact has this property value |
| `IS_VALUE_OF` | LITERAL | PROPERTY | The literal is a value of this property type |

**Example — instance fragment:**
```
(Alice) --[IS_INSTANCE_OF]--> (EMPLOYEE)
(IBM)   --[IS_INSTANCE_OF]--> (COMPANY)

(fact-001) --[REPRESENTS]--> (WORKED_AT)
(fact-001) --[HEAD]---------> (Alice)
(fact-001) --[TAIL]---------> (IBM)

(fact-001) --[HAS_PROPERTY]--> (lit-001:"1998-06-01")
(lit-001)  --[IS_VALUE_OF]---> (START_DATE)
```

---

### 3.3 Provenance Edges

| Edge type | Source label | Target label | Meaning |
|-----------|-------------|-------------|---------|
| `EXTRACTED_FROM` | ENTITY, FACT, LITERAL, CLASS, RELATION, or PROPERTY | EXTRACTION | Item was asserted by this extraction |
| `EXTRACTED_FROM` | EXTRACTION | SOURCE | Assertion came from this source |

One EXTRACTION node is created per item. The same SOURCE node may be the target of many EXTRACTION→SOURCE edges when multiple items are drawn from the same source.

**Example — provenance chain:**
```
(Alice)      --[EXTRACTED_FROM]--> (ext-001:"Alice joined IBM...")
(fact-001)   --[EXTRACTED_FROM]--> (ext-002:"Alice joined IBM...")
(lit-001)    --[EXTRACTED_FROM]--> (ext-003:"...in June 1998.")
(WORKED_AT)  --[EXTRACTED_FROM]--> (ext-004:"Alice joined IBM...")
(ext-001)    --[EXTRACTED_FROM]--> (src-001:"Alice's résumé")
(ext-002)    --[EXTRACTED_FROM]--> (src-001:"Alice's résumé")
(ext-003)    --[EXTRACTED_FROM]--> (src-001:"Alice's résumé")
(ext-004)    --[EXTRACTED_FROM]--> (src-001:"Alice's résumé")
```

---

## 4. Meta-Schema

The meta-schema is a fixed `DataSchema` that describes KBDB's own graph structure — the node labels and edge types — using the same CLASS/RELATION/PROPERTY vocabulary as any other KBDB schema. It is returned by `fetch_meta_schema()` and never stored in any user database.

It allows any agent or tool to inspect the structure of the KB graph itself without hard-coding label names, enabling generic graph traversal, introspection, and tooling.

**Meta-schema classes (node labels as classes):**

| Name | Description |
|------|-------------|
| `CLASS` | A schema class node |
| `RELATION` | A schema relation type node |
| `PROPERTY` | A schema property node |
| `ENTITY` | An instance entity node |
| `FACT` | An instance fact node |
| `LITERAL` | A typed scalar value node |
| `SOURCE` | A provenance source node |
| `EXTRACTION` | A provenance assertion node |

**Meta-schema relations (edge types as relations):**

| Name | Domain | Range | Description |
|------|--------|-------|-------------|
| `IS_SUBCLASS_OF` | CLASS | CLASS | Class hierarchy |
| `DEFINE_PROPERTY` | CLASS or RELATION | PROPERTY | Property ownership |
| `DOMAIN` | RELATION | CLASS | Valid head type |
| `RANGE` | RELATION | CLASS | Valid tail type |
| `IS_INSTANCE_OF` | ENTITY | CLASS | Entity classification |
| `REPRESENTS` | FACT | RELATION | Fact typing |
| `HEAD` | FACT | ENTITY | Fact subject |
| `TAIL` | FACT | ENTITY | Fact object |
| `HAS_PROPERTY` | ENTITY or FACT | LITERAL | Property value attachment |
| `IS_VALUE_OF` | LITERAL | PROPERTY | Property type reference |
| `EXTRACTED_FROM` | ENTITY, FACT, LITERAL, CLASS, RELATION, PROPERTY, or EXTRACTION | EXTRACTION or SOURCE | Provenance link |

---

## 5. Literal Types

| `literal_type` | Python types | Description |
|---------------|-------------|-------------|
| `STRING` | `str` | Default; used for text, names, identifiers |
| `NUMBER` | `int`, `float` | Numeric values |
| `DATE` | `str` (ISO 8601) | Dates and datetimes stored as strings |
| `BOOLEAN` | `bool` | True/false values |
