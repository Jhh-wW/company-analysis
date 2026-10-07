"""측정·내부 관리 역할의 지원칸 제한과 실제 사업 문제의 보존을 검증한다."""
import hashlib

import pytest

from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_problem, challenge_eligibility_quote_problem,
    challenge_eligibility_scope,
)
from src.shared.report_evidence.business_slot_scope import business_slot_quote_problem

MANAGEMENT = (
    "회사는 추정과 가정에 기후 관련 위험을 고려합니다. 기후 관련 위험은 재무제표의 추정 및 가정의 불확실성을 증가시킵니다.",
    "비금융자산의 손상: 전환 위험은 사용가치에 영향을 미칠 수 있습니다.",
    "공정가치 측정 자산의 경우 물리적 위험과 전환 위험의 영향을 가치평가에 반영합니다. 일부 투자자는 전환 위험을 가치평가에 고려할 것으로 예상합니다.",
    "회사 법률 리스크는 증가하고 있습니다. 지적재산권과 공정거래법 분야에서 법률 리스크가 발생할 수 있으며 전담 조직을 갖추고 있습니다.",
    "활동내역 | 세부내용 | 처리결과; 준법교육 실시 | 임직원 대상 공정거래법 준법교육 진행 | 준법 문화 확산; 법무시스템 운영 | 사내 법률자문 및 계약 검토 진행 | 임직원 업무 지원",
)


@pytest.mark.parametrize("source", MANAGEMENT)
def test_measurement_and_internal_management_cannot_supply_current_slots(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert challenge_eligibility_problem(source) == "challenge_business_relation_unbound"
    assert not challenge_eligibility_scope(source).score_text.strip()
    for slot in ("current_challenges:issue", "current_challenges:response"):
        assert business_slot_quote_problem(source, slot, 0, len(source))
    assert not business_slot_quote_problem(source, "past_changes:performance", 0, len(source))
    assert hashlib.sha256(source.encode()).hexdigest() == digest


@pytest.mark.parametrize("source", MANAGEMENT)
@pytest.mark.parametrize("current", (
    "고객의 대출 연체율이 급증하여 현재 채무조정 서비스를 확대하고 있다.",
    "환경규제에 대응하여 제품의 냉매 기술을 개발하고 있다.",
    "제품 결함으로 생산이 중단되어 복구 작업을 진행하고 있다.",
))
def test_only_management_clause_is_excluded_from_mixed_business_source(source, current):
    mixed = source + "; " + current
    assert current in challenge_eligibility_scope(mixed).score_text
    assert not challenge_eligibility_quote_problem(current, mixed, "current_challenges:issue")
    assert challenge_eligibility_quote_problem(source, mixed, "current_challenges:response")


@pytest.mark.parametrize("source", (
    "환경규제에 대응하여 제품의 냉매 기술을 개발하고 있다.",
    "제품 기술을 환경규제 기준에 맞춰 개선하고 있다.",
    "대출 고객의 연체율이 상승하여 금융 서비스를 개선하고 있다.",
    "보험 고객의 보상 지급 지연이 지속되고 있다.",
    "고객에게 법률 자문 서비스를 제공하며, 법률 리스크에 관한 계약 검토 서비스를 운영한다.",
    "사내 법무팀은 고객에게 계약 법률 자문 서비스를 제공하고 있다.",
    "특허 분쟁이 발생하여 현재 소송을 진행하고 있다. 법률 리스크에 대응하기 위한 전담 조직이 있다.",
    "법률 리스크가 발생할 수 있어 전담 조직을 갖추고 있다. 현재 제품 특허 소송이 진행 중이다.",
    "회계 측정에 기후 위험을 고려한다. 새 규제로 제품 생산이 중단됐다.",
))
def test_actual_product_regulation_customer_service_and_dispute_are_preserved(source):
    assert not challenge_eligibility_problem(source)
    assert challenge_eligibility_scope(source).score_text.strip()


@pytest.mark.parametrize("service", ("법률 자문 서비스", "계약 검토 플랫폼"))
def test_서비스뒤에_고객수혜자가_명시된본업제공은_대응인용으로_보존한다(service):
    source = f"사내 법무팀은 {service}를 고객사에게 제공하고 있다."
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert not challenge_eligibility_problem(source)
    assert challenge_eligibility_scope(source).score_text.strip()
    # 고객 본업 제공 사실은 실제 문제 발생을 말하지 않으므로 issue 허용과 구분한다.
    assert not challenge_eligibility_quote_problem(source, source, "current_challenges:response")
    assert hashlib.sha256(source.encode()).hexdigest() == digest


def test_고객자문서비스제공자체는_실제문제issue를_채우지않는다():
    source = "사내 법무팀은 법률 자문 서비스를 고객사에게 제공하고 있다."
    assert challenge_eligibility_quote_problem(source, source, "current_challenges:issue") == (
        "challenge_business_relation_unbound"
    )


@pytest.mark.parametrize("service", ("법률 자문 서비스", "계약 검토 플랫폼"))
def test_고객본업제공과_실제고객문제가_섞인원문의_issue를_보존한다(service):
    ordinary = f"사내 법무팀은 {service}를 고객사에게 제공하고 있다."
    problem = "고객의 계약 소송이 진행 중이며 법원 처리 지연으로 피해가 발생했다."
    source = ordinary + " " + problem
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert problem in challenge_eligibility_scope(source, "current_challenges:issue").score_text
    assert not challenge_eligibility_quote_problem(problem, source, "current_challenges:issue")
    assert not business_slot_quote_problem(source, "current_challenges:issue",
                                           len(ordinary) + 1, len(source))
    assert not challenge_eligibility_quote_problem(ordinary, source, "current_challenges:response")
    assert hashlib.sha256(source.encode()).hexdigest() == digest


@pytest.mark.parametrize("service", ("법률 자문 서비스", "계약 검토 플랫폼"))
def test_임직원에게만_제공하는서비스는_내부관리경계를_해제하지않는다(service):
    source = f"사내 법무팀은 {service}를 임직원에게 제공하고 있다."
    assert challenge_eligibility_problem(source) == "challenge_business_relation_unbound"
