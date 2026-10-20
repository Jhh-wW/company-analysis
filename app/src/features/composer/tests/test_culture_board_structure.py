"""이사회 자체의 구성과 실제 구성원 업무 제도를 구별한다."""

import json
import re

import pytest

from src.features.composer.culture_constants import CULTURE_SECTION_EVIDENCE_OFFCONTRACT
from src.features.composer.culture_guard import culture_section_evidence_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


# 2026-10-12 f979 실행의 공식 공시 조각 125 중 연속 원문이다.
BOARD_SOURCE = (
    "대표이사와 이사회 의장을 분리함으로써 경영진의 업무 집행 관련 견제와 균형을 행사하고, "
    "이사회가 독립적으로 운영될 수 있도록 하였습니다."
    "이사회 내에는 효율적 의사결정 및 업무집행의 전문성 강화를 위해 감사위원회, "
    "사외이사후보추천위원회, 경영위원회, 내부거래위원회, ESG위원회, 보상위원회 총 6개의 위원회를 운영하고 있습니다. "
    "특히, 2025년 제12차 이사회(2025.12.30)에서 전원 사외이사로 구성된 보상위원회 신규 설치를 의결하여 "
    "이사 보수 결정에 대한 객관성, 투명성 및 절차적 정당성을 확보하고자 하였습니다."
)


@pytest.mark.parametrize("claim", [
    "대표이사와 이사회 의장을 분리하여 경영진 업무 집행에 대한 견제와 균형을 행사하고 있다.",
    "이사회 내에는 감사위원회·사외이사후보추천위원회·경영위원회·내부거래위원회·ESG위원회·보상위원회 총 6개 위원회를 운영하고 있다.",
    "전원 사외이사로 구성된 보상위원회 신규 설치를 의결하여 이사 보수 결정의 객관성·투명성·절차적 정당성을 확보하고자 하였다.",
    "이사회는 사내이사 3명과 사외이사 5명으로 구성되어 있다.",
])
def test_pure_board_structure_is_not_working_culture(claim):
    assert culture_section_evidence_problem(claim, {"1": claim}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("claim", [
    "이사회 내 보상위원회를 운영하며 직원의 성과 평가와 보상 정책을 심의한다.",
    "이사회는 사외이사로 구성되며 직원 교육과 승진 정책을 승인한다.",
    "대표이사와 이사회 의장을 분리하고 담당 부서장에게 현장 업무의 전결 권한을 위임한다.",
    "보상위원회는 임직원의 평가 결과에 따라 성과 보상을 결정한다.",
    "인사팀은 구성원과 분기별 성장 피드백을 나누고 교육 계획을 수립한다.",
    "이사회 내 보상위원회를 운영하며 담당 부서장이 임금 정책을 집행한다.",
    "이사회 내 위원회를 운영하며 직원의 근무시간과 휴가 제도를 심의한다.",
    "이사회는 사외이사로 구성되며 임직원의 인사제도와 근무환경 개선안을 심의한다.",
    "이사회 내 위원회를 운영하며 부서 간 협업 절차를 정한다.",
])
def test_board_connected_employee_procedures_survive(claim):
    assert culture_section_evidence_problem(claim, {"1": claim}) == ""


def test_unrelated_employee_policy_does_not_rescue_board_structure():
    claim = "이사회 내에는 감사위원회와 보상위원회 등 위원회를 운영하고 있다."
    source = claim + " 직원 교육은 인사팀이 담당하고 분기별 교육 과정을 운영한다."
    assert culture_section_evidence_problem(claim, {"1": source}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("claim", [
    "대표이사와 이사회 의장을 분리하여 경영진 업무 집행에 대한 견제와 균형을 행사하고, 이사회 내에는 감사위원회·사외이사후보추천위원회·경영위원회·내부거래위원회·ESG위원회·보상위원회 총 6개 위원회를 운영하고 있다.",
    "2025년 제12차 이사회(2025.12.30)에서는 전원 사외이사로 구성된 보상위원회 신규 설치를 의결하여 이사 보수 결정의 객관성·투명성·절차적 정당성을 확보하고자 하였다.",
])
def test_saved_report_board_claims_are_rejected_against_their_original_source(claim):
    assert culture_section_evidence_problem(claim, {"125": BOARD_SOURCE}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


def test_positive_model_verdict_cannot_publish_board_structure_as_culture():
    bad = "이사회 내에는 감사위원회와 보상위원회 등 위원회를 운영하고 있다."
    good = "인사팀은 구성원과 분기별 성장 피드백을 나누고 교육 계획을 수립한다."

    def ask(prompt):
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        return json.dumps({"판정": [{"번호": item.number, "결과": "참", "장": item.section,
                                    "근거": list(item.citations)} for item in items]}, ensure_ascii=False)

    diagnostics = []
    result = verify_report(
        ComposedReport((ComposedSection("culture", (
            ComposedSentence(bad, ("1",), "확인"), ComposedSentence(good, ("2",), "확인"),
        )),)),
        (CollectedFragment("1", "공식자료", bad), CollectedFragment("2", "공식자료", good)),
        None, ask, diagnostics=diagnostics,
    )
    assert tuple(sentence.text for sentence in result.sections[0].sentences) == (good,)
    assert any(item["reason_code"] == CULTURE_SECTION_EVIDENCE_OFFCONTRACT for item in diagnostics)
