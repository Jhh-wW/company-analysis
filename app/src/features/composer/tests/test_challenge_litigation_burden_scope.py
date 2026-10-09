"""소송 진행과 추정부담금 평가만인 후보의 사업 과제 범위를 확인한다."""
import hashlib
import json

import pytest

from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer import verify as v


REASON = "challenge_business_relation_unbound"
SLOT = "current_challenges:issue"
PROCEDURE = (
    "연결회사는 당기말 현재 임금 추가지급청구소송(2024년 3월 2일 1심 판결) 및 "
    "휴일수당 추가지급청구소송(2024년 9월 5일 1심 판결)이 각각 2심 항소심 진행 중이며, "
    "최종 부담금액은 연결회사가 추정한 금액과 달라질 수 있다."
)


@pytest.mark.parametrize("candidate", [
    PROCEDURE,
    "당사는 현재 임금청구소송이 항소심 진행 중이며 최종 부담금은 당사가 추정한 금액과 다를 수 있다.",
    "임금청구소송(2024-03-02 1심 판결)은 지방법원에서 항소심 계류 중이다. 최종 부담액은 추정한 금액과 달라질 수 있다.",
    "회사는 보고기간말 현재 임금청구소송(2024.3.2 1심 선고)이 2심 진행 중이다.",
    "통상임금청구소송과 수당청구소송이 각각 항소심 심리 중이다.",
])
def test_procedure_and_estimated_burden_do_not_establish_business_issue(candidate):
    source = candidate + " 별개 특허 사건으로 제품 판매가 중단됐다."
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    assert challenge_business_problem(candidate, {"a": source}, claim_slot=SLOT) == REASON
    assert challenge_business_problem(candidate, {"a": source}, cells=(candidate, "회사는 대체 설비를 확보했다.")) == REASON
    assert hashlib.sha256(source.encode()).hexdigest() == source_hash


@pytest.mark.parametrize("candidate", [
    "제품 결함 관련 손해배상청구소송은 지방법원에서 항소심 진행 중이다.",
    "제조물 책임 소송이 항소심 진행 중이며 최종 부담금액은 추정한 금액과 달라질 수 있다.",
    "특허침해소송이 항소심 진행 중이다.",
    "납품권 확인소송이 항소심 계류 중이다.",
    "고객 피해 관련 손해배상청구소송이 항소심 진행 중이다.",
    "대출금 반환청구소송이 항소심 진행 중이다.",
    "보험금 지급청구소송이 항소심 진행 중이다.",
    "법률서비스 고객의 소송 수행이 지연되어 회사는 대리 인력을 추가 배치했다.",
    "임금청구소송이 항소심 진행 중이며 이로 인해 생산이 중단됐다.",
    "임금청구소송이 항소심 진행 중이며 회사는 해당 사건의 증거를 제출했다.",
    "임금청구소송이 항소심 진행 중이며 회사는 근로자와 합의하여 수당을 지급했다.",
    "임금청구소송(공장 생산이 중단됨)이 항소심 진행 중이다.",
    PROCEDURE + " 같은 임금 사건으로 생산라인이 중단되어 회사는 대체 인력을 투입했다.",
    "은행은 고객의 연체 채권 회수가 지연되어 소송을 진행하고 있다.",
    "회사는 대체 설비를 확보했으며 임금청구소송이 항소심 진행 중이다.",
    "회사는 타이어를 개발하고 임금청구소송이 항소심 진행 중이다.",
])
def test_same_event_business_effect_and_actual_response_remain(candidate):
    assert not challenge_business_problem(candidate, {"a": candidate}, claim_slot=SLOT)


@pytest.mark.parametrize("slot", ["current_challenges:response", ""])
def test_only_issue_slot_is_restricted(slot):
    assert not challenge_business_problem(PROCEDURE, {"a": PROCEDURE}, claim_slot=slot)
    assert not challenge_business_problem(PROCEDURE, {"a": PROCEDURE}, claim_slot=SLOT, require_current=False)


@pytest.mark.parametrize("candidate", [
    "최종 부담금액은 회사가 추정한 금액과 달라질 수 있다.",
    "임금청구소송이 항소심 진행 중이며 최종 부담금을 지급했다.",
    "임금청구소송이 항소심 진행 중이며 새로운 청구 내용을 확인하고 있다.",
])
def test_unknown_or_actual_predicates_are_left_to_meaning_review(candidate):
    assert not challenge_business_problem(candidate, {"a": candidate}, claim_slot=SLOT)


def test_model_true_cannot_borrow_other_case_business_effect():
    sources = {
        "a": PROCEDURE,
        "b": "별개 특허 사건으로 제품 판매가 중단됐다.",
    }
    # 다른 사건의 자기 원문을 추가해도 선택한 절차 문장에 사업 영향을 이식하지 않는다.
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["a", "b"]}]}, ensure_ascii=False)
    problems = {}
    result = v._apply_grounding(
        raw, {1: "참"}, {1: (PROCEDURE, sources)},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: SLOT}, grounding_problems=problems,
    )
    assert result[1] != "참"
    assert problems[1] == REASON


@pytest.mark.parametrize("actor", [
    "제조법인㈜", "제조법인(주)", "제조법인주식회사",
    "㈜제조법인", "(주)제조법인", "주식회사제조법인",
])
def test_explicit_legal_entity_spelling_does_not_change_procedure_scope(actor):
    procedure = f"{actor}는 현재 성과급청구소송이 항소심 진행 중이다."
    assert challenge_business_problem(procedure, {"a": procedure}, claim_slot=SLOT) == REASON
    assessment = f"{actor}는 현재 성과급청구소송이 항소심 진행 중이며 최종 부담금은 {actor}가 추정한 금액과 다를 수 있다."
    assert challenge_business_problem(assessment, {"a": assessment}, claim_slot=SLOT) == REASON
    business = f"{actor}는 현재 특허침해소송이 항소심 진행 중이다."
    assert not challenge_business_problem(business, {"a": business}, claim_slot=SLOT)
    response = f"{actor}는 현재 성과급청구소송이 항소심 진행 중이며 해당 사건의 증거를 제출했다."
    assert not challenge_business_problem(response, {"a": response}, claim_slot=SLOT)
