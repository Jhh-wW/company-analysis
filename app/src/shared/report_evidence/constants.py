"""근거 준비 계약의 버전과 기계 상태값."""

from __future__ import annotations

from enum import Enum
from typing import Final


EVIDENCE_CONTRACT_VERSION: Final[str] = "report-evidence-v1"

# 여러 feature와 실서비스 adapter가 함께 쓰는 source_kind 정본. 생산자별
# 문자열을 소비자가 접두어로 추측하지 않게 공식 수집 경계의 닫힌 어휘를
# 전부 여기 둔다. analysis_engine은 app을 import할 수 없으므로 DART 다섯 값의
# 사본을 가지며, 별도 완전성 시험이 두 목록의 일치를 강제한다.
SOURCE_KIND_DART_BUSINESS_REPORT: Final[str] = "dart_business_report"
SOURCE_KIND_DART_AUDIT_REPORT: Final[str] = "dart_audit_report"
SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT: Final[str] = (
    "dart_consolidated_audit_report"
)
SOURCE_KIND_DART_SEMIANNUAL_REPORT: Final[str] = "dart_semiannual_report"
SOURCE_KIND_DART_QUARTERLY_REPORT: Final[str] = "dart_quarterly_report"
SOURCE_KIND_OFFICIAL_WEB_PAGE: Final[str] = "official_web_page"
SOURCE_KIND_OFFICIAL_RECRUIT_PAGE: Final[str] = "official_recruit_page"
SOURCE_KIND_OFFICIAL_IR_PDF: Final[str] = "official_ir_pdf"
#: DART root와 다른 등록 도메인이지만, 실제 HTML에서 DART 법인명과
#: 사업자/법인등록번호를 함께 재검증한 보조 공식 페이지. 기존
#: ``official_web_page``(DART root 계열 REQUIRED)와 타입을 나눠
#: OPTIONAL 의미가 조용히 섞이지 않게 한다.
SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE: Final[str] = (
    "official_identity_verified_web_page"
)
OFFICIAL_WEB_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        SOURCE_KIND_OFFICIAL_WEB_PAGE,
        SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
        SOURCE_KIND_OFFICIAL_IR_PDF,
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    }
)

FORMAL_DOCUMENT_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_AUDIT_REPORT,
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
        *OFFICIAL_WEB_SOURCE_KINDS,
    }
)

# 「그 출처 전체를 아예 확인할 수 없었다」는 site-probe 게이트 시도의
# source_kind 정본(P1-B). robots.txt 차단은 그 호스트의 모든 후보 페이지
# 조회를 원천 차단하는 유일한 지점 — 개별 후보 페이지 하나가 404거나
# IR PDF가 없는 것과는 질이 다른 실패다. build_section_bundle이 requirement
# («이 경로가 유일한 확인 길인가»)와 outcome-kind(«막힌 것인가, 없는
# 것인가»)를 분리할 때, 「막힘」쪽 신호를 이 목록으로 좁혀서 본다 — 그러지
# 않으면 흔한 개별 후보 페이지 실패(정상적으로도 자주 있는 일)까지
# 매 슬롯을 UNKNOWN으로 끌어내려 「IR 1건 실패가 9장을 다 죽이던 P0」가
# 되살아난다.
SOURCE_KIND_ROBOTS_TXT: Final[str] = "robots_txt"
SITE_PROBE_GATE_SOURCE_KINDS: Final[frozenset[str]] = frozenset({SOURCE_KIND_ROBOTS_TXT})
FORMAL_ATTEMPT_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {*FORMAL_DOCUMENT_SOURCE_KINDS, *SITE_PROBE_GATE_SOURCE_KINDS}
)


class CollectionState(str, Enum):
    """외부 자료 한 경로를 확인한 결과."""

    OK = "OK"
    MISSING = "MISSING"
    FAILED = "FAILED"
    TRUNCATED = "TRUNCATED"


class EvidenceReadiness(str, Enum):
    """한 장을 쓸 근거가 준비됐는지에 대한 판정."""

    READY = "READY"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class SourceRequirement(str, Enum):
    """해당 조회 경로가 필수 의미를 확인하는 유일한 길인지 구분한다."""

    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class SourceTier(str, Enum):
    """제품 정책으로 허용한 출처 등급."""

    TIER_1_OFFICIAL = "TIER_1_OFFICIAL"
    TIER_2_PUBLIC = "TIER_2_PUBLIC"
    TIER_3_TRUSTED = "TIER_3_TRUSTED"


class ReportExecutionOutcome(str, Enum):
    """사용자 요청이 최종적으로 끝나는 네 가지 상태."""

    COMPLETE = "COMPLETE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    QUALITY_FAILURE = "QUALITY_FAILURE"


class GenerationGateStatus(str, Enum):
    """유료 보고서 작성기를 부르기 직전의 결정."""

    READY_FOR_GENERATION = "READY_FOR_GENERATION"
    STOP_INSUFFICIENT_EVIDENCE = "STOP_INSUFFICIENT_EVIDENCE"
    STOP_TRANSIENT_FAILURE = "STOP_TRANSIENT_FAILURE"


class ReleaseMode(str, Enum):
    """새 계약이 기존 사용자 결과에 미치는 범위."""

    SHADOW = "SHADOW"
    ENFORCE_NO_PARTIAL = "ENFORCE_NO_PARTIAL"
    FULL = "FULL"
