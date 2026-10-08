"""공식 수집 경로에서도 회계 목적과 실제 사업 문제의 지원칸을 구분한다."""
import pytest

from features.evidence_collection.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_quote_problem,
)


@pytest.mark.parametrize("source", (
    "회사가 채택한 제ㆍ개정 기준서는 부채의 유동ㆍ비유동 분류에 관한 것이다. "
    "보고기간 이후 상환될 수 있는 위험의 정보를 공시해야 한다.",
    "파생상품은 공정가치로 측정한다. 현금흐름변동위험을 감소시키기 위한 경우 "
    "현금흐름위험회피회계를 적용하고 있다.",
    "당사는 과거 대손경험률로 산정한 대손추정액을 근거로 대손충당금을 설정하고 있다.",
    "종속기업에 해당하지만 회계 부담 합리화 방안에 따라 연결하지 아니하고 지분법을 적용하였다.",
))
def test_측정과_분류_목적은_현재사업문제지원이_아니다(source):
    assert not challenge_eligibility_scope(source).score_text.strip()
    assert challenge_eligibility_quote_problem(source, source, "current_challenges:issue")


@pytest.mark.parametrize("actual", (
    "제품 수요가 감소하여 생산라인을 축소하고 있다.",
    "생산설비의 리스료가 15% 상승했다.",
    "고객의 대출 연체율이 상승하여 채무조정 서비스를 확대하고 있다.",
    "계약 분쟁이 발생하여 제품 납품 소송이 진행 중이다.",
    "환경규제에 맞춰 제품의 냉매 기술을 개발하고 있다.",
    "고객에게 통화선도 파생상품 서비스를 제공하고 있다.",
))
def test_혼합_실제사업관계는_보존하고_회계만_자른인용은_제한한다(actual):
    policy = "현금흐름위험을 감소시키기 위한 경우 현금흐름위험회피회계를 적용하며"
    source = policy + " " + actual
    assert actual in challenge_eligibility_scope(source).score_text
    assert not challenge_eligibility_quote_problem(actual, source, "current_challenges:response")
    assert challenge_eligibility_quote_problem(policy, source, "current_challenges:issue")


@pytest.mark.parametrize("business", (
    "은행은 고객에게 파생상품 계약을 제공하고 수수료를 받는다.",
    "은행은 고객의 환헤지 주문을 받아 파생상품 계약을 체결하고 수수료를 받는다.",
    "은행은 수출기업에 파생상품 계약을 체결하고 수수료를 받는다.",
))
def test_고객제공_계약대가_절은_뒤의_회계문맥에_의해_제외되지않는다(business):
    policy = "위험회피회계에서는 공정가치로 측정한다."
    source = business + " " + policy
    assert business in challenge_eligibility_scope(source).score_text
    assert not challenge_eligibility_quote_problem(business, source, "current_challenges:response")
    assert challenge_eligibility_quote_problem(policy, source, "current_challenges:response")
