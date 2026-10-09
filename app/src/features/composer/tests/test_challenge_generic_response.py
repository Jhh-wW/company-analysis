"""일반적인 의지와 실제 사업 활동을 구분하는 반례."""

import json

import pytest

from src.features.composer import verify
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_generic_response import generic_response_problem


@pytest.mark.parametrize("candidate", [
    "회사는 지속 가능한 성장을 위해 노력하고 있다.",
    "당사는 기업가치 제고를 위해 최선을 다하고 있다고 밝혔다.",
    "이러한 거시경제 및 경영환경의 어려움에 대응하여 회사는 지속 가능한 성장을 위해 노력해왔다고 밝히고 있다.",
    "시장환경 변화에 대응하여 당사는 경쟁력 강화를 위해 노력 중이다.",
])
def test_generic_effort_does_not_fill_business_response(candidate):
    source = candidate + " 회사는 고객 문의 처리 서버를 증설했다."
    assert generic_response_problem(candidate) == "challenge_business_relation_unbound"
    assert challenge_business_problem(candidate, {"a": source}, claim_slot="current_challenges:response")
    assert challenge_business_problem("서비스 중단", {"a": source}, cells=("서비스 중단", candidate))
    assert not challenge_business_problem(candidate, {"a": source}, require_current=False)


@pytest.mark.parametrize("candidate", [
    "회사는 지속 가능한 성장을 위해 고객 지원센터를 개설했다.",
    "회사는 고객 문의 처리 서버를 증설하기 위해 노력하고 있다.",
    "회사는 지속 가능한 성장을 위해 노력하고 있다. 이를 위해 생산라인을 증설했다.",
    "회사는 경쟁력 강화를 위해 노력하며 불량 검사를 자동화했다.",
    "회사는 경영환경 악화에 대응하여 유료 콘텐츠 서비스를 출시했다.",
    "회사는 지속 가능한 성장을 위해 신규 고객과 공급 계약을 체결했다.",
    "회사는 전력 소비 절감을 위해 설비 교체를 진행하고 있다.",
    "지속가능성 자문 서비스는 고객의 환경 보고서를 검토한다.",
])
def test_concrete_or_mixed_activity_remains_for_semantic_review(candidate):
    assert not generic_response_problem(candidate)


def test_true_verdict_cannot_borrow_activity_from_another_source_clause():
    text = "회사는 지속 가능한 성장을 위해 노력하고 있다."
    source = text + " 회사는 생산 설비를 교체했다."
    raw = json.dumps([{"번호": 1, "결과": "참", "근거": ["a"]}], ensure_ascii=False)
    problems = {}
    result = verify._apply_grounding(
        raw, {1: "참"}, {1: (text, {"a": source})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: "current_challenges:response"}, grounding_problems=problems,
    )
    assert result[1] != "참"
    assert problems[1] == "challenge_business_relation_unbound"
