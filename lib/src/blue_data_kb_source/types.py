from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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
    score: float | None = None


@dataclass
class DataSearchResult:
    id: str
    type: str                      # "ENTITY" | "FACT"
    name: str
    description: str | None = None
    class_name: str | None = None  # entities only
    head_name: str | None = None   # facts only
    relation_name: str | None = None
    tail_name: str | None = None
    score: float | None = None     # populated for mode="semantic"


MatchType = Literal["exact", "normalized", "alias", "search", "structural"]
# ResolutionCandidate emits only the name-based values: "structural" appears only on
# fact-dedupe results, and "alias" is reserved for the future alias layer.


@dataclass
class ResolutionCandidate:
    node_id: str
    name: str
    label: str                     # ENTITY | CLASS | RELATION | PROPERTY
    match_type: MatchType
    score: float | None = None     # populated for match_type="search" under a semantic index


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
