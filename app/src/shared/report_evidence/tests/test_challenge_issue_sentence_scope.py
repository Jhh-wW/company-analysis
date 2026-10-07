"""5장 문제 문장 자체의 일반 설명 경계를 검증한다."""
import pytest

from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_quote_problem,
    challenge_issue_problem,
)
from src.shared.report_evidence.business_slot_scope import business_slot_scope

ISSUE = "current_challenges:issue"
RESPONSE = "current_challenges:response"
ORDINARY = (
    "회사는 분석정보를 고객에게 제공하고 있다.",
    "회사는 고객 서비스를 여러 플랫폼을 통해 제공하며, 이를 지원하는 하드웨어와 소프트웨어 인프라를 유지·개발하고 있다.",
    "유통산업은 기술 발전에 따라 빠르게 변화하고 있으며, 채널 간의 경계가 사라지는 융합 시대에 진입했다.",
)
ACTUAL = (
    "회사는 고객 서비스를 제공하고 있으나, 결제 장애로 고객 피해가 발생했다.",
    "유통산업은 빠르게 변화하고 있으며 회사의 고객 이탈이 증가했다.",
    "회사는 분석정보를 제공하고 있으며 생산원가 부담이 커졌다.",
    "회사는 보험 서비스를 제공하고 있으며 고객의 보험금 지급 지연이 발생했다.",
    "회사는 대출 서비스를 제공하고 있으며 고객 연체율이 상승했다.",
    "규제로 냉매 제품의 판매가 중단되었다.",
    "공급처 이탈로 조달비용이 증가했다.",
    "회사는 핵심 시장의 수요 둔화를 겪고 있다.",
    "고객의 실제 발생한 소송이 진행 중이며 법률 서비스를 제공하고 있다.",
)

@pytest.mark.parametrize("text", ORDINARY)
def test_일반설명은_issue만제한하고_대응및원문은보존한다(text):
    before = text.encode()
    assert challenge_issue_problem(text) == "challenge_business_relation_unbound"
    assert not business_slot_scope(text, ISSUE).score_text
    assert business_slot_scope(text, RESPONSE).score_text == text
    assert challenge_eligibility_quote_problem(text, text, ISSUE)
    assert not challenge_eligibility_quote_problem(text, text, RESPONSE)
    assert text.encode() == before

@pytest.mark.parametrize("text", ACTUAL)
def test_실제문제및혼합문장은기존의미검수에남긴다(text):
    assert not challenge_issue_problem(text)
    assert business_slot_scope(text, ISSUE).score_text == text

def test_혼합원문의일반절만제한하고실제절좌표를유지한다():
    ordinary, actual = ORDINARY[0], ACTUAL[-1]
    source = ordinary + " " + actual
    scope = challenge_eligibility_scope(source, ISSUE)
    assert actual in scope.score_text and ordinary not in scope.score_text
    assert all(source[start:end] == ordinary[:-1] for start,end,_ in scope.excluded_spans)
    assert challenge_eligibility_quote_problem(ordinary, source, ISSUE)
    assert not challenge_eligibility_quote_problem(actual, source, ISSUE)

def test_문제어휘가없는임의문장을일괄금지하지않는다():
    text = "회사는 새 거래 조건에서 기존 계약을 잃었다."
    assert not challenge_issue_problem(text)
