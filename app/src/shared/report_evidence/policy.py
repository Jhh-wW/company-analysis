"""아홉 장을 쓰기 전에 반드시 채워야 하는 의미 칸의 정본."""

from __future__ import annotations

from typing import Final


EVIDENCE_SLOT_POLICY_VERSION: Final[str] = "section-evidence-slots-v1"

REQUIRED_EVIDENCE_SECTION_IDS: Final[tuple[str, ...]] = (
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

# 문장 개수로 자료 충분성을 대신하지 않는다. 각 장이 답해야 하는 서로 다른
# 질문을 닫힌 의미 칸으로 두고, 이 칸이 실제 근거 조각이나 검증 FactRecord로
# 채워진 경우에만 작성기로 넘긴다. 풍부함은 별도 report-quality-v2 계약이
# 평가하므로 여기서 임의의 문서·조각 개수를 품질 기준으로 쓰지 않는다.
REQUIRED_EVIDENCE_SLOTS_BY_SECTION: Final[dict[str, tuple[str, ...]]] = {
    "identity": (
        "identity:corporate_identity",
        "identity:business_definition",
    ),
    "business_model": (
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:value_exchange",
    ),
    "portfolio": (
        "portfolio:product_role",
        "portfolio:revenue_link",
    ),
    "past_changes": (
        "past_changes:historical_performance",
        "past_changes:completed_execution",
    ),
    "current_challenges": (
        "current_challenges:issue",
        "current_challenges:response",
    ),
    "future_strategy": (
        "future_strategy:stated_plan",
        "future_strategy:plan_status",
    ),
    "operations_partners": (
        "operations_partners:value_chain",
        "operations_partners:operating_role",
    ),
    "culture": (
        "culture:work_principle",
        "culture:verified_case",
    ),
    "competitive_position": (
        "competitive_position:self_context",
        "competitive_position:stated_differentiator",
        "competitive_position:limitation",
    ),
}

# 이 칸은 근거 수집기가 임의 산문으로 채우지 않는다. 3개년 실적·동일조건
# 비교를 맡은 구조화 검증기가 검증된 FactRecord ID를 정확한 칸에 주입한다.
INJECTED_EVIDENCE_SLOTS_BY_SECTION: Final[dict[str, tuple[str, ...]]] = {
    "past_changes": ("past_changes:historical_performance",),
    "competitive_position": (
        "competitive_position:limitation",
    ),
}


def required_slots_for(section_id: str) -> tuple[str, ...]:
    """한 장의 전체 필수 의미 칸을 돌려주며 오타는 조용히 받지 않는다."""

    clean = str(section_id).strip()
    try:
        return REQUIRED_EVIDENCE_SLOTS_BY_SECTION[clean]
    except KeyError as error:
        raise ValueError(f"알 수 없는 근거 장 식별자입니다: {clean}") from error


def injected_slots_for(section_id: str) -> tuple[str, ...]:
    """구조화 검증기가 주입해 채워야 할 칸."""

    required = required_slots_for(section_id)
    injected = INJECTED_EVIDENCE_SLOTS_BY_SECTION.get(section_id, ())
    if set(injected) - set(required):  # 상수 편집 실수를 import 뒤에도 막는다.
        raise ValueError(f"{section_id}의 주입 의미 칸 정책이 손상됐습니다")
    return injected


def collector_slots_for(section_id: str) -> tuple[str, ...]:
    """수집 생산부가 원문 후보를 제공해야 할 칸."""

    required = required_slots_for(section_id)
    injected = set(injected_slots_for(section_id))
    return tuple(slot_id for slot_id in required if slot_id not in injected)
