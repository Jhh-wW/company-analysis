"""금융관리 열거와 비용 비교를 실제 사업 과제로 승격하지 않는다."""
import json

import pytest

from src.features.composer import verify as v
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.logic import summary_candidates
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence

FINANCE = (
    "회사는 유동성 위험을 충분한 요구불예금 보유, 적립금과 차입한도 유지, "
    "예측현금흐름과 실제현금흐름 관찰, 금융자산과 금융부채의 만기구조 대응으로 관리한다고 밝혔다."
)
EXPENSE = "연구개발비는 전기 대비 감소했으며 기술 역량 투자 규모의 변화이나 사업상 제약의 원인은 확인하기 어렵다."
EXPENSE_TABLE = "연구개발비 100,000 200,000 광고선전비 30,000 40,000"
BUSINESS = "회사는 원자재 부족으로 납품이 중단됐다."


@pytest.mark.parametrize("slot", ["current_challenges:response", "current_challenges:unresolved_gap", ""])
def test_financial_candidate_cannot_borrow_business_fact_from_mixed_source(slot):
    source = FINANCE + " " + BUSINESS + " 자본금 100,000 100,000"
    assert challenge_business_problem(FINANCE, {"a": source}, claim_slot=slot) == "accounting_policy_boilerplate"


@pytest.mark.parametrize("candidate", [
    "회사는 유동성 부족으로 생산을 중단했고, 대체 자금을 확보했다.",
    "은행은 고객에게 유동성 관리 서비스를 제공하고, 수수료를 받는다.",
    "회사는 유동성 위험을 관리하며, 고객의 연체 채권 회수가 지연됐다.",
    FINANCE + " " + BUSINESS,
])
def test_real_pressure_business_service_and_mixed_claim_are_preserved(candidate):
    assert not challenge_business_problem(candidate, {"a": candidate}, claim_slot="current_challenges:response")


@pytest.mark.parametrize("slot", ["current_challenges:unresolved_gap", "current_challenges:issue", "current_challenges:response", ""])
def test_expense_pair_alone_is_not_business_constraint(slot):
    assert challenge_business_problem(EXPENSE, {"a": EXPENSE_TABLE}, claim_slot=slot) == "challenge_business_relation_unbound"


@pytest.mark.parametrize("support", [
    "회사는 연구개발 활동을 중단했다.",
    "당사는 연구개발 인력을 축소했다.",
    "연구개발 과제가 취소됐다.",
    "회사는 연구개발 계획을 취소했다.",
])
def test_same_research_activity_event_is_preserved(support):
    assert not challenge_business_problem(EXPENSE, {"a": EXPENSE_TABLE + ". " + support},
                                          claim_slot="current_challenges:unresolved_gap")


@pytest.mark.parametrize("borrowed", [
    "다른회사는 연구개발 활동을 중단했다.",
    "비교법인은 연구개발 활동을 중단했다.",
    "고객사의 연구개발 활동이 중단됐다.",
    "비교법인의 연구개발 활동이 중단됐다.",
    "당사는 수주가 감소하는 경우 연구개발 활동을 중단했다고 가정한다.",
    "당사는 향후 연구개발 활동을 중단할 계획이다.",
    "회사는 납품 활동을 중단했다.",
    EXPENSE_TABLE + "이며 회사는 공사 수행을 중단했다.",
])
def test_other_actor_condition_plan_and_other_activity_do_not_support_proxy(borrowed):
    assert challenge_business_problem(EXPENSE, {"a": EXPENSE_TABLE + ". " + borrowed},
                                      claim_slot="current_challenges:unresolved_gap") == "challenge_business_relation_unbound"


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("candidate,source,slot,reason", [
    (FINANCE, FINANCE + " " + BUSINESS, "current_challenges:response", "accounting_policy_boilerplate"),
    (EXPENSE, EXPENSE_TABLE, "current_challenges:unresolved_gap", "challenge_business_relation_unbound"),
])
def test_model_true_is_rejected_before_public_fact_and_summary(candidate, source, slot, reason, wrapped):
    row = {"번호": 1, "근거": ["a"], "결과": "참"}
    raw = json.dumps({"판정": [row]} if wrapped else [row], ensure_ascii=False)
    problems = {}
    verdict = v._apply_grounding(raw, {1: "참"}, {1: (candidate, {"a": source})},
                                diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
                                claim_slots_by_number={1: slot}, grounding_problems=problems)
    assert verdict[1] != "참" and problems[1] == reason
    original = ComposedSentence(candidate, ("a",), "확인", verification_state="verified", planned_claim_slot=slot)
    survivors = (original,) if verdict[1] == "참" else ()
    final = ComposedReport((ComposedSection("current_challenges", survivors),))
    assert not summary_candidates(final)
    assert not has_verified_direct_business_issue(final)
    assert original.text == candidate


def test_expense_source_business_event_with_claim_is_preserved():
    candidate = "연구개발비는 감소했고 회사는 연구개발 활동을 중단했다."
    source = EXPENSE_TABLE + ". 회사는 연구개발 활동을 중단했다."
    assert not challenge_business_problem(candidate, {"a": source}, claim_slot="current_challenges:issue")
