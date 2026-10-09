"""산업 배경을 회사 직접 과제로 세지 않고 정상 제약과 산업 원문을 보존한다."""
import json

import pytest

from src.features.composer import verify
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_industry_scope import industry_only_challenge_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.pipeline import _late_official_industry_problems
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence


TREND = (
    "이러한 산업 환경 변화 속에서 전통기업은 신사업으로 영역을 확장하고, "
    "기존 사업자들은 서로의 영역으로 진입하여 경쟁 구도가 복잡해지고 있다."
)
OUTLOOK = (
    "계측 산업에서 성장의 중심축이 서비스로 이동하는 변화가 전망되는 가운데, "
    "각 업체들이 새로운 성장동력을 발굴하는 경쟁이 심화되고 있다."
)
DAMAGE = "회사는 고객 이탈로 납품이 중단되어 매출에 손실이 발생했다."


@pytest.mark.parametrize("text", [TREND, OUTLOOK])
def test_general_industry_observation_is_not_a_direct_company_issue(text):
    assert challenge_business_problem(text, {"s": text + " " + DAMAGE},
                                      claim_slot="current_challenges:issue") == "challenge_business_relation_unbound"


@pytest.mark.parametrize("text", [
    TREND + " " + DAMAGE,
    OUTLOOK + " " + DAMAGE,
    "이러한 산업 환경 변화 속에서 회사는 계약 취소로 납품을 중단했다.",
    "계측 산업에서 각 업체들이 경쟁하고 있지만 당사 제품 공급은 중단되어 손실이 발생했다.",
    "이러한 산업 환경 변화 속에서 전통기업은 경쟁하고 당사 고객의 계약이 취소되어 매출 손실이 발생했다.",
    "이러한 산업 환경 변화 속에서 가람제작은 고객 이탈로 납품을 중단했고, 기존 업체들은 영역을 확장하고 있다.",
    "이러한 산업 환경 변화 속에서 기존 업체들은 영역을 확장하고 가람제작은 고객 이탈로 납품을 중단했다.",
])
def test_explicit_company_constraint_in_same_claim_is_preserved(text):
    assert not industry_only_challenge_problem(text, claim_slot="current_challenges:issue")


@pytest.mark.parametrize("slot", [
    "current_challenges:response", "current_challenges:unresolved_gap",
    "industry_context:interpretation", "portfolio:product_role",
])
def test_new_environment_boundary_does_not_retag_other_slots(slot):
    assert not industry_only_challenge_problem(TREND, claim_slot=slot)


@pytest.mark.parametrize("text", [TREND, OUTLOOK])
def test_model_true_cannot_promote_general_industry_into_direct_issue(text):
    raw = json.dumps([{"번호": 1, "장": "current_challenges", "근거": ["s"], "결과": "참"}], ensure_ascii=False)
    reasons = {}
    verdict = verify._apply_grounding(
        raw, {1: "참"}, {1: (text, {"s": text})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: "current_challenges:issue"}, grounding_problems=reasons,
    )
    assert verdict[1] != "참"
    assert reasons[1] == "challenge_business_relation_unbound"


def report_with(text):
    sentence = ComposedSentence(text, ("s",), "확인", verification_state="verified",
                                planned_claim_slot="current_challenges:issue")
    return ComposedReport((ComposedSection("current_challenges", (sentence,)),))


@pytest.mark.parametrize("text", [TREND, OUTLOOK])
def test_existing_industry_sentence_does_not_close_optional_callback(text):
    report = report_with(text)
    assert not has_verified_direct_business_issue(report)
    calls = []
    result = _late_official_industry_problems(
        report, callback=lambda selected: calls.append(selected) or (),
        anchors=(), problems=(), fragments=(), company_id="synthetic", diagnostics=[],
    )
    assert result == () and calls == [()]
    assert report.sections[0].sentences[0].text == text


def test_real_company_issue_still_skips_callback():
    report = report_with(DAMAGE)
    assert has_verified_direct_business_issue(report)
    calls = []
    _late_official_industry_problems(
        report, callback=lambda selected: calls.append(selected) or (),
        anchors=(), problems=(), fragments=(), company_id="synthetic", diagnostics=[],
    )
    assert not calls
