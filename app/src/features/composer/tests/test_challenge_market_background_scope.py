"""시장 배경 경쟁 설명의 직접 과제 승격과 같은 배경 실제 피해 보존을 확인한다."""
import json

import pytest

from src.features.composer import verify as v
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_industry_scope import industry_only_challenge_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.logic import summary_candidates
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence

REASON = "challenge_business_relation_unbound"
OBSERVATION = (
    "온라인 서비스 시장에서 선두 매체가 독주하는 가운데 다른 매체의 강세가 지속되고 있으며, "
    "동영상 매체의 성장이 계속될 것으로 예상된다."
)
DAMAGE = "회사는 광고주 이탈로 납품이 중단되어 매출에 손실이 발생했다."
RESPONSE = "회사는 관련 인프라를 자체 구축하여 광고 서비스를 제공하고 있다."


@pytest.mark.parametrize("text", [
    OBSERVATION,
    "국내 서비스 시장에서 선두 매체가 독주하고 있다.",
    "서비스 시장에서는 다른 매체의 강세가 지속되고 있다.",
    "국내 서비스 시장에서 선두 업체의 점유율이 높다.",
    "서비스 산업 내에서는 동영상 매체의 성장이 지속될 것으로 전망된다.",
    "국내 영상 시장에서 선도 플랫폼이 독주하고 다른 매체가 강세를 보인다.",
    "서비스 시장에서는 대형 기업들의 점유율이 높아지고 동영상 매체가 성장할 것으로 예상된다.",
    "국내 광고 시장에서 선도 업체가 강세를 보이며, 향후 동영상 매체가 성장할 전망이다.",
])
@pytest.mark.parametrize("slot", ["current_challenges:issue", "current_challenges:initial_signal", ""])
def test_closed_market_observation_is_not_direct_company_issue(text, slot):
    sources = {"a": text + " " + DAMAGE + " " + RESPONSE}
    assert challenge_business_problem(text, sources, claim_slot=slot) == REASON
    assert sources["a"] == text + " " + DAMAGE + " " + RESPONSE


@pytest.mark.parametrize("text", [
    "국내 반도체 시장에서 합성기술은 고객 이탈로 납품 중단을 겪고 있다.",
    "국내 반도체 시장에서 당사에 대한 수요가 감소해 납품이 중단됐다.",
    "국내 반도체 시장에서는 당사에 대한 수요가 감소해 납품이 중단됐다.",
    "광고 시장에서 회사는 고객 이탈로 납품 중단을 겪고 있다.",
    OBSERVATION + " " + DAMAGE,
    "서비스 시장에서 선두 매체가 독주하고 있으며 회사는 인력 부족으로 납품이 중단됐다.",
    "서비스 시장에서 회사는 고객에게 유료 서비스를 제공하고 대가를 받는다.",
    "서비스 시장에서 회사는 점유율이 높지만 원자재 부족으로 납품을 중단했다.",
    "국내 서비스 시장에서는 당사 고객의 계약 취소가 발생해 매출이 감소했다.",
])
def test_market_background_does_not_delete_company_activity_or_damage(text):
    assert not industry_only_challenge_problem(text, claim_slot="current_challenges:issue")


def test_other_sentence_and_response_cell_cannot_lend_direct_problem():
    mixed = OBSERVATION + " " + RESPONSE
    assert challenge_business_problem(mixed, {"a": mixed}, claim_slot="current_challenges:issue") == REASON
    assert challenge_business_problem(mixed, {"a": mixed}, cells=(OBSERVATION, RESPONSE)) == REASON


@pytest.mark.parametrize("slot", ["current_challenges:response", "industry_context:interpretation"])
def test_response_and_industry_interpretation_are_outside_direct_issue_rule(slot):
    assert not industry_only_challenge_problem(OBSERVATION, claim_slot=slot)


@pytest.mark.parametrize("slot", ["current_challenges:issue", "current_challenges:initial_signal", ""])
def test_model_true_does_not_publish_or_summarize_closed_market_observation(slot):
    raw = json.dumps([{"번호": 1, "장": "current_challenges", "결과": "참", "근거": ["a"]}], ensure_ascii=False)
    problems = {}
    result = v._apply_grounding(raw, {1: "참"}, {1: (OBSERVATION, {"a": OBSERVATION + " " + DAMAGE})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: slot}, grounding_problems=problems)
    assert result[1] != "참" and problems[1] == REASON
    sentence = ComposedSentence(OBSERVATION, ("a",), "확인", verification_state="verified", planned_claim_slot=slot)
    report = ComposedReport((ComposedSection("current_challenges", (sentence,) if result[1] == "참" else ()),))
    assert not has_verified_direct_business_issue(report)
    assert not summary_candidates(report)
