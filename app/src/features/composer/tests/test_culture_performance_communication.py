"""직원 성과 소통의 정상 절과 평가 목적 차용을 구분한다."""

import pytest

from src.features.composer.culture_constants import CULTURE_SECTION_EVIDENCE_OFFCONTRACT
from src.features.composer.culture_guard import culture_section_evidence_problem


PEOPLE = "다면평가를 통해 구성원의 역량과 노력할 점을 파악하여 성장을 지원한다."
COMMUNICATION = (
    "더불어 성과리뷰(Reflection&Dialogue), 1:1 Meeting 등을 통해 "
    "연중 수시 성과 Communication을 진행하고 있습니다."
)
SOURCE = PEOPLE + " " + COMMUNICATION
CLAIM = "성과리뷰와 1:1 Meeting 등을 통해 연중 수시로 성과 커뮤니케이션을 진행한다."


@pytest.mark.parametrize("candidate", (
    COMMUNICATION,
    CLAIM,
    "성과리뷰를 통해 수시로 성과 소통을 진행한다.",
    "1:1 미팅을 통해 성과 커뮤니케이션을 진행한다.",
))
def test_current_performance_communication_preserves_employee_context(candidate):
    assert culture_section_evidence_problem(candidate, {"own": SOURCE}) == ""


def test_people_in_same_clause_support_current_communication():
    source = "직원은 성과리뷰와 1:1 Meeting을 통해 성과 소통을 진행한다."
    assert culture_section_evidence_problem(source, {"own": source}) == ""


def test_separate_assessment_and_communication_keep_their_own_purposes():
    source = "360도 다면평가로 구성원의 역량을 파악한다. " + COMMUNICATION
    claim = "360도 다면평가로 구성원의 역량을 파악한다. " + CLAIM
    assert culture_section_evidence_problem(claim, {"own": source}) == ""


@pytest.mark.parametrize("candidate", (
    "360도 다면평가와 성과리뷰, 1:1 Meeting을 통해 연중 수시로 성과 소통을 진행한다.",
    "성과리뷰와 1:1 Meeting을 통해 매일 성과 소통을 진행한다.",
    "성과리뷰와 1:1 Meeting을 통해 성과 소통을 실시한다.",
    "고객사는 성과리뷰와 1:1 Meeting을 통해 성과 소통을 진행한다.",
))
def test_changed_instrument_cadence_activity_and_actor_are_not_rescued(candidate):
    assert culture_section_evidence_problem(candidate, {"own": SOURCE}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("source", (
    "고객사는 구성원의 역량을 파악한다. " + COMMUNICATION,
    "임직원의 교육을 지원한다.\n" + COMMUNICATION,
    "임직원의 교육을 지원한다. 제품을 판매한다. " + COMMUNICATION,
    COMMUNICATION,
    PEOPLE + " " + COMMUNICATION.replace("진행하고 있습니다", "진행할 계획입니다"),
    PEOPLE + " " + COMMUNICATION.replace("진행하고 있습니다", "진행하지 않습니다"),
    PEOPLE + " " + COMMUNICATION.replace("진행하고 있습니다", "중단했습니다"),
    PEOPLE + " " + COMMUNICATION.replace("더불어", "고객사는"),
))
def test_unbound_people_other_actor_and_noncurrent_source_do_not_open_exception(source):
    assert culture_section_evidence_problem(CLAIM, {"own": source}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


def test_other_fragment_people_cannot_supply_communication_subject():
    assert culture_section_evidence_problem(CLAIM, {"people": PEOPLE, "communication": COMMUNICATION}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT
