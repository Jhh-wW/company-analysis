"""옛 오프라인 골든 응답에 현재 주장 범주와 합성 문서 신원을 명시한다.

저장된 후보 글과 원문은 바꾸지 않는다. 인용 없는 해석이나 근거가 부족한
비교도 그대로 남겨 실제 공개 경계가 제외하는지 확인할 수 있게 한다.
"""

from copy import deepcopy


_SLOTS = {
    "identity": ("corporate_identity", "self_positioning", "self_positioning", "business_definition", "business_definition", "business_definition"),
    "business_model": ("revenue_model", "revenue_model", "regional_mix", "revenue_model", "value_exchange", "value_exchange"),
    "portfolio": ("product_role", "customer_fit", "lifecycle_stage", "lifecycle_stage", "portfolio_priority", "portfolio_priority"),
    "past_changes": ("completed_execution", "completed_execution", "historical_performance", "historical_performance", "cumulative_change", "change_limit"),
    "current_challenges": ("issue", "initial_signal", "issue", "issue", "response", "next_check"),
    "future_strategy": ("stated_plan", "stated_plan", "plan_timing", "stated_plan", "plan_condition", "stated_plan"),
    "operations_partners": ("value_chain", "distribution_relation", "partnership", "operating_role", "partnership", "supply_relation"),
    "culture": ("work_principle", "work_principle", "verified_case", "work_principle", "verified_case", "work_principle"),
    "competitive_position": ("comparison_metric", "comparison_metric", "comparison_judgment", "comparison_judgment", "comparison_target", "comparison_judgment"),
}

# 원문별로 검토한 공개 제외 목록. 생산 검증 술어를 복사해 기대값을 만들지 않는다.
# 무인용 해석, 자기 인용에서 설명하지 않는 전망·비용 조건과 9장의 회사 우위로
# 연결되지 않은 비교는 공개 근거가 없다.
# ★ 기대값 갱신 (2026-09-23 4차 중복 삭제 엄밀 증명, 총괄 확정) — 1장 [2]
#   「공식 표어는 '…'이며 Leader's Code로 …를 제시한다」는 이제 1장에 남는다.
#   8장 두 문장과 글자는 같지만 1장 문장의 뒤 절은 주어가 생략돼, 그 절의 주체가
#   8장 독립 문장의 주체와 같다는 것을 표면으로 증명할 수 없다(「X의 파트너는
#   Y이며 …」 반례와 같은 구조). 증명 없는 삭제보다 중복 노출을 택한다 —
#   골든 38·35는 안전한 의미 보존보다 우선하지 않는다(총괄 허용).
GOLDEN_OMITTED_INDICES = {
    "identity": frozenset({5}),
    "business_model": frozenset({5}),
    "portfolio": frozenset({4, 5}),
    "past_changes": frozenset({4, 5}),
    "current_challenges": frozenset({2, 5}),
    "future_strategy": frozenset({5}),
    "operations_partners": frozenset({4, 5}),
    "culture": frozenset({5}),
    "competitive_position": frozenset({0, 1, 2, 3, 4, 5}),
}


def golden_public_rows(sections, section_id):
    return tuple(row for index, row in enumerate(sections[section_id]["문장들"])
                 if index not in GOLDEN_OMITTED_INDICES[section_id])


def golden_responses_with_slots(responses):
    result = deepcopy(responses)
    for section_id, payload in result["장별_응답"].items():
        assert len(payload["문장들"]) == len(_SLOTS[section_id])
        for row, slot in zip(payload["문장들"], _SLOTS[section_id]):
            row["주장슬롯"] = f"{section_id}:{slot}"
    return result


def golden_fragments_with_identity(fragments):
    result = deepcopy(fragments)
    for number, row in result.items():
        if not str(number).isdigit():
            continue
        if row.get("종류") == "공식IR":
            row["종류"] = "공식 IR"
        if not row.get("출처") and number != "11":
            # 옛 fixture의 공시 발췌는 같은 합성 사업보고서에 속한다.
            # 실제 외부 문서를 내려받았다고 가장하는 URL은 만들지 않는다.
            row["출처"] = "https://fixtures.invalid/jyp/annual-report"
            row["문서명"] = "오프라인 합성 사업보고서"
            row["문서일"] = "2026-03-10"
    return result
