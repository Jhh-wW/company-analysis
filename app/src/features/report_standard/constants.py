"""기업분석 canonical(v4) 출력 계약의 안정적인 식별자.

내부 저장 키는 의미 기반 ID를 쓰고, 화면 번호·제목·태그는 이 파일에서만
관리한다. 숫자 키를 저장 키로 재사용하면 레거시 채용 블록 5~8과 충돌한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from src.shared.report_quality.constants import COMPARISON_JUDGMENTS


CANONICAL_SCHEMA_VERSION: Final[str] = "company-report-v4-canonical"

# 5장 흐름표를 원·선 관계도로 바꿀 수 있는 닫힌 계약이다. ``ReportTable``에는
# 장 id가 없으므로 composer가 실제로 내는 캡션·머리글을 함께 대조하고, 하나라도
# 어긋나면 기존 표로 남긴다. 긴 문구를 임의로 줄이지 않기 위해 글자 폭은
# 반각=1·전각=2인 보수적 단위로 세며, 원 안 두 줄을 넘으면 도식을 만들지 않는다.
RELATION_PAIR_CAPTION: Final[str] = "과제와 대응"
RELATION_PAIR_HEADERS: Final[tuple[str, str]] = (
    "지금 겪는 과제",
    "회사가 밝힌 대응",
)
RELATION_PAIR_MIN_ROWS: Final[int] = 2
RELATION_PAIR_MAX_ROWS: Final[int] = 5
RELATION_PAIR_MAX_TEXT_LINES: Final[int] = 2
RELATION_PAIR_LINE_HALF_UNITS: Final[int] = 18


@dataclass(frozen=True)
class SectionSpec:
    """본문 한 장의 내부 ID와 표시 메타데이터."""

    section_id: str
    display_number: str
    title: str
    tag: str = ""


SECTION_SPECS: Final[tuple[SectionSpec, ...]] = (
    SectionSpec("identity", "1", "기업 정체성"),
    SectionSpec("business_model", "2", "사업 구조와 수익 모델"),
    SectionSpec("portfolio", "3", "핵심 제품·서비스와 포트폴리오 역할"),
    SectionSpec("past_changes", "4", "3개년 주요 변화와 실행", "#과거"),
    SectionSpec("current_challenges", "5", "당면 과제와 대응", "#현재"),
    SectionSpec("future_strategy", "6", "성장 전략", "#미래"),
    SectionSpec("operations_partners", "7", "사업 운영과 파트너 구조"),
    SectionSpec("culture", "8", "인재상과 일하는 방식"),
    SectionSpec("competitive_position", "9", "동종업계 비교 결과"),
)

SECTION_BY_ID: Final[dict[str, SectionSpec]] = {
    spec.section_id: spec for spec in SECTION_SPECS
}
CANONICAL_SECTION_IDS: Final[tuple[str, ...]] = tuple(SECTION_BY_ID)

# 기존 완전본의 장별 내용 검증 범위다. 여기 포함된 장이 출고 후보에
# 들어오면 종전 prose·구조 블록·의미 검증을 그대로 적용한다. 다만 이 집합
# 자체를 "한 장이라도 빠지면 전체 차단"으로 쓰지 않고, 아래 동적 최소
# 계약으로 부분 보고서 출고 가능성을 별도 판정한다.
OPTIONAL_BASIC_SECTION_IDS: Final[frozenset[str]] = frozenset(
    {"current_challenges", "culture"}
)
CONDITIONAL_SECTION_IDS: Final[frozenset[str]] = frozenset(
    {*OPTIONAL_BASIC_SECTION_IDS, "competitive_position"}
)
REQUIRED_SECTION_IDS: Final[frozenset[str]] = frozenset(
    section_id
    for section_id in CANONICAL_SECTION_IDS
    if section_id not in CONDITIONAL_SECTION_IDS
)

# 공개 가능한 부분 보고서의 동적 최소 계약. 1장 공식 정체성, 2장 수익 구조,
# 4장 연속 3개 완료 사업연도 실적표가 모두 있어야 한다. 미래 계획이나 현재
# 문제만으로 공식 실적 확인을 대신하지 않는다. 나머지 장은 근거가 있을 때만
# 포함하고 빈 문장으로 채우지 않는다.
MINIMUM_CORE_SECTION_IDS: Final[frozenset[str]] = frozenset(
    {"identity", "business_model"}
)
MINIMUM_IDENTITY_CLAIM_TYPES: Final[frozenset[str]] = frozenset(
    {"identity_summary", "official_self_definition", "operating_scope"}
)
MINIMUM_SITUATION_SECTION_IDS: Final[frozenset[str]] = frozenset(
    {"past_changes"}
)
MINIMUM_PUBLISHABLE_SECTION_COUNT: Final[int] = 3
PARTIAL_ELIGIBLE_SECTION_IDS: Final[frozenset[str]] = frozenset(
    set(CANONICAL_SECTION_IDS)
    - set(MINIMUM_CORE_SECTION_IDS)
    - set(MINIMUM_SITUATION_SECTION_IDS)
)

COMPARISON_SHORTFALL_REASON: Final[str] = (
    "양사 공식자료를 같은 지표·기간·범위로 맞추지 못해 9장 동종업계 비교는 "
    "제공하지 않았습니다. 경쟁우위가 없다는 뜻은 아닙니다."
)

CURRENT_CHALLENGES_SHORTFALL_REASON: Final[str] = (
    "공식 자료에서 기준일 현재 미해결 문제와 회사가 이미 시작한 대응을 함께 "
    "확인하지 못해 5장 당면 과제와 대응은 제공하지 않았습니다. 문제가 없다는 "
    "뜻은 아닙니다."
)

CURRENT_RESPONSE_SHORTFALL_REASON: Final[str] = (
    "공식 자료에서 기준일 현재 미해결 과제는 확인했지만 회사가 이미 시작한 대응을 "
    "같은 과제에 연결하지 못해 5장은 확인된 과제만 제공합니다. 회사가 대응하지 "
    "않는다는 뜻은 아닙니다."
)

CULTURE_SHORTFALL_REASON: Final[str] = (
    "공식 채용·문화 자료에서 전사 가치 또는 확인 가능한 업무 사례를 확보하지 "
    "못해 8장 인재상과 일하는 방식은 제공하지 않았습니다. 조직문화가 없다는 "
    "뜻은 아닙니다."
)

PORTFOLIO_SHORTFALL_REASON: Final[str] = (
    "공식 자료에서 핵심 제품·서비스와 사업 내 역할을 함께 확인하지 "
    "못해 3장 핵심 제품·서비스와 포트폴리오 역할은 제공하지 않았습니다. "
    "제품·서비스가 없다는 뜻은 아닙니다."
)

PAST_CHANGES_SHORTFALL_REASON: Final[str] = (
    "연속 3개 완료 사업연도의 검증 실적표를 공식 자료와 결속하지 못해 "
    "4장 3개년 주요 변화와 실행은 제공하지 않았습니다. 과거 변화가 없다는 "
    "뜻은 아닙니다."
)

FUTURE_STRATEGY_SHORTFALL_REASON: Final[str] = (
    "공식 자료에서 기준일 후의 미실행 계획을 확인하지 못해 6장 성장 전략은 "
    "제공하지 않았습니다. 성장 전략이 없다는 뜻은 아닙니다."
)

OPERATIONS_PARTNERS_SHORTFALL_REASON: Final[str] = (
    "최근 공식 자료에서 현재 운영 구조 또는 파트너 역할을 확인하지 못해 7장 "
    "사업 운영과 파트너 구조는 제공하지 않았습니다. 운영 구조나 파트너가 "
    "없다는 뜻은 아닙니다."
)

IDENTITY_SUMMARY_SHORTFALL_REASON: Final[str] = (
    "공식 정체성 사실은 확인했지만 이를 쉬운 말로 정리한 검증 문장을 확보하지 "
    "못해 1장은 공식 원문 중심으로 제공합니다. 회사 정체성이 불분명하다는 뜻은 "
    "아닙니다."
)

CUSTOMER_MARKET_SHORTFALL_REASON: Final[str] = (
    "공식 자료에서 고객·시장 범위를 검증하지 못해 2장은 확인된 수익 구조만 "
    "제공합니다. 고객이나 시장이 없다는 뜻은 아닙니다."
)

PAST_NARRATIVE_SHORTFALL_REASON: Final[str] = (
    "연속 3개 완료 사업연도 공식 실적은 확인했지만 최근 완료 실행과 변화 해석을 "
    "함께 검증하지 못해 4장은 공식 실적표만 제공합니다. 변화가 없다는 뜻은 "
    "아닙니다."
)

# 조건부 장은 서로 다른 결손 사유다. 한 문장으로 합치면 5·8·9장 중 무엇이
# 부족했는지 사용자와 운영자가 구분할 수 없으므로 장별 표준 문구를 따로 둔다.
CONDITIONAL_SECTION_SHORTFALL_REASONS: Final[dict[str, str]] = {
    "current_challenges": CURRENT_CHALLENGES_SHORTFALL_REASON,
    "culture": CULTURE_SHORTFALL_REASON,
    "competitive_position": COMPARISON_SHORTFALL_REASON,
}

# 부분 보고서에서 빠진 장은 모두 서로 다른 표준 결손 사유를 남긴다.
# 기존 5·8·9장 문구는 그대로 재사용해 기존 Grade.PARTIAL의 의미를
# 바꾸지 않고, 새로 허용한 동적 결손만 명시적으로 추가한다.
PARTIAL_SECTION_SHORTFALL_REASONS: Final[dict[str, str]] = {
    "portfolio": PORTFOLIO_SHORTFALL_REASON,
    "current_challenges": CURRENT_CHALLENGES_SHORTFALL_REASON,
    "future_strategy": FUTURE_STRATEGY_SHORTFALL_REASON,
    "operations_partners": OPERATIONS_PARTNERS_SHORTFALL_REASON,
    "culture": CULTURE_SHORTFALL_REASON,
    "competitive_position": COMPARISON_SHORTFALL_REASON,
}

# 요약은 새 문장을 검수한 결과가 아니라 이미 검증된 FactRecord.claim을 글자 그대로
# 재사용한다. 새 독립 요약 검수가 실행된 것처럼 오해되는 명칭은 쓰지 않는다.
SUMMARY_VERIFICATION_STATUS: Final[str] = "verified_fact_reuse"
TIME_SECTION_IDS: Final[tuple[str, ...]] = (
    "past_changes",
    "current_challenges",
    "future_strategy",
)
CANONICAL_TIME_STATES: Final[frozenset[str]] = frozenset(
    {"standing", "completed", "current_issue", "current_response", "future_plan"}
)
SECTION_TIME_STATES: Final[dict[str, frozenset[str]]] = {
    "identity": frozenset({"standing"}),
    "business_model": frozenset({"standing"}),
    "portfolio": frozenset({"standing"}),
    "past_changes": frozenset({"completed"}),
    "current_challenges": frozenset({"current_issue", "current_response"}),
    "future_strategy": frozenset({"future_plan"}),
    "operations_partners": frozenset({"standing"}),
    "culture": frozenset({"standing"}),
    "competitive_position": frozenset({"standing"}),
}

COMPARISON_JUDGMENT_LABELS: Final[dict[str, str]] = {
    "competitive_advantage": "경쟁우위",
    "operating_characteristic": "운영 특성",
}

# 비교 조건을 충족하지 못한 축은 조사 실패 사유로만 남긴다. 공개 사실의
# 닫힌 claim_type 목록과 섞지 않아 빈 9장·근거 부족 카드를 출고하지 않는다.
INTERNAL_ONLY_CLAIM_TYPES_BY_SECTION: Final[dict[str, frozenset[str]]] = {
    "competitive_position": frozenset({"comparison_limitation"}),
}

# 장마다 의미가 다른 임의 문자열을 조용히 받아들이면 demo·실서비스·문서가 서로
# 다른 이름을 쓰게 된다. AI가 고르는 유형과 프로그램이 만드는 표·비교 유형을 모두
# 포함한 canonical 닫힌 목록이다.
CANONICAL_CLAIM_TYPES_BY_SECTION: Final[dict[str, frozenset[str]]] = {
    "identity": frozenset(
        {"identity_summary", "official_self_definition", "operating_scope"}
    ),
    "business_model": frozenset({"revenue_model", "customer_market", "revenue_mix"}),
    "portfolio": frozenset({"priority_product"}),
    "past_changes": frozenset(
        {"completed_execution", "change_interpretation", "historical_performance"}
    ),
    "current_challenges": frozenset({"current_issue", "current_response"}),
    "future_strategy": frozenset({"future_plan"}),
    "operations_partners": frozenset({"operating_core", "partner_role"}),
    "culture": frozenset({"official_value", "work_example"}),
    "competitive_position": frozenset({"competitive_comparison"}),
}
SUMMARY_MIN_ITEMS: Final[int] = 3
SUMMARY_MAX_ITEMS: Final[int] = 5

#: relationship_or_action에 내부 claim_type 키가 폴백된 경우의 화면용 한국어 라벨.
#: 조립기(canonical_report._fact_from_claim)는 이 필드를 채울 값이 없으면
#: claim_type 내부 키를 그대로 남기므로, 렌더는 이 맵을 경유해 한국어로 바꾼다.
#: CANONICAL_CLAIM_TYPES_BY_SECTION의 모든 claim_type 키를 빠짐없이 포함해야
#: 하며, 완전성은 시험(test_render_no_internal_keys.py)이 보장한다.
RELATIONSHIP_KEY_LABELS: Final[dict[str, str]] = {
    # 1장 기업 정체성
    "identity_summary": "공식 자료 기반 정체성 요약",
    "official_self_definition": "회사의 공식 자기정의",
    "operating_scope": "공식 자료에 적힌 사업 범위",
    # 2장 사업 구조와 수익 모델
    "revenue_model": "공식 자료 기반 수익 구조",
    "customer_market": "공식 자료 기반 고객·시장",
    "revenue_mix": "공식 자료 기반 수익 구성",
    # 3장 핵심 제품·서비스
    "priority_product": "공식 자료 기반 중점 제품·서비스",
    # 4장 3개년 주요 변화와 실행
    "completed_execution": "공식 자료로 확인된 완료 실행",
    "change_interpretation": "공식 실적에 근거한 변화 해석",
    "historical_performance": "완료 사업연도 공식 실적",
    # 5장 당면 과제와 대응
    "current_issue": "공식 자료로 확인된 현재 과제",
    "current_response": "공식 자료로 확인된 진행 중 대응",
    # 6장 성장 전략
    "future_plan": "공식 발표된 미실행 계획",
    # 7장 사업 운영과 파트너 구조
    "operating_core": "공식 자료 기반 핵심 운영",
    "partner_role": "공식 자료 기반 파트너 역할",
    # 8장 인재상과 일하는 방식
    "official_value": "회사의 공식 가치",
    "work_example": "공식 자료 기반 업무 사례",
    # 9장 동종업계 비교
    "competitive_comparison": "공식 자료 기반 동종업계 비교",
}

#: 맵에 없는 영문 내부 키가 폴백돼도 빈 문자열 대신 쓰는 기본 라벨.
#: 빈 값은 출고 게이트의 빈 항목 검사에 걸려 보고서 전체 차단을 일으킨다.
RELATIONSHIP_KEY_FALLBACK_LABEL: Final[str] = "공식 자료 기반 확인 항목"
