"""산업 주어 설명의 직접 과제 배치와 회사 혼합 관계 보존을 확인한다."""
import json

import pytest

from src.features.composer import verify as v
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_industry_scope import industry_only_challenge_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.logic import summary_candidates
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence

TREND = "정보산업은 기술 발전을 기반으로 빠르게 변화하고 있으며 산업 간 융합의 시기가 도래했다."
OUTLOOK = "향후 정보산업에서는 성장의 중심이 서비스 부문으로 이동할 것으로 전망된다."
MARKET = "국내 온라인 서비스 시장에서는 다른 매체가 강세를 보이며 동영상 매체가 성장할 것으로 예상된다."
BUSINESS = "당사의 광고주 감소로 납품이 중단되어 매출에 손실이 발생했다."
RESPONSE = "회사는 관련 서비스의 인프라를 자체 구축하여 광고 서비스를 제공하고 있다."
REASON = "challenge_business_relation_unbound"


@pytest.mark.parametrize("text", [TREND, OUTLOOK, MARKET])
@pytest.mark.parametrize("slot", ["current_challenges:issue", "current_challenges:initial_signal", ""])
def test_industry_subject_cannot_become_direct_issue_or_unknown(text, slot):
    sources = {"a": text + " " + BUSINESS + " " + RESPONSE}
    assert challenge_business_problem(text, sources, claim_slot=slot) == REASON
    assert sources["a"] == text + " " + BUSINESS + " " + RESPONSE


@pytest.mark.parametrize("text", [
    BUSINESS, TREND + " " + BUSINESS,
    "산업 경쟁이 심화되어 당사의 광고주 감소로 납품이 중단됐다.",
    "당사는 서비스 시장에서 고객 이탈로 판매가 감소하는 문제를 겪고 있다.",
    "회사의 주력 시장에서는 규제 때문에 제품 공급이 중단됐다.",
    "주식회사 한빛은 산업 변화로 기술 제약을 겪어 납품을 중단했다.",
    "회사가 영위하는 시장은 경쟁 심화로 고객 수요가 감소했다.",
])
def test_same_candidate_company_business_relation_is_preserved(text):
    assert not industry_only_challenge_problem(text, claim_slot="current_challenges:issue")
    assert not challenge_business_problem(text, {"a": text}, claim_slot="current_challenges:issue")


def test_response_and_existing_industry_context_path_are_preserved():
    assert not challenge_business_problem(RESPONSE, {"a": RESPONSE}, claim_slot="current_challenges:response")
    assert not industry_only_challenge_problem(TREND, claim_slot="current_challenges:response")
    # 별도 검증된 산업 context는 산문 직접 과제 가드의 입력으로 바꾸지 않는다.
    assert not industry_only_challenge_problem(TREND, claim_slot="industry_context:interpretation")
    assert not challenge_business_problem(TREND, {"a": TREND}, require_current=False)


@pytest.mark.parametrize("slot", ["current_challenges:initial_signal", "current_challenges:issue", ""])
@pytest.mark.parametrize("wrapped", [False, True])
def test_model_true_cannot_publish_industry_description_via_partial_slot(slot, wrapped):
    entries = [{"번호": 1, "결과": "참", "근거": ["a"]}]
    raw = json.dumps({"판정": entries} if wrapped else entries, ensure_ascii=False)
    problems = {}
    result = v._apply_grounding(
        raw, {1: "참"}, {1: (TREND, {"a": TREND + " " + BUSINESS})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: slot}, grounding_problems=problems,
    )
    assert result[1] != "참" and problems[1] == REASON
    sentence = ComposedSentence(TREND, ("a",), "확인", verification_state="verified", planned_claim_slot=slot)
    report = ComposedReport((ComposedSection("current_challenges", (sentence,) if result[1] == "참" else ()),))
    assert not has_verified_direct_business_issue(report)
    assert not summary_candidates(report)


def test_response_cell_cannot_lend_company_scope_to_industry_issue_cell():
    assert challenge_business_problem(TREND + " " + RESPONSE, {"a": TREND + " " + RESPONSE}, cells=(TREND, RESPONSE)) == REASON


def test_routine_company_sale_cannot_supply_missing_direct_problem():
    text = "반도체 시장은 경쟁이 심화되고 있다. 회사는 반도체를 판매하고 있다."
    assert challenge_business_problem(text, {"a": text}, claim_slot="current_challenges:issue") == REASON


@pytest.mark.parametrize("text", [
    "국내 반도체 시장에서 합성기술은 고객 이탈로 납품 중단을 겪고 있다.",
    "국내 반도체 시장에서 당사에 대한 수요가 감소해 납품이 중단됐다.",
    "국내 반도체 시장에서는 당사에 대한 수요가 감소해 납품이 중단됐다.",
    "당사는 원자재 부족으로 납품이 중단됐다.",
    TREND + " 회사는 고객 이탈로 납품 중단을 겪고 있다.",
    TREND + " 회사는 인력이 부족해 제품을 판매하고 있다.",
])
def test_market_background_and_same_company_effect_are_preserved(text):
    assert not industry_only_challenge_problem(text, claim_slot="current_challenges:issue")
    assert not challenge_business_problem(text, {"a": text}, claim_slot="current_challenges:issue")


@pytest.mark.parametrize("join", ["있으며", "있고"])
def test_connected_industry_trend_and_company_routine_do_not_supply_issue(join):
    text = f"광고 시장은 경쟁이 심화되고 {join} 회사는 광고 서비스를 제공하고 있다."
    assert challenge_business_problem(text, {"a": text}, claim_slot="current_challenges:issue") == REASON
    assert challenge_business_problem(text, {"a": text}, claim_slot="") == REASON
    assert not challenge_business_problem(text, {"a": text}, claim_slot="current_challenges:response")


@pytest.mark.parametrize("join", ["있으며", "있고"])
def test_connected_company_damage_remains_subject_to_semantic_review(join):
    text = f"광고 시장은 경쟁이 심화되고 {join} 회사는 광고주 이탈로 납품 중단을 겪고 있다."
    assert not industry_only_challenge_problem(text, claim_slot="current_challenges:issue")
    assert not challenge_business_problem(text, {"a": text}, claim_slot="current_challenges:issue")
