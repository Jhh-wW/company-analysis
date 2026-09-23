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

# 호환 제목 목록의 보관 상한이다. 정식 수집의 후보 반복자는 제목 목록을
# 만들지 않으므로 이 값이 끝부분 원문 순회를 중단하지 않는다.
MAX_TEXT_SEGMENTS_PER_DOCUMENT: Final[int] = 4_096
# 반복 상투문구 탐지 dict도 서로 다른 짧은 줄 수만큼 객체가 늘어난다.
# 입력 바이트 상한과 별도로 해시 색인을 제한하며, 포화 뒤에도 순회한다.
MAX_BOILERPLATE_DISTINCT_LINES_PER_DOCUMENT: Final[int] = 32_768

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
#: ★ competitive_position은 self_context와 stated_differentiator를 수집한다.
#:   후자는 회사 신원·공식 Source 등록부를 확인하는 app의 결정론 투영기가
#:   승격하므로 relevance.py가 키워드로 흉내 내지 않는다. 동일 조건 비교 4개
#:   슬롯은 선택 사항이며 구조화 비교기가 실제 자료가 있을 때만 채운다.
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
        "competitive_position:stated_differentiator",
    ),
}

COLLECTOR_SLOT_IDS: Final[frozenset[str]] = frozenset(
    slot_id for slots in COLLECTOR_SLOTS_BY_SECTION.values() for slot_id in slots
)

#: ══════════════════════════════════════════════════════════
#: 선택 후보 슬롯 — 원문이 직접 뒷받침할 때만 조각에 싣는 칸.
#: ★ 정본: app/src/shared/report_evidence/policy.py의
#:   OPTIONAL_CANDIDATE_SLOTS_BY_SECTION. 엔진은 app을 import할 수 없어 값을
#:   복사하며 app 시험이 두 값을 대조한다.
#: ★ 필수 커버리지와 분리한다. 이 칸은 조회 기록(attempts.slot_ids)·필수 칸
#:   충족·장 준비 판정에 절대 쓰지 않는다 — 없어도 수집 실패가 아니다.
#: ★ 왜 필요한가(2026-09-23 4차 실측) — 감사보고서 「회사의 개요」 문단에 본점
#:   소재지가 있고 작가도 그 문단으로 identity:official_location 문장을 정확히
#:   썼지만, 문단의 지원 칸이 필수 칸뿐이라 검수 전에 탈락했다.
#: ★ 좁게 연다 — 「2026년」처럼 어느 문단에나 있는 약한 낱말 칸(plan_timing
#:   등)을 열면 여러 장의 문단을 납치한다(relevance.py 주석). 본점·소재지처럼
#:   강한 직접 표현이 있는 칸만 넣는다.
OPTIONAL_CANDIDATE_SLOTS_BY_SECTION: Final[dict[str, tuple[str, ...]]] = {
    "identity": ("identity:official_location",),
}

OPTIONAL_CANDIDATE_SLOT_IDS: Final[frozenset[str]] = frozenset(
    slot_id for slots in OPTIONAL_CANDIDATE_SLOTS_BY_SECTION.values() for slot_id in slots
)

#: 이 엔진이 인식하는 전체 slot_id — composer 45개 어휘 ∪ 수집기 전용 신규
#: 슬롯(self_context·stated_differentiator). EvidenceFragment·CollectionAttempt
#: 검증은 이 합집합
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
#: FAILED였을 때 쓴다(P1-1) — 「모르면 audit_only로 지어내지 않는다」.
COMPANY_TYPE_UNDECIDED: Final[str] = "undecided"
VALID_COMPANY_TYPES: Final[frozenset[str]] = frozenset({
    COMPANY_TYPE_LISTED, COMPANY_TYPE_AUDIT_ONLY, COMPANY_TYPE_FINANCIAL, COMPANY_TYPE_UNDECIDED,
})

#: CollectionAttempt.reason_code · EvidenceFragment.reason_codes 공통 형식.
REASON_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")

#: DART 전문에서 웹 후보를 무한히 꺼내 네트워크 예산을 잠식하지 않게 하는
#: 수집 실행 전체 상한. 실제 접속은 app의 더 작은 host/page 상한을 다시 받는다.
MAX_OFFICIAL_URL_CANDIDATES: Final[int] = 12

# ══════════════════════════════════════════════════════════
# 공시 종류(source_kind)와 조회 순서
# ══════════════════════════════════════════════════════════

SOURCE_KIND_BUSINESS_REPORT: Final[str] = "dart_business_report"
SOURCE_KIND_AUDIT_REPORT: Final[str] = "dart_audit_report"
SOURCE_KIND_CONSOLIDATED_AUDIT_REPORT: Final[str] = "dart_consolidated_audit_report"
SOURCE_KIND_SEMIANNUAL_REPORT: Final[str] = "dart_semiannual_report"
SOURCE_KIND_QUARTERLY_REPORT: Final[str] = "dart_quarterly_report"


class FilingKindSpec(NamedTuple):
    """공시 종류 하나를 어떻게 찾을지 — DART pblntf_ty·이름 키워드·기본 요구도."""

    source_kind: str
    pblntf_ty: str
    name_keyword: str
    requirement: str


#: ★ 기존 `analysis_engine/tools/survey_audit_reports.py`의
#:   `FILING_LOOKUP_DEFAULT`(사업보고서 우선, 감사보고서 폴백) 안전선을 그대로
#:   따른다. 상장 여부로 조회 순서를 가르지 않는다 — 이 feature는 그 구분을
#:   미리 알지 못하고(그것이 오히려 이 feature의 산출인 company_type이다),
#:   사업보고서가 없으면 감사보고서로 넘어가는 폴백만 지킨다.
FILING_KIND_SPECS: Final[tuple[FilingKindSpec, ...]] = (
    FilingKindSpec(SOURCE_KIND_BUSINESS_REPORT, "A", "사업보고서", REQUIREMENT_REQUIRED),
    FilingKindSpec(SOURCE_KIND_AUDIT_REPORT, "F", "감사보고서", REQUIREMENT_REQUIRED),
    FilingKindSpec(
        SOURCE_KIND_CONSOLIDATED_AUDIT_REPORT,
        "F",
        "연결감사보고서",
        REQUIREMENT_OPTIONAL,
    ),
    FilingKindSpec(SOURCE_KIND_SEMIANNUAL_REPORT, "A", "반기보고서", REQUIREMENT_OPTIONAL),
    FilingKindSpec(SOURCE_KIND_QUARTERLY_REPORT, "A", "분기보고서", REQUIREMENT_OPTIONAL),
)

FILING_KIND_SPEC_BY_SOURCE_KIND: Final[dict[str, FilingKindSpec]] = {
    spec.source_kind: spec for spec in FILING_KIND_SPECS
}

#: 당기 연차 문서 — 사업보고서와 감사보고서가 모두 있으면 둘 다 고른다.
PRIMARY_LOOKUP_ORDER: Final[tuple[str, ...]] = (SOURCE_KIND_BUSINESS_REPORT, SOURCE_KIND_AUDIT_REPORT)
#: 연결감사보고서는 일반 감사보고서와 접수번호가 다른 별도 문서로 고른다.
CONSOLIDATED_LOOKUP_ORDER: Final[tuple[str, ...]] = (SOURCE_KIND_CONSOLIDATED_AUDIT_REPORT,)
#: 보충 자료 — 있으면 더하고 없어도 실패로 보지 않는다(OPTIONAL).
SUPPLEMENT_LOOKUP_ORDER: Final[tuple[str, ...]] = (SOURCE_KIND_SEMIANNUAL_REPORT, SOURCE_KIND_QUARTERLY_REPORT)

#: 「[첨부정정]」 공시의 원본 zip엔 본문이 없고 고친 첨부만 있다(survey_audit_reports.py
#: 실측과 동일 근거) — 본문이 없으므로 통째로 후보에서 뺀다.
EXCLUDED_REPORT_NAME_MARKERS: Final[tuple[str, ...]] = ("첨부정정",)
#: 연결 공시는 전용 연결감사보고서 종류에서만 받는다. 사업보고서와 일반
#: 감사보고서에서는 같은 문자열을 계속 제외해 종류 간 중복을 막는다.
CONSOLIDATED_REPORT_NAME_MARKER: Final[str] = "연결"
#: 내용을 고치는 «기재정정»만 정정 계보로 묶는다. 첨부만 고치는 정정과는 다른 표시다.
CONTENT_CORRECTION_BRACKET_MARKER: Final[str] = "기재정정"

#: 관련 문서 상한 — «관측용» 상수다. 정상 회사를 거절하는 근거로 쓰지 않는다.
MAX_RELATED_FILINGS: Final[int] = 6

#: source_kind별로 그 공시가 «영향을 주는» 의미 칸의 대략적 범위.
#: ★ 왜 굵은 단위인가 — 문단 단위 정밀 배정은 relevance.py가 조각마다 따로 한다.
#:   CollectionAttempt는 «공시 조회 단계»의 기록이라 아직 문단을 안 봤다. 그래서
#:   여기서는 «이 공시 종류가 대체로 어떤 장을 채우는가»만 굵게 표시한다.
#:   사업/감사보고서는 전문(全文)이라 9개 장 전체에 걸치고, 반기·분기보고서는
#:   최근 실적·진행 상황 보충이라 4장·5장에 걸친다고 본다.
#: ★ attempts.slot_ids도 COLLECTOR_SLOTS_BY_SECTION
#:   (수집기 1차 표적)에서만 고른다. composer 45개 전체가 아니다 — 수집기가
#:   채우지 않기로 한 슬롯(historical_performance·비교 4종)을 「이 공시가
#:   영향을 준다」고 기록하면 다른 담당자가 잘못된 커버리지 기대를 갖는다.
_COLLECTOR_SLOTS_SORTED: Final[tuple[str, ...]] = tuple(sorted(COLLECTOR_SLOT_IDS))
SOURCE_KIND_SLOT_SCOPE: Final[dict[str, tuple[str, ...]]] = {
    SOURCE_KIND_BUSINESS_REPORT: _COLLECTOR_SLOTS_SORTED,
    SOURCE_KIND_AUDIT_REPORT: _COLLECTOR_SLOTS_SORTED,
    SOURCE_KIND_CONSOLIDATED_AUDIT_REPORT: _COLLECTOR_SLOTS_SORTED,
    SOURCE_KIND_SEMIANNUAL_REPORT: (
        COLLECTOR_SLOTS_BY_SECTION["past_changes"] + COLLECTOR_SLOTS_BY_SECTION["current_challenges"]
    ),
    SOURCE_KIND_QUARTERLY_REPORT: (
        COLLECTOR_SLOTS_BY_SECTION["past_changes"] + COLLECTOR_SLOTS_BY_SECTION["current_challenges"]
    ),
}

#: 전문(全文) 연차 공시만 선택 후보 칸을 채점한다. 반기·분기 보고서는 4·5장
#: 보충 자료라 1장 소재지 같은 칸을 새로 주장하지 않는다.
_OPTIONAL_SLOTS_SORTED: Final[tuple[str, ...]] = tuple(sorted(OPTIONAL_CANDIDATE_SLOT_IDS))
SOURCE_KIND_OPTIONAL_SLOT_SCOPE: Final[dict[str, tuple[str, ...]]] = {
    SOURCE_KIND_BUSINESS_REPORT: _OPTIONAL_SLOTS_SORTED,
    SOURCE_KIND_AUDIT_REPORT: _OPTIONAL_SLOTS_SORTED,
    SOURCE_KIND_CONSOLIDATED_AUDIT_REPORT: _OPTIONAL_SLOTS_SORTED,
    SOURCE_KIND_SEMIANNUAL_REPORT: (),
    SOURCE_KIND_QUARTERLY_REPORT: (),
}

#: 조각 채점 허용 범위 = 필수 커버리지 칸 ∪ 선택 후보 칸. 조회 기록(attempt)과
#: 필수 커버리지는 계속 SOURCE_KIND_SLOT_SCOPE만 쓴다. 두 표를 합치지 않고 따로
#: 두어 선택 칸이 「이 공시로 확인했다」는 커버리지 주장으로 새지 않게 한다.
SOURCE_KIND_CANDIDATE_SLOT_SCOPE: Final[dict[str, tuple[str, ...]]] = {
    source_kind: slot_ids + SOURCE_KIND_OPTIONAL_SLOT_SCOPE[source_kind]
    for source_kind, slot_ids in SOURCE_KIND_SLOT_SCOPE.items()
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

# 소수와 숫자만 있는 표 셀을 제외한 명시적 제목. 번호 목록의 문맥은
# 짧은 제목 보존 규칙으로 처리하며, 의미 분류의 완전성을 주장하지 않는다.
DOCUMENT_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:[IVXLCDM]{1,6}\.|제\s?[0-9]{1,3}\s?장|[가나다라마바사아자차카타파하]\.)\s*\S|"
    r"[0-9]{1,6}\.(?![0-9])\s*[^\d\s])"
)
# 하위 번호 제목은 직후 문단의 문맥으로만 추적한다.
PARAGRAPH_SUBHEADING_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9]{1,3}\)\s*\S")
MAX_CANDIDATE_WINDOW_CHARS: Final[int] = 4_096
# 고정 창 경계 가까이에 문장 끝이 있으면 그곳에서 나누어 단어·부정문 절단을 줄인다.
CANDIDATE_WINDOW_SENTENCE_LOOKBACK_CHARS: Final[int] = 512
CANDIDATE_WINDOW_SENTENCE_END_PATTERN: Final[re.Pattern[str]] = re.compile(r"[.!?](?=\s)")
MAX_HEADING_CONTEXT_CHARS: Final[int] = 256
RETAINED_TOP_PER_SLOT: Final[int] = 6
RETAINED_RECENT_PER_SLOT: Final[int] = 3
RETAINED_CHANGE_PER_SLOT: Final[int] = 3
MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT: Final[int] = 128
MAX_UNCLASSIFIED_CHARS_PER_DOCUMENT: Final[int] = 64 * 1024
# 후속 비교 생산기의 원천을 보관할 신호이며 사실 인정·장 배정 신호가 아니다.
UNCLASSIFIED_PRIORITY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"경쟁|동종|비교|대비|점유율|\bcompet(?:itor|itors|e|es|ed|ing|ition)\b",
    re.IGNORECASE,
)
# 아주 짧은 반복 줄도 첫 관측은 남기되 수백만 개 후보 생성을 피한다.
MAX_SHORT_DUPLICATE_TEXTS_PER_DOCUMENT: Final[int] = 2_048
CHANGE_CONTEXT_MARKERS: Final[tuple[str, ...]] = (
    "취소", "철회", "중단", "위험", "변경", "정정", "연기", "지연", "종료",
)
PLAN_CHANGE_STATUS_MARKERS: Final[tuple[str, ...]] = (
    "계획을 취소", "계획을 철회", "계획을 변경", "투자를 취소", "투자를 중단",
    "일정을 연기", "일정을 변경", "시기를 변경", "추진을 중단",
)
SCAN_VERSION: Final[str] = "document_scan/1"
SCAN_STATE_COMPLETE: Final[str] = "COMPLETE"
SCAN_STATE_INCOMPLETE: Final[str] = "INCOMPLETE"
REASON_SELECTION_COMPRESSED: Final[str] = "document_selection_compressed"
REASON_CHANGE_CONTEXT: Final[str] = "selection_change_context"
REASON_RECENT_CONTEXT: Final[str] = "selection_recent_context"
# 일반 장문 보관량은 개수·문자 한도와 슬롯별 더 작은 몫을 함께 적용한다.
# 발견 후보는 이 한도와 무관하게 즉시 채점하며 마감까지 순회를 계속한다.
MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT: Final[int] = 8_192
MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT: Final[int] = 4 * 1024 * 1024
# 일반 writer 조각 하한보다 짧은 독립 문장은 9장 같은 별도 생산기가 필요할
# 수 있다. 전부 보존하면 8MiB의 ``a\n\n`` 입력 하나가 수백만 객체가 되므로
# 문서당 개수·총문자 상한을 동시에 둔 관측 차선만 허용한다.
MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT: Final[int] = 128
MAX_SHORT_OBSERVATION_CHARS_PER_DOCUMENT: Final[int] = 2_048
TOC_HEADING_MARKERS: Final[tuple[str, ...]] = ("목차",)
#: 같은 문단이 문서 전체에서 이 횟수 이상 반복되면 상투 문구(면책 등)로 본다.
BOILERPLATE_MIN_REPEAT_COUNT: Final[int] = 2
BOILERPLATE_MIN_CHARS: Final[int] = 8

# ══════════════════════════════════════════════════════════
# 관계법인 회계범위 각주 운반 — legacy 발췌 보충(entity_scope_footnote.py)
# ══════════════════════════════════════════════════════════

#: 표 행 이름 «바로 뒤»에 붙은 각주 표식 「이름(*)」·「이름(*1)」.
ENTITY_FOOTNOTE_ROW_MARK_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<=\S)\((\*\d{0,2})\)")
#: 공백(또는 문서 처음) 뒤에서 시작하는 각주 정의 「(*) 설명」·「(*1) 설명」.
ENTITY_FOOTNOTE_DEFINITION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|(?<=\s))\((\*\d{0,2})\)(?=\s*\S)"
)
#: 재무제표 주석 번호 제목 「5. 매도가능증권」 — 한 주석(표+각주)의 경계.
ENTITY_FOOTNOTE_NOTE_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|(?<=\s))\d{1,2}\.\s?[가-힣]"
)
#: 한 주석 안의 소표 머리 «후보» 「(1) 당기말 내역」·「(2) 전기말 내역」. 괄호 숫자는
#: 표 안 음수 금액 「(12)」에도 쓰이므로 이것만으로 소표를 확정하지 않는다 — 후보가
#: 행 앞 기간 표제를 담지 못하면 상한 안에서 더 앞 후보를 검토한다.
ENTITY_FOOTNOTE_SUBTABLE_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|(?<=\s))\(\d{1,2}\)(?=\s)"
)
#: 기간 표제 — 당기말·전기말·당기·전기·기초·기말·반기·분기 낱말과 연도 날짜.
#: 「당기손익」「전기요금」처럼 뒤에 다른 낱말이 붙은 말은 기간으로 보지 않는다.
ENTITY_FOOTNOTE_PERIOD_LABEL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![가-힣])(?:전전|당|전)(?:반기|분기|기)(?:말|초)?"
    r"(?=[^가-힣]|$|현재|중|의|에|과|와|및|은|는|부터|까지)"
    r"|(?<![가-힣])기(?:초|말)(?=[^가-힣]|$|현재|의|에|과|와|및|은|는)"
    r"|(?<!\d)(?:19|20)\d{2}\s*(?:년|[.\-/]\s*\d{1,2})"
)
#: 회계 적용 범위를 제한하는 각주 표현(공백 무시 비교). 거래 사실을 부정하는
#: 말이 아니라 「연결·지분법·종속기업 회계 범위」를 좁히는 표현만 넣는다.
ENTITY_SCOPE_LIMIT_MARKERS: Final[tuple[str, ...]] = (
    "종속기업에서제외",
    "관계기업에서제외",
    "연결대상에서제외",
    "연결범위에서제외",
    "연결재무제표작성대상에서제외",
    "지분법적용대상에서제외",
    "지분법을적용하지",
    "지분법적용을중단",
)
#: 같은 문장에 있으면 아직 일어나지 않은 제외로 보고 운반 근거로 쓰지 않는다
#: (예정 제외를 현재 범위 제한처럼 전하지 않기 위해서다. 공백 무시 비교).
ENTITY_SCOPE_FUTURE_MARKERS: Final[tuple[str, ...]] = ("예정", "계획", "할것", "될것")
#: 각주 정의 안 문장 끝 — 「…되었습니다. 또한, …」를 문장별로 가른다.
ENTITY_FOOTNOTE_SENTENCE_END_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<=다\.)\s+")
#: 법인 이름이 아니라 표의 머리·분류·금액 이름인 낱말. 이름 토큰 묶음의 앞뒤
#: 경계를 정하는 데만 쓴다. v1 목록이며 전 공시 표를 전수 조사하지 않았다(확인
#: 못 함). 모르는 머리말이 앞에 붙으면 이름이 관계자 조각과 일치하지 않아 운반을
#: 포기한다(잘못 붙이는 대신 놓치는 쪽).
ENTITY_FOOTNOTE_TABLE_WORDS: Final[frozenset[str]] = frozenset({
    "단위", "천원", "백만원", "원", "구분", "회사명", "법인명", "기업명", "특수관계자명",
    "특수관계자", "종속기업", "종속회사", "관계기업", "관계회사", "공동기업", "지배기업",
    "기타", "당기", "전기", "당기말", "전기말", "기초", "기말", "지분율", "지분률",
    "취득원가", "장부금액", "장부가액", "순자산가액", "소재지", "업종", "결산월",
    "합계", "소계", "계", "주식수", "보유주식수", "금융수익", "영업수익", "영업비용",
    "대여금", "기타채권", "기타채무", "대여", "회수", "매출", "매입", "채권", "채무",
})
#: 이름 토큰 앞뒤에서 떼어 보는 괄호·구두점.
ENTITY_FOOTNOTE_TOKEN_PUNCTUATION: Final[str] = "()[]{}:;,.·「」『』\"'"
#: 행 표식 앞에서 이름을 찾는 최대 거리. 이 안에서 경계를 못 찾으면 이름 시작을
#: 확정할 수 없으므로 운반하지 않는다.
ENTITY_FOOTNOTE_LABEL_WINDOW_CHARS: Final[int] = 120
#: 이름 토큰 수 상한 — 경계 없이 이어지는 긴 토큰열을 이름으로 오인하지 않는다.
ENTITY_FOOTNOTE_LABEL_MAX_TOKENS: Final[int] = 8
#: 이름의 최소 글자 수(공백 제외). 짧은 약칭 한두 글자를 법인으로 보지 않는다.
ENTITY_FOOTNOTE_LABEL_MIN_CHARS: Final[int] = 4
#: 운반 구간 글자 상한 기본값 — legacy 발췌 조각 상한(run_pilot.FRAG_CHARS)과 같다.
#: 호출자는 실제 엔진 값을 넘긴다.
ENTITY_FOOTNOTE_DEFAULT_MAX_CHARS: Final[int] = 1_200
#: 한 문서에서 보충하는 각주 조각 수 상한 — 작성 입력을 불리지 않는다.
ENTITY_SCOPE_FOOTNOTE_MAX_FRAGMENTS: Final[int] = 3
#: legacy 조각 원문위치 표기 — audit_financials의 「평문 문자 N-M」 관례와 같다.
ENTITY_FOOTNOTE_LOCATION_TEMPLATE: Final[str] = "평문 문자 {start}-{end}"

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

COLLECTOR_VERSION: Final[str] = "evidence_collection/2.0"
#: 2.0: EOF 후보 반복자·유한 문단 구간·제목 오인 방지. 1.x의 저장 한도
#: 잘림 기록을 새 완료 증명으로 재사용하지 않는다.
PARSER_VERSION: Final[str] = "evidence_collection_segment/2.0"
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
#: — 전송 장애(document_fetch_failed)와 다른 값이어야 필수 슬롯이
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
#: OpenDART 목록의 접수일(``rcept_dt``)이 실제 달력의 YYYYMMDD가 아닐 때.
#: 원문 부재가 아니라 외부 응답/내부 계약 이상이므로 REQUIRED+FAILED로 남긴다.
REASON_FILING_RECEIPT_DATE_INVALID: Final[str] = "filing_receipt_date_invalid"
#: 문서를 성공적으로 받았지만 채점 가능한(scored) 조각이 하나도 없어 최종
#: documents/fragments에서 제외했을 때 — 「조회했다」는 사실은 이 attempt로
#: 보존한다.
REASON_DOCUMENT_NO_SCORED_EVIDENCE: Final[str] = "document_no_scored_evidence"
REASON_DOCUMENT_SECTION_COUNT_EXCEEDED: Final[str] = (
    "document_section_count_exceeded"
)
REASON_DOCUMENT_LINE_INDEX_EXCEEDED: Final[str] = "document_line_index_exceeded"
REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED: Final[str] = (
    "document_fragment_count_exceeded"
)
REASON_DOCUMENT_FRAGMENT_CHARS_EXCEEDED: Final[str] = (
    "document_fragment_chars_exceeded"
)

#: identity_binding 문자열 안에 넣는 검증 상태 값(P1-4) — fetcher가 문서
#: 소유 회사 메타를 실제로 돌려줘 대조했는지, 메타가 아예 없어 대조하지
#: 못했는지를 정직하게 구분한다(«검증했다»고 거짓 주장하지 않는다).
IDENTITY_CHECK_VERIFIED: Final[str] = "verified_match"
IDENTITY_CHECK_UNVERIFIED: Final[str] = "unverifiable_no_fetcher_metadata"

# ══════════════════════════════════════════════════════════
# generation=8 후속 — 목록 행 수준 혼입 방어·
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

# ══════════════════════════════════════════════════════════
# 감사인 표준 문구 — 감사보고서의 감사의견·감사인 책임·경영진 책임 단락
# ══════════════════════════════════════════════════════════

#: 감사보고서 «감사인 표준 문구» 절을 알아보는 표지(공백을 지운 표면형).
#: ★ 왜 필요한가(2026-09-23 5차 유료 실행, 독립 검수 상-3) — 감사보고서
#:   「감사인의 책임」 단락(주어는 감사인 「우리」)이 「위험」·「대응」 두 낱말만으로
#:   current_challenges의 과제·대응 칸을 받았다. 작가는 감사인의 감사절차 설계·
#:   감사증거 입수를 회사의 대응으로 옮겨 적었고, 검수 AI는 그 문장을 «참»이라 했다.
#: ★ 감사기준서 예시 보고서의 «긴 표준 문형»만 넣는다. 회사가 주어인 내부회계관리
#:   제도 운영 서술(「중요성의 관점에서 효과적으로 설계·운영」·「합리적인 확신을
#:   제공」·「중요한 왜곡표시를 예방」), 핵심감사사항의 회사 위험 서술, 계속기업
#:   불확실성 강조 문단의 회사 사정에도 나오는 짧은 낱말(「왜곡표시」·「합리적인
#:   확신」·「중요성의 관점에서」·「계속기업」·「핵심감사사항」·「감사절차」)은
#:   일부러 넣지 않았다. 홈페이지 자기소개에 흔한 「우리의 목적·책임」도 뺐다.
#: ★ 사본 세 벌 — 이 파일, app/src/features/chapter_evidence/constants.py,
#:   app/src/features/composer/audit_boilerplate_constants.py. 엔진은 app을 import할
#:   수 없고 app의 두 feature도 서로 import하지 않아 값을 복사한다. app 시험
#:   (chapter_evidence/tests/test_auditor_boilerplate_parity.py)이 ast로 대조한다.
AUDITOR_BOILERPLATE_MARKERS: Final[tuple[str, ...]] = (
    # 감사보고서 머리말·수신인·서명 문형
    "독립된감사인",
    "이사회귀중",
    "감사보고서의근거가된감사",
    # 「감사보고서일」 단독은 회사 주석의 기준일(「감사보고서일 현재 소송 결과를 예측할 수
    # 없습니다」)에도 쓰인다 — 감사인 문형 세 가지만 남긴다(2026-09-23 B 수정 재검토 R2).
    "감사보고서일까지입수",
    "감사보고서일현재로유효",
    "감사보고서일후",
    "감사보고서를발행",
    # 감사의견·감사의견근거 단락
    "감사인의책임",
    "우리의의견으로는",
    "재무제표를감사하였",
    "내부회계관리제도를감사하였",
    "유의적인회계정책의요약을포함한",
    "재무제표감사",
    "감사기준",
    "독립성관련윤리적요구사항",
    "감사받지아니한",
    # 감사인 책임 단락의 감사 행위
    "감사증거",
    "감사절차를설계",
    "감사와관련된내부통제",
    "효과성에대한의견을표명",
    "의견을변형",
    "의견형성",
    # 사업보고서에 옮겨 실린 핵심감사사항 머리 문단의 감사인 문형 — 구조 표지로도 쓴다(R6).
    "우리의의견형성",
    "수행한주요감사절차",
    "전문가적회의주의",
    # 「전문가적 회의주의」의 실제 번역 변형 — 5차 감사보고서 원문이 이 꼴이었다(R7).
    "전문가적의구심",
    "감사범위와감사시기",
    "발견하지못할위험",
    "왜곡표시는중요하다고간주",
    "계속기업전제의적절성",
    "전반적인표시와구조",
    "회계추정치와관련공시의합리성",
    # 경영진·지배기구 책임 단락(감사보고서 안의 표준 문형)
    "경영진과지배기구의책임",
    "공정하게표시할책임",
    "계속기업전제의사용",
    "계속기업관련사항을공시할책임",
    "재무보고절차의감시",
)
#: 표지가 이 복합어 «안에서만» 나타나면 감사인 문구로 세지 않는다 — 회사 내부
#: 감사 조직의 규정(「내부감사기준」)은 감사인 문구가 아니다.
AUDITOR_BOILERPLATE_EXCLUDED_COMPOUNDS: Final[dict[str, tuple[str, ...]]] = {
    "감사기준": ("내부감사기준",),
    # 회사 내부·자체·품질 감사 조직의 절차 설계는 감사인 행위가 아니다 — composer
    # 후보 쪽 행위 표지와 같은 면제(2026-09-23 독립 검토 F4).
    "감사절차를설계": ("내부감사절차를설계", "자체감사절차를설계", "품질감사절차를설계"),
}
#: 감사보고서에만 나오는 «구조 표지» — 문서 종류를 모르는 자리에서는 글에 이 표지가
#: 있을 때만 감사인 문구 판정을 건다(AUDITOR_BOILERPLATE_MARKERS 의 부분집합).
#: ★ 왜 필요한가(2026-09-23 독립 검토 F1) — 「감사증거」「감사기준」「재무제표감사」
#:   「감사보고서를 발행」 같은 짧은 표지는 감사 소프트웨어·감사 서비스 회사의 사업
#:   문장에도 나온다. 그 낱말만으로 판정하면 그 업종의 사업 문단이 통째로 버려진다.
#:   여기에는 감사인 보고서 문형에만 나오는 긴 표현만 둔다.
#: ★ 머리말 표지(「독립된감사인」 등)만으로는 부족하다 — 5차 실측 조각 12(감사인 책임
#:   단락)에는 머리말이 없었다. 구조 표지는 긴 감사인 문형 5개였다(감사보고서일까지 입수·
#:   발견하지 못할 위험·계속기업전제의 적절성·전반적인 표시와 구조·회계추정치와 관련 공시의 합리성).
#: ★ 「감사보고서일」 단독은 넣지 않는다(2026-09-23 B 수정 재검토 R2) — 실제 사업보고서의 회사
#:   주석(「감사보고서일 현재 … 영향을 예측할 수 없습니다」)이 이 낱말만으로 관문을 열고 문단째
#:   버려졌다. 감사인만 쓰는 세 문형(까지 입수·현재로 유효·후)으로 좁혔다.
#: ★ 「우리의 의견형성」(사업보고서의 핵심감사사항 머리 문단, R6)과 「전문가적 의구심」(R7)을 더했다.
#:   실제 공시 22건에서 두 문형은 감사인 문단에만 나왔다.
AUDITOR_STRUCTURAL_MARKERS: Final[tuple[str, ...]] = (
    "독립된감사인",
    "이사회귀중",
    "감사보고서의근거가된감사",
    "감사보고서일까지입수",
    "감사보고서일현재로유효",
    "감사보고서일후",
    "감사인의책임",
    "우리의의견으로는",
    "재무제표를감사하였",
    "내부회계관리제도를감사하였",
    "유의적인회계정책의요약을포함한",
    "독립성관련윤리적요구사항",
    "우리의의견형성",
    "수행한주요감사절차",
    "전문가적회의주의",
    "전문가적의구심",
    "감사범위와감사시기",
    "발견하지못할위험",
    "왜곡표시는중요하다고간주",
    "계속기업전제의적절성",
    "전반적인표시와구조",
    "회계추정치와관련공시의합리성",
    "경영진과지배기구의책임",
    "계속기업전제의사용",
    "계속기업관련사항을공시할책임",
    "재무보고절차의감시",
)
#: 절에 이 표면형이 있으면 회사 고유 사실로 보고 감사인 문구로 세지 않는다 —
#: 화폐 금액, 회사에 실제로 일어난 제재·재작성 사건.
AUDITOR_BOILERPLATE_EXEMPTION_PATTERN: Final[str] = (
    r"\d[\d,.]*(?:조|억|만|천|백만|십억)?원|위반|과징금|제재|재작성|오류수정"
)
#: 판단 단위인 절의 경계 — 「…다.」 문장 끝, 공백·끝 앞의 마침표류, 줄바꿈.
AUDITOR_CLAUSE_SPLIT_PATTERN: Final[str] = r"(?<=다)\.|[.。!?](?=\s|$)|[\r\n]+"
#: 절의 «내용 글자»(한글·영문·숫자) 한 자의 모양.
AUDITOR_CONTENT_CHAR_PATTERN: Final[str] = r"[0-9A-Za-z가-힣]"
#: 내용 글자가 이 수 이하인 절은 «제목·접속 꼬리»로 보고 문단 판정에서 세지 않는다.
#: 감사보고서 표제(「감사의견근거」·「핵심감사사항」 6자, 「기타사항」·「강조사항」
#: 4자)와 「또한, 우리는:」(5자)이 넉넉히 들어가고, 사업 사실을 단정하는 가장 짧은
#: 꼴의 문장(「당사는 반도체를 만든다」 10자)은 들어가지 않는 값이다.
AUDITOR_TRIVIAL_CLAUSE_MAX_CONTENT_CHARS: Final[int] = 9
