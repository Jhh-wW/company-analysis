"""넓은 공식 웹 수집 결과의 계약(자료형).

★ 필드 이름은 지시받은 명세를 그대로 따른다 — 나중에 앱 공용 계약으로
  1:1 변환되므로 여기서 임의로 이름을 바꾸지 않는다.
★ 빈 문자열·중복 ID·형식이 어긋난 값은 객체 생성 즉시(``__post_init__``)
  한국어 ``ValueError``로 막는다. 잘못된 데이터가 다음 모듈까지 조용히
  흘러가면 안 된다(§원칙: 근거계약 — 자료 부족과 확인 실패를 분리한다).

설계 결정 — usable_ranges:
  document identity 명세에는 본문 원문을 담는 별도 필드 이름이 없고
  ``usable_ranges``(「본문 구간 — nav·footer boilerplate 제외」)만 있다.
  그래서 이 필드를 «경계를 뺀 실제 본문 텍스트 조각들의 튜플»로 구현한다
  (문자 오프셋 쌍이 아니라 텍스트 그 자체). 숫자 오프셋 쌍으로 두면 원문을
  같이 들고 다녀야 해서 계약이 하나 더 늘고, 다음 모듈(장별 근거 변환)이
  바로 쓸 수 있는 형태가 아니다. 이 해석은 팀 리드에게 최종 보고에서
  명시적으로 알린다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from src.features.homepage.constants import WIDE_REQUIRED_SLOT_IDS

_SHA256_HEX: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_ATTEMPT_ID: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")

REQUIREMENT_REQUIRED = "REQUIRED"
REQUIREMENT_OPTIONAL = "OPTIONAL"
_REQUIREMENTS = frozenset({REQUIREMENT_REQUIRED, REQUIREMENT_OPTIONAL})

ATTEMPT_STATE_OK = "OK"
ATTEMPT_STATE_MISSING = "MISSING"
ATTEMPT_STATE_FAILED = "FAILED"
ATTEMPT_STATE_TRUNCATED = "TRUNCATED"
_ATTEMPT_STATES = frozenset(
    {ATTEMPT_STATE_OK, ATTEMPT_STATE_MISSING, ATTEMPT_STATE_FAILED, ATTEMPT_STATE_TRUNCATED}
)

#: 출처 등급 — «후보»일 뿐이며 최종 확정은 통합 담당의 몫이다(브리핑 §1).
SOURCE_TIER_1_OFFICIAL = "TIER_1_OFFICIAL"
SOURCE_TIER_2_PUBLIC = "TIER_2_PUBLIC"
SOURCE_TIER_3_TRUSTED = "TIER_3_TRUSTED"
_SOURCE_TIERS = frozenset({SOURCE_TIER_1_OFFICIAL, SOURCE_TIER_2_PUBLIC, SOURCE_TIER_3_TRUSTED})

#: fragment.slot_id에 허용하는 어휘 — 이 collector 전용(팀 리드 2026-08-31 통보,
#: 정본은 `app/src/shared/report_evidence/policy.py`). comparison_*·limitation·
#: historical_performance는 이 집합에 없으므로 애초에 만들어질 수 없다.
_ALLOWED_SLOT_IDS: frozenset[str] = frozenset(WIDE_REQUIRED_SLOT_IDS)


def _require_nonblank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 값이 비어 있거나 문자열이 아닙니다")
    return value


def _require_requirement(value: object, field_name: str = "requirement") -> str:
    if value not in _REQUIREMENTS:
        raise ValueError(
            f"{field_name} 값이 올바르지 않습니다(REQUIRED/OPTIONAL만 허용): {value!r}"
        )
    return value  # type: ignore[return-value]


@dataclass(frozen=True)
class WideDocumentIdentity:
    """넓은 공식 웹 수집이 확인한 문서 하나의 신원 계약."""

    company_id: str
    document_id: str
    canonical_url: str
    source_kind: str
    publisher: str
    title: str
    #: 발행일을 알 수 없으면 빈 문자열을 허용한다(필드 자체는 항상 유지).
    published_on: str
    collected_at: str
    content_sha256: str
    identity_binding: str
    usable_ranges: tuple[str, ...]
    collector_version: str
    parser_version: str
    requirement: str
    #: 출처 등급 — 이 수집기가 매기는 값은 항상 ``SOURCE_TIER_1_OFFICIAL``
    #: «후보»뿐이다. 최종 확정은 통합 담당의 몫이다(브리핑 §1).
    source_tier: str

    def __post_init__(self) -> None:
        for name in (
            "company_id",
            "document_id",
            "canonical_url",
            "source_kind",
            "publisher",
            "title",
            "collected_at",
            "content_sha256",
            "identity_binding",
            "collector_version",
            "parser_version",
        ):
            _require_nonblank(getattr(self, name), name)
        if not isinstance(self.published_on, str):
            raise ValueError("published_on은 문자열이어야 합니다(모르면 빈 문자열)")
        if not _SHA256_HEX.match(self.content_sha256):
            raise ValueError(
                "content_sha256 형식이 올바르지 않습니다(64자리 소문자 16진수)"
            )
        _require_requirement(self.requirement)
        if self.source_tier not in _SOURCE_TIERS:
            raise ValueError(
                "source_tier 값이 올바르지 않습니다"
                "(TIER_1_OFFICIAL/TIER_2_PUBLIC/TIER_3_TRUSTED만 허용): "
                f"{self.source_tier!r}"
            )
        if not isinstance(self.usable_ranges, tuple):
            raise ValueError("usable_ranges는 tuple[str, ...]이어야 합니다")
        if not self.usable_ranges:
            raise ValueError("usable_ranges가 비어 있습니다(본문 구간이 하나도 없음)")
        for item in self.usable_ranges:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("usable_ranges 안에 비어 있는 구간이 있습니다")


@dataclass(frozen=True)
class WideCollectionAttempt:
    """조회 경로 하나의 시도 기록."""

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
        _require_nonblank(self.attempt_id, "attempt_id")
        if not _ATTEMPT_ID.match(self.attempt_id):
            raise ValueError("attempt_id 형식이 올바르지 않습니다")
        _require_nonblank(self.source_kind, "source_kind")
        _require_requirement(self.requirement)
        if self.state not in _ATTEMPT_STATES:
            raise ValueError(
                f"state 값이 올바르지 않습니다(OK/MISSING/FAILED/TRUNCATED만 허용): "
                f"{self.state!r}"
            )
        if not isinstance(self.slot_ids, tuple):
            raise ValueError("slot_ids는 tuple[str, ...]이어야 합니다")
        for item in self.slot_ids:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("slot_ids 안에 비어 있는 값이 있습니다")
        _require_nonblank(self.reason_code, "reason_code")
        if not _REASON_CODE.match(self.reason_code):
            raise ValueError(
                "reason_code 형식이 올바르지 않습니다"
                "(^[A-Za-z0-9_.:-]{1,100}$ 만 허용)"
            )
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms는 0 이상이어야 합니다")
        if self.bytes_downloaded < 0:
            raise ValueError("bytes_downloaded는 0 이상이어야 합니다")
        if self.documents_seen < 0:
            raise ValueError("documents_seen는 0 이상이어야 합니다")


@dataclass(frozen=True)
class WideFragment:
    """usable_ranges 구간 하나에 슬롯을 매긴 조각(fragment).

    ★ ``location``은 ``canonical_url#조각index`` 형식이다 — index는 그
      조각이 나온 문서의 ``usable_ranges`` 안 위치를 가리킨다(0부터 시작).
    ★ ``text_sha256``은 생성 시점에 ``text``의 실제 SHA-256과 일치하는지
      검증한다 — 둘이 어긋나면 조용히 통과시키지 않고 즉시 막는다.
    ★ ``slot_id``는 이 collector 전용 어휘(``_ALLOWED_SLOT_IDS``, 정본은
      `app/src/shared/report_evidence/policy.py`)만 허용한다.
      comparison_*·limitation·historical_performance는 이 어휘에 없으므로
      만들 수 없다.
    """

    fragment_id: str
    document_id: str
    location: str
    text_sha256: str
    text: str
    section_id: str
    slot_id: str
    score_millis: int
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("fragment_id", "document_id", "location", "text", "section_id", "slot_id"):
            _require_nonblank(getattr(self, name), name)
        if "#" not in self.location:
            raise ValueError("location은 'canonical_url#조각index' 형식이어야 합니다")
        _prefix, _separator, index_part = self.location.rpartition("#")
        if not index_part.isdigit():
            raise ValueError("location의 조각index는 0 이상 정수여야 합니다")
        if not _SHA256_HEX.match(self.text_sha256):
            raise ValueError(
                "text_sha256 형식이 올바르지 않습니다(64자리 소문자 16진수)"
            )
        actual_sha256 = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if actual_sha256 != self.text_sha256:
            raise ValueError("text_sha256이 text의 실제 SHA-256과 일치하지 않습니다")
        if self.slot_id not in _ALLOWED_SLOT_IDS:
            raise ValueError(f"slot_id가 허용 어휘 밖입니다: {self.slot_id!r}")
        if self.section_id != self.slot_id.split(":", 1)[0]:
            raise ValueError("section_id가 slot_id의 장(section)과 일치하지 않습니다")
        if not (0 <= self.score_millis <= 1000):
            raise ValueError("score_millis는 0~1000 사이여야 합니다")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise ValueError("reason_codes는 1개 이상인 tuple[str, ...]이어야 합니다")
        for code in self.reason_codes:
            if not isinstance(code, str) or not _REASON_CODE.match(code):
                raise ValueError(
                    "reason_codes 형식이 올바르지 않습니다"
                    "(^[A-Za-z0-9_.:-]{1,100}$ 만 허용)"
                )


@dataclass(frozen=True)
class WideCollectionResult:
    """수집 한 번의 전체 결과 — 문서 목록과 시도 기록 목록."""

    documents: tuple[WideDocumentIdentity, ...]
    attempts: tuple[WideCollectionAttempt, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.documents, tuple):
            raise ValueError("documents는 tuple[WideDocumentIdentity, ...]이어야 합니다")
        if not isinstance(self.attempts, tuple):
            raise ValueError("attempts는 tuple[WideCollectionAttempt, ...]이어야 합니다")
        seen_document_ids: set[str] = set()
        for document in self.documents:
            if not isinstance(document, WideDocumentIdentity):
                raise ValueError("documents 안에 WideDocumentIdentity가 아닌 값이 있습니다")
            if document.document_id in seen_document_ids:
                raise ValueError(f"document_id가 중복되었습니다: {document.document_id}")
            seen_document_ids.add(document.document_id)
        seen_attempt_ids: set[str] = set()
        for attempt in self.attempts:
            if not isinstance(attempt, WideCollectionAttempt):
                raise ValueError("attempts 안에 WideCollectionAttempt가 아닌 값이 있습니다")
            if attempt.attempt_id in seen_attempt_ids:
                raise ValueError(f"attempt_id가 중복되었습니다: {attempt.attempt_id}")
            seen_attempt_ids.add(attempt.attempt_id)
