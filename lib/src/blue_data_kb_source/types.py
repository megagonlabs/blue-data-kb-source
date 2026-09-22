from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


class KBError(Exception):
    """Base exception for all blue-kb errors."""


class SchemaError(KBError):
    """Raised for schema validation or consistency failures."""


class KnowledgeError(KBError):
    """Raised for knowledge-layer write or query failures."""


class ExtractionError(KBError):
    """Raised for LLM extraction failures."""


class ProvenanceError(KBError):
    """Raised for provenance-tracking failures."""


class IngestionError(KBError):
    """Raised when an assert's resolve phase fails; nothing has been written.

    `candidates` carries what an ambiguous name was ambiguous between (empty
    for non-ambiguity failures such as judge errors).
    """

    def __init__(self, message: str, candidates: list[ResolutionCandidate] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


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
class NodeRef:
    node_id: str
    label: str
    name: str
    extraction_id: str | None = None    # provenance for this node


@dataclass
class AssertResult:
    node_id: str                        # the thing you asserted
    created: bool
    match_type: str | None = None       # MatchType; "structural" on a fact-dedupe hit;
                                        # None when created, and always on edge asserts
    extraction_id: str | None = None
    created_nodes: list[NodeRef] = field(default_factory=list)
    widened_relations: list[NodeRef] = field(default_factory=list)


@dataclass
class IngestFailure:
    arm: str                            # "facts", "entities", ...
    index: int                          # row position within that arm
    reason: str
    candidates: list[ResolutionCandidate] = field(default_factory=list)


@dataclass
class IngestReport:
    written: int = 0                    # rows that created their subject
    deduped: int = 0                    # existing item, new evidence attached (structural)
    skipped: int = 0                    # existing item, nothing written
    failures: list[IngestFailure] = field(default_factory=list)
    created: dict[str, int] = field(default_factory=dict)   # label -> count, full cascade
    widened: list[str] = field(default_factory=list)        # relation names; the audit
                                                            # surface for bad rows


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
