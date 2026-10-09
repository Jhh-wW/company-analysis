"""조사 대상과 공동 조사 상대를 섞지 않는 원문 경계."""
import pytest

from src.shared.report_evidence.partnership_scope import (
    research_partnership_problem, research_partnership_claim_problem,
)
from src.shared.report_evidence.partnership_scope_constants import PARTNERSHIP_SLOT, OPERATING_ROLE_SLOT

SOLO = "온빛연구소가 기업 80곳과 금융사 20곳의 사업보고서를 전수 조사해 결과를 공개했다."
JOINT = "온빛연구소가 새봄대학과 함께 기업 80곳의 사업보고서를 전수 조사해 결과를 공개했다."


@pytest.mark.parametrize("text", [
    SOLO,
    "온빛연구소는 고객 80명을 설문 조사하여 결과를 공개했다.",
    "온빛연구소는 실태조사를 수행하고 결과를 발표했다.",
])
def test_조사_대상은_제휴상대가_아니다(text):
    assert research_partnership_problem(text, PARTNERSHIP_SLOT) == "scope_condition_unbound"
    assert research_partnership_problem(text, OPERATING_ROLE_SLOT) == ""


@pytest.mark.parametrize("text", [JOINT,
    "온빛연구소는 새봄대학과 조사 협약을 체결했다.",
    "온빛연구소는 새봄대학과 공동으로 기업 사업보고서를 전수 조사했다.",
    "온빛연구소와 새봄대학은 함께 기업 사업보고서를 전수 조사했다.",
    "온빛연구소는 신형 장비를 개발하고 시제품을 생산했다.",
])
def test_공동조사와_일반_연구개발은_단독조사로_제외하지_않는다(text):
    assert research_partnership_problem(text, PARTNERSHIP_SLOT) == ""


def test_다른문장_다른근거_제휴는_단독조사의_상대가_아니다():
    claim = "온빛연구소는 기업들과 협력해 사업보고서를 전수 조사했다."
    for sources in ({"1": SOLO + " 온빛연구소는 새봄대학과 협약을 맺었다."},
                    {"1": SOLO, "2": JOINT}):
        assert research_partnership_claim_problem(claim, sources) == "scope_condition_unbound"
    assert research_partnership_claim_problem(SOLO, {"1": SOLO}, claim_slot=PARTNERSHIP_SLOT)
    assert research_partnership_claim_problem(SOLO, {"1": SOLO}, claim_slot=OPERATING_ROLE_SLOT) == ""
    assert research_partnership_claim_problem(JOINT, {"1": SOLO, "2": JOINT},
                                               claim_slot=PARTNERSHIP_SLOT) == ""


def test_일반제휴와_부정_양태는_전체승인하지_않는다():
    # 이 좁은 가드 밖의 일반 계약/부정·시점은 기존 의미 검수가 맡는다.
    assert research_partnership_claim_problem("온빛연구소는 새봄대학과 협약을 맺었다.",
                                               {"1": "새봄대학과 협약을 맺었다."}) == ""
    text = "온빛연구소는 새봄대학과 공동조사가 아닌 전수 조사를 수행했다."
    assert research_partnership_problem(text, PARTNERSHIP_SLOT)


def test_동일문장의_다른_조사도_상대를_빌리지_않는다():
    source = ("온빛연구소는 기업 80곳의 사업보고서를 전수 조사했고 "
              "새봄대학과 함께 고객 30명을 설문 조사했다.")
    claim = "온빛연구소는 기업 80곳과 공동으로 사업보고서를 전수 조사했다."
    assert research_partnership_claim_problem(claim, {"1": source})
    valid = "새봄대학과 함께 고객 30명을 설문 조사했다."
    assert research_partnership_claim_problem(valid, {"1": source}, claim_slot=PARTNERSHIP_SLOT) == ""


def test_함께_조사한_목적어_나열을_공동수행자라고_추정하지_않는다():
    text = "온빛연구소는 기업 80곳과 금융사 20곳의 사업보고서를 함께 전수 조사했다."
    assert research_partnership_problem(text, PARTNERSHIP_SLOT)
