"""평상시 주석과 실제 사업 사건의 독립 반례."""
from features.evidence_collection.challenge_accounting_policy import (
    is_challenge_accounting_policy, split_challenge_accounting_policy,
)
from features.evidence_collection.relevance import score_fragment_slots_with_signal


POLICIES = (
    "회사는 판매로 인하여 부담하는 보증책임에 대한 충당부채를 합리적으로 추정하여 인식하고 있다.",
    "과거 축적된 경험에 근거하여 할인 및 반품을 추정한다.",
    "회사는 제품의 판매에서 고객에게 수량할인을 제공하며 고객은 불량재화를 반품할 권리를 가지고 있다.",
    "거래가격은 변동대가의 추정치를 포함하며 수익을 인식한다.",
    "충당부채 1.5억원은 보증책임을 추정하여 인식한 금액이다.",
    "반품이 발생할 경우를 고려하여 보증충당부채를 추정한다.",
    "리콜을 실시할 수 있는 경우 환불액을 추정하여 인식한다.",
    "공장 투자용 대출금 잔액은 20억원이다.",
    "유동성 위험을 관리하고 금융부채 잔액 1,234백만원을 확인했습니다.",
    "유동성 위험관리 방식을 변경했습니다.",
    "금융부채의 공정가치는 30억원으로 평가하였다.",
    "제품의 결함이 발견되면 보증충당부채를 추정하여 인식한다.",
    "고객의 연체율이 상승할 것으로 예상하여 대출 충당부채를 인식한다.",
    "생산 중단으로 손실이 발생할 경우 충당부채를 추정하여 인식한다.",
)
EVENTS = (
    "제품 결함으로 반품이 급증하여 보증충당부채를 추가로 인식하였다.",
    "리콜을 실시한 제품의 환불금액을 추정하여 충당부채로 인식하였다.",
    "규제기관의 시정 명령을 받아 보증충당부채를 인식하였다.",
    "회사는 고객에게 수익인식 회계 자문 서비스를 제공한다.",
    "회사는 기업에 반품관리 솔루션을 제공하며 예상 환불액을 추정한다.",
    "자금부족과 유동성위험으로 고객 납품을 중단하였다.",
    "대출 고객의 연체율이 상승하여 충당부채를 인식하였다.",
    "금융회사는 고객에게 유동성 관리 서비스를 제공한다.",
)


def test_policies_are_not_current_business_challenges():
    for text in POLICIES:
        assert is_challenge_accounting_policy(text), text
        scores, observed = score_fragment_slots_with_signal(text)
        assert observed
        assert not any(score.slot_id.startswith("current_challenges:") for score in scores)


def test_actual_events_and_service_are_retained():
    for text in EVENTS:
        assert not is_challenge_accounting_policy(text), text


def test_mixed_source_is_preserved_and_only_policy_scoring_is_removed():
    text = POLICIES[0] + " 제품 결함으로 반품이 급증하여 생산을 중단하였다."
    split = split_challenge_accounting_policy(text)
    assert split.excluded_clauses == 1
    assert "제품 결함" in split.score_text
    assert "합리적으로 추정" not in split.score_text
    assert text.startswith(POLICIES[0])


def test_no_signal_reentry_cannot_restore_filtered_policy():
    text = "보증책임에 대한 충당부채를 추정하여 인식한다."
    _, observed = score_fragment_slots_with_signal(text)
    assert observed


def test_same_sentence_actual_event_is_retained_beside_conditional_policy():
    for text in (
        "제품 결함으로 리콜을 실시했으며, 추가 손실이 발생할 경우 충당부채를 인식한다.",
        "납품이 중단되었으며, 손실이 발생할 경우 충당부채를 인식한다.",
    ):
        split = split_challenge_accounting_policy(text)
        assert split.excluded_clauses == 1
        assert "충당부채" not in split.score_text
        assert "실시했으며" in split.score_text or "중단되었으며" in split.score_text
        assert not is_challenge_accounting_policy(text)


def test_accounting_subject_survives_comma_for_scoring_only():
    text = "운송용역의 거래가격을 배분하며, 발생한 원가를 기준으로 진행률을 산정하고 추정치를 매년 검토한다."
    split = split_challenge_accounting_policy(text)
    assert split.score_text == ""
    assert split.excluded_clauses == 2
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert not any(score.slot_id.startswith("current_challenges:") for score in scores)


def test_pure_financial_exposure_is_observed_and_cannot_reenter_as_no_signal():
    for text in (
        "회사는 외화 거래를 수행하므로 환율변동으로 인한 위험에 노출되어 있다.",
        "회사는 신용위험을 관리하며, 위험을 식별하여 정기적으로 대응한다.",
        "금리위험 관리대책을 변경하여 차입 이자를 줄였다.",
    ):
        assert split_challenge_accounting_policy(text).score_text == ""
        scores, observed = score_fragment_slots_with_signal(text)
        assert observed
        assert not any(score.slot_id.startswith("current_challenges:") for score in scores)


def test_financial_risk_and_actual_business_or_service_are_separated():
    for text in (
        "환율변동 위험이 커져 원재료 조달이 중단되었다.",
        "회사는 고객에게 외환 위험관리 솔루션을 제공한다.",
        "거래가격을 배분하며, 제품 결함으로 리콜을 실시했다.",
    ):
        assert not is_challenge_accounting_policy(text)
        assert split_challenge_accounting_policy(text).score_text
    mixed = "환율위험을 정기적으로 관리하며, 고객 납품이 중단되었다."
    split = split_challenge_accounting_policy(mixed)
    assert split.excluded_clauses == 1
    assert split.score_text.strip() == "고객 납품이 중단되었다"


def test_policy_context_does_not_cross_sentence_and_market_business_risk_remains():
    text = "거래가격을 배분한다. 시장 수요 감소 위험으로 경쟁이 심화되었다."
    split = split_challenge_accounting_policy(text)
    assert "시장 수요 감소" in split.score_text
    assert split.excluded_clauses == 1


def test_interest_monitoring_policy_cannot_reenter_as_a_business_response():
    for text in (
        "이자율과 외화위험을 관리하기 위해 필요한 경우 파생상품계약을 체결하고 있습니다."
        "연결회사는 이자율 변동으로 인한 불확실성 제거와 금융원가 최소화를 위해, "
        "주기적인 금리동향 모니터링과 적절한 대응방안 수립을 운용하고 있습니다.",
        "회사는 이자율과 외화위험을 관리하기 위해 파생상품계약을 체결하고 있으며, "
        "주기적인 금리동향 모니터링과 적절한 대응방안 수립을 운용하고 있다.",
        "회사는 금융원가를 최소화하기 위해 금리동향을 점검한다.",
        "이자율 변동에 따른 불확실성을 제거하고 있다.",
    ):
        assert is_challenge_accounting_policy(text), text
        assert split_challenge_accounting_policy(text).score_text == ""
        scores, observed = score_fragment_slots_with_signal(text)
        assert observed
        assert not any(score.slot_id.startswith("current_challenges:") for score in scores)


def test_interest_customer_service_and_independent_operating_problem_remain():
    for text in (
        "회사는 고객에게 금리동향 모니터링 서비스를 제공한다.",
        "회사는 기업 고객에게 이자율 분석 보고서를 발행합니다.",
        "회사는 고객에게 파생상품 위험관리 서비스를 제공한다.",
        "은행은 대출 고객의 연체율이 상승하여 심사 기준을 강화했다.",
        "회사는 공장 안전센서를 모니터링하고 설비 개선을 실시했다.",
    ):
        assert not is_challenge_accounting_policy(text), text
        assert split_challenge_accounting_policy(text).score_text == text
    for text in (
        "회사는 금융원가를 최소화하며, 고객 납품이 중단되었다.",
        "회사는 금리동향을 모니터링한다. 제품 결함으로 리콜을 실시했다.",
    ):
        split = split_challenge_accounting_policy(text)
        assert not is_challenge_accounting_policy(text)
        assert split.excluded_clauses == 1
        assert "금리동향" not in split.score_text and "금융원가" not in split.score_text
        assert "납품이 중단" in split.score_text or "리콜을 실시" in split.score_text


def test_customer_financial_service_and_actual_problem_are_preserved():
    for text in (
        "회사는 기업 고객에게 공정가치 평가보고서를 발행합니다.",
        "회사는 고객을 대상으로 대출 서비스를 제공한다.",
        "회사는 고객에게 신용위험 평가 서비스를 제공한다.",
        "회사는 차주에게 대출 서비스를 제공합니다.",
        "회사는 대출 고객 확보에 어려움을 겪고 있다.",
        "고객 대출 연체율이 높아 회사가 심사 기준을 강화했다.",
        "고객 대출에서 부실률이 급등했고 회사는 신규 심사모형을 도입했다.",
    ):
        assert not is_challenge_accounting_policy(text), text
        assert split_challenge_accounting_policy(text).score_text == text


def test_mixed_actual_operating_response_is_retained_and_conditional_discovery_is_not():
    mixed = "회사는 반품 충당부채를 추정하며, 불량을 줄이기 위해 검사 공정을 자동화했다."
    split = split_challenge_accounting_policy(mixed)
    assert split.excluded_clauses == 1
    assert "검사 공정을 자동화했다" in split.score_text
    assert "충당부채" not in split.score_text
    assert not is_challenge_accounting_policy(mixed)
    hypothetical = "회사는 제품 보증기간 중 결함이 발견되었을 때 수익을 인식하지 않고 보증충당부채를 추정합니다."
    assert is_challenge_accounting_policy(hypothetical)
    assert split_challenge_accounting_policy(hypothetical).score_text == ""
    assert score_fragment_slots_with_signal(hypothetical)[1]


def test_financial_words_do_not_turn_customer_workflow_or_operational_response_into_policy():
    for text in (
        "은행은 대출 신청 고객의 긴 대기시간을 줄이기 위해 비대면 심사를 도입했다.",
        "회사는 장비 납기 지연을 해결하기 위해 대출을 받아 신규 조립라인을 설치했다.",
        "회사는 공급 지연에 대응하기 위해 차입금으로 신규 생산설비를 도입했다.",
    ):
        assert not is_challenge_accounting_policy(text)
        assert split_challenge_accounting_policy(text).score_text == text
    mixed = "회사는 금융부채를 측정하며, 반도체 고객 수요가 줄어 생산라인 가동률이 떨어졌다."
    split = split_challenge_accounting_policy(mixed)
    assert split.excluded_clauses == 1
    assert "수요가 줄어 생산라인 가동률이 떨어졌다" in split.score_text
    assert "금융부채" not in split.score_text


def test_planned_customer_service_is_not_current_business_fact():
    text = "회사는 고객에게 대출 서비스를 제공할 예정이다."
    assert is_challenge_accounting_policy(text)
    assert split_challenge_accounting_policy(text).score_text == ""
