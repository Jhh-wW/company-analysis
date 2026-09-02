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

#: ══════════════════════════════════════════════════════════
#: 수집기 필수 슬롯 — composer 45개 중 «수집기가 원문 후보를 채워야 하는»
#: 부분집합.
#: ★ 정본: app/src/shared/report_evidence/policy.py. 이 dict는 그 파일의
#:   REQUIRED_EVIDENCE_SLOTS_BY_SECTION에서 INJECTED_EVIDENCE_SLOTS_BY_SECTION을
#:   뺀 값과 정확히 같다(실측). 엔진은 app을 import할 수 없어 값을 복사해 두므로
#:   정본이 바뀌면 이 dict도 다시 대조해야 한다.
#: ★ past_changes:historical_performance는 «뺐다» — 구조화 실적기가
#:   재무 API 수치로 직접 채운다. 수집기의 키워드 채점이 이 슬롯에 문단을
#:   배정하면 같은 슬롯에 권위가 다른 두 값(원문 인용 vs 정확한 재무 수치)이
#:   겹쳐 어느 쪽을 믿을지 모호해진다 — relevance.py가 이 슬롯을 아예
#:   채점 대상에서 뺀 이유다.
#: ★ competitive_position은 self_context 하나뿐 — 비교 대상·지표·근거·
#:   판단 4개는 구조화 검증기가 채운다. self_context는 composer 45개 어휘에
#:   «없던» 새 슬롯이다(「자사가 스스로 서술한 시장 내 위치·강점」). 아래
#:   ALL_SLOT_IDS·SLOT_SECTION_OF에 합쳐 이 엔진이 인식하는 slot_id로 만든다.
COLLECTOR_SLOTS_BY_SECTION: Final[dict[str, tuple[str, ...]]] = {
    "identity": (
        "identity:corporate_identity", "identity:business_definition",
    ),
    "business_model": (
        "business_model:revenue_model", "business_model:customer_type",
        "business_model:value_exchange",
    ),
    "portfolio": (
        "portfolio:product_role", "portfolio:revenue_link",
    ),
    "past_changes": (
        "past_changes:completed_execution",
    ),
    "current_challenges": (
        "current_challenges:issue", "current_challenges:response",
    ),
    "future_strategy": (
        "future_strategy:stated_plan", "future_strategy:plan_status",
    ),
    "operations_partners": (
        "operations_partners:value_chain", "operations_partners:operating_role",
    ),
    "culture": (
        "culture:work_principle", "culture:verified_case",
    ),
    "competitive_position": (
        "competitive_position:self_context",
    ),
}

COLLECTOR_SLOT_IDS: Final[frozenset[str]] = frozenset(
    slot_id for slots in COLLECTOR_SLOTS_BY_SECTION.values() for slot_id in slots
)

#: 이 엔진이 인식하는 전체 slot_id — composer 45개 어휘 ∪ 수집기 전용 신규
#: 슬롯(self_context). EvidenceFragment·CollectionAttempt 검증은 이 합집합
#: 기준이다(보조 태그로 composer 슬롯을 붙이는 것도 허용하므로 45개를
#: 빼지 않는다 — historical_performance·비교 4종도 «유효한 slot_id»이긴
#: 하다, 다만 relevance.py가 스스로 배정하지 않을 뿐이다).
ALL_SLOT_IDS: Final[frozenset[str]] = frozenset(
    slot_id for slots in CLAIM_SLOTS_BY_SECTION.values() for slot_id in slots
) | COLLECTOR_SLOT_IDS

#: 슬롯 id → 소속 장 id. 조각의 slot_id·section_id 정합성 검사에 쓴다.
SLOT_SECTION_OF: Final[dict[str, str]] = {
    slot_id: section_id
    for section_id, slots in CLAIM_SLOTS_BY_SECTION.items()
    for slot_id in slots
} | {
    slot_id: section_id
    for section_id, slots in COLLECTOR_SLOTS_BY_SECTION.items()
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
#: 판정 근거가 될 문서를 하나도 확보하지 못했거나 필수 목록 조회 자체가
#: FAILED였을 때 쓴다(P1-1, 2026-08-31 team-lead 통보) — 「모르면 audit_only로
#: 지어내지 않는다」. 소비자에게는 team-lead가 별도로 통보했다.
COMPANY_TYPE_UNDECIDED: Final[str] = "undecided"
VALID_COMPANY_TYPES: Final[frozenset[str]] = frozenset({
    COMPANY_TYPE_LISTED, COMPANY_TYPE_AUDIT_ONLY, COMPANY_TYPE_FINANCIAL, COMPANY_TYPE_UNDECIDED,
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
#: ★ 2026-08-31 team-lead 통보 — attempts.slot_ids도 COLLECTOR_SLOTS_BY_SECTION
#:   (수집기 1차 표적)에서만 고른다. composer 45개 전체가 아니다 — 수집기가
#:   채우지 않기로 한 슬롯(historical_performance·비교 4종)을 「이 공시가
#:   영향을 준다」고 기록하면 다른 담당자가 잘못된 커버리지 기대를 갖는다.
_COLLECTOR_SLOTS_SORTED: Final[tuple[str, ...]] = tuple(sorted(COLLECTOR_SLOT_IDS))
SOURCE_KIND_SLOT_SCOPE: Final[dict[str, tuple[str, ...]]] = {
    SOURCE_KIND_BUSINESS_REPORT: _COLLECTOR_SLOTS_SORTED,
    SOURCE_KIND_AUDIT_REPORT: _COLLECTOR_SLOTS_SORTED,
    SOURCE_KIND_SEMIANNUAL_REPORT: (
        COLLECTOR_SLOTS_BY_SECTION["past_changes"] + COLLECTOR_SLOTS_BY_SECTION["current_challenges"]
    ),
    SOURCE_KIND_QUARTERLY_REPORT: (
        COLLECTOR_SLOTS_BY_SECTION["past_changes"] + COLLECTOR_SLOTS_BY_SECTION["current_challenges"]
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
#: 조회는 정상 처리됐지만 fetcher가 「이 문서는 원래 없다」고 확인한 경우
#: (P0-2) — 전송 장애(document_fetch_failed)와 다른 값이어야 필수 슬롯이
#: 일시 장애(FAILED)와 확인된 부재(MISSING)로 갈린다.
REASON_DOCUMENT_FETCH_MISSING: Final[str] = "document_fetch_missing"
REASON_DOCUMENT_TOO_LARGE: Final[str] = "document_too_large"
REASON_TOTAL_BYTES_EXCEEDED: Final[str] = "total_bytes_exceeded"
REASON_DOCUMENT_DUPLICATE: Final[str] = "document_duplicate_sha256"
REASON_CAP_REACHED: Final[str] = "cap_reached"
REASON_DEADLINE_EXCEEDED: Final[str] = "deadline_exceeded"
REASON_NO_SIGNAL: Final[str] = "no_keyword_signal"
#: fetcher가 돌려준 문서 메타의 corp_code가 요청한 회사와 다를 때(P1-4) — 다른
#: 회사 문서가 조용히 섞여 들어가는 것을 막는다.
REASON_DOCUMENT_IDENTITY_MISMATCH: Final[str] = "document_identity_mismatch"
#: 자료형 생성 검증(EvidenceCollectionError)이 문서 1건에서만 실패했을 때(P1-2) —
#: harvest 전체를 무너뜨리지 않고 이 문서만 버린다.
REASON_DOCUMENT_MODEL_INVALID: Final[str] = "document_model_invalid"
#: 문서를 성공적으로 받았지만 채점 가능한(scored) 조각이 하나도 없어 최종
#: documents/fragments에서 제외했을 때(P0-3) — 「조회했다」는 사실은 이 attempt로
#: 보존한다.
REASON_DOCUMENT_NO_SCORED_EVIDENCE: Final[str] = "document_no_scored_evidence"

#: identity_binding 문자열 안에 넣는 검증 상태 값(P1-4) — fetcher가 문서
#: 소유 회사 메타를 실제로 돌려줘 대조했는지, 메타가 아예 없어 대조하지
#: 못했는지를 정직하게 구분한다(«검증했다»고 거짓 주장하지 않는다).
IDENTITY_CHECK_VERIFIED: Final[str] = "verified_match"
IDENTITY_CHECK_UNVERIFIED: Final[str] = "unverifiable_no_fetcher_metadata"

# ══════════════════════════════════════════════════════════
# generation=8 후속 (2026-08-31 team-lead 통보) — 목록 행 수준 혼입 방어·
# 필터 제외/행 없음 구분
# ══════════════════════════════════════════════════════════

#: list.json 행에 corp_code가 실려 왔는데 요청한 회사와 다를 때(item 3) —
#: 문서를 아예 조회하지도 않고 목록 단계에서 미리 버린다. 다른 회사 문서가
#: document 단계까지 흘러오는 것 자체를 줄인다(P1-4의 document 단계 방어와
#: 이중 방어선).
REASON_LIST_ROW_IDENTITY_MISMATCH: Final[str] = "list_row_identity_mismatch"
#: DART가 행을 «돌려주긴 했지만»(대상 회사가 그 공시유형 자체는 낸 적이
#: 있지만) 이름 키워드·연결/정정 제외·corp_code 불일치 등 필터로 전부
#: 걸러졌을 때(item 4) — 「행이 아예 없었다」(list_query_missing)와 원인이
#: 달라 구분한다. 실존 공시가 「자료 부재」로 잘못 읽히는 것을 막는다.
REASON_LIST_ROWS_ALL_FILTERED: Final[str] = "list_rows_all_filtered"
