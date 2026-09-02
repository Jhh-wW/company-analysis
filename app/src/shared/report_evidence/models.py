"""출처 문서부터 장별 근거까지 잃지 않고 나르는 불변 자료형."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    ReportExecutionOutcome,
    SourceRequirement,
    SourceTier,
)


def _require_text(value: str, *, label: str) -> str:
    clean = str(value).strip()
    if not clean:
        raise ValueError(f"{label}은(는) 비워 둘 수 없습니다")
    return clean


def _require_sha256(value: str, *, label: str) -> str:
    clean = str(value).strip()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise ValueError(f"{label} SHA-256 형식이 올바르지 않습니다")
    return clean


def _require_unique_texts(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    clean = tuple(_require_text(value, label=label) for value in values)
    if len(set(clean)) != len(clean):
        raise ValueError(f"{label}에 중복 값을 넣을 수 없습니다")
    return clean


_REASON_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _require_reason_codes(
    values: tuple[str, ...], *, label: str, allow_empty: bool
) -> tuple[str, ...]:
    clean = _require_unique_texts(values, label=label)
    if not allow_empty and not clean:
        raise ValueError(f"{label}은(는) 한 개 이상 필요합니다")
    if any(_REASON_CODE.fullmatch(value) is None for value in clean):
        raise ValueError(
            f"{label}은(는) 영문·숫자·점·밑줄·콜론·하이픈 기계 코드여야 합니다"
        )
    return clean


@dataclass(frozen=True)
class DocumentTextRange:
    """문서 본문에서 근거로 사용할 수 있는 반열림 구간."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("문서 본문 구간은 0 이상이며 끝이 시작보다 커야 합니다")


@dataclass(frozen=True)
class CollectedEvidenceDocument:
    """원문을 실제로 열어 회사 신원과 내용을 확인한 독립 문서 한 건."""

    company_id: str
    document_id: str
    canonical_url: str
    source_tier: SourceTier
    source_kind: str
    publisher: str
    title: str
    published_on: str
    collected_at: str
    content_sha256: str
    exact_evidence_hashes: tuple[str, ...]
    identity_binding: str
    usable_ranges: tuple[DocumentTextRange, ...]
    collector_version: str
    parser_version: str
    requirement: SourceRequirement

    def __post_init__(self) -> None:
        for label, value in (
            ("회사 식별자", self.company_id),
            ("문서 식별자", self.document_id),
            ("정규 URL", self.canonical_url),
            ("출처 종류", self.source_kind),
            ("발행자", self.publisher),
            ("문서 제목", self.title),
            ("수집 시각", self.collected_at),
            ("회사 결속 근거", self.identity_binding),
            ("수집기 버전", self.collector_version),
            ("파서 버전", self.parser_version),
        ):
            _require_text(value, label=label)
        _require_sha256(self.content_sha256, label="문서 내용")
        exact_hashes = _require_unique_texts(
            self.exact_evidence_hashes, label="문서의 정확한 근거 조각 해시"
        )
        if not exact_hashes:
            raise ValueError("수집 문서에는 정확한 근거 조각 해시가 한 개 이상 필요합니다")
        for evidence_hash in exact_hashes:
            _require_sha256(evidence_hash, label="문서의 정확한 근거 조각")
        if not self.usable_ranges:
            raise ValueError("수집 문서에는 실제로 사용할 수 있는 본문 구간이 필요합니다")
        ordered = tuple(sorted(self.usable_ranges, key=lambda item: (item.start, item.end)))
        if ordered != self.usable_ranges:
            raise ValueError("문서 본문 구간은 시작 위치 순서로 저장해야 합니다")
        if any(left.end > right.start for left, right in zip(ordered, ordered[1:])):
            raise ValueError("문서 본문 구간은 서로 겹칠 수 없습니다")


@dataclass(frozen=True)
class EvidenceFragment:
    """출처 문서의 위치와 원문 해시에 묶인 장별 근거 한 조각.

    한 원문 범위가 같은 장의 여러 질문에 답할 수 있다. 그때 원문을 슬롯마다
    복제하지 않고 ``covered_slot_ids``로 커버리지만 함께 싣는다.
    """

    company_id: str
    fragment_id: str
    document_id: str
    location: str
    text_sha256: str
    text: str
    section_id: str
    slot_id: str
    score_millis: int
    reason_codes: tuple[str, ...]
    period_start: str = ""
    period_end: str = ""
    unit: str = ""
    company_scope: str = ""
    covered_slot_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label, value in (
            ("회사 식별자", self.company_id),
            ("근거 조각 식별자", self.fragment_id),
            ("원본 문서 식별자", self.document_id),
            ("원문 위치", self.location),
            ("근거 원문", self.text),
            ("장 식별자", self.section_id),
            ("의미 칸 식별자", self.slot_id),
        ):
            _require_text(value, label=label)
        digest = _require_sha256(self.text_sha256, label="근거 원문")
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != digest:
            raise ValueError("근거 원문과 저장된 SHA-256이 일치하지 않습니다")
        if not 0 <= self.score_millis <= 1000:
            raise ValueError("근거 선택 점수는 0부터 1000 사이의 정수여야 합니다")
        _require_reason_codes(
            self.reason_codes, label="근거 선택 사유 코드", allow_empty=False
        )
        covered = self.covered_slot_ids or (self.slot_id,)
        _require_unique_texts(covered, label="근거 조각이 채우는 의미 칸")
        if self.slot_id not in covered:
            raise ValueError("근거 조각이 채우는 의미 칸은 대표 의미 칸을 포함해야 합니다")
        object.__setattr__(self, "covered_slot_ids", tuple(covered))


@dataclass(frozen=True)
class CollectionAttempt:
    """근거를 못 찾은 경우까지 보존하는 출처 조회 기록."""

    company_id: str
    attempt_id: str
    source_kind: str
    requirement: SourceRequirement
    state: CollectionState
    slot_ids: tuple[str, ...]
    reason_code: str
    elapsed_ms: int = 0
    bytes_downloaded: int = 0
    documents_seen: int = 0

    def __post_init__(self) -> None:
        _require_text(self.company_id, label="회사 식별자")
        _require_text(self.attempt_id, label="수집 시도 식별자")
        _require_text(self.source_kind, label="수집 출처 종류")
        _require_unique_texts(self.slot_ids, label="수집 대상 의미 칸")
        if not self.slot_ids:
            raise ValueError("수집 시도에는 확인하려던 의미 칸이 필요합니다")
        _require_reason_codes(
            (self.reason_code,), label="수집 결과 사유 코드", allow_empty=False
        )
        if min(self.elapsed_ms, self.bytes_downloaded, self.documents_seen) < 0:
            raise ValueError("수집 시간·바이트·문서 수는 음수일 수 없습니다")


@dataclass(frozen=True)
class ChapterEvidenceCandidates:
    """수집 생산부가 한 장에 제안한 문서·조각·조회 결과."""

    company_id: str
    section_id: str
    documents: tuple[CollectedEvidenceDocument, ...]
    fragments: tuple[EvidenceFragment, ...]
    attempts: tuple[CollectionAttempt, ...]
    candidate_readiness: EvidenceReadiness
    reason_codes: tuple[str, ...]
    estimated_tokens: int
    max_chars: int
    max_estimated_tokens: int

    def __post_init__(self) -> None:
        _require_text(self.company_id, label="회사 식별자")
        _require_text(self.section_id, label="장 식별자")
        _require_reason_codes(
            self.reason_codes, label="후보 판정 사유 코드", allow_empty=True
        )
        if self.max_chars <= 0 or self.max_estimated_tokens <= 0:
            raise ValueError("장별 문자·예상 토큰 예산은 0보다 커야 합니다")
        if not 0 <= self.estimated_tokens <= self.max_estimated_tokens:
            raise ValueError("장별 예상 토큰은 0 이상이며 선언한 상한 이하여야 합니다")
        document_ids = tuple(document.document_id for document in self.documents)
        _require_unique_texts(document_ids, label="문서 식별자")
        if any(document.company_id != self.company_id for document in self.documents):
            raise ValueError("다른 회사의 문서를 한 장 후보에 섞을 수 없습니다")
        fragment_ids = tuple(fragment.fragment_id for fragment in self.fragments)
        _require_unique_texts(fragment_ids, label="근거 조각 식별자")
        if any(fragment.company_id != self.company_id for fragment in self.fragments):
            raise ValueError("다른 회사의 근거 조각을 한 장 후보에 섞을 수 없습니다")
        if any(fragment.section_id != self.section_id for fragment in self.fragments):
            raise ValueError("다른 장의 근거 조각을 한 장 후보에 섞을 수 없습니다")
        unknown_documents = sorted(
            {
                fragment.document_id
                for fragment in self.fragments
                if fragment.document_id not in set(document_ids)
            }
        )
        if unknown_documents:
            raise ValueError(
                "근거 조각의 원본 문서가 후보 문서 목록에 없습니다: "
                + ", ".join(unknown_documents)
            )
        documents_by_id = {
            document.document_id: document for document in self.documents
        }
        unbound_fragments = sorted(
            fragment.fragment_id
            for fragment in self.fragments
            if fragment.text_sha256
            not in documents_by_id[fragment.document_id].exact_evidence_hashes
        )
        if unbound_fragments:
            raise ValueError(
                "근거 조각 해시가 원본 문서의 허용 목록에 없습니다: "
                + ", ".join(unbound_fragments)
            )
        attempt_ids = tuple(attempt.attempt_id for attempt in self.attempts)
        _require_unique_texts(attempt_ids, label="수집 시도 식별자")
        if any(attempt.company_id != self.company_id for attempt in self.attempts):
            raise ValueError("다른 회사의 수집 시도를 한 장 후보에 섞을 수 없습니다")
        if sum(len(fragment.text) for fragment in self.fragments) > self.max_chars:
            raise ValueError("장별 근거 원문이 선언한 문자 예산을 초과했습니다")


@dataclass(frozen=True)
class InjectedSlotFacts:
    """Codex의 구조화 검증기가 한 의미 칸에 주입한 사실 ID."""

    slot_id: str
    fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.slot_id, label="주입 의미 칸 식별자")
        _require_unique_texts(self.fact_ids, label="주입 사실 식별자")
        if not self.fact_ids:
            raise ValueError("주입 의미 칸에는 검증된 사실이 한 개 이상 필요합니다")


@dataclass(frozen=True)
class SectionEvidenceBundle:
    """Codex가 최종 판정한 한 장의 작성 입력."""

    company_id: str
    section_id: str
    required_slot_ids: tuple[str, ...]
    filled_slot_ids: tuple[str, ...]
    missing_slot_ids: tuple[str, ...]
    documents: tuple[CollectedEvidenceDocument, ...]
    fragments: tuple[EvidenceFragment, ...]
    injected_slot_facts: tuple[InjectedSlotFacts, ...]
    readiness: EvidenceReadiness
    reason_codes: tuple[str, ...]
    estimated_tokens: int
    max_chars: int
    max_estimated_tokens: int

    def __post_init__(self) -> None:
        _require_text(self.company_id, label="회사 식별자")
        _require_text(self.section_id, label="장 식별자")
        required = _require_unique_texts(
            self.required_slot_ids, label="필수 의미 칸 식별자"
        )
        if not required:
            raise ValueError("한 장에는 필수 의미 칸이 한 개 이상 필요합니다")
        filled = _require_unique_texts(self.filled_slot_ids, label="채운 의미 칸")
        missing = _require_unique_texts(self.missing_slot_ids, label="빈 의미 칸")
        if set(filled) & set(missing):
            raise ValueError("같은 의미 칸을 채움과 비움으로 동시에 기록할 수 없습니다")
        if set(filled) | set(missing) != set(required):
            raise ValueError("채운 칸과 빈 칸은 필수 의미 칸 전체를 정확히 나눠야 합니다")
        if self.readiness is EvidenceReadiness.READY and missing:
            raise ValueError("빈 필수 의미 칸이 있는 장을 READY로 만들 수 없습니다")
        if self.readiness is not EvidenceReadiness.READY and not missing:
            raise ValueError("모든 필수 의미 칸이 찼다면 장 준비 상태는 READY여야 합니다")
        _require_reason_codes(
            self.reason_codes, label="최종 근거 판정 사유 코드", allow_empty=True
        )
        if self.max_chars <= 0 or self.max_estimated_tokens <= 0:
            raise ValueError("장별 문자·예상 토큰 예산은 0보다 커야 합니다")
        if not 0 <= self.estimated_tokens <= self.max_estimated_tokens:
            raise ValueError("장별 예상 토큰은 0 이상이며 선언한 상한 이하여야 합니다")
        document_ids = tuple(document.document_id for document in self.documents)
        _require_unique_texts(document_ids, label="문서 식별자")
        if any(document.company_id != self.company_id for document in self.documents):
            raise ValueError("다른 회사의 문서를 최종 근거 묶음에 섞을 수 없습니다")
        fragment_ids = tuple(fragment.fragment_id for fragment in self.fragments)
        _require_unique_texts(fragment_ids, label="근거 조각 식별자")
        if any(fragment.company_id != self.company_id for fragment in self.fragments):
            raise ValueError("다른 회사의 근거 조각을 최종 근거 묶음에 섞을 수 없습니다")
        if any(fragment.section_id != self.section_id for fragment in self.fragments):
            raise ValueError("다른 장의 근거 조각을 최종 근거 묶음에 섞을 수 없습니다")
        unknown_fragment_slots = sorted(
            {
                slot_id
                for fragment in self.fragments
                for slot_id in fragment.covered_slot_ids
                if slot_id not in set(required)
            }
        )
        if unknown_fragment_slots:
            raise ValueError(
                "필수 정책에 없는 의미 칸의 근거 조각을 최종 묶음에 넣을 수 없습니다: "
                + ", ".join(unknown_fragment_slots)
            )
        unknown_documents = sorted(
            {
                fragment.document_id
                for fragment in self.fragments
                if fragment.document_id not in set(document_ids)
            }
        )
        if unknown_documents:
            raise ValueError(
                "최종 근거 조각의 원본 문서가 문서 목록에 없습니다: "
                + ", ".join(unknown_documents)
            )
        documents_by_id = {
            document.document_id: document for document in self.documents
        }
        unbound_fragments = sorted(
            fragment.fragment_id
            for fragment in self.fragments
            if fragment.text_sha256
            not in documents_by_id[fragment.document_id].exact_evidence_hashes
        )
        if unbound_fragments:
            raise ValueError(
                "최종 근거 조각 해시가 원본 문서의 허용 목록에 없습니다: "
                + ", ".join(unbound_fragments)
            )
        injected_slot_ids = tuple(item.slot_id for item in self.injected_slot_facts)
        _require_unique_texts(injected_slot_ids, label="주입 의미 칸")
        if set(injected_slot_ids) - set(required):
            raise ValueError("필수 정책에 없는 의미 칸의 사실을 주입할 수 없습니다")


@dataclass(frozen=True)
class GenerationGateDecision:
    """9장 전체가 준비된 경우에만 유료 작성을 허용하는 결정."""

    company_id: str
    status: GenerationGateStatus
    outcome: ReportExecutionOutcome | None
    required_section_ids: tuple[str, ...]
    ready_section_ids: tuple[str, ...]
    insufficient_section_ids: tuple[str, ...]
    unknown_section_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.company_id, label="회사 식별자")
        required = _require_unique_texts(
            self.required_section_ids, label="필수 장 식별자"
        )
        if not required:
            raise ValueError("생성 게이트에는 필수 장이 한 개 이상 필요합니다")
        partitions = (
            self.ready_section_ids,
            self.insufficient_section_ids,
            self.unknown_section_ids,
        )
        for values, label in zip(
            partitions, ("준비된 장", "근거 부족 장", "확인 못 한 장")
        ):
            _require_unique_texts(values, label=label)
        ready_set, insufficient_set, unknown_set = map(set, partitions)
        if (
            ready_set & insufficient_set
            or ready_set & unknown_set
            or insufficient_set & unknown_set
        ):
            raise ValueError("한 장을 서로 다른 생성 게이트 상태에 중복 기록할 수 없습니다")
        if ready_set | insufficient_set | unknown_set != set(required):
            raise ValueError("생성 게이트 판정은 필수 장 전체를 정확히 나눠야 합니다")
        _require_reason_codes(
            self.reason_codes, label="생성 게이트 사유 코드", allow_empty=True
        )
        expected = {
            GenerationGateStatus.READY_FOR_GENERATION: None,
            GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE: (
                ReportExecutionOutcome.INSUFFICIENT_EVIDENCE
            ),
            GenerationGateStatus.STOP_TRANSIENT_FAILURE: (
                ReportExecutionOutcome.TRANSIENT_FAILURE
            ),
        }[self.status]
        if self.outcome is not expected:
            raise ValueError("생성 게이트 상태와 최종 중단 결과가 맞지 않습니다")

    @property
    def can_call_ai(self) -> bool:
        """아홉 장이 모두 준비된 경우에만 참이다."""

        return self.status is GenerationGateStatus.READY_FOR_GENERATION
