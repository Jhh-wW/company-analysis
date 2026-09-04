"""수집 산출물의 내부 자료형 — frozen dataclass.

이 자료형은 app 공용 계약으로 나중에 1:1 변환될 엔진 내부 계약이다. 생성 시
빈 문자열·중복 id·SHA-256 형식 오류·구간 겹침을 ValueError로 막는다 — 잘못된
값이 나중 단계로 조용히 흘러가지 않게 하기 위함이다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from core.dart_client import normalize_document_web_url
from features.evidence_collection import constants as c

_SHA256_HEX_LENGTH = 64
_SHA256_HEX_CHARS = frozenset("0123456789abcdef")
_DART_RECEIPT_RE = re.compile(r"[0-9]{14}")
_DART_RAW_LOCATION_RE = re.compile(
    r"raw_xml_chars:([0-9]{1,10})-([0-9]{1,10})"
)
_MAX_OFFICIAL_URL_LENGTH = 2048
_MAX_SOURCE_MEMBER_NAME_LENGTH = 512


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


def _require_iso_date(value: str, field_name: str) -> str:
    """공개 Source까지 그대로 전달할 문서 날짜를 엄격한 ISO 날짜로 잠근다."""

    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise EvidenceCollectionError(f"{field_name}은(는) YYYY-MM-DD 형식이어야 합니다")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise EvidenceCollectionError(
            f"{field_name}은(는) 실제 달력에 존재하는 날짜여야 합니다"
        ) from error
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
        _require_iso_date(self.published_on, "published_on")
        _require_sha256(self.content_sha256, "content_sha256")
        object.__setattr__(
            self,
            "usable_ranges",
            _require_sorted_nonoverlapping_ranges(tuple(self.usable_ranges)),
        )


@dataclass(frozen=True)
class EvidenceFragment:
    """문서 안의 의미 조각 — 장·슬롯 채점 결과까지 실은 최종 단위.

    ★ generation=8 — ``company_id``는 이 조각을
    만든 «조회가 실제로 대상으로 한 회사»를 직접 싣는다. harvest의
    company_id에서 나중에 채워 넣는 값이 아니다 — 수집 시점(collect.py)에
    실제 대상 값을 넣어야 하고, DartEvidenceHarvest가 이 값과 harvest 전체의
    company_id가 다르면 생성을 거절한다(아래). 「회사별로 따로 수집하니
    섞일 리 없다」는 호출자 기억에 기대지 않고 자료형이 직접 막는다.
    """

    company_id: str
    fragment_id: str
    document_id: str
    location: str
    text_sha256: str
    text: str
    section_id: str = ""
    slot_id: str = ""
    score_millis: int = 0
    reason_codes: tuple[str, ...] = ()
    #: 같은 원문 범위가 직접 뒷받침하는 같은 장의 슬롯들. 원문을 슬롯마다
    #: 복제하지 않고 ID 하나로 나르기 위한 계약이다. 빈 값은 레거시
    #: 생성자를 위해 ``(slot_id,)``로 정규화한다.
    covered_slot_ids: tuple[str, ...] = ()
    period_start: str = ""
    period_end: str = ""
    unit: str = ""
    company_scope: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.company_id, "company_id"),
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
        covered_slot_ids = self.covered_slot_ids or (
            (self.slot_id,) if self.slot_id else ()
        )
        if len(set(covered_slot_ids)) != len(covered_slot_ids):
            raise EvidenceCollectionError("covered_slot_ids에 중복을 넣을 수 없습니다")
        if self.slot_id and self.slot_id not in covered_slot_ids:
            raise EvidenceCollectionError("covered_slot_ids는 대표 slot_id를 포함해야 합니다")
        for covered_slot_id in covered_slot_ids:
            if covered_slot_id not in c.ALL_SLOT_IDS:
                raise EvidenceCollectionError(
                    f"알 수 없는 covered_slot_id: {covered_slot_id!r}"
                )
            if c.SLOT_SECTION_OF[covered_slot_id] != self.section_id:
                raise EvidenceCollectionError(
                    "covered_slot_ids는 모두 section_id와 같은 장이어야 합니다"
                )
        object.__setattr__(self, "covered_slot_ids", tuple(covered_slot_ids))
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
    """조회 경로 하나의 결과 — 「수집 장애」와 「자료 없음」을 분리해 남긴다.

    ★ generation=8 — ``company_id``는 EvidenceFragment와 같은 이유로
    필수다: 이 조회가 실제로 대상으로 한 회사를 조회기록 자신이 싣는다.
    """

    company_id: str
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
        _require_nonempty(self.company_id, "company_id")
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
class OfficialUrlCandidate:
    """DART 전문에서 발견했지만 아직 회사 공식 여부는 검증하지 않은 URL."""

    company_id: str
    url: str
    source_document_id: str
    source_receipt_no: str
    source_member_name: str
    source_location: str
    source_document_sha256: str
    source_payload_sha256: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.company_id, "company_id"),
            (self.url, "url"),
            (self.source_document_id, "source_document_id"),
            (self.source_receipt_no, "source_receipt_no"),
            (self.source_member_name, "source_member_name"),
            (self.source_location, "source_location"),
        ):
            _require_nonempty(value, name)
        if (
            len(self.url) > _MAX_OFFICIAL_URL_LENGTH
            or normalize_document_web_url(self.url) != self.url
        ):
            raise EvidenceCollectionError(
                "공식 URL 후보가 DART 원문 URL 정본과 다릅니다"
            )
        if _DART_RECEIPT_RE.fullmatch(self.source_receipt_no) is None:
            raise EvidenceCollectionError("공식 URL 후보의 DART 접수번호 형식이 올바르지 않습니다")
        if not self.source_document_id.endswith(f":{self.source_receipt_no}"):
            raise EvidenceCollectionError("공식 URL 후보의 문서와 DART 접수번호가 다릅니다")
        member_name = self.source_member_name.replace("\\", "/")
        if (
            member_name != self.source_member_name
            or len(member_name) > _MAX_SOURCE_MEMBER_NAME_LENGTH
            or member_name.startswith("/")
            or any(part in {"", ".", ".."} for part in member_name.split("/"))
            or any(ord(character) < 32 for character in member_name)
        ):
            raise EvidenceCollectionError("공식 URL 후보의 ZIP 멤버 이름이 올바르지 않습니다")
        location_match = _DART_RAW_LOCATION_RE.fullmatch(self.source_location)
        if (
            location_match is None
            or int(location_match.group(2)) <= int(location_match.group(1))
        ):
            raise EvidenceCollectionError("공식 URL 후보의 원문 위치 형식이 올바르지 않습니다")
        _require_sha256(self.source_document_sha256, "source_document_sha256")
        _require_sha256(self.source_payload_sha256, "source_payload_sha256")


@dataclass(frozen=True)
class DartEvidenceHarvest:
    """이번 수집 전체 결과 — 문서·조각·시도 기록을 한데 묶는다.

    ``fragments``는 정확한 의미 칸까지 분류된, 보고서 근거로 쓸 수 있는
    조각이다. ``unclassified_fragments``는 원문을 실제로 읽었지만 현재의
    결정론 분류기가 어느 의미 칸인지 증명하지 못한 조각이다. 둘을 한 배열에
    섞으면 뒤 단계가 무분류 문단을 근거로 오인하고, 후자를 버리면 분류기
    어휘가 좁다는 내부 결함을 회사의 자료 부족으로 오인한다. 그래서 별도
    차선으로 끝까지 보존한다.
    """

    company_id: str
    company_type: str
    documents: tuple[CollectedDocument, ...]
    fragments: tuple[EvidenceFragment, ...]
    attempts: tuple[CollectionAttempt, ...]
    unclassified_documents: tuple[CollectedDocument, ...] = ()
    unclassified_fragments: tuple[EvidenceFragment, ...] = ()
    official_url_candidates: tuple[OfficialUrlCandidate, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.company_id, "company_id")
        _require_choice(self.company_type, c.VALID_COMPANY_TYPES, "company_type")
        document_ids = [doc.document_id for doc in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise EvidenceCollectionError("documents의 document_id가 중복됩니다")
        fragment_ids = [
            fragment.fragment_id
            for fragment in (*self.fragments, *self.unclassified_fragments)
        ]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise EvidenceCollectionError("분류·무분류 fragments의 fragment_id가 중복됩니다")
        known_document_ids = set(document_ids)
        for fragment in self.fragments:
            if fragment.document_id not in known_document_ids:
                raise EvidenceCollectionError(
                    f"조각 {fragment.fragment_id}의 document_id가 documents에 없습니다"
                )
            if not fragment.section_id or not fragment.slot_id:
                raise EvidenceCollectionError(
                    f"분류 조각 {fragment.fragment_id}에 장·의미 칸이 없습니다"
                )
        unclassified_document_ids = [
            document.document_id for document in self.unclassified_documents
        ]
        if len(unclassified_document_ids) != len(set(unclassified_document_ids)):
            raise EvidenceCollectionError(
                "unclassified_documents의 document_id가 중복됩니다"
            )
        known_unclassified_document_ids = set(unclassified_document_ids)
        for fragment in self.unclassified_fragments:
            if fragment.document_id not in known_unclassified_document_ids:
                raise EvidenceCollectionError(
                    "무분류 조각의 document_id가 unclassified_documents에 없습니다: "
                    f"{fragment.fragment_id}"
                )
            if (
                fragment.section_id
                or fragment.slot_id
                or fragment.covered_slot_ids
                or fragment.score_millis != 0
            ):
                raise EvidenceCollectionError(
                    f"무분류 조각 {fragment.fragment_id}에 근거 의미를 미리 붙일 수 없습니다"
                )
        # ★ generation=8 — document·fragment·attempt 어느 하나라도 harvest의
        # company_id와 다르면 즉시 거절한다. 같은 document_id·같은 슬롯의
        # 「다른 회사」 조회 결과가 섞여 들어와도 대상 회사의 준비 판정을
        # 조용히 바꾸지 못하게 자료형이 직접 막는다(호출자 기억에 기대지 않음).
        for document in (*self.documents, *self.unclassified_documents):
            if document.company_id != self.company_id:
                raise EvidenceCollectionError(
                    f"문서 {document.document_id}의 company_id가 harvest와 다릅니다"
                )
        for fragment in (*self.fragments, *self.unclassified_fragments):
            if fragment.company_id != self.company_id:
                raise EvidenceCollectionError(
                    f"조각 {fragment.fragment_id}의 company_id가 harvest와 다릅니다"
                )
        for attempt in self.attempts:
            if attempt.company_id != self.company_id:
                raise EvidenceCollectionError(
                    f"시도 {attempt.attempt_id}의 company_id가 harvest와 다릅니다"
                )
        seen_candidates: set[tuple[str, str, str]] = set()
        for candidate in self.official_url_candidates:
            if candidate.company_id != self.company_id:
                raise EvidenceCollectionError("공식 URL 후보의 company_id가 harvest와 다릅니다")
            key = (candidate.url, candidate.source_document_id, candidate.source_location)
            if key in seen_candidates:
                raise EvidenceCollectionError("공식 URL 후보 provenance가 중복됩니다")
            seen_candidates.add(key)
