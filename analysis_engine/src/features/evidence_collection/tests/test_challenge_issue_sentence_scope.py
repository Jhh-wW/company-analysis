"""5장 문제 문장 자체의 일반 설명 경계를 검증한다."""
import pytest

from features.evidence_collection.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_quote_problem,
    challenge_issue_problem,
)
from features.evidence_collection.business_slot_scope import business_slot_scope
from features.evidence_collection.relevance import score_fragment_slots_with_signal

ISSUE = "current_challenges:issue"
RESPONSE = "current_challenges:response"
ORDINARY = (
    "회사는 분석정보를 고객에게 제공하고 있다.",
    "회사는 고객 서비스를 여러 플랫폼을 통해 제공하며, 이를 지원하는 하드웨어와 소프트웨어 인프라를 유지·개발하고 있다.",
    "유통산업은 기술 발전에 따라 빠르게 변화하고 있으며, 채널 간의 경계가 사라지는 융합 시대에 진입했다.",
    "회사의 개발부는 하드웨어 유지보수, 소프트웨어 개발, 서버와 스토리지 관리 등 기술 인프라 전반을 담당하고 있다.",
    "연구개발 조직은 회사의 인프라와 소프트웨어 개발 업무 등을 담당하고 있습니다.",
    "인프라 주요업무: 하드웨어 유지보수, 데이터베이스 관리, 온라인 기사엔진 개발 및 관리",
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
    "개발부는 서버 유지보수를 담당하고 있으며 서비스 장애로 고객 피해가 발생했다.",
    "개발부는 규제로 판매가 중단된 냉매 제품의 소프트웨어 개발을 담당하고 있다.",
    "개발부는 고객 결제 지연이 해결되지 않아 인프라 전환이 필요한 업무를 담당하고 있다.",
    "개발부는 접근권한을 잃어 납품에 차질이 생긴 설비의 유지보수를 담당하고 있다.",
    "기존 장비가 노후화되어 개발부는 서버 유지보수를 담당하고 있다.",
    "인력 충원이 되지 않아 개발부는 서버 유지보수와 소프트웨어 개발을 함께 담당하고 있다.",
    "접속이 자주 끊겨 개발부는 서버와 소프트웨어 유지보수를 담당하고 있다.",
    "개발부는 기존 장비가 노후화되어 서버 유지보수를 담당하고 있다.",
    "개발부는 인력 충원이 되지 않아 서버 유지보수를 담당하고 있다.",
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

def test_일반설명제외관측이약신호AI재유입을막는다():
    scores, observed = score_fragment_slots_with_signal(ORDINARY[0], allowed_slot_ids=frozenset({ISSUE}))
    assert not scores and observed

@pytest.mark.parametrize("text", ORDINARY[3:])
def test_업무목록제외관측이issue재유입을막는다(text):
    scores, observed = score_fragment_slots_with_signal(text, allowed_slot_ids=frozenset({ISSUE}))
    assert not scores and observed

def test_업무목록과실제문제의정확절을구분한다():
    ordinary, actual = ORDINARY[3], ACTUAL[9]
    source = ordinary + " " + actual
    scope = challenge_eligibility_scope(source, ISSUE)
    assert actual in scope.score_text and ordinary not in scope.score_text
    assert challenge_eligibility_quote_problem(ordinary, source, ISSUE)
    assert not challenge_eligibility_quote_problem(actual, source, ISSUE)
    assert business_slot_scope(source, RESPONSE).score_text == source

def test_AI가고른업무목록부분은같은원문의다른문제를빌리지못한다():
    from features.evidence_collection.business_slot_scope import business_slot_quote_problem
    ordinary, actual = ORDINARY[3], ACTUAL[9]
    source = ordinary + " " + actual
    quote = "소프트웨어 개발"
    start = source.index(quote)
    assert business_slot_quote_problem(source, ISSUE, start, start + len(quote))
    start = source.index(actual)
    assert not business_slot_quote_problem(source, ISSUE, start, start + len(actual))
