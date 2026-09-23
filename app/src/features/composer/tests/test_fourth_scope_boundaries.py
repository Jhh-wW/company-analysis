"""실측 표현을 복사하지 않은 원칙·특례 및 지분관계의 대칭 회귀."""

import pytest

from src.features.composer.accounting_policy_guard import accounting_policy_problem
from src.features.composer.grounding import grounding_problem
from src.features.composer.scope_guard import scope_problem

RECOGNITION_SOURCE = (
    "용역 수익은 진행기준에 따라 인식한다. 다만 1년 내 완료되는 용역은 "
    "특례를 적용하여 완료한 날에 수익으로 인식한다."
)


@pytest.mark.parametrize("candidate", [
    "모든 용역 수익은 프로젝트가 완료된 날에 인식한다.",
    "용역 매출은 프로젝트를 완료할 때 수익으로 인식되며, 1년 이내 완료되는 용역은 완료 시점에 인식된다.",
    "2년 이내 완료되는 용역 수익은 완료한 날에 인식한다.",
])
def test_completion_exception_is_not_a_general_rule(candidate):
    assert scope_problem(candidate, {"1": RECOGNITION_SOURCE}) == "scope_condition_unbound"


@pytest.mark.parametrize("candidate", [
    "용역 수익은 진행기준에 따라 인식한다.",
    "1년 이내 완료되는 용역 수익은 완료한 날에 인식한다.",
    "용역 수익은 진행기준이며, 1년 내 완료되는 용역은 완료 시점에 인식한다.",
    "프로젝트가 완료되어 고객에게 인도되었다.",
])
def test_principle_and_exact_exception_survive(candidate):
    assert scope_problem(candidate, {"1": RECOGNITION_SOURCE}) == ""


def test_direct_completion_principle_is_not_rejected():
    claim = "용역 수익은 완료한 날에 인식한다."
    assert scope_problem(claim, {"1": claim}) == ""


@pytest.mark.parametrize("exception", [
    "1년 내에 완료되는 용역에 대해서는, 완료한 날에 수익을 인식한다.",
    "1년 내에 완료되는 용역에 대해서는， 완료한 날에 수익을 인식한다.",
    "1년 내에 완료되는 용역은, 완료한 날에 수익을 인식한다.",
    "1년 내에 완료되는 용역 중, 계약조건이 확정된 경우에만, 완료한 날에 수익을 인식한다.",
    "1년 내에 완료되는 용역에 대해서는, 완료한 날에, 수익을 인식한다.",
])
def test_source_comma_does_not_detach_completion_exception_condition(exception):
    source = "용역 매출은 진행기준으로 인식한다. " + exception
    assert scope_problem("용역 매출은 완료 시점에 인식된다.", {"1": source}) == "scope_condition_unbound"


@pytest.mark.parametrize("verdict", ["참", "애매"])
def test_grounding_does_not_approve_the_comma_separated_exception_as_a_principle(verdict):
    source = (
        "용역 매출은 진행기준으로 인식한다. 1년 내에 완료되는 용역에 대해서는, "
        "완료한 날에 수익을 인식한다."
    )
    candidate = "용역 매출은 완료 시점에 인식된다."
    assert grounding_problem(candidate, {"1": source}, {"결과": verdict}) == "scope_condition_unbound"
    assert scope_problem("1년 내 완료되는 용역 수익은 완료한 날에 인식한다.", {"1": source}) == ""


def test_compound_completion_exception_keeps_its_explicit_conditions():
    source = (
        "용역 매출은 진행기준으로 인식한다. 1년 내에 완료되는 용역 중, "
        "계약조건이 확정된 경우에만, 완료한 날에 수익을 인식한다."
    )
    candidate = "계약조건이 확정된 1년 내 완료 용역의 수익은 완료한 날에 인식한다."
    assert scope_problem(candidate, {"1": source}) == ""
    assert scope_problem(candidate.replace("1년", "2년"), {"1": source}) == "scope_condition_unbound"


@pytest.mark.parametrize("boundary", [". ", "; ", "。", "\n"])
def test_open_source_condition_does_not_cross_sentence_boundary(boundary):
    source = (
        "용역 매출은 진행기준으로 인식한다. 1년 내에 완료되는 용역에 대해서는"
        + boundary + "완료한 날에 수익을 인식한다."
    )
    assert scope_problem("용역 매출은 완료 시점에 인식된다.", {"1": source}) == ""


@pytest.mark.parametrize("intermediate", [
    "고객에게 검토 결과를 통지한다, ",
    "계약조건을 검토하며 ",
    "계약서를 확인하고 ",
])
def test_completed_source_statement_does_not_lend_its_condition(intermediate):
    source = (
        "용역 매출은 진행기준으로 인식한다. 1년 내에 완료되는 용역에 대해서는, "
        + intermediate + "완료한 날에 수익을 인식한다."
    )
    assert scope_problem("용역 매출은 완료 시점에 인식된다.", {"1": source}) == ""


def test_source_condition_does_not_cross_fragment_or_independent_subject():
    principle = "용역 매출은 진행기준으로 인식한다."
    completion = "용역 수익은 완료한 날에 인식한다."
    sources = {
        "1": principle + " 1년 내에 완료되는 용역에 대해서는,",
        "2": completion,
    }
    assert scope_problem(completion, sources) == ""
    assert scope_problem(completion, {"1": sources["1"] + " " + completion}) == ""


def test_explicit_completion_principle_survives_alongside_limited_exception():
    source = (
        "프로젝트 수익은 진행기준으로 인식한다. 1년 내 완료되는 용역에 대해서는, "
        "완료한 날에 수익을 인식한다. 유지보수 용역 수익은 완료한 날에 인식한다."
    )
    assert scope_problem("유지보수 용역 수익은 완료한 날에 인식한다.", {"1": source}) == ""


def test_candidate_general_rule_is_not_excused_by_another_limited_clause():
    source = (
        "용역 매출은 진행기준으로 인식한다. 1년 내 완료되는 용역에 대해서는, "
        "완료한 날에 수익을 인식한다."
    )
    candidate = "용역 매출은 완료 시점에 인식된다. 1년 내 완료되는 용역 수익은 완료한 날에 인식한다."
    assert scope_problem(candidate, {"1": source}) == "scope_condition_unbound"


@pytest.mark.parametrize("candidate,blocked", [
    ("가람 Japan은 현재 종속기업이다.", True),
    ("가람 Japan은 제외되지 않은 현재 종속기업이다.", True),
    ("나래 Japan은 현재 종속기업이다.", False),
    ("가람 Japanese는 현재 종속기업이다.", False),
    ("나래는 과거 종속기업이었다. 가람 Japan은 현재 종속기업이다.", True),
    ("가람 Japan은 과거 종속기업이었으나 현재 종속기업에서 제외되었다.", False),
])
def test_exclusion_is_bound_to_exact_named_entity(candidate, blocked):
    source = "가람 Japan은 지분 처분에 따라 종속기업에서 제외되었다."
    assert bool(scope_problem(candidate, {"1": source})) is blocked


def test_planned_future_exclusion_does_not_erase_current_relationship():
    source = "가람 Japan은 다음 기에 종속기업에서 제외될 예정이다."
    assert scope_problem("가람 Japan은 현재 종속기업이다.", {"1": source}) == ""


@pytest.mark.parametrize("text", [
    "회사는 유형자산의 취득원가에서 감가상각누계액을 차감하여 장부금액을 정한다.",
    "회사는 1년 내 완료되는 용역에 대해 완료한 날에 수익으로 인식하는 특례를 적용한다.",
])
def test_asset_measurement_and_completion_policy_are_boilerplate(text):
    assert accounting_policy_problem(text) == "accounting_policy_boilerplate"


@pytest.mark.parametrize("text", [
    "회사는 생산설비 17억원을 취득했고 유형자산으로 인식했다.",
    "회사는 재고 평가방법을 총평균법에서 선입선출법으로 변경했다.",
    "회사는 소프트웨어 개발 용역과 콘텐츠 공급으로 매출을 올린다.",
    "콘텐츠 매출은 고객이 콘텐츠를 제공받는 시점에 수익을 인식한다.",
    "회사는 용역 제공을 완료한 날에 수익으로 인식한다.",
])
def test_specific_company_event_and_revenue_sources_survive(text):
    assert accounting_policy_problem(text) == ""
