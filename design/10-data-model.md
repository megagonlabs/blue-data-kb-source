# KBDB — Data Model

## Overview

KBDB stores all data as a typed property graph. Every piece of knowledge — schema elements and instance data alike — is represented as a node or an edge, each carrying a label that identifies its role. The graph is divided into named databases; a single `KBSource` instance may hold multiple independent databases.

The graph has two distinct layers that are kept strictly separate:

- **Schema layer** — CLASS, RELATION, and PROPERTY nodes that define the vocabulary of the domain
- **Instance layer** — ENTITY, FACT, and LITERAL nodes that hold concrete knowledge grounded in the schema

A third layer records where knowledge came from:

- **Provenance layer** — SOURCE and EXTRACTION nodes that link every instance item back to its originating document and text span

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

Additional fields are specific to each label.

---

### 2.1 Schema Nodes

#### CLASS

Represents a category of entity. Used as the type of ENTITY instances.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | Uppercase by convention (e.g., `PERSON`, `COMPANY`) |
| `description` | string \| null | |

**Example:**
```
label:       CLASS
id:          "cls-001"
name:        "EMPLOYEE"
description: "A person employed by an organization"
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

**Example:**
```
label:        RELATION
id:           "rel-001"
name:         "WORKED_AT"
description:  "Employment relationship between a person and a company"
is_symmetric: false
```

---

#### PROPERTY

Represents an attribute that can be attached to a CLASS or RELATION. Used to type LITERAL values.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | Uppercase by convention (e.g., `NAME`, `START_DATE`) |
| `description` | string \| null | |

**Example:**
```
label:       PROPERTY
id:          "prop-001"
name:        "START_DATE"
description: "Date when employment began"
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

**Example:**
```
label:       ENTITY
id:          "ent-001"
name:        "Alice"
description: null
```

---

#### FACT

A concrete instance of a RELATION, connecting two ENTITY nodes.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string \| null | Optional label for the fact (e.g., `"Alice at IBM"`) |
| `description` | string \| null | |

**Example:**
```
label: FACT
id:    "fact-001"
name:  null
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

`literal_type` is inferred from the Python value type when not supplied: `int`/`float` → `NUMBER`, `bool` → `BOOLEAN`, everything else → `STRING`.

**Example:**
```
label:        LITERAL
id:           "lit-001"
value:        "1998-06-01"
literal_type: "DATE"
```

---

### 2.3 Provenance Nodes

#### SOURCE

Represents an originating document or data source. Serves as the top-level anchor for provenance chains.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `name` | string | Human-readable name for the source (e.g., `"Alice's résumé"`) |
| `source` | string | Opaque source identifier (e.g., filename, URL, session ID) |
| `description` | string \| null | |

**Example:**
```
label:       SOURCE
id:          "src-001"
name:        "Alice's résumé"
source:      "resume_alice.pdf"
description: "PDF résumé uploaded 2026-06-01"
```

---

#### EXTRACTION

Captures a verbatim text span that an entity, fact, or literal was drawn from. Each instance item gets its own EXTRACTION node, even when multiple items share the same source passage.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | |
| `text` | string | The verbatim text span |
| `name` | string \| null | |
| `description` | string \| null | |

**Example:**
```
label: EXTRACTION
id:    "ext-001"
text:  "Alice joined IBM as a software engineer in June 1998."
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
| `EXTRACTED_FROM` | ENTITY, FACT, or LITERAL | EXTRACTION | Instance was drawn from this text span |
| `EXTRACTED_FROM` | EXTRACTION | SOURCE | Text span came from this source document |

One EXTRACTION node is created per instance item. The same SOURCE node may be the target of many EXTRACTION→SOURCE edges when multiple items are drawn from the same document.

**Example — provenance chain:**
```
(Alice)    --[EXTRACTED_FROM]--> (ext-001:"Alice joined IBM...")
(fact-001) --[EXTRACTED_FROM]--> (ext-001:"Alice joined IBM...")
(lit-001)  --[EXTRACTED_FROM]--> (ext-001:"...in June 1998.")
(ext-001)  --[EXTRACTED_FROM]--> (src-001:"Alice's résumé")
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
| `SOURCE` | A provenance source document node |
| `EXTRACTION` | A provenance text span node |

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
| `EXTRACTED_FROM` | ENTITY, FACT, LITERAL, or EXTRACTION | EXTRACTION or SOURCE | Provenance link |

---

## 5. Literal Types

| `literal_type` | Python types | Description |
|---------------|-------------|-------------|
| `STRING` | `str` | Default; used for text, names, identifiers |
| `NUMBER` | `int`, `float` | Numeric values |
| `DATE` | `str` (ISO 8601) | Dates and datetimes stored as strings |
| `BOOLEAN` | `bool` | True/false values |
