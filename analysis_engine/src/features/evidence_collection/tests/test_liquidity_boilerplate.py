"""5장 유동성 상용구는 채점에서만 빼고 회사 사실은 보존한다."""

from features.evidence_collection.liquidity_boilerplate import (
    is_liquidity_boilerplate_clause,
    split_liquidity_clauses,
)


PURE_POLICY = "회사는 부채상환을 포함하여 예상 영업자금수요를 충당할 유동성을 예측하고 관리합니다."
ACTUAL_EVENT = "유동성 위험과 관련하여 차입금 1,234백만원의 만기를 변경했습니다."
ACTUAL_PRESSURE = "회사는 유동성 위험이 악화되어 자금조달에 차질이 발생했습니다."


def test_pure_liquidity_policy_has_no_challenge_score_text() -> None:
    split = split_liquidity_clauses(PURE_POLICY)
    assert split.only_boilerplate
    assert split.score_text == ""
    assert split.excluded_clauses == 1


def test_mixed_paragraph_keeps_real_business_clause_without_rewriting_source() -> None:
    text = PURE_POLICY + "\n" + ACTUAL_EVENT
    split = split_liquidity_clauses(text)
    assert split.excluded_clauses == 1
    assert split.retained_clauses == 1
    assert "1,234백만원" in split.score_text
    assert PURE_POLICY not in split.score_text
    assert text.endswith(ACTUAL_EVENT)


def test_money_event_and_financial_business_pressure_are_preserved() -> None:
    assert not is_liquidity_boilerplate_clause(ACTUAL_EVENT)
    assert not is_liquidity_boilerplate_clause(ACTUAL_PRESSURE)
    assert split_liquidity_clauses(ACTUAL_PRESSURE).score_text == ACTUAL_PRESSURE


def test_table_row_and_heading_only_tail_are_not_combined_into_false_policy() -> None:
    table = "유동성 위험 | 예측하고 관리 ; 실제 자금조달 차질 | 발생"
    assert not is_liquidity_boilerplate_clause(table)
    split = split_liquidity_clauses(table)
    assert split.excluded_clauses == 1
    assert split.retained_clauses == 1
    assert split.score_text.strip() == "실제 자금조달 차질 | 발생"
    assert not is_liquidity_boilerplate_clause("유동성 위험")


def test_numbers_without_currency_are_not_company_specific_exemption() -> None:
    policy = PURE_POLICY + " 3개월 내 점검합니다."
    split = split_liquidity_clauses(policy)
    assert split.excluded_clauses == 1
    assert split.retained_clauses == 1


def test_decimal_currency_and_foreign_amount_keep_original_issue_clause() -> None:
    cases = (
        "유동성 위험에 노출된 금융부채는 1.5억원입니다.",
        "유동성 위험에 노출된 금융부채는 1.5 USD입니다.",
        "유동성 위험에 노출된 금융부채는 $1.5입니다.\n",
    )
    for text in cases:
        split = split_liquidity_clauses(text)
        assert split.excluded_clauses == 0
        assert split.score_text == text


def test_qualitative_company_risk_change_is_preserved() -> None:
    cases = (
        "회사의 신용등급 하락으로 유동성위험이 증가했습니다.",
        "채무가 연체되어 유동성위험이 커졌습니다.",
        "유동성위험이 확대되어 금융기관과 협의 중입니다.",
    )
    for text in cases:
        assert not is_liquidity_boilerplate_clause(text)
        assert split_liquidity_clauses(text).score_text == text


def test_financial_business_offering_is_not_treated_as_accounting_policy() -> None:
    text = "금융회사는 고객에게 유동성위험 관리 서비스를 제공합니다."
    assert not is_liquidity_boilerplate_clause(text)
    assert split_liquidity_clauses(text).score_text == text


def test_actual_business_disruption_and_financial_deliverables_are_preserved() -> None:
    for text in (
        "당사는 유동성위험으로 인해 신규 주문의 수주를 중단하였다.",
        "당사는 유동성위험에 대응하기 위해 공장 생산을 중단하였다.",
        "당사는 기업고객에게 유동성위험 모니터링 솔루션을 제공한다.",
        "당사는 기업고객에게 유동성위험 관리 솔루션을 제공한다.",
        "당사는 기업고객에게 유동성위험 평가 보고서를 발행한다.",
        "부채상환을 위한 유동성 관리와 관련하여 공장 생산을 중단하였다.",
    ):
        assert not is_liquidity_boilerplate_clause(text)
        assert split_liquidity_clauses(text).score_text == text


def test_policy_conditions_and_customer_payment_obligations_are_not_real_deliveries() -> None:
    for text in (
        "유동성위험을 관리하며 필요시 공장 생산을 중단할 수 있다.",
        "유동성위험을 관리하며 고객에게 지급할 대금을 점검한다.",
    ):
        assert is_liquidity_boilerplate_clause(text)
