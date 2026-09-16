# Quick Start

The fastest way to see the knowledge base in action is the `kb` CLI — no Python code required. See [installation.md](installation.md) if you haven't installed anything yet.

```bash
cd lib && bash scripts/install.sh
cd ../platform/cli && bash scripts/install.sh
```

## 1. Create a database

Databases persist as JSON files under `~/.kb/` by default (override with `export BLUE_DATA_KB_SOURCE_DATA_DIR=/path/to/dir`, or `kb profile config BLUE_DATA_KB_SOURCE_DATA_DIR /path/to/dir`).

```bash
kb database create main
kb database select main
```

## 2. Declare a schema

Classes, relations, and properties — the typed vocabulary your data will use.

```bash
kb schema class add PERSON "A human individual"
kb schema class add COMPANY "A business organization"
kb schema relation add WORKED_AT "Employment relationship" --domain PERSON --range COMPANY
kb schema class property add AGE "Age of a person" --class PERSON
```

## 3. Add data

Entities and facts, referenced by name — no IDs to track by hand. `--text` records the verbatim source span for provenance.

```bash
kb data entity add --name Alice --class PERSON --text "Alice is a software engineer."
kb data entity add --name IBM --class COMPANY

kb data fact add --head Alice --relation WORKED_AT --tail IBM \
    --text "Alice joined IBM in June 1998."

kb data entity property set 30 --entity Alice --property AGE --type NUMBER
```

## 4. Look at what you built

```bash
kb schema list
kb data list
```

```
      Entities
┏━━━━━━━┳━━━━━━━━━┓
┃ Name  ┃ Class   ┃
┡━━━━━━━╇━━━━━━━━━┩
│ IBM   │ COMPANY │
│ Alice │ PERSON  │
└───────┴─────────┘
        Facts
┏━━━━━━━┳━━━━━━━━━━━┳━━━━━━┓
┃ Head  ┃ Relation  ┃ Tail ┃
┡━━━━━━━╇━━━━━━━━━━━╇━━━━━━┩
│ Alice │ WORKED_AT │ IBM  │
└───────┴───────────┴──────┘
```

## 5. Ask questions (needs `OPENAI_API_KEY`)

Store the key in your profile once, rather than exporting it in every shell — it is written to `~/.kb/.profiles` (mode `0600`) and never echoed back:

```bash
kb profile config OPENAI_API_KEY        # prompts; enter the key, or $OPENAI_API_KEY to reference the variable

kb data answer "Where did Alice work?"
kb data validate "Alice worked at IBM"
```

An exported `OPENAI_API_KEY` still works and takes precedence. See [cli.md](cli.md#profiles-and-configuration) for the full parameter list.

`answer`/`validate` run an LLM agent that searches and traverses the graph — no query language to learn. Add `--include-metadata` to also see when each fact was added and what it was extracted from.

## 6. Or let an LLM read the text for you

Instead of steps 2–3, `kb data extract` can populate schema and data itself from a passage:

```bash
kb data extract \
    "Alice joined IBM as a software engineer in June 1998. She later moved to Google in 2005." \
    --query "Where did Alice work?" --query "When did she start?"
```

---

## Using the library directly

Everything above is a thin wrapper over the Python library. The same walkthrough in code:

```python
import asyncio
from blue_data_kb_source import MemoryKBSource

async def main():
    kb = MemoryKBSource(name="quickstart")
    kb.create_database("main")

    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )

    alice = await kb.add_entity("main", "Alice", class_id=person, text="Alice is a software engineer.")
    ibm = await kb.add_entity("main", "IBM", class_id=company)
    await kb.add_fact("main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at,
                       text="Alice joined IBM in June 1998.")

    print(await kb.fetch_database_data("main", compact=True))

asyncio.run(main())
```

See [guide.md](guide.md) for the full walkthrough (provenance, search, change hooks, export/import) and [api-reference.md](api-reference.md) for every method.

---

## Connecting an LLM agent (MCP)

To let Claude or another MCP client build and query a knowledge base directly:

```bash
cd platform/mcp && bash scripts/install.sh
blue-data-kb-mcp
```

See [mcp.md](mcp.md) for the Claude Desktop config and full tool reference.

---

## Next steps

- [features.md](features.md) — what the library can do, at a glance
- [guide.md](guide.md) — task-oriented walkthrough of the Python API
- [cli.md](cli.md) — full `kb` command reference
- [mcp.md](mcp.md) — full MCP tool reference
