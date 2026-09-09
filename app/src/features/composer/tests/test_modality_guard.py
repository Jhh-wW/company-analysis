"""실제 계획 한정 절단과 정상 현재·계획·인용의 의미 있는 대조쌍."""
from __future__ import annotations

import pytest

from src.features.composer.modality_constants import MODALITY_PLAN_ASSERTED
from src.features.composer.modality_guard import modality_problem

REAL_SOURCE = (
    "당행은 대외시스템과의 연계를 기반으로 기업상품 취급에 필요한 데이터를 수집, "
    "관리하는 자체개발 B2B 전용 MP(Market Place) 서비스인 「원비즈 e-MP 서비스」를 "
    "2025년 6월 출시하였습니다. 이를 통해, 수수료 없는 비대면 서비스 등 차별화된 "
    "고객 경험을 제공하고 공급망금융시장을 선도해나가고자 합니다. "
    "2025년 10월 「삼성월렛머니 서비스」를 출시하고 고객 편의성과 만족도를 높이고 있습니다. "
    "향후 자산관리 서비스 등 기술 적용 범위를 확대하여 AX혁신을 지속할 계획입니다."
)
REAL_BAD = (
    "우리은행은 2025년 6월 '대외시스템과의 연계를 기반으로 기업상품 취급에 필요한 "
    "데이터를 수집, 관리하는 자체개발 B2B 전용 MP(Market Place) 서비스인 "
    "「원비즈 e-MP 서비스」'를 출시했으며, 이를 통해 '수수료 없는 비대면 서비스 등 "
    "차별화된 고객 경험을 제공하고 공급망금융시장을 선도'한다고 밝혔다. [8][57]"
)
REAL_GOOD = (
    "2025년 6월 출시한 원비즈 e-MP 서비스는 기업상품 취급에 필요한 데이터를 "
    "수집·관리하는 자체개발 B2B 전용 마켓플레이스로, 기업 고객에게 수수료 없는 "
    "비대면 공급망금융 서비스를 제공하며 공급망금융시장 선도를 목표로 한다. [8][57][193]"
)


def test_real_woori_truncated_quote_is_rejected_but_goal_is_preserved():
    assert modality_problem(REAL_BAD, {"8": REAL_SOURCE, "57": REAL_SOURCE}) == MODALITY_PLAN_ASSERTED
    assert modality_problem(REAL_GOOD, {"8": REAL_SOURCE, "57": REAL_SOURCE}) == ""


@pytest.mark.parametrize("quote", [("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”"), ("「", "」"), ("『", "』")])
@pytest.mark.parametrize("spacing", ["", " "])
def test_quote_boundary_does_not_hide_truncated_intention(quote, spacing):
    left, right = quote
    source = "공급망금융시장을 선도해나가고자 합니다."
    candidate = f"회사는 {left}공급망금융시장을 선도{right}{spacing}한다고 밝혔다."
    assert modality_problem(candidate, {"공시": source}) == MODALITY_PLAN_ASSERTED


@pytest.mark.parametrize("tail", [
    "하고자 합니다", "하려고 한다", "하려는 계획이다", "할 계획이다", "할 예정이다",
    "할 방침이다", "해 나가고자 합니다", "해 나갈 계획이다", "하기를 목표로 한다",
    "를 목표로 한다", "가 목표다", "하는 것이 목표다", "한다는 목표를 밝혔다",
])
def test_plan_tail_and_matching_current_assertion_form_a_negative_pair(tail):
    source = f"가람기업은 해외사업을 확대{tail}."
    assert modality_problem("가람기업은 해외사업을 확대한다.", {"공시": source}) == MODALITY_PLAN_ASSERTED
    assert modality_problem(source, {"공시": source}) == ""


@pytest.mark.parametrize("assertion", ["확대한다", "확대합니다", "확대했다", "확대하였습니다", "확대하고 있다", "확대해 왔다"])
def test_current_and_completed_claims_require_matching_direct_support(assertion):
    plan = "가람기업은 해외사업을 확대할 계획이다."
    candidate = f"가람기업은 해외사업을 {assertion}."
    assert modality_problem(candidate, {"계획": plan}) == MODALITY_PLAN_ASSERTED
    assert modality_problem(candidate, {"계획": plan, "실적": candidate}) == ""


@pytest.mark.parametrize(("candidate", "source"), [
    ("가람기업은 국내사업을 확대했다.", "가람기업은 국내사업을 확대했다. 해외사업을 확대할 계획이다."),
    ("가람기업은 국내사업을 확대했다.", "가람기업은 국내사업을 확대했고 해외사업을 확대할 계획이다."),
    ("가람기업은 국내사업을 확대한다.", "가람기업은 해외사업을 확대할 계획이다."),
    ("가람기업은 검사장비를 공급한다.", "가람기업은 검사장비를 공급하며, 해외사업을 확대할 계획이다."),
    ("가람기업은 해외사업 확대를 목표로 한다.", "가람기업은 해외사업을 확대하고자 합니다."),
    ("가람기업은 해외사업을 확대할 계획이다.", "가람기업은 해외사업을 확대하고자 합니다."),
    ("가람기업은 '해외사업을 확대한다'는 목표를 밝혔다.", "가람기업은 해외사업을 확대하고자 합니다."),
    ("가람기업은 '해외사업을 확대하고자 한다'고 밝혔다.", "가람기업은 해외사업을 확대하고자 한다."),
    ("가람기업은 '해외사업을 확대한다'고 밝혔다.", "가람기업은 '해외사업을 확대한다'고 밝혔다."),
    ("가람기업은 「새봄 서비스」를 출시했다.", "가람기업은 「새봄 서비스」를 출시했다. 해외사업 확대가 목표다."),
    ("가람기업은 '기존' 서비스를 출시했다.", "가람기업은 '새봄' 서비스를 출시할 계획이다."),
    ("가람기업은 '시장 선도' 상품을 출시했다.", "가람기업은 공급망금융시장을 선도하고자 한다. '시장 선도' 상품을 출시했다."),
    ("가람기업은 해외사업을 확대한다.", "고객만족을 최우선 목표로 해외사업을 확대한다."),
    ("가람기업은 해외사업을 확대하지 않는다.", "가람기업은 해외사업을 확대할 계획이다."),
    ("가람기업은 이미 신제품을 출시했다. 해외사업은 확대할 계획이다.", "가람기업은 신제품을 출시했다. 해외사업을 확대할 예정이다."),
])
def test_unrelated_plans_current_facts_and_product_quotes_are_not_removed(candidate, source):
    assert modality_problem(candidate, {"공시": source}) == ""


def test_unrelated_plan_in_candidate_does_not_excuse_asserted_target():
    source = "가람기업은 해외사업을 확대할 계획이다."
    candidate = "가람기업은 해외사업을 확대한다. 국내매장을 개점할 계획이다."
    assert modality_problem(candidate, {"공시": source}) == MODALITY_PLAN_ASSERTED


def test_other_activity_or_other_company_is_not_independent_direct_support():
    candidate = "가람기업은 해외사업을 확대했다."
    planned = "가람기업은 해외사업을 확대할 계획이다."
    for unrelated in ("가람기업은 국내사업을 확대했다.", "나래기업은 해외사업을 확대했다."):
        assert modality_problem(candidate, {"계획": planned, "다른실적": unrelated}) == MODALITY_PLAN_ASSERTED


def test_quotes_and_goal_markers_do_not_become_direct_achievement_support():
    candidate = "가람기업은 해외사업을 확대한다."
    source = "가람기업은 '해외사업을 확대한다'는 목표를 밝혔다."
    assert modality_problem(candidate, {"공시": source}) == MODALITY_PLAN_ASSERTED


def test_distinct_product_name_prevents_same_generic_service_alignment():
    planned = "가람기업은 '새봄' 서비스를 출시할 계획이다."
    assert modality_problem("가람기업은 '새봄' 서비스를 출시했다.", {"공시": planned}) == MODALITY_PLAN_ASSERTED
    assert modality_problem("가람기업은 '기존' 서비스를 출시했다.", {"공시": planned}) == ""


def test_sources_are_read_only_and_empty_inputs_have_no_modality_judgment():
    sources = {"공시": "가람기업은 해외사업을 확대할 계획이다."}
    before = dict(sources)
    assert modality_problem("", sources) == ""
    assert modality_problem("가람기업은 해외사업을 확대한다.", {}) == ""
    assert sources == before


def test_omitted_activity_object_does_not_align_by_company_subject_alone():
    assert modality_problem("가람기업은 확대했다.", {"공시": "가람기업은 확대할 계획이다."}) == ""


@pytest.mark.parametrize("subject", ("당행", "당사", "회사"))
def test_report_company_subject_cannot_borrow_a_competitors_achievement(subject):
    claim = f"{subject}는 공급망금융시장을 선도한다."
    plan = f"{subject}는 공급망금융시장을 선도할 계획이다."
    assert modality_problem(claim, {
        "계획": plan, "다른회사": "경쟁사는 공급망금융시장을 선도한다.",
    }) == MODALITY_PLAN_ASSERTED
    assert modality_problem(claim, {"계획": plan, "직접실적": claim}) == ""


def test_report_company_aliases_keep_direct_current_evidence():
    assert modality_problem("당사는 해외사업을 확대한다.", {
        "계획": "당사는 해외사업을 확대할 계획이다.",
        "직접실적": "회사는 해외사업을 확대한다.",
    }) == ""


def test_subject_omission_does_not_supply_another_company_as_direct_support():
    assert modality_problem("해외사업을 확대한다.", {
        "계획": "해외사업을 확대할 계획이다.",
        "다른회사": "경쟁사는 해외사업을 확대한다.",
    }) == MODALITY_PLAN_ASSERTED
