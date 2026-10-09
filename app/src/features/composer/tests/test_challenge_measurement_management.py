"""최종 자기 인용 가드에서 측정·일반 업무가 다시 현재 과제가 되지 않는다."""
import pytest

from src.features.composer.challenge_business_scope import challenge_business_problem


@pytest.mark.parametrize("candidate,source", (
    ("회사는 비금융자산의 사용가치에 미치는 영향을 평가하고 있다.",
     "비금융자산의 손상: 전환 위험은 사용가치에 영향을 미칠 수 있다."),
    ("기후 관련 위험은 재무제표의 추정 및 가정의 불확실성을 증가시킨다.",
     "회사는 추정과 가정에 기후 관련 위험을 고려한다. 기후 관련 위험은 재무제표의 추정 및 가정의 불확실성을 증가시킨다."),
    ("회사는 배출권에 대해 순부채접근법을 채택한다.",
     "회사는 추정과 가정에 기후 관련 위험을 고려한다. 온실가스 배출: 회사는 배출권을 받고 실제 배출량의 배출권을 제출한다. 회사는 순부채접근법을 채택한다."),
    ("회사는 준법교육 및 사내 법률자문과 계약 검토를 진행한다.",
     "임직원 준법교육 실시와 사내 법률자문 및 계약 검토를 진행한다."),
    ("법률 리스크가 발생할 수 있으며 전담 조직을 갖추고 있다.",
     "법률 리스크가 발생할 수 있으며 전담 조직을 갖추고 있다."),
))
def test_self_source_management_cannot_restore_body_or_flow(candidate, source):
    assert challenge_business_problem(candidate, {"1": source})
    assert challenge_business_problem(candidate, {"1": source}, cells=(candidate, "관리 대응"))


@pytest.mark.parametrize("current", (
    "새 규제로 제품 생산이 중단되어 대응하고 있다.",
    "보험 고객의 보상 지급 지연이 지속되고 있다.",
    "고객에게 계약 법률 자문 서비스를 제공하고 있다.",
    "특허 소송이 현재 진행 중이다.",
))
def test_mixed_policy_source_does_not_remove_the_actual_business_sentence(current):
    source = "비금융자산의 손상: 전환 위험은 사용가치에 영향을 미칠 수 있다. " + current
    assert not challenge_business_problem(current, {"1": source})
