"""검수가 참으로 돌려도 신용 측정 정책은 5장 본문·표를 채우지 않는다."""
import pytest

from src.features.composer import verify as v
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow

@pytest.mark.parametrize("candidate,source,expected_reason", (
    ("회사는 기대신용손실 측정 시 미래전망정보를 반영하고 있다.",
     "회사의 금융자산 회계정책은 기대신용손실 측정 시 미래전망정보를 반영하는 것이다.", "challenge_business_relation_unbound"),
    ("회사는 신용위험의 유의적 증가 여부를 판단하기 위해 연체 정보를 활용한다.",
     "회사는 신용위험의 유의적 증가 여부를 판단하기 위해 연체 정보를 활용한다.", "accounting_policy_boilerplate"),
    ("회사는 금융자산의 회수지연 현황 및 회수대책이 보고되고 지연사유에 따라 적절한 조치를 취한다.",
     "회사는 금융자산의 회수지연 현황 및 회수대책이 보고되고 지연사유에 따라 적절한 조치를 취한다.", "challenge_business_relation_unbound"),
))
def test_credit_policy_prose_and_flow_are_restricted_even_after_true_review(monkeypatch, candidate, source, expected_reason):
    assert challenge_business_problem(candidate, {"1": source})
    monkeypatch.setattr(v, "_semantic_review", lambda groups, *args, **kwargs: groups)
    row = FlowRow((candidate, "정기적인 측정 및 관리"), ("1",))
    report = ComposedReport(sections=(ComposedSection("current_challenges", (), flow_rows=(row,)),))
    diagnostics = []
    result = v.verify_report(report, (CollectedFragment("1", "DART", source),), None, lambda *args: "", diagnostics=diagnostics)
    assert not result.sections[0].flow_rows
    assert any(item["reason_code"] == expected_reason for item in diagnostics)
    assert report.sections[0].flow_rows == (row,)


def test_actual_customer_event_is_kept_and_measurement_only_crop_is_not():
    policy = "회사는 기대신용손실 측정 시 미래전망정보를 반영한다."
    actual = "결제 대행 서비스 고객의 결제 장애가 발생했다."
    source = policy + " " + actual
    assert not challenge_business_problem(actual, {"1": source})
    assert challenge_business_problem("미래전망정보를 반영", {"1": source})
    assert challenge_business_problem("", {"1": source}, cells=("기대신용손실 측정", "미래전망정보 반영"))


def test_real_financial_service_problem_is_not_blocked_by_measurement_context():
    source = "은행은 기대신용손실 측정 서비스를 고객에게 제공하고 있다. 고객의 결제 장애가 발생했다."
    assert not challenge_business_problem("고객의 결제 장애가 발생했다.", {"1": source})
