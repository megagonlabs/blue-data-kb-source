# KBDB — Coding Style

## Tooling

| Concern | Tool | Config |
|---------|------|--------|
| Formatting | `ruff format` | `pyproject.toml` |
| Linting | `ruff` | `pyproject.toml` |
| Type checking | `pyright` (strict) | `pyproject.toml` |

All three run in CI. `ruff format` and `ruff` are also expected to pass locally before committing.

### `pyproject.toml` configuration

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]   # pycodestyle, pyflakes, isort, pyupgrade

[tool.pyright]
pythonVersion = "3.10"
typeCheckingMode = "strict"
venvPath = "."
venv = ".venv"
```

---

## Type Hints

Type hints are required on all public methods, public class attributes, and module-level constants. Private methods must also be annotated.

Use Python 3.10+ syntax throughout — no imports from `typing` where newer syntax applies:

```python
# correct
def declare_class(self, database: str, name: str, description: str) -> str: ...
def add_entity(self, database: str, name: str, class_id: str | None = None) -> str: ...

# incorrect
from typing import Optional
def add_entity(self, database: str, name: str, class_id: Optional[str] = None) -> str: ...
```

All packages must include a `py.typed` marker file to declare inline type information.

---

## Async

All I/O-bound methods are `async def`. This applies uniformly across all layers — `KBSource`, `SchemaManager`, `KnowledgeManager`, `ProvenanceTracker`, `ExtractionEngine` — not just at interface boundaries. Graph Store access and OpenAI calls are both I/O-bound.

```python
# correct — async throughout
class KBSource:
    async def declare_class(self, database: str, name: str, description: str) -> str: ...
    async def add_entity(self, database: str, name: str, ...) -> str: ...

class SchemaManager:
    async def declare_class(self, database: str, name: str, description: str) -> str: ...

class ExtractionEngine:
    async def import_text(self, database: str, text: str, ...) -> None: ...

# incorrect — sync in core, async only at interface
class KBSource:
    def declare_class(self, database: str, name: str, description: str) -> str: ...
```

**CLI bridge** — Click commands are synchronous. Use `asyncio.run()` to call async library methods from CLI handlers:

```python
@click.command()
def declare(name: str, description: str) -> None:
    class_id = asyncio.run(kb.declare_class("main", name, description))
    console.print(class_id)
```

---

## Naming

| Construct | Convention | Example |
|-----------|-----------|---------|
| Classes | PascalCase | `SchemaManager`, `KnowledgeManager` |
| Methods / functions | snake_case | `declare_class`, `import_text` |
| Module-level constants | UPPER_SNAKE_CASE | `CLASS`, `RELATION`, `IS_INSTANCE_OF` |
| Private members | single `_` prefix | `_node_name`, `_fire_change` |
| Modules / packages | lowercase | `schema/`, `manager.py` |

Node label constants and edge type constants (`CLASS`, `RELATION`, `IS_INSTANCE_OF`, etc.) are UPPER_SNAKE_CASE because they are string constants used as typed identifiers throughout the graph, not enum values.

Do not use double-underscore (`__`) name mangling. Use `_` for all private members.

---

## Imports

- Absolute imports only within the package; no relative imports.
- Order: stdlib → third-party → internal, each group separated by a blank line.
- No wildcard imports (`from x import *`).

```python
from __future__ import annotations

from collections.abc import Callable

import openai

from blue_data_kb_source.types import AnswerResult, ValidationResult
```

Import ordering is enforced by `ruff` (isort rules).

---

## Docstrings

One-line docstring on public classes and public methods only. No docstrings on private methods, `__init__`, properties, or anything self-explanatory from the name. No multi-line parameter documentation — types belong in signatures.

```python
class SchemaManager:
    """Manages the typed vocabulary of a knowledge base database."""

    async def declare_class(self, database: str, name: str, description: str) -> str:
        """Declare a CLASS node; return its ID (idempotent — existing name returns existing ID)."""
        ...

    async def _lookup_by_name(self, database: str, label: str, name: str) -> str | None:
        # no docstring — private method
        ...
```

---

## Comments

Write a comment only when the *why* is non-obvious: a hidden constraint, a subtle invariant, or a workaround for a specific external behaviour. Never describe what the code does — well-named identifiers already do that.

```python
# correct — explains a non-obvious constraint
# OpenAI tool calls may arrive in batches; process all before continuing the loop
# to avoid interleaving schema declarations with instance creation calls
for tool_call in message.tool_calls:
    results.append(await self._dispatch(tool_call))

# incorrect — restates the code
# look up the node by name
existing = await self._lookup_by_name(database, label, name)
```

---

## Error Handling

A `KBError` base exception is defined in `types.py`. All custom exceptions inherit from it.

```python
class KBError(Exception): ...
class SchemaError(KBError): ...
class KnowledgeError(KBError): ...
class ExtractionError(KBError): ...
class ProvenanceError(KBError): ...
```

- Do not catch and swallow exceptions silently.
- Let exceptions propagate to interface boundaries, where each interface handles them appropriately:
  - **REST API** — FastAPI exception handlers convert `KBError` subtypes to HTTP responses.
  - **MCP server** — tool handlers catch `KBError` and return structured error responses.
  - **CLI** — top-level Click error handler renders `KBError` messages via Rich.
- Only catch specific exception types; never bare `except:` or `except Exception:`.
