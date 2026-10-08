"""명시 계획 슬롯의 주어 생략과 기존 미래 근거 계약을 함께 검사한다."""
import pytest

from src.features.composer.future_plan_constants import STATED_PLAN_SLOT
from src.features.composer.future_plan_guard import future_plan_prose_problem


def proof(quote, target="팬 플랫폼 사업", activity="확대", source="1", mode="계획"):
    return {"미래근거": [{"근거": source, "대상": target, "활동": activity,
                         "원문": quote, "양태": mode}]}


def problem(candidate, source, evidence, slot=STATED_PLAN_SLOT):
    return future_plan_prose_problem(candidate, {"1": source}, evidence, claim_slot=slot)


@pytest.mark.parametrize("candidate", [
    "팬 플랫폼 사업을 확대할 계획이다.",
    "성장위원회는 팬 플랫폼 사업을 확대할 계획이다.",
    "회사는 팬 플랫폼 사업을 확대할 계획이다.",
    "시제품 부문은 팬 플랫폼 사업을 확대할 계획이다.",
])
def test_stated_plan_requires_proof_regardless_of_subject(candidate):
    assert problem(candidate, "당사는 팬 플랫폼 사업을 확대할 계획입니다.", None)


@pytest.mark.parametrize("candidate", [
    "팬 플랫폼 사업을 확대할 계획이다.",
    "성장위원회는 팬 플랫폼 사업을 확대할 계획이다.",
    "회사는 팬 플랫폼 사업을 확대할 계획이다.",
    "시제품 부문은 팬 플랫폼 사업을 확대할 계획이다.",
])
def test_correct_own_plan_is_preserved(candidate):
    source = "당사는 팬 플랫폼 사업을 확대할 계획입니다."
    assert problem(candidate, source, proof(source)) == ""


def test_current_goal_discussion_cannot_support_future_execution():
    source = "위원회는 향후 방향성을 논의하며 ESG 운영을 관리하고 있습니다."
    candidate = "위원회는 향후 방향성을 논의하며 ESG 경영을 추진해 나갈 예정이다."
    evidence = proof(source, "위원회", "ESG 경영을 추진해 나갈 예정이다")
    assert problem(candidate, source, evidence) == "future_plan_activity_not_in_quote"


@pytest.mark.parametrize("source", [
    "경쟁사는 팬 플랫폼 사업을 확대할 계획입니다.",
    "당사는 팬 플랫폼 사업을 확대하고 있습니다.",
    "팬 플랫폼 사업을 확대할 것으로 예상됩니다.",
])
def test_other_actor_present_and_outlook_cannot_become_company_plan(source):
    assert problem("팬 플랫폼 사업을 확대할 계획이다.", source, proof(source))


def test_passive_normal_plan_is_preserved():
    source = "팬 플랫폼 사업은 확대될 예정입니다."
    assert problem("팬 플랫폼 사업은 확대될 예정이다.", source, proof(source)) == ""


def test_quote_and_own_id_contract_remains_closed():
    source = "당사는 팬 플랫폼 사업을 확대할 계획입니다."
    candidate = "팬 플랫폼 사업을 확대할 계획이다."
    assert problem(candidate, source, proof(source, source="2")) == "future_plan_source_not_cited"
    assert problem(candidate, source, proof("당사는 팬 플랫폼 사업을 확대할 방침입니다."))


def test_one_plan_proof_cannot_cover_two_activities():
    source = "당사는 팬 플랫폼 사업을 확대할 계획입니다."
    candidate = "팬 플랫폼 사업을 확대할 계획이며 생산 설비를 증설할 예정이다."
    assert problem(candidate, source, proof(source)) == "future_plan_second_claim_unproven"


@pytest.mark.parametrize("evidence", [None, {}, {"미래근거": []}])
def test_nonplan_narrative_does_not_require_new_proof(evidence):
    assert problem("위원회는 성과를 보고하고 있다.", "성과를 보고합니다.", evidence) == ""


def test_supplied_proof_is_checked_without_a_future_marker():
    source = "당사는 팬 플랫폼 사업을 확대하고 있습니다."
    assert problem("팬 플랫폼 사업을 확대하고 있다.", source, proof(source))


def test_other_slot_and_default_call_keep_original_activation():
    candidate = "팬 플랫폼 사업을 확대할 계획이다."
    source = "당사는 팬 플랫폼 사업을 확대할 계획입니다."
    assert problem(candidate, source, None, "future_strategy:plan_status") == ""
    assert future_plan_prose_problem(candidate, {"1": source}, None) == ""


def test_normal_investment_plan_keeps_same_exact_proof():
    source = "향후 투자계획은 생산성 향상을 위한 투자 수행 예정입니다."
    candidate = "시제품 부문은 생산성 향상을 위한 투자를 수행할 예정이다."
    assert problem(candidate, source, proof(source, "투자", "수행")) == ""
