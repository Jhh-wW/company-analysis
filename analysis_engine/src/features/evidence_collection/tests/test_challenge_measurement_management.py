"""회계·일반 내부관리 신호가 현재 문제·대응 채점이나 무신호 우회로 되살아나지 않는다."""
import pytest

from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.business_slot_scope import business_slot_quote_problem


@pytest.mark.parametrize("source", (
    "비금융자산의 손상: 전환 위험은 사용가치에 영향을 미칠 수 있다.",
    "회사는 추정과 가정에 기후 관련 위험을 고려한다. 기후 관련 위험은 재무제표의 추정 및 가정의 불확실성을 증가시킨다.",
    "공정가치 측정 자산의 경우 전환 위험을 가치평가에 반영한다. 일부 투자자는 전환 위험을 가치평가에 고려한다.",
    "법률 리스크가 발생할 수 있으며 이에 대응할 전담 조직을 갖추고 있다.",
    "임직원 준법교육 실시와 사내 법률자문 및 계약 검토를 진행한다.",
))
def test_management_has_no_current_slot_and_retains_observed_signal(source):
    scores, observed = score_fragment_slots_with_signal(source)
    assert observed
    assert not [x for x in scores if x.section_id == "current_challenges"]
    for slot in ("current_challenges:issue", "current_challenges:response"):
        assert business_slot_quote_problem(source, slot, 0, len(source))


@pytest.mark.parametrize("current", (
    "새 환경규제에 대응하여 제품 기술을 개발하고 있다.",
    "고객의 대출 연체율이 상승하여 대응 서비스를 개선하고 있다.",
    "현재 특허 소송이 진행 중이며 계약 분쟁에 대응하고 있다.",
))
def test_real_business_issue_survives_a_mixed_measurement_paragraph(current):
    source = "비금융자산의 손상: 전환 위험은 사용가치에 영향을 미칠 수 있다. " + current
    scores, observed = score_fragment_slots_with_signal(source)
    assert observed
    assert any(x.section_id == "current_challenges" for x in scores)


@pytest.mark.parametrize("beneficiary,blocked", (("고객사", False), ("임직원", True)))
def test_역어순_법률서비스의_고객수혜자와_내부수혜자를_구분한다(beneficiary, blocked):
    from features.evidence_collection.challenge_eligibility import challenge_eligibility_problem
    source = f"사내 법무팀은 계약 검토 플랫폼을 {beneficiary}에게 제공하고 있다."
    assert bool(challenge_eligibility_problem(source)) is blocked
    assert bool(business_slot_quote_problem(source, "current_challenges:issue", 0, len(source))) is blocked
