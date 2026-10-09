"""회사의 산업환경 보고와 자기 사업 피해를 구분한다."""

import json

import pytest

from src.features.composer import verify
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_industry_scope import industry_only_challenge_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.pipeline import _late_official_industry_problems
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence


@pytest.mark.parametrize("text", [
    "이러한 산업 환경 속에서 가람매체는 어려운 경기상황과 전체 광고시장에서 신문광고비가 지속적으로 하락하는 흐름을 직접 언급하고 있다.",
    "회사는 광고 시장 전반의 수익 하락을 발표했다.",
    "가람매체는 정보 산업 전반의 경쟁 심화를 설명하고 있다.",
    "가람매체는 국내 시장의 전반적인 침체를 보고하였다.",
    "회사는 업계 전반의 광고비 하락을 공시하고 있다.",
    "당사는 국내 부품 업계의 원자재 공급 부족을 공시했다.",
    "회사는 전체 광고 시장에서 지면 광고의 수요가 감소하고 있다고 밝혔다.",
])
def test_reporting_an_industry_problem_does_not_establish_company_damage(text):
    assert industry_only_challenge_problem(text, claim_slot="current_challenges:issue")
    assert challenge_business_problem(text, {"1": text}, claim_slot="current_challenges:issue")


@pytest.mark.parametrize("text", [
    "회사는 산업 침체로 자사 생산지연이 발생했다고 설명하고 있다.",
    "가람제작은 산업 침체로 고객의 수주 취소가 발생했다고 언급하고 있다.",
    "회사는 시장 변화 때문에 납품 중단을 겪고 있다고 보고하고 있다.",
    "가람제작은 시장 수요 감소로 손실을 입고 있다고 밝히고 있다.",
    "회사는 전체 광고시장의 하락 속에서 당사 제품의 공급 차질을 설명하고 있다.",
])
def test_explicit_own_business_constraint_keeps_existing_review(text):
    assert not industry_only_challenge_problem(text, claim_slot="current_challenges:issue")


@pytest.mark.parametrize("slot", ["current_challenges:response", "industry_context:interpretation"])
def test_other_slots_keep_their_existing_contract(slot):
    assert not industry_only_challenge_problem(
        "회사는 광고 시장 전반의 수익 하락을 발표했다.", claim_slot=slot,
    )


def test_model_true_cannot_close_the_official_callback_opportunity():
    text = "가람매체는 전체 광고시장에서 신문광고비가 하락하는 흐름을 언급하고 있다."
    raw = json.dumps([{"번호": 1, "장": "current_challenges", "근거": ["1"], "결과": "참"}], ensure_ascii=False)
    verdict = verify._apply_grounding(
        raw, {1: "참"}, {1: (text, {"1": text})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: "current_challenges:issue"},
    )
    assert verdict[1] != "참"
    sentence = ComposedSentence(text, ("1",), "확인", verification_state="verified",
                                planned_claim_slot="current_challenges:issue")
    report = ComposedReport((ComposedSection("current_challenges", (sentence,)),))
    assert not has_verified_direct_business_issue(report)
    calls = []
    assert not _late_official_industry_problems(
        report, callback=lambda selected: calls.append(selected) or (),
        anchors=(), problems=(), fragments=(), company_id="synthetic", diagnostics=[],
    )
    assert len(calls) == 1
