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
