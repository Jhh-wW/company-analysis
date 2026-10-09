"""소송 절차 자체와 사업 문제 관계를 구분하는 최종 슬롯 경계."""
import json

import pytest

from src.features.composer import verify as v
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow
from src.features.composer.logic import summary_candidates

PROCEDURE = "처분취소 사건은 대법원에서 3심 진행 중이다."
BUSINESS_PROBLEM = "고객의 공사대금 미지급으로 공사가 중단되어 회사에 손실이 발생했다."
REASON = "challenge_business_relation_unbound"


@pytest.mark.parametrize("candidate", [
    PROCEDURE,
    "처분취소 사건은 대법원에서 3심 진행 중으로, 회사가 교육기관을 상대로 소송을 진행하고 있다.",
    "채무부존재확인 및 공사대금반환청구 사건도 각각 서울중앙지방법원과 서울남부지방법원에서 1심 진행 중이다.",
    "회사는 계약취소 소송을 진행하고 있다.",
    "손해배상청구",
])
def test_pure_procedure_is_not_a_direct_business_issue(candidate):
    sources = {"a": candidate + " " + BUSINESS_PROBLEM}
    assert challenge_business_problem(candidate, sources, claim_slot="current_challenges:issue") == REASON
    assert challenge_business_problem(candidate, sources, cells=(candidate, BUSINESS_PROBLEM)) == REASON
    assert sources["a"] == candidate + " " + BUSINESS_PROBLEM


@pytest.mark.parametrize("candidate", [
    BUSINESS_PROBLEM,
    "회사는 납품 중단 피해로 계약이행 청구 소송을 진행하고 있다.",
    "고객이 공사대금을 미지급하여 회사가 공사대금 청구 소송을 진행하고 있다.",
    "특허 침해로 제품 판매가 금지되어 회사는 소송을 진행하고 있다.",
    "법률서비스 회사는 고객의 소송을 대리하며 지급 지연 문제에 대응하고 있다.",
    "은행은 고객의 연체 채권 회수 문제로 소송을 진행하고 있다.",
    PROCEDURE + " " + BUSINESS_PROBLEM,
    "처분취소 사건은 대법원에서 3심 진행 중이며 이로 인해 공사 계약이 중단됐다.",
])
def test_business_effect_and_core_customer_service_remain(candidate):
    assert not challenge_business_problem(candidate, {"a": candidate}, claim_slot="current_challenges:issue")


@pytest.mark.parametrize("slot", ["current_challenges:response", ""])
def test_procedure_facts_are_preserved_outside_issue(slot):
    assert not challenge_business_problem(PROCEDURE, {"a": PROCEDURE}, claim_slot=slot)
    assert not challenge_business_problem(PROCEDURE, {"a": PROCEDURE}, claim_slot="current_challenges:issue", require_current=False)


@pytest.mark.parametrize("wrapped", [False, True])
def test_model_true_cannot_borrow_business_effect_from_another_clause(wrapped):
    entries = [{"번호": 1, "결과": "참", "근거": ["a"]}]
    raw = json.dumps({"판정": entries} if wrapped else entries, ensure_ascii=False)
    problems = {}
    result = v._apply_grounding(
        raw, {1: "참"}, {1: (PROCEDURE, {"a": PROCEDURE + " " + BUSINESS_PROBLEM})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: "current_challenges:issue"}, grounding_problems=problems,
    )
    assert result[1] != "참" and problems[1] == REASON
    # 최종 생존 문장이 없으면 표지 후보와 산업 대체를 막는 가짜 직접 issue도 없다.
    before = ComposedSentence(PROCEDURE, ("a",), "확인", verification_state="verified",
                              planned_claim_slot="current_challenges:issue")
    survivors = (before,) if result[1] == "참" else ()
    final = ComposedReport((ComposedSection("current_challenges", survivors),))
    assert not has_verified_direct_business_issue(final)
    assert not summary_candidates(final)
    good = ComposedReport((ComposedSection("current_challenges", (
        ComposedSentence(BUSINESS_PROBLEM, ("a",), "확인", verification_state="verified",
                         planned_claim_slot="current_challenges:issue"),
    )),))
    assert has_verified_direct_business_issue(good)


def test_final_table_cannot_borrow_business_effect_from_response_cell(monkeypatch):
    monkeypatch.setattr(v, "_semantic_review", lambda groups, *args, **kwargs: groups)
    row = FlowRow(("손해배상청구", BUSINESS_PROBLEM), ("a",))
    report = ComposedReport((ComposedSection("current_challenges", (), flow_rows=(row,)),))
    source = "손해배상청구 사건은 지방법원에서 1심 진행 중이다. " + BUSINESS_PROBLEM
    final = v.verify_report(report, (CollectedFragment("a", "공식 자료", source),),
                            None, lambda *args: "")
    assert not final.sections[0].flow_rows
    assert report.sections[0].flow_rows == (row,)
