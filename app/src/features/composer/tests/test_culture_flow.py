"""문화 열의 암묵적 의미와 미래 사업목표 경계에 대한 소수 회귀."""
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.culture_constants import CULTURE_EVIDENCE_SCOPE_MISMATCH
from src.features.composer.culture_guard import culture_flow_problem


@pytest.fixture(scope="module")
def public_cases():
    return json.loads(Path(__file__).with_name("culture_flow_public_cases.json").read_text(encoding="utf-8"))


def test_preserved_public_evidence_hashes(public_cases):
    assert public_cases["source_evidence_sha256"] == "7567f8270c6253fa2bd16799ad33f23eb5f3e700ce4ef4e884caa9b84f020036"
    for source in public_cases["sources"].values():
        assert hashlib.sha256(source["text"].encode()).hexdigest() == source["exact_sha256"]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_actual_three_culture_rows(public_cases, index):
    case = public_cases["cases"][index]
    sources = {key: public_cases["sources"][key]["text"] for key in case["citations"]}
    assert culture_flow_problem(case["cells"], sources) == case["expected_problem"]


@pytest.mark.parametrize("principle", [
    "상생금융 추진 계획", "향후 상생금융 확대", "상생금융을 추진할 예정이다", "상생금융 확대 목표",
])
def test_actual_business_goal_explicitly_presented_as_future_stays(public_cases, principle):
    cells = ["금융의 사회적 책임", principle, ""]
    assert culture_flow_problem(cells, {"219": public_cases["sources"]["219"]["text"]}) == ""


@pytest.mark.parametrize("principle,example", [
    ("상생금융 추진", "향후 검토 계획"),
    ("상생금융 추진 계획이 아니라 현재 원칙", ""),
    ("상생금융 추진 목표를 조직의 운영 원칙으로 삼는다", ""),
])
def test_plan_word_elsewhere_or_denied_does_not_excuse_current_principle(public_cases, principle, example):
    assert culture_flow_problem(["금융의 사회적 책임", principle, example],
                                {"219": public_cases["sources"]["219"]["text"]}) == CULTURE_EVIDENCE_SCOPE_MISMATCH


@pytest.mark.parametrize("extra", [
    "타사의 조직문화는 안전 검토이다.",
    "회사는 조직문화 행사를 지원했다.",
    "회사의 의사결정 절차는 이사회 승인이다.",
    "우리의 조직문화는 공개하지 않았다.",
])
def test_unrelated_or_unavailable_culture_does_not_rescue_goal(public_cases, extra):
    case = public_cases["cases"][2]
    source = public_cases["sources"]["219"]["text"]
    assert culture_flow_problem(case["cells"], {"공식공시": source + "\n" + extra}) == CULTURE_EVIDENCE_SCOPE_MISMATCH


@pytest.mark.parametrize("value,source", [
    ("지역 기여", "회사는 지역 기여를 확대할 계획이다."),
    ("고객 신뢰", "새빛산업은 고객 신뢰 증진을 경영목표로 정했다."),
    ("공동 성장", "회사는 공동 성장을 도모하고자 한다."),
])
def test_goal_boundary_does_not_depend_on_bank_company_or_product(value, source):
    assert culture_flow_problem([value, "새로운 사업 확대", ""], {"문서": source}) == CULTURE_EVIDENCE_SCOPE_MISMATCH


@pytest.mark.parametrize("cells,source", [
    (["협업", "공동 검토 후 승인", ""], "회사의 의사결정 절차는 공동 검토 후 승인이다."),
    (["책임", "담당자에게 결정권한 위임", ""], "담당자에게 결정권한을 위임한다."),
    (["안전", "정기회의에서 위험 검토", ""], "위원회 정기회의에서 위험을 검토한다."),
    (["도전", "새로운 시도 장려", ""], "회사는 도전하는 인재를 공식 인재상으로 정의했다."),
    (["고객신뢰", "담당자자율", ""], "고객신뢰를 높이고자 한다. 회사의 일하는 방식은 담당자자율이다."),
    (["조직문화 지원", "교육 프로그램 제공", ""], "회사는 조직문화 지원 교육 프로그램을 제공한다."),
])
def test_normal_procedures_values_and_ordinary_statements_stay(cells, source):
    assert culture_flow_problem(cells, {"원문": source}) == ""


@pytest.mark.parametrize("extra", [
    "타사의 일하는 방식은 담당자자율이다.",
    "다른 회사의 일하는 방식은 담당자자율이다.",
    "회사의 일하는 방식은 담당자자율이 아니며 시행하지 않는다.",
    "담당자자율이라는 일하는 방식은 미도입 상태다.",
])
def test_same_words_in_other_company_or_negative_procedure_do_not_rescue_goal(extra):
    source = "고객신뢰를 높이고자 한다. " + extra
    assert culture_flow_problem(["고객신뢰", "담당자자율", ""], {"자료": source}) == CULTURE_EVIDENCE_SCOPE_MISMATCH


@pytest.mark.parametrize("source", [
    "고객신뢰 향상은 별도 경영목표가 아니다.",
    "고객신뢰 향상을 위한 사업을 할 계획은 없다.",
])
def test_negated_goal_is_not_treated_as_positive_goal_evidence(source):
    assert culture_flow_problem(["고객신뢰", "담당자자율", ""], {"자료": source}) == ""


@pytest.mark.parametrize("cells,sources", [
    (["경제적 공헌", "지원 확대", ""], {"자료": "상생금융을 추진할 계획이다."}),
    (["신뢰", "협력", ""], {}),
    (["값", "원칙"], {"자료": "값을 늘릴 계획이다."}),
])
def test_unbound_paraphrases_or_missing_contract_stay_unknown(cells, sources):
    # 빈 반환은 승인 신호가 아니다. 원문 누락/다른 회사의 고유명사/의미
    # 바꿔쓰기는 caller의 기존 신원·형식·의미 검수가 계속 책임진다.
    assert culture_flow_problem(cells, sources) == ""
