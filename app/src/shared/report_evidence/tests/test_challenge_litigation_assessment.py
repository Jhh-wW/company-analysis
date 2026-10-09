"""소송 회계 평가와 실제 사업 피해·대응을 절 단위로 구분한다."""
import hashlib
import pytest
from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_quote_problem,
)

ASSESSMENTS = (
    "해당 소송으로 인한 자원의 유출금액 및 시기는 불확실하다.",
    "회사의 재무상태에 중요한 영향을 미치지 않을 것으로 판단한다.",
    "회사는 소송과 관련하여 충당부채로 인식한 금액이 없다.",
)

@pytest.mark.parametrize("text", ASSESSMENTS)
def test_측정평가를_사업대응으로_계수하지않는다(text):
    digest = hashlib.sha256(text.encode()).hexdigest()
    assert not challenge_eligibility_scope(text, "current_challenges:response").score_text.strip()
    assert challenge_eligibility_quote_problem(text, text, "current_challenges:response")
    assert hashlib.sha256(text.encode()).hexdigest() == digest

@pytest.mark.parametrize("actual", (
    "제품 결함 소송으로 고객 납품이 중단되어 대체 공급을 진행하고 있다.",
    "법률회사는 고객에게 소송 자문 서비스를 제공하고 있다.",
    "은행은 고객의 결제 장애를 복구하여 금융 서비스를 제공하고 있다.",
    "회사는 제품 피해 고객에게 배상금을 지급하고 재발방지 설비를 설치하고 있다.",
))
def test_혼합원문의_실제피해_법무금융본업_조치를_남긴다(actual):
    source = ASSESSMENTS[0] + " " + actual
    scope = challenge_eligibility_scope(source, "current_challenges:response")
    assert actual in scope.score_text
    assert not challenge_eligibility_quote_problem(actual, source, "current_challenges:response")
    assert challenge_eligibility_quote_problem(ASSESSMENTS[0], source, "current_challenges:response")

def test_표행을보존하고_뒤각주평가와_충당부채설명만_분리한다():
    source = ("구분 계류법원 원고 피고 진행상황 소송금액 제품보증청구 법원 회사 고객 1심 진행중 100 "
              "(*1) 해당 소송으로 인한 자원의 유출금액 및 시기는 불확실하다. "
              "한편, 당사가 상기 소송과 관련하여 충당부채로 인식한 금액은 없습니다.")
    scope = challenge_eligibility_scope(source, "current_challenges:response")
    assert "제품보증청구" in scope.score_text and "1심 진행중" in scope.score_text
    assert "자원의 유출금액" not in scope.score_text and "충당부채" not in scope.score_text
    assert all(source[start:end] for start, end, _ in scope.excluded_spans)

def test_같은절의_실제배상행동을_측정평가때문에_삭제하지않는다():
    text = "회사는 고객에게 배상금을 지급했다며 소송과 관련한 충당부채로 인식한 금액은 없다고 설명했다."
    assert challenge_eligibility_scope(text, "current_challenges:response").score_text == text
    assert not challenge_eligibility_quote_problem(text, text, "current_challenges:response")
    assert challenge_eligibility_quote_problem("충당부채로 인식한 금액은 없다.", text, "current_challenges:response")
