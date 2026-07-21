# KBDB — Overview

## Objective

Agentic systems accumulate knowledge from a wide range of sources — documents, conversations, databases, and tool outputs. This knowledge is inherently relational: entities have types, properties, and relationships to other entities. Capturing it in a way that is structured, queryable, and attributable to its sources is essential for building agents that reason reliably and transparently over what they know.

## What KBDB Does

KBDB provides a typed knowledge-base layer for agentic pipelines. Specifically, it enables a system to:

- Define and evolve a typed schema of entity classes, relation types, and properties — a structured vocabulary for the domain
- Populate the knowledge base with entity instances, factual relations between entities, and property values, all grounded in the schema
- Track the provenance of every piece of knowledge back to its source text and originating document
- Extract structured knowledge from free text using an LLM, guided by and contributing to the current schema
- Query the knowledge base at the schema level and the instance level, using structured graph queries or natural-language questions that an LLM answers, validates, and enriches by traversing the graph
- Notify downstream components in real time when new knowledge is added

## Design Philosophy

KBDB treats the knowledge base as a formal database — with a well-defined schema, typed data, and a clear separation between schema elements and instance data. The schema is not fixed upfront: it evolves as new data sources are processed and new concepts emerge, and each ingestion pass is both guided by the current schema and may contribute new elements to it. The structure of the knowledge base itself is represented by a consistent meta-schema, giving any tool or agent a uniform way to introspect and work with the KB's own graph structure.
