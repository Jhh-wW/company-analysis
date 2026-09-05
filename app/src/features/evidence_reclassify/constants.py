"""근거 재판정의 닫힌 정책과 프롬프트 상수."""

from __future__ import annotations

from typing import Final

from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)


RECLASSIFY_PROMPT_VERSION: Final[str] = "evidence-reclassify-v1"
RECLASSIFY_SCHEMA_NAME: Final[str] = "evidence_reclassification"
MAX_PROMPT_CHARS: Final[int] = 60_000
MAX_SLOTS_PER_PARAGRAPH: Final[int] = 3
RECLASSIFIED_SCORE_MILLIS: Final[int] = 1_000
RECLASSIFIED_ORIGIN: Final[str] = "ai_reclassified"

# 구조화 사실 생산부가 소유한 칸은 산문 재판정으로 채우지 않는다. 정본 policy의
# collector 분리를 그대로 읽어 와 새 기능 안에 슬롯 사본을 만들지 않는다.
ALLOWED_SECTION_IDS: Final[tuple[str, ...]] = REQUIRED_EVIDENCE_SECTION_IDS
ALLOWED_SLOT_IDS_BY_SECTION: Final[dict[str, tuple[str, ...]]] = {
    section_id: collector_slots_for(section_id)
    for section_id in ALLOWED_SECTION_IDS
}
ALLOWED_SLOT_IDS: Final[tuple[str, ...]] = tuple(
    slot_id
    for section_id in ALLOWED_SECTION_IDS
    for slot_id in ALLOWED_SLOT_IDS_BY_SECTION[section_id]
)

PREFERRED_SECTION_MARKERS: Final[tuple[str, ...]] = (
    "사업의 내용",
    "회사의 개요",
    "영업부문",
    "수익 구분",
    "수익구분",
)

# 이 어휘는 실행 완료 사실에도 드물게 섞일 수 있다. 그러나 인용 검증만으로
# 시간 상태를 증명할 수 없으므로 재판정 차선에서는 미래 장에만 보수적으로 둔다.
PLAN_FORECAST_TERMS: Final[tuple[str, ...]] = (
    "계획",
    "예정",
    "전망",
    "향후",
    "목표",
    "추진",
    "검토 중",
    "착수",
    "will",
    "plan to",
    "expected to",
    "forecast",
)

SECTION_PURPOSES: Final[dict[str, str]] = {
    "identity": "회사가 공식적으로 정의한 기업의 본질과 사업 범위를 확인한다.",
    "business_model": "누구에게 어떤 가치를 제공하고 어떤 경로로 수익을 얻는지 확인한다.",
    "portfolio": "현재 핵심 제품·서비스와 전체 포트폴리오 안의 역할을 확인한다.",
    "past_changes": "지난 3개년의 실제 실행과 확인된 변화를 구분한다.",
    "current_challenges": "현재 미해결 과제와 회사가 실제로 하는 대응을 확인한다.",
    "future_strategy": "회사가 공식적으로 밝힌 미래 계획과 실행 조건을 확인한다.",
    "operations_partners": "사업 운영 단계와 회사·파트너가 맡는 역할을 확인한다.",
    "culture": "전사 공통 가치·행동 원칙과 공식 자료의 실제 사례를 확인한다.",
    "competitive_position": "공식 자료에 나타난 시장 맥락과 자사의 구체적 강점을 확인한다.",
}

SLOT_DESCRIPTIONS: Final[dict[str, str]] = {
    "identity:corporate_identity": "회사의 공식 자기 정의",
    "identity:business_definition": "회사가 영위한다고 밝힌 사업의 정의",
    "business_model:revenue_model": "대가를 받고 수익을 만드는 방식",
    "business_model:customer_type": "실제 구매자·사용자·수혜자 유형",
    "business_model:value_exchange": "고객에게 주는 가치와 받는 대가",
    "portfolio:product_role": "핵심 제품·서비스의 이름과 사업 역할",
    "portfolio:revenue_link": "제품·서비스의 매출 기여 또는 실제 실행 근거",
    "past_changes:completed_execution": "이미 완료된 실행과 그 시점",
    "current_challenges:issue": "현재 성과를 제약하는 미해결 문제",
    "current_challenges:response": "문제에 대해 현재 수행 중인 대응",
    "future_strategy:stated_plan": "회사가 공식적으로 밝힌 미래 계획",
    "future_strategy:plan_status": "계획의 승인·조건·진행 상태",
    "operations_partners:value_chain": "제품·서비스가 고객에게 닿는 운영 단계",
    "operations_partners:operating_role": "각 단계에서 회사가 직접 맡는 역할",
    "culture:work_principle": "전사 공통 가치와 행동 원칙",
    "culture:verified_case": "공식 자료로 확인되는 실제 조직 사례",
    "competitive_position:self_context": "시장 맥락 속 자사의 구체적 강점",
}

ASSIGNMENTS_KEY: Final[str] = "assignments"
REMOVALS_KEY: Final[str] = "removals"

REJECT_INVALID_ITEM: Final[str] = "invalid_item"
REJECT_INVALID_PARAGRAPH_ID: Final[str] = "invalid_paragraph_id"
REJECT_PARAGRAPH_NOT_FOUND: Final[str] = "paragraph_not_found"
REJECT_INVALID_SECTION_ID: Final[str] = "invalid_section_id"
REJECT_INVALID_SLOT_ID: Final[str] = "invalid_slot_id"
REJECT_SECTION_SLOT_MISMATCH: Final[str] = "section_slot_mismatch"
REJECT_INVALID_QUOTE: Final[str] = "invalid_quote"
REJECT_QUOTE_NOT_FOUND: Final[str] = "quote_not_found"
REJECT_PLAN_TERM_OUTSIDE_FUTURE: Final[str] = "plan_term_outside_future_strategy"
REJECT_PARAGRAPH_SLOT_LIMIT: Final[str] = "paragraph_slot_limit"
REJECT_DUPLICATE_ASSIGNMENT: Final[str] = "duplicate_assignment"
REJECT_INVALID_REMOVAL_REASON: Final[str] = "invalid_removal_reason"

AI_RECLASSIFIED_REASON_CODE: Final[str] = "ai_reclassified"
REMOVAL_REASON_CODE_PREFIX: Final[str] = "ai_reclassified_removed"
