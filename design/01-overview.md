# blue-kb — Overview

## Objective

Agentic systems accumulate knowledge from a wide range of sources — documents, conversations, databases, and tool outputs. This knowledge is inherently relational: entities have types, properties, and relationships to other entities. Capturing it in a way that is structured, queryable, and attributable to its sources is essential for building agents that reason reliably and transparently over what they know.

## What blue-kb Does

blue-kb provides a typed knowledge-base layer for agentic pipelines. Specifically, it enables a system to:

- Define and evolve a typed schema of entity classes, relation types, and properties — a structured vocabulary for the domain, either declared directly, grown from free text using an LLM, or created on demand when ingested knowledge names something the schema lacks
- Populate the knowledge base with entity instances, factual relations between entities, and property values, all grounded in the schema
- Track the provenance of every piece of knowledge back to its source text and originating document
- Ingest ready-made statements from outside — an external extractor, an agent, another knowledge base, or a person — by name, matching each to what the graph already holds and creating what it lacks, schema included
- Extract structured knowledge from free text using an LLM, against the schema as it currently stands
- Query the knowledge base at the schema level and the instance level, using structured graph queries or natural-language questions that an LLM answers, validates, and enriches by traversing the graph
- Notify downstream components in real time when new knowledge is added

## Design Philosophy

blue-kb treats the knowledge base as a formal database — with a well-defined schema, typed data, and a clear separation between schema elements and instance data. The schema is not fixed upfront: it evolves as new data sources are processed and new concepts emerge, however knowledge arrives — written directly by a developer, extracted from free text by an LLM, or ingested ready-made from outside and matched by name. Growing the schema and populating it with data are deliberately kept as two distinct operations, even when a single high-level call triggers both in sequence: schema is declared or grown first, instances are written only against the schema as it then stands, and each operation can be driven on its own. The structure of the knowledge base itself is represented by a consistent meta-schema, giving any tool or agent a uniform way to introspect and work with the KB's own graph structure.
