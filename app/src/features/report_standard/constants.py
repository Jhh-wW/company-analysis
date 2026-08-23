"""기업분석 canonical(v4) 출력 계약의 안정적인 식별자.

내부 저장 키는 의미 기반 ID를 쓰고, 화면 번호·제목·태그는 이 파일에서만
관리한다. 숫자 키를 저장 키로 재사용하면 레거시 채용 블록 5~8과 충돌한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


CANONICAL_SCHEMA_VERSION: Final[str] = "company-report-v4-canonical"


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

# 1~8장은 회사 자체를 설명하는 기본 보고서의 필수 장이다. 9장은 양사의 공식
# 자료를 같은 조건으로 맞출 수 있을 때만 붙이는 조건부 장이다. 비교 근거가 없다고
# 검증된 1~8장을 함께 폐기하면 사용자는 이미 발생한 조사비를 내고 아무 결과도
# 받지 못한다. 반대로 빈 9장을 일반론으로 채우면 경쟁우위를 지어내게 된다.
CONDITIONAL_SECTION_IDS: Final[frozenset[str]] = frozenset(
    {"competitive_position"}
)
REQUIRED_SECTION_IDS: Final[frozenset[str]] = frozenset(
    section_id
    for section_id in CANONICAL_SECTION_IDS
    if section_id not in CONDITIONAL_SECTION_IDS
)

COMPARISON_SHORTFALL_REASON: Final[str] = (
    "양사 공식자료를 같은 지표·기간·범위로 맞추지 못해 9장 동종업계 비교는 "
    "제공하지 않았습니다. 경쟁우위가 없다는 뜻은 아닙니다."
)

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

COMPARISON_JUDGMENTS: Final[frozenset[str]] = frozenset(
    {"competitive_advantage", "operating_characteristic"}
)
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
