"""근거 재판정 요청·검증 결과의 불변 자료형."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReclassifyDiagnostics:
    """프롬프트 절단과 응답 항목 검증 결과를 함께 나르는 진단."""

    total_items: int = 0
    accepted_items: int = 0
    rejected_items: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    prompt_chars: int = 0
    candidate_paragraphs_total: int = 0
    candidate_paragraphs_included: int = 0
    candidate_paragraphs_truncated: int = 0

    @property
    def total_count(self) -> int:
        return self.total_items

    @property
    def rejected_count(self) -> int:
        return self.rejected_items

    @property
    def reason_counts(self) -> dict[str, int]:
        return dict(self.rejected_by_reason)


@dataclass(frozen=True)
class CandidateParagraph:
    """프롬프트와 검증이 같은 원문·식별자를 보도록 정규화한 후보."""

    paragraph_id: str
    text: str
    source: dict[str, Any]
    input_index: int
    is_unclassified: bool
    is_preferred_section: bool
    score_millis: int


@dataclass(frozen=True)
class ReclassifyRequest:
    """호출자가 원하는 LLM에 넘길 프롬프트와 structured-output 스키마."""

    prompt: str
    schema: dict[str, Any]
    candidate_paragraph_ids: tuple[str, ...]
    diagnostics: ReclassifyDiagnostics

    @property
    def json_schema(self) -> dict[str, Any]:
        return self.schema


@dataclass(frozen=True)
class ReclassifyAssignment:
    paragraph_id: str
    section_id: str
    slot_id: str
    quote: str
    exact_quote: str
    quote_start: int
    quote_end: int


@dataclass(frozen=True)
class ReclassifyRemoval:
    paragraph_id: str
    section_id: str
    reason: str


@dataclass(frozen=True)
class ReclassifyRejectedItem:
    item_type: str
    item_index: int
    paragraph_id: str
    reason_code: str
    item: dict[str, Any]


@dataclass(frozen=True)
class ReclassifyResult:
    assignments: tuple[ReclassifyAssignment, ...]
    removals: tuple[ReclassifyRemoval, ...]
    rejected: tuple[ReclassifyRejectedItem, ...]
    diagnostics: ReclassifyDiagnostics
    candidate_paragraphs: tuple[CandidateParagraph, ...] = field(
        default=(), repr=False
    )

    @property
    def accepted(self) -> tuple[ReclassifyAssignment, ...]:
        return self.assignments

    @property
    def additions(self) -> tuple[ReclassifyAssignment, ...]:
        return self.assignments
