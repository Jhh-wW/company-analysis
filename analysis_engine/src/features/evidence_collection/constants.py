"""수집 엔진의 이름 있는 상수 — DART 근거수집 feature 전용.

★ 엔진 경계: 이 feature는 app 패키지를 import하지 않는다(요구사항 9번,
`tests/test_boundaries.py`가 강제). 아래 section_id·slot_id 어휘는
`app/src/features/composer/constants.py`의 `SECTION_IDS`·`CLAIM_SLOTS_BY_SECTION`
값을 그대로 옮긴 «엔진 사본»이다 — import가 아니라 값 복사다. 정본이 바뀌면
이 파일도 같이 바꿔야 한다.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

from core.dart_client import (
    DOCUMENT_MEMBER_MAX_BYTES,
    DOCUMENT_ZIP_MAX_MEMBERS,
    DOCUMENT_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES,
    TIMEOUT_DOCUMENT_SEC,
    ZIP_MEMBER_MAX_COMPRESSION_RATIO,
)

# ══════════════════════════════════════════════════════════
# 장·슬롯 어휘 — composer/constants.py 사본 (값 임의 변경 금지)
# ══════════════════════════════════════════════════════════

SECTION_IDS: Final[tuple[str, ...]] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
    "competitive_position",
)

CLAIM_SLOTS_BY_SECTION: Final[dict[str, tuple[str, ...]]] = {
    "identity": (
        "identity:corporate_identity", "identity:business_definition",
        "identity:legal_scope", "identity:official_location", "identity:self_positioning",
    ),
    "business_model": (
        "business_model:revenue_model", "business_model:customer_type",
        "business_model:sales_channel", "business_model:regional_mix",
        "business_model:value_exchange",
    ),
    "portfolio": (
        "portfolio:product_role", "portfolio:portfolio_priority",
        "portfolio:customer_fit", "portfolio:revenue_link", "portfolio:lifecycle_stage",
    ),
    "past_changes": (
        "past_changes:historical_performance", "past_changes:completed_execution",
        "past_changes:cumulative_change", "past_changes:change_context",
        "past_changes:change_limit",
    ),
    "current_challenges": (
        "current_challenges:issue", "current_challenges:response",
        "current_challenges:initial_signal", "current_challenges:unresolved_gap",
        "current_challenges:next_check",
    ),
    "future_strategy": (
        "future_strategy:stated_plan", "future_strategy:plan_status",
        "future_strategy:plan_timing", "future_strategy:plan_condition",
        "future_strategy:execution_signal",
    ),
    "operations_partners": (
        "operations_partners:value_chain", "operations_partners:operating_role",
        "operations_partners:supply_relation", "operations_partners:distribution_relation",
        "operations_partners:partnership",
    ),
    "culture": (
        "culture:leadership", "culture:work_principle", "culture:decision_process",
        "culture:organization_change", "culture:verified_case",
    ),
    "competitive_position": (
        "competitive_position:comparison_target", "competitive_position:comparison_metric",
        "competitive_position:comparison_basis", "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    ),
}

ALL_SLOT_IDS: Final[frozenset[str]] = frozenset(
    slot_id for slots in CLAIM_SLOTS_BY_SECTION.values() for slot_id in slots
)

#: 슬롯 id → 소속 장 id. 조각의 slot_id·section_id 정합성 검사에 쓴다.
SLOT_SECTION_OF: Final[dict[str, str]] = {
    slot_id: section_id
    for section_id, slots in CLAIM_SLOTS_BY_SECTION.items()
    for slot_id in slots
}

# ══════════════════════════════════════════════════════════
# 자료형에 쓰는 닫힌 값 목록
# ══════════════════════════════════════════════════════════

SOURCE_TIER_OFFICIAL: Final[str] = "TIER_1_OFFICIAL"
VALID_SOURCE_TIERS: Final[frozenset[str]] = frozenset({SOURCE_TIER_OFFICIAL})

REQUIREMENT_REQUIRED: Final[str] = "REQUIRED"
REQUIREMENT_OPTIONAL: Final[str] = "OPTIONAL"
VALID_REQUIREMENTS: Final[frozenset[str]] = frozenset({REQUIREMENT_REQUIRED, REQUIREMENT_OPTIONAL})

ATTEMPT_STATE_OK: Final[str] = "OK"
ATTEMPT_STATE_MISSING: Final[str] = "MISSING"
ATTEMPT_STATE_FAILED: Final[str] = "FAILED"
ATTEMPT_STATE_TRUNCATED: Final[str] = "TRUNCATED"
VALID_ATTEMPT_STATES: Final[frozenset[str]] = frozenset({
    ATTEMPT_STATE_OK, ATTEMPT_STATE_MISSING, ATTEMPT_STATE_FAILED, ATTEMPT_STATE_TRUNCATED,
})

COMPANY_TYPE_LISTED: Final[str] = "listed"
COMPANY_TYPE_AUDIT_ONLY: Final[str] = "audit_only"
COMPANY_TYPE_FINANCIAL: Final[str] = "financial"
VALID_COMPANY_TYPES: Final[frozenset[str]] = frozenset({
    COMPANY_TYPE_LISTED, COMPANY_TYPE_AUDIT_ONLY, COMPANY_TYPE_FINANCIAL,
})

#: CollectionAttempt.reason_code · EvidenceFragment.reason_codes 공통 형식.
REASON_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")

# ══════════════════════════════════════════════════════════
# 공시 종류(source_kind)와 조회 순서
# ══════════════════════════════════════════════════════════

SOURCE_KIND_BUSINESS_REPORT: Final[str] = "dart_business_report"
SOURCE_KIND_AUDIT_REPORT: Final[str] = "dart_audit_report"
SOURCE_KIND_SEMIANNUAL_REPORT: Final[str] = "dart_semiannual_report"
SOURCE_KIND_QUARTERLY_REPORT: Final[str] = "dart_quarterly_report"


class FilingKindSpec(NamedTuple):
    """공시 종류 하나를 어떻게 찾을지 — DART pblntf_ty·이름 키워드·기본 요구도."""

    source_kind: str
    pblntf_ty: str
    name_keyword: str
    requirement: str


#: ★ 2026-08-31 — 기존 `analysis_engine/tools/survey_audit_reports.py`의
#:   `FILING_LOOKUP_DEFAULT`(사업보고서 우선, 감사보고서 폴백) 안전선을 그대로
#:   따른다. 상장 여부로 조회 순서를 가르지 않는다 — 이 feature는 그 구분을
#:   미리 알지 못하고(그것이 오히려 이 feature의 산출인 company_type이다),
#:   사업보고서가 없으면 감사보고서로 넘어가는 폴백만 지킨다.
FILING_KIND_SPECS: Final[tuple[FilingKindSpec, ...]] = (
    FilingKindSpec(SOURCE_KIND_BUSINESS_REPORT, "A", "사업보고서", REQUIREMENT_REQUIRED),
    FilingKindSpec(SOURCE_KIND_AUDIT_REPORT, "F", "감사보고서", REQUIREMENT_REQUIRED),
    FilingKindSpec(SOURCE_KIND_SEMIANNUAL_REPORT, "A", "반기보고서", REQUIREMENT_OPTIONAL),
    FilingKindSpec(SOURCE_KIND_QUARTERLY_REPORT, "A", "분기보고서", REQUIREMENT_OPTIONAL),
)

FILING_KIND_SPEC_BY_SOURCE_KIND: Final[dict[str, FilingKindSpec]] = {
    spec.source_kind: spec for spec in FILING_KIND_SPECS
}

#: 원본 1건만 고르는 «필수» 단계 — 사업보고서를 먼저 보고, 없을 때만 감사보고서로.
PRIMARY_LOOKUP_ORDER: Final[tuple[str, ...]] = (SOURCE_KIND_BUSINESS_REPORT, SOURCE_KIND_AUDIT_REPORT)
#: 보충 자료 — 있으면 더하고 없어도 실패로 보지 않는다(OPTIONAL).
SUPPLEMENT_LOOKUP_ORDER: Final[tuple[str, ...]] = (SOURCE_KIND_SEMIANNUAL_REPORT, SOURCE_KIND_QUARTERLY_REPORT)

#: 「[첨부정정]」 공시의 원본 zip엔 본문이 없고 고친 첨부만 있다(survey_audit_reports.py
#: 실측과 동일 근거) — 본문이 없으므로 통째로 후보에서 뺀다.
EXCLUDED_REPORT_NAME_MARKERS: Final[tuple[str, ...]] = ("첨부정정",)
#: 연결재무제표만 담은 별도 공시는 본문 성격이 달라 제외한다(run_pilot.py와 동일 안전선).
CONSOLIDATED_REPORT_NAME_MARKER: Final[str] = "연결"
#: 내용을 고치는 «기재정정»만 정정 계보로 묶는다. 첨부만 고치는 정정과는 다른 표시다.
CONTENT_CORRECTION_BRACKET_MARKER: Final[str] = "기재정정"

#: 관련 문서 상한 — «관측용» 상수다. 정상 회사를 거절하는 근거로 쓰지 않는다.
MAX_RELATED_FILINGS: Final[int] = 3

#: source_kind별로 그 공시가 «영향을 주는» 의미 칸의 대략적 범위.
#: ★ 왜 굵은 단위인가 — 문단 단위 정밀 배정은 relevance.py가 조각마다 따로 한다.
#:   CollectionAttempt는 «공시 조회 단계»의 기록이라 아직 문단을 안 봤다. 그래서
#:   여기서는 «이 공시 종류가 대체로 어떤 장을 채우는가»만 굵게 표시한다.
#:   사업/감사보고서는 전문(全文)이라 9개 장 전체에 걸치고, 반기·분기보고서는
#:   최근 실적·진행 상황 보충이라 4장·5장에 걸친다고 본다.
_ALL_SLOTS_SORTED: Final[tuple[str, ...]] = tuple(sorted(ALL_SLOT_IDS))
SOURCE_KIND_SLOT_SCOPE: Final[dict[str, tuple[str, ...]]] = {
    SOURCE_KIND_BUSINESS_REPORT: _ALL_SLOTS_SORTED,
    SOURCE_KIND_AUDIT_REPORT: _ALL_SLOTS_SORTED,
    SOURCE_KIND_SEMIANNUAL_REPORT: (
        CLAIM_SLOTS_BY_SECTION["past_changes"] + CLAIM_SLOTS_BY_SECTION["current_challenges"]
    ),
    SOURCE_KIND_QUARTERLY_REPORT: (
        CLAIM_SLOTS_BY_SECTION["past_changes"] + CLAIM_SLOTS_BY_SECTION["current_challenges"]
    ),
}

# ══════════════════════════════════════════════════════════
# 비용·안전 상한
# ══════════════════════════════════════════════════════════

#: zip 압축 해제 상한은 core.dart_client 값을 그대로 재사용한다(단일 소스 유지 —
#: 소유자 지침 「기존 core/dart_client.py의 상한·상태 처리 패턴을 재사용하라」).
ZIP_BOMB_MAX_TOTAL_UNCOMPRESSED_BYTES: Final[int] = DOCUMENT_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES
ZIP_BOMB_MAX_MEMBER_BYTES: Final[int] = DOCUMENT_MEMBER_MAX_BYTES
ZIP_BOMB_MAX_MEMBERS: Final[int] = DOCUMENT_ZIP_MAX_MEMBERS
ZIP_BOMB_MAX_COMPRESSION_RATIO: Final[int] = ZIP_MEMBER_MAX_COMPRESSION_RATIO
DOCUMENT_FETCH_TIMEOUT_SEC: Final[int] = TIMEOUT_DOCUMENT_SEC

#: 이 feature 자체가 받아들이는 문서 본문 텍스트 상한 — fetcher 구현이 무엇을
#: 돌려주든(실제 zip 해제 방어는 fetcher 쪽 책임) 이 층에서 한 번 더 막는다.
MAX_DOCUMENT_TEXT_BYTES: Final[int] = 8 * 1024 * 1024
MAX_TOTAL_TEXT_BYTES: Final[int] = 24 * 1024 * 1024
DEFAULT_COLLECTION_DEADLINE_SECONDS: Final[float] = 45.0

#: 유료 AI 호출로 자동 전환되지 않음을 못 박는 host allowlist. DART 계열뿐이다.
#: `tests/test_boundaries.py`가 이 값과 소스 전체를 함께 검사한다.
ALLOWED_HOST_ALLOWLIST: Final[frozenset[str]] = frozenset({
    "opendart.fss.or.kr",
    "dart.fss.or.kr",
})

# ══════════════════════════════════════════════════════════
# 전문 분할
# ══════════════════════════════════════════════════════════

MIN_FRAGMENT_CHARS: Final[int] = 20
TOC_HEADING_MARKERS: Final[tuple[str, ...]] = ("목차",)
#: 같은 문단이 문서 전체에서 이 횟수 이상 반복되면 상투 문구(면책 등)로 본다.
BOILERPLATE_MIN_REPEAT_COUNT: Final[int] = 2
BOILERPLATE_MIN_CHARS: Final[int] = 8

# ══════════════════════════════════════════════════════════
# 장별 적합도 채점
# ══════════════════════════════════════════════════════════

RELEVANCE_KEYWORD_HIT_SCORE_MILLIS: Final[int] = 250
RELEVANCE_HEADING_BONUS_MILLIS: Final[int] = 200
RELEVANCE_MAX_SCORE_MILLIS: Final[int] = 1000

# ══════════════════════════════════════════════════════════
# 회사 유형 판정 — 문서 증거로만(회사 이름 하드코딩 금지)
# ══════════════════════════════════════════════════════════

#: 일반 회사 재무제표의 핵심 항목. 이게 «없으면» 금융업 후보로 본다.
REVENUE_LINE_ITEM_KEYWORD: Final[str] = "매출액"
#: 은행·보험·카드 등 금융업이 「매출액」 대신 쓰는 수익 항목 이름(v1 휴리스틱,
#: 실제 DART 금융업 재무제표 항목 전체를 조사하지 않았다 — 확인 못 함으로 남긴다).
FINANCIAL_COMPANY_REVENUE_KEYWORDS: Final[tuple[str, ...]] = (
    "이자수익", "보험영업수익", "보험료수익", "수수료수익", "영업수익",
)

# ══════════════════════════════════════════════════════════
# 버전·출처 표기
# ══════════════════════════════════════════════════════════

COLLECTOR_VERSION: Final[str] = "evidence_collection/1.0"
PARSER_VERSION: Final[str] = "evidence_collection_segment/1.0"
DART_PUBLISHER_NAME: Final[str] = "금융감독원 전자공시시스템(DART)"
#: composer/constants.py DART_DOCUMENT_URL_TEMPLATE와 같은 값(rcept_no만 다른 키 이름).
DART_DOCUMENT_URL_TEMPLATE: Final[str] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# ══════════════════════════════════════════════════════════
# CollectionAttempt.reason_code / EvidenceFragment.reason_codes 값
# ══════════════════════════════════════════════════════════

REASON_LIST_QUERY_OK: Final[str] = "list_query_ok"
REASON_LIST_QUERY_MISSING: Final[str] = "list_query_missing"
REASON_LIST_QUERY_FAILED: Final[str] = "list_query_failed"
REASON_DOCUMENT_FETCH_OK: Final[str] = "document_fetch_ok"
REASON_DOCUMENT_FETCH_FAILED: Final[str] = "document_fetch_failed"
REASON_DOCUMENT_TOO_LARGE: Final[str] = "document_too_large"
REASON_TOTAL_BYTES_EXCEEDED: Final[str] = "total_bytes_exceeded"
REASON_DOCUMENT_DUPLICATE: Final[str] = "document_duplicate_sha256"
REASON_CAP_REACHED: Final[str] = "cap_reached"
REASON_DEADLINE_EXCEEDED: Final[str] = "deadline_exceeded"
REASON_NO_SIGNAL: Final[str] = "no_keyword_signal"
