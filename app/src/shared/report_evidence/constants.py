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
SOURCE_KIND_NEWS: Final[str] = "news"
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

# 언론 보도는 공개 산문의 인용·부록에만 쓰는 보조 문서다. 공식 문서 목록과
# 합치면 숫자·표·회사 정체성·동일조건 비교까지 채울 수 있으므로 별도 닫힌
# 목록으로 둔다.
SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {SOURCE_KIND_NEWS}
)

# 언론 보조 문장을 아예 받지 않는 장. 9장은 「회사가 밝힌 차별점」이라 회사가
# 스스로 밝힌 말만 싣는 장이고, 기자가 쓴 비교나 증권 칼럼의 해석은 그 정의에
# 맞지 않는다. 기간만 1년으로 두는 뉴스 확장 창 목록과는 뜻이 다르므로(그쪽은
# 조건부로 대상이 된다) 하나로 합치지 않는다.
#
# ★ 왜 shared에 있나 — 이 목록을 «수집하는 쪽»(news_intake)과 «보고서에 싣는
#   쪽»(composer)이 둘 다 본다. 어느 한 feature 안에 두면 다른 feature가 그
#   feature를 직접 import하게 되고(경계 위반), 목록을 베껴 적으면 한쪽만
#   바뀔 때 조용히 어긋난다. 정본은 여기 하나뿐이고 news_intake는 별명으로
#   같은 객체를 다시 내보낸다.
NEWS_EXCLUDED_SECTION_IDS: Final[frozenset[str]] = frozenset({"competitive_position"})

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


#: 수집 시도 식별자는 생산부가 ``<종류>-<일련번호>``로 만든다. 요약부는
#: 「sitemap 목록 조회」와 「본문 페이지 조회」를 갈라야 하는데 둘 다 같은
#: source_kind로 기록되므로, 이 접두어가 유일한 구분 근거다. 소비자가 문자열을
#: 접두어로 «추측»하지 않게 생산자·소비자가 같은 상수를 참조한다.
ATTEMPT_KIND_SITEMAP: Final[str] = "sitemap"
ATTEMPT_ID_KIND_SEPARATOR: Final[str] = "-"


def is_sitemap_attempt(attempt_id: object) -> bool:
    """sitemap 목록 조회 시도인지 식별자 접두어로 판정한다.

    sitemap은 «후보를 찾는 보조 목록»이다. robots가 막거나 403·404여도 본문
    페이지를 못 읽었다는 뜻이 아니다. 그래서 「오류」 판정과 후보 범위 완전성
    계산에서 본문 페이지 실패와 같이 세면 안 된다 — 같이 세면 자바스크립트로
    그리는 사이트처럼 sitemap만 막힌 회사가 「자료 없음」에서 「오류」로
    나빠진다.
    """

    return str(attempt_id or "").startswith(
        f"{ATTEMPT_KIND_SITEMAP}{ATTEMPT_ID_KIND_SEPARATOR}"
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
    TIER_SUPPLEMENTARY = "TIER_SUPPLEMENTARY"


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
