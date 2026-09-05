"""보고서 문장을 분류하는 원자 주장 범주의 정본.

근거 충분성의 최소 의미 칸(``report_evidence.policy``)과 문장 분류 범주는
같은 것이 아니다. 한 의미 칸에는 서로 다른 사실이 여러 개 있을 수 있고,
한 장은 독자가 이해할 만큼 여러 문장을 가져야 한다. 따라서 ``claim_slot``은
고유 번호가 아니라 닫힌 **범주**이며, 같은 범주의 서로 다른 원자 사실을
여러 번 가질 수 있다.

composer와 품질 평가기가 각자 목록을 복사하면 프롬프트는 허용하지만 평가기는
거절하는 모순이 생긴다. 두 기능이 실제로 함께 쓰므로 shared 한 벌로 둔다.
"""

from __future__ import annotations

from typing import Final


CLAIM_SLOT_POLICY_VERSION: Final[str] = "report-claim-slots-v1"

CLAIM_SECTION_IDS: Final[tuple[str, ...]] = (
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
        "identity:corporate_identity",
        "identity:business_definition",
        "identity:legal_scope",
        "identity:official_location",
        "identity:self_positioning",
    ),
    "business_model": (
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:sales_channel",
        "business_model:regional_mix",
        "business_model:value_exchange",
    ),
    "portfolio": (
        "portfolio:product_role",
        "portfolio:portfolio_priority",
        "portfolio:customer_fit",
        "portfolio:revenue_link",
        "portfolio:lifecycle_stage",
    ),
    "past_changes": (
        "past_changes:historical_performance",
        "past_changes:completed_execution",
        "past_changes:cumulative_change",
        "past_changes:change_context",
        "past_changes:change_limit",
    ),
    "current_challenges": (
        "current_challenges:issue",
        "current_challenges:response",
        "current_challenges:initial_signal",
        "current_challenges:unresolved_gap",
        "current_challenges:next_check",
    ),
    "future_strategy": (
        "future_strategy:stated_plan",
        "future_strategy:plan_status",
        "future_strategy:plan_timing",
        "future_strategy:plan_condition",
        "future_strategy:execution_signal",
    ),
    "operations_partners": (
        "operations_partners:value_chain",
        "operations_partners:operating_role",
        "operations_partners:supply_relation",
        "operations_partners:distribution_relation",
        "operations_partners:partnership",
    ),
    "culture": (
        "culture:leadership",
        "culture:work_principle",
        "culture:decision_process",
        "culture:organization_change",
        "culture:verified_case",
    ),
    "competitive_position": (
        "competitive_position:self_context",
        "competitive_position:stated_differentiator",
        "competitive_position:limitation",
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
    ),
}


def claim_slots_for(section_id: str) -> tuple[str, ...]:
    """한 장에서 허용하는 주장 범주를 돌려주며 오타는 거절한다."""

    if type(section_id) is not str:
        raise TypeError("주장 장 식별자는 문자열이어야 합니다")
    clean = section_id.strip()
    try:
        return CLAIM_SLOTS_BY_SECTION[clean]
    except KeyError as error:
        raise ValueError(f"알 수 없는 주장 장 식별자입니다: {clean}") from error
