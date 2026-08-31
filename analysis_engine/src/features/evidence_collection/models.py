"""수집 산출물의 내부 자료형 — frozen dataclass.

이 자료형은 app 공용 계약으로 나중에 1:1 변환될 엔진 내부 계약이다. 생성 시
빈 문자열·중복 id·SHA-256 형식 오류·구간 겹침을 ValueError로 막는다 — 잘못된
값이 나중 단계로 조용히 흘러가지 않게 하기 위함이다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from features.evidence_collection import constants as c

_SHA256_HEX_LENGTH = 64
_SHA256_HEX_CHARS = frozenset("0123456789abcdef")


class EvidenceCollectionError(ValueError):
    """수집 자료형 생성 규칙을 어긴 값 — 근거 없이 채우지 않기 위한 방어선."""


def _require_nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceCollectionError(f"{field_name}은(는) 빈 문자열일 수 없습니다")
    return value


def _require_choice(value: str, choices: frozenset[str], field_name: str) -> str:
    if value not in choices:
        raise EvidenceCollectionError(f"{field_name} 값이 허용된 목록에 없습니다: {value!r}")
    return value


def _require_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_HEX_LENGTH or any(
        ch not in _SHA256_HEX_CHARS for ch in value
    ):
        raise EvidenceCollectionError(f"{field_name}은(는) 소문자 16진수 SHA-256이어야 합니다")
    return value


def _require_nonnegative_int(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvidenceCollectionError(f"{field_name}은(는) 0 이상의 정수여야 합니다")
    return value


@dataclass(frozen=True)
class DocumentTextRange:
    """문서 원문에서 실제로 쓸 수 있는 구간 — 목차·면책은 여기 안 들어간다."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not isinstance(self.start, int) or isinstance(self.start, bool):
            raise EvidenceCollectionError("DocumentTextRange.start는 정수여야 합니다")
        if not isinstance(self.end, int) or isinstance(self.end, bool):
            raise EvidenceCollectionError("DocumentTextRange.end는 정수여야 합니다")
        if self.start < 0 or self.end <= self.start:
            raise EvidenceCollectionError("DocumentTextRange는 0<=start<end 를 지켜야 합니다")


def _require_sorted_nonoverlapping_ranges(
    ranges: tuple[DocumentTextRange, ...],
) -> tuple[DocumentTextRange, ...]:
    ordered = tuple(sorted(ranges, key=lambda r: (r.start, r.end)))
    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            raise EvidenceCollectionError("usable_ranges 구간이 서로 겹칩니다")
    return ordered


@dataclass(frozen=True)
class CollectedDocument:
    """문서 하나의 신원 — 왜 이 회사 것인지(identity_binding)까지 포함."""

    company_id: str
    document_id: str
    canonical_url: str
    source_tier: str
    source_kind: str
    publisher: str
    title: str
    published_on: str
    collected_at: str
    content_sha256: str
    identity_binding: str
    usable_ranges: tuple[DocumentTextRange, ...]
    collector_version: str
    parser_version: str
    requirement: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.company_id, "company_id"),
            (self.document_id, "document_id"),
            (self.canonical_url, "canonical_url"),
            (self.source_kind, "source_kind"),
            (self.publisher, "publisher"),
            (self.title, "title"),
            (self.published_on, "published_on"),
            (self.collected_at, "collected_at"),
            (self.identity_binding, "identity_binding"),
            (self.collector_version, "collector_version"),
            (self.parser_version, "parser_version"),
        ):
            _require_nonempty(value, name)
        _require_choice(self.source_tier, c.VALID_SOURCE_TIERS, "source_tier")
        _require_choice(self.requirement, c.VALID_REQUIREMENTS, "requirement")
        _require_sha256(self.content_sha256, "content_sha256")
        object.__setattr__(
            self,
            "usable_ranges",
            _require_sorted_nonoverlapping_ranges(tuple(self.usable_ranges)),
        )


@dataclass(frozen=True)
class EvidenceFragment:
    """문서 안의 의미 조각 — 장·슬롯 채점 결과까지 실은 최종 단위."""

    fragment_id: str
    document_id: str
    location: str
    text_sha256: str
    text: str
    section_id: str = ""
    slot_id: str = ""
    score_millis: int = 0
    reason_codes: tuple[str, ...] = ()
    period_start: str = ""
    period_end: str = ""
    unit: str = ""
    company_scope: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.fragment_id, "fragment_id"),
            (self.document_id, "document_id"),
            (self.location, "location"),
            (self.text, "text"),
        ):
            _require_nonempty(value, name)
        _require_sha256(self.text_sha256, "text_sha256")
        actual = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if actual != self.text_sha256:
            raise EvidenceCollectionError("text_sha256이 text의 실제 SHA-256과 일치하지 않습니다")
        if self.section_id and self.section_id not in c.SECTION_IDS:
            raise EvidenceCollectionError(f"알 수 없는 section_id: {self.section_id!r}")
        if self.slot_id:
            if self.slot_id not in c.ALL_SLOT_IDS:
                raise EvidenceCollectionError(f"알 수 없는 slot_id: {self.slot_id!r}")
            if not self.section_id:
                raise EvidenceCollectionError("slot_id가 있으면 section_id도 있어야 합니다")
            if c.SLOT_SECTION_OF[self.slot_id] != self.section_id:
                raise EvidenceCollectionError("slot_id가 가리키는 장이 section_id와 다릅니다")
        if not isinstance(self.score_millis, int) or isinstance(self.score_millis, bool) or not (
            0 <= self.score_millis <= 1000
        ):
            raise EvidenceCollectionError("score_millis는 0~1000 사이의 정수여야 합니다")
        if not self.reason_codes:
            raise EvidenceCollectionError("reason_codes는 1개 이상이어야 합니다")
        for code in self.reason_codes:
            if not c.REASON_CODE_PATTERN.fullmatch(code):
                raise EvidenceCollectionError(f"reason_code 형식이 올바르지 않습니다: {code!r}")


@dataclass(frozen=True)
class CollectionAttempt:
    """조회 경로 하나의 결과 — 「수집 장애」와 「자료 없음」을 분리해 남긴다."""

    attempt_id: str
    source_kind: str
    requirement: str
    state: str
    slot_ids: tuple[str, ...]
    reason_code: str
    elapsed_ms: int
    bytes_downloaded: int
    documents_seen: int

    def __post_init__(self) -> None:
        _require_nonempty(self.attempt_id, "attempt_id")
        _require_nonempty(self.source_kind, "source_kind")
        _require_choice(self.requirement, c.VALID_REQUIREMENTS, "requirement")
        _require_choice(self.state, c.VALID_ATTEMPT_STATES, "state")
        if not self.slot_ids:
            raise EvidenceCollectionError("slot_ids는 1개 이상이어야 합니다")
        for slot_id in self.slot_ids:
            if slot_id not in c.ALL_SLOT_IDS:
                raise EvidenceCollectionError(f"알 수 없는 slot_id: {slot_id!r}")
        if not c.REASON_CODE_PATTERN.fullmatch(self.reason_code):
            raise EvidenceCollectionError(f"reason_code 형식이 올바르지 않습니다: {self.reason_code!r}")
        _require_nonnegative_int(self.elapsed_ms, "elapsed_ms")
        _require_nonnegative_int(self.bytes_downloaded, "bytes_downloaded")
        _require_nonnegative_int(self.documents_seen, "documents_seen")


@dataclass(frozen=True)
class DartEvidenceHarvest:
    """이번 수집 전체 결과 — 문서·조각·시도 기록을 한데 묶는다."""

    company_id: str
    company_type: str
    documents: tuple[CollectedDocument, ...]
    fragments: tuple[EvidenceFragment, ...]
    attempts: tuple[CollectionAttempt, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.company_id, "company_id")
        _require_choice(self.company_type, c.VALID_COMPANY_TYPES, "company_type")
        document_ids = [doc.document_id for doc in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise EvidenceCollectionError("documents의 document_id가 중복됩니다")
        fragment_ids = [fragment.fragment_id for fragment in self.fragments]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise EvidenceCollectionError("fragments의 fragment_id가 중복됩니다")
        known_document_ids = set(document_ids)
        for fragment in self.fragments:
            if fragment.document_id not in known_document_ids:
                raise EvidenceCollectionError(
                    f"조각 {fragment.fragment_id}의 document_id가 documents에 없습니다"
                )
