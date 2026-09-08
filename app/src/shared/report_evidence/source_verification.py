"""출처 검증 기능과 보고서 공개 판정이 공유하는 읽기 전용 계약."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SourceVerification:
    """정본 출처 검증기가 확인한 신원이며 공개 판정 자체는 포함하지 않는다."""

    source_id: str
    number: int
    document_identity: str
    content_sha256: str
    formal_kind: str
    exact_evidence_hashes: tuple[str, ...]
    official: bool
    news: bool
    evidence_bound: bool


class SourceVerifier(Protocol):
    """기능 간 직접 import 없이 실제 등록부와 원문 결속을 검증한다."""

    def __call__(
        self,
        source: object,
        registry: tuple[object, ...],
        *,
        reference_date: str,
        evidence_text: str,
    ) -> SourceVerification | None: ...
