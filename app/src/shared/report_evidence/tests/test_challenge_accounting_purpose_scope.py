"""회계정책의 목적과 실제 사업 문제를 같은 원문의 자기 인용으로 구분한다."""
import hashlib

import pytest

from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_quote_problem,
)

POLICIES = (
    "회사가 채택한 제ㆍ개정 기준서는 부채의 유동ㆍ비유동 분류에 관한 것이다. "
    "보고기간 이후 상환될 수 있는 위험의 정보를 공시해야 한다.",
    "파생상품은 공정가치로 측정한다. 현금흐름변동위험을 감소시키기 위한 경우 "
    "현금흐름위험회피회계를 적용하고 있다.",
    "당사는 과거 대손경험률로 산정한 대손추정액을 근거로 대손충당금을 설정하고 있다.",
    "종속기업에 해당하지만 회계 부담 합리화 방안에 따라 연결하지 아니하고 지분법을 적용하였다.",
)
SLOTS = ("current_challenges:issue", "current_challenges:response")


@pytest.mark.parametrize("source", POLICIES)
def test_회계분류_측정_목적만으로_직접사업과제지원칸을_채우지않는다(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    scope = challenge_eligibility_scope(source)
    assert not scope.score_text.strip()
    assert scope.excluded_spans
    for slot in SLOTS:
        assert challenge_eligibility_quote_problem(source, source, slot)
    assert hashlib.sha256(source.encode()).hexdigest() == digest


@pytest.mark.parametrize("purpose", ("감소시키기 위한", "감소시키기 위하여", "감소시키기 위해"))
def test_목적의_감소는_실제문제_변화예외로_쓰지않는다(purpose):
    source = f"현금흐름위험을 {purpose} 현금흐름위험회피회계를 적용하고 공정가치로 측정한다."
    assert not challenge_eligibility_scope(source).score_text.strip()


@pytest.mark.parametrize("actual", (
    "제품 수요가 감소하여 생산라인을 축소하고 있다.",
    "생산설비의 리스료가 15% 상승했다.",
    "고객의 대출 연체율이 상승하여 채무조정 서비스를 확대하고 있다.",
    "계약 분쟁이 발생하여 제품 납품 소송이 진행 중이다.",
    "환경규제에 맞춰 제품의 냉매 기술을 개발하고 있다.",
    "고객에게 통화선도 파생상품 서비스를 제공하고 있다.",
))
def test_같은절의_실제문제와_고객본업은_남기고_회계부분은_빌리지않는다(actual):
    policy = "현금흐름위험을 감소시키기 위한 경우 현금흐름위험회피회계를 적용하며"
    source = policy + " " + actual
    assert actual in challenge_eligibility_scope(source).score_text
    assert not challenge_eligibility_quote_problem(actual, source, "current_challenges:response")
    for slot in SLOTS:
        assert challenge_eligibility_quote_problem(policy, source, slot)


def test_실제소송표와_금액을_회계정책_문맥때문에_버리지않는다():
    source = POLICIES[1] + "; 계약분쟁 | 공사대금 청구 | 1심 진행 중 | 600,000"
    quote = "계약분쟁 | 공사대금 청구 | 1심 진행 중 | 600,000"
    assert quote in challenge_eligibility_scope(source).score_text
    assert not challenge_eligibility_quote_problem(quote, source, "current_challenges:issue")


@pytest.mark.parametrize("business", (
    "은행은 고객에게 파생상품 계약을 제공하고 수수료를 받는다.",
    "은행은 고객의 환헤지 주문을 받아 파생상품 계약을 체결하고 수수료를 받는다.",
    "은행은 수출기업에 파생상품 계약을 체결하고 수수료를 받는다.",
))
def test_고객계약과_대가의_본업절이_다른_회계절_문맥을_빌려_제외되지않는다(business):
    policy = "위험회피회계에서는 공정가치로 측정한다."
    source = business + " " + policy
    assert business in challenge_eligibility_scope(source).score_text
    assert not challenge_eligibility_quote_problem(business, source, "current_challenges:response")
    assert challenge_eligibility_quote_problem(policy, source, "current_challenges:response")


def test_고객계약서비스가_아닌_내부헤지목적절은_여전히_측정문맥으로_제한한다():
    purpose = "당사는 매매목적 또는 특정위험을 회피하기 위하여 파생상품계약을 체결하고 있다."
    source = purpose + " 위험회피회계에서는 공정가치로 측정한다."
    assert not challenge_eligibility_scope(source).score_text.strip()
    assert challenge_eligibility_quote_problem(purpose, source, "current_challenges:response")
