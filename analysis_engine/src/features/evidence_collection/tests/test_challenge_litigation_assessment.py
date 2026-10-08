"""수집 채점에서도 소송 회계 평가와 실제 사업 대응을 분리한다."""
import pytest
from features.evidence_collection.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_quote_problem,
)

@pytest.mark.parametrize("assessment", (
    "해당 소송으로 인한 자원의 유출금액 및 시기는 불확실하다.",
    "회사의 재무상태에 중요한 영향을 미치지 않을 것으로 판단한다.",
    "회사는 소송과 관련하여 충당부채로 인식한 금액이 없다.",
))
def test_소송측정평가는_현재사업대응_지원이_아니다(assessment):
    assert not challenge_eligibility_scope(assessment, "current_challenges:response").score_text.strip()
    assert challenge_eligibility_quote_problem(assessment, assessment, "current_challenges:response")

@pytest.mark.parametrize("actual", (
    "제품 결함 소송으로 고객 납품이 중단되어 대체 공급을 진행하고 있다.",
    "법률회사는 고객에게 소송 자문 서비스를 제공하고 있다.",
    "은행은 고객의 결제 장애를 복구하여 금융 서비스를 제공하고 있다.",
    "회사는 제품 피해 고객에게 배상금을 지급하고 재발방지 설비를 설치하고 있다.",
))
def test_측정평가와_섞여도_실제사업피해와_고객본업을_보존한다(actual):
    assessment = "해당 소송으로 인한 자원의 유출금액 및 시기는 불확실하다."
    source = assessment + " " + actual
    assert actual in challenge_eligibility_scope(source, "current_challenges:response").score_text
    assert not challenge_eligibility_quote_problem(actual, source, "current_challenges:response")
    assert challenge_eligibility_quote_problem(assessment, source, "current_challenges:response")

def test_같은절의_실제배상행동과_측정부분인용을_구별한다():
    text = "회사는 고객에게 배상금을 지급했다며 소송과 관련한 충당부채로 인식한 금액은 없다고 설명했다."
    assert challenge_eligibility_scope(text, "current_challenges:response").score_text == text
    assert challenge_eligibility_quote_problem("충당부채로 인식한 금액은 없다.", text, "current_challenges:response")
