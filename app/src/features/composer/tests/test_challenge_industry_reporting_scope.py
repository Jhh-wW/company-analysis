"""산업 수식·보고절의 직접 과제 승격과 실제 회사 제약 보존을 확인한다."""
import json

import pytest

from src.features.composer import verify
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.logic import summary_candidates
from src.features.composer.pipeline import _late_official_industry_problems
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence

REASON = "challenge_business_relation_unbound"
TREND = (
    "정보 산업 전반에서는 플랫폼 간 경계가 사라지는 융합이 진행되면서 "
    "사업구조가 다양하고 복잡해지고 있다고 공식 자료는 설명한다."
)
OUTLOOK = (
    "이러한 산업 환경 변화 속에서 공식 자료는 향후 성장의 중심이 서비스 부문으로 "
    "이동할 것으로 전망되며 산업 수준에서 선도 서비스가 우세해질 가능성이 높다고 기술한다."
)
RESPONSE = "회사는 온라인 인프라를 구축하여 관련 서비스를 제공하고 있다."
DAMAGE = "회사는 고객 이탈로 납품이 중단되어 매출에 손실이 발생했다."


@pytest.mark.parametrize("text", [
    TREND, OUTLOOK,
    "공시 자료는 정보 산업 전체에서는 사업구조가 복잡해지고 있다고 설명한다.",
    "보고서는 향후 산업 환경에서 서비스 부문이 성장할 가능성이 높다고 전망한다.",
    "보고서는 향후 계측 산업 전반에서 선도 회사가 시장을 장악할 가능성이 높다고 기술한다.",
    "공시 자료는 향후 계측 산업 전반에서 다른 회사가 시장을 장악할 가능성이 높다고 기술한다.",
    "공식 보고서는 향후 운송 산업 전반에서 공급 차질이 예상된다고 설명한다.",
    "공시에서는 산업 환경에서 향후 비용 증가가 계속될 것으로 전망한다.",
    "보고서에 따르면 향후 운송 산업 전반에서 경쟁 심화가 예상된다.",
])
@pytest.mark.parametrize("slot", ["", "current_challenges:issue", "current_challenges:initial_signal"])
def test_industry_report_is_not_a_direct_company_problem(text, slot):
    assert challenge_business_problem(text, {"a": text + " " + DAMAGE}, claim_slot=slot) == REASON


@pytest.mark.parametrize("text", [
    TREND + " " + DAMAGE,
    "공식 자료는 산업 전반의 수요 감소 때문에 당사 고객의 계약이 취소되어 납품이 중단됐다고 설명한다.",
    "국내 서비스 시장에서 합성기업은 고객 이탈로 납품 중단을 겪고 있다.",
    "공급업체 전체에 원자재 부족이 발생했으며 당사는 그 때문에 납품을 중단했다.",
    "공시 자료는 향후 산업 변화에 대응할 계획이며 현재 회사 제품의 결함으로 공급이 중단됐다고 설명한다.",
    "공시 자료는 계측 산업 전반에서는 당사 원자재 공급이 중단되어 납품 손실을 겪고 있다고 설명한다.",
])
def test_actual_company_constraint_in_the_candidate_is_preserved(text):
    assert not challenge_business_problem(text, {"a": text}, claim_slot="current_challenges:issue")


@pytest.mark.parametrize("slot", ["current_challenges:response", "industry_context:interpretation"])
def test_response_and_industry_interpretation_keep_their_existing_contract(slot):
    from src.features.composer.challenge_industry_scope import industry_only_challenge_problem
    assert not industry_only_challenge_problem(OUTLOOK, claim_slot=slot)


@pytest.mark.parametrize("text", [TREND, OUTLOOK])
def test_model_true_cannot_close_the_optional_industry_opportunity(text):
    raw = json.dumps([{"번호": 1, "장": "current_challenges", "근거": ["a"], "결과": "참"}], ensure_ascii=False)
    problems = {}
    verdict = verify._apply_grounding(
        raw, {1: "참"}, {1: (text, {"a": text + " " + DAMAGE})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: "current_challenges:issue"}, grounding_problems=problems,
    )
    assert verdict[1] != "참" and problems[1] == REASON
    response = ComposedSentence(RESPONSE, ("b",), "확인", verification_state="verified",
                                planned_claim_slot="current_challenges:response")
    report = ComposedReport((ComposedSection("current_challenges", (response,)),))
    assert not has_verified_direct_business_issue(report)
    assert not summary_candidates(report)
    calls = []
    assert not _late_official_industry_problems(
        report, callback=lambda selected: calls.append(selected) or (), anchors=(), problems=(),
        fragments=(), company_id="synthetic", diagnostics=[],
    )
    assert len(calls) == 1
