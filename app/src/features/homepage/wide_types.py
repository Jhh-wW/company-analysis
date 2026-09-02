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
  바로 쓸 수 있는 형태가 아니다.
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

#: fragment.slot_id에 허용하는 어휘 — 이 collector 전용
#: (정본은 `app/src/shared/report_evidence/policy.py`). comparison_*·limitation·
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
    #: 출처 등급 — 결속된 공식 웹은 TIER_1, 공식 HTML의 exact 외부 IR
    #: 첨부는 TIER_3 낮은 신뢰 provenance다. 후자는 OPTIONAL이며 필수
    #: 슬롯 조각으로 승격하지 않는다.
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
    """조회 경로 하나의 시도 기록.

    ★ 계약 generation=8: ``company_id``는 이 attempt가 실제로 조회를 시도한
      대상 회사를 그 자리(``wide_collect._CollectionState.add_attempt``)에서
      직접 싣는다 — document의 company_id에서 나중에 채워 넣거나, 변환 단계
      (``wide_evidence_mapping``)에서 호출 인자로 덮어쓰지 않는다. 같은
      document_id·슬롯이라도 다른 회사 조회 결과가 섞이면 안 되므로, 앱
      계약은 대상 회사와 다른 값이면 생성 즉시 거절한다.
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
        _require_nonblank(self.company_id, "company_id")
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
        if not self.slot_ids:
            # : 빈 slot_ids는 앱 계약(CollectionAttempt)이 생성 즉시 거절한다.
            # 특정 슬롯을 좁혀낼 수 없는 attempt(robots·sitemap·전체 truncation
            # 등)도 「영향받은 닫힌 collector slot 집합」을 항상 명시해야 한다 —
            # 좁혀낼 수 없으면 허용 어휘 전체를 쓴다(호출자 책임, wide_collect.py
            # `_ALL_SLOT_IDS_FALLBACK` 참조). 여기서 빈 값을 조용히 통과시키면
            # 잘못된 데이터가 다음 모듈까지 흘러가 통합 경계에서야 걸린다.
            raise ValueError("slot_ids가 비어 있습니다(허용 어휘 중 최소 1개는 명시해야 합니다)")
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
    ★ 계약 generation=8: ``company_id``는 이 조각이 실제로 나온 조회가
      대상으로 한 회사를 조각 생성 자리(``wide_fragments.build_fragments``
      호출자)에서 직접 실어야 한다 — ``document.company_id``에서 나중에
      채워 넣거나 변환 단계에서 덮어쓰지 않는다. 같은 document_id·슬롯이라도
      다른 회사 조회 결과가 섞이면 안 되므로, 앱 계약은 대상 회사와 다른
      값이면 생성 즉시 거절한다.
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
    covered_slot_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "company_id",
            "fragment_id",
            "document_id",
            "location",
            "text",
            "section_id",
            "slot_id",
        ):
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
        covered = self.covered_slot_ids or (self.slot_id,)
        if len(set(covered)) != len(covered):
            raise ValueError("covered_slot_ids에 중복을 넣을 수 없습니다")
        if self.slot_id not in covered:
            raise ValueError("covered_slot_ids는 대표 slot_id를 포함해야 합니다")
        if any(slot_id not in _ALLOWED_SLOT_IDS for slot_id in covered):
            raise ValueError("covered_slot_ids에 허용 어휘 밖 값이 있습니다")
        if any(slot_id.split(":", 1)[0] != self.section_id for slot_id in covered):
            raise ValueError("covered_slot_ids는 모두 같은 장이어야 합니다")
        object.__setattr__(self, "covered_slot_ids", tuple(covered))
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
    """수집 한 번의 전체 결과 — 대상 회사·문서 목록·시도 기록 목록.

    ★ 계약 generation=8 마지막 고리: ``company_id``는
      ``collect_official_web_documents(company_id=...)``가 호출 인자로
      받은 값을 그대로 싣는 **정본**이다. documents·attempts에서
      역산하지 않는다 — 문서 생성부에 버그가 생겨 문서들이 일제히 엉뚱한
      회사 값을 가져도 서로는 일치하므로, 역산 방식은 그 오류를 못
      잡는다. 이 필드가 독립된 정본이라야 대조가 의미를 갖는다.
    """

    company_id: str
    documents: tuple[WideDocumentIdentity, ...]
    attempts: tuple[WideCollectionAttempt, ...]

    def __post_init__(self) -> None:
        _require_nonblank(self.company_id, "company_id")
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
            if document.company_id != self.company_id:
                # 내부 모순 — 조용히 하나를 골라 감추지 않는다.
                raise ValueError(
                    "document.company_id가 결과의 company_id와 다릅니다(내부 불일치): "
                    f"document_id={document.document_id} document.company_id={document.company_id!r} "
                    f"result.company_id={self.company_id!r}"
                )
        seen_attempt_ids: set[str] = set()
        for attempt in self.attempts:
            if not isinstance(attempt, WideCollectionAttempt):
                raise ValueError("attempts 안에 WideCollectionAttempt가 아닌 값이 있습니다")
            if attempt.attempt_id in seen_attempt_ids:
                raise ValueError(f"attempt_id가 중복되었습니다: {attempt.attempt_id}")
            seen_attempt_ids.add(attempt.attempt_id)
            if attempt.company_id != self.company_id:
                raise ValueError(
                    "attempt.company_id가 결과의 company_id와 다릅니다(내부 불일치): "
                    f"attempt_id={attempt.attempt_id} attempt.company_id={attempt.company_id!r} "
                    f"result.company_id={self.company_id!r}"
                )
