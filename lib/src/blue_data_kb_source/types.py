from __future__ import annotations

from dataclasses import dataclass, field


class KBError(Exception):
    """Base exception for all KBDB errors."""


class SchemaError(KBError):
    """Raised for schema validation or consistency failures."""


class KnowledgeError(KBError):
    """Raised for knowledge-layer write or query failures."""


class ExtractionError(KBError):
    """Raised for LLM extraction failures."""


class ProvenanceError(KBError):
    """Raised for provenance-tracking failures."""


@dataclass
class SchemaSearchResult:
    id: str
    type: str
    name: str
    description: str


@dataclass
class AnswerResult:
    entity: str | None = None
    relation: str | None = None
    value: str | None = None
    provenance: str | None = None


@dataclass
class ValidationResult:
    supporting: list[dict] = field(default_factory=list)
    counter: list[dict] = field(default_factory=list)


@dataclass
class EnrichmentResult:
    entity: str | None = None
    relation: str | None = None
    value: str | None = None
    distance: int = 0
