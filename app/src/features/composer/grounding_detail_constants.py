"""수치 결속 진단의 원문 없는 세부 단계와 재작성 안내."""

GROUNDING_DETAIL_VERSION = "grounding-detail-v1"
GROUNDING_DETAIL_GUIDES = {
    "entries_missing": "수치 근거 배열이 비었거나 배열이 아닙니다.",
    "entry_type": "수치 근거 항목이 객체가 아닙니다.",
    "expression_fields": "표현·항목·원문항목이 없거나 문자열이 아닙니다.",
    "expression_not_in_candidate": "검증 표현과 항목이 후보 문장에 정확히 있지 않습니다.",
    "quote_not_bound": "제출한 연속 원문 구절을 그 문장의 인용에서 찾지 못했습니다.",
    "metric_mismatch": "후보 항목과 원문 항목이 서로 다릅니다.",
    "candidate_value_missing": "후보 숫자 표기가 없거나 둘 이상이라 결속할 수 없습니다.",
    "candidate_value_scope": "후보 숫자가 해당 항목·표현의 범위에 결속되지 않습니다.",
    "source_value_scope": "원문 숫자를 해당 항목·원문 구절에서 확인하지 못했습니다.",
    "value_mismatch": "후보 숫자가 원문 값과 허용된 환산·반올림으로 일치하지 않습니다.",
    "dimension_mismatch": "후보의 금액·수량·외화·비율 차원이 원문과 다릅니다.",
    "period_mismatch": "후보 숫자의 기간과 원문 숫자의 기간이 다릅니다.",
    "parenthetical_scope": "괄호 안 숫자가 괄호 밖 다른 항목으로 옮겨졌습니다.",
    "numeric_coverage": "문장에 있는 모든 숫자가 각각 근거에 결속되지 않았습니다.",
    "grounding_missing": "요구한 수치·추세·시점 근거가 응답에 없습니다.",
    "grounding_shape": "근거 항목의 이름 또는 자료형이 계약과 다릅니다.",
    "trend_invalid": "추세의 방향·기간·연속 관측이 원문과 결속되지 않았습니다.",
    "time_invalid": "시점 표현이 원문의 같은 활동에 결속되지 않았습니다.",
}
