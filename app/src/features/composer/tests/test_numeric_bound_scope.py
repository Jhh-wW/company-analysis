"""숫자만 같아도 상한·하한이나 연결 기준이 다르면 결속하지 않는다."""
import pytest

from src.features.composer.grounding import grounding_problem


def _proof(candidate, source, *, metric="부채비율", value="150% 이하", source_value=None):
    return {
        "검증근거": {"수치": [{
            "표현": candidate, "항목": metric, "근거": "source",
            "원문": source, "원문항목": metric,
            "후보값": value, "원문값": source_value if source_value is not None else value,
        }]},
    }


def test_consolidated_ratio_with_upper_bound_has_literal_numeric_proof():
    source = "부채비율(연결기준) 150% 이하 유지"
    candidate = "회사는 부채비율을 연결기준 150% 이하로 유지한다."
    assert grounding_problem(candidate, {"source":source}, _proof(candidate, source)) == ""


@pytest.mark.parametrize("bound", ("이하", "이상", "미만", "초과"))
def test_closed_comparison_words_are_preserved(bound):
    source = f"가입금액 8억원 {bound}"
    candidate = f"회사의 가입금액은 8억원 {bound}로 정해져 있다."
    assert grounding_problem(candidate, {"source":source}, _proof(
        candidate, source, metric="가입금액", value=f"8억원 {bound}",
    )) == ""


@pytest.mark.parametrize("source_bound,candidate_bound", (("이하","이상"),("미만","이하"),("초과","이상")))
def test_reversed_or_changed_inclusivity_is_rejected(source_bound, candidate_bound):
    source = f"가입금액 8억원 {source_bound}"
    candidate = f"회사의 가입금액은 8억원 {candidate_bound}로 정해져 있다."
    assert grounding_problem(candidate, {"source":source}, _proof(
        candidate, source, metric="가입금액", value=f"8억원 {candidate_bound}", source_value=f"8억원 {source_bound}",
    )) == "semantic_grounding_invalid"


def test_bare_value_cannot_hide_changed_comparison_words():
    source = "가입금액 8억원 이하"
    candidate = "회사의 가입금액은 8억원 이상이다."
    assert grounding_problem(candidate, {"source":source}, _proof(
        candidate, source, metric="가입금액", value="8억원",
    )) == "semantic_grounding_invalid"


@pytest.mark.parametrize("basis", ("별도기준", "개별기준", ""))
def test_changed_or_missing_consolidation_basis_is_rejected(basis):
    source = "부채비율(연결기준) 150% 이하 유지"
    candidate = f"회사는 부채비율을 {basis} 150% 이하로 유지한다."
    assert grounding_problem(candidate, {"source":source}, _proof(candidate, source)) == "semantic_grounding_invalid"


@pytest.mark.parametrize("suffix", ("", " "))
def test_cropped_quote_cannot_remove_source_upper_bound(suffix):
    source = "가입금액 8억원 이하"
    quote = "가입금액 8억원" + suffix
    candidate = "회사의 가입금액은 8억원이다."
    assert grounding_problem(candidate, {"source":source}, _proof(
        candidate, quote, metric="가입금액", value="8억원",
    )) == "semantic_grounding_invalid"


def test_other_metric_cannot_lend_its_bound_value():
    source = "계약금액 8억원 이하, 가입금액 5억원"
    candidate = "회사의 가입금액은 8억원 이하이다."
    assert grounding_problem(candidate, {"source":source}, _proof(
        candidate, source, metric="가입금액", value="8억원 이하",
    )) == "semantic_grounding_invalid"


def test_natural_comparison_copula_is_bound_to_source():
    source = "가입금액은 8억원 이하입니다."
    candidate = "회사의 가입금액은 8억원 이하이다."
    assert grounding_problem(candidate, {"source":source}, _proof(
        candidate, source, metric="가입금액", value="8억원 이하",
    )) == ""


@pytest.mark.parametrize("basis", ("별도기준", "개별기준", ""))
def test_basis_before_metric_cannot_be_changed_or_dropped(basis):
    source = "연결기준 부채비율 150% 이하 유지"
    candidate = f"회사는 {basis} 부채비율을 150% 이하로 유지한다."
    assert grounding_problem(candidate, {"source":source}, _proof(candidate, source, value="150%")) == "semantic_grounding_invalid"


def test_same_basis_before_metric_is_preserved():
    source = "연결기준 부채비율 150% 이하 유지"
    candidate = "회사는 연결기준 부채비율을 150% 이하로 유지한다."
    assert grounding_problem(candidate, {"source":source}, _proof(candidate, source, value="150%")) == ""


def test_basis_of_other_metric_is_not_attached_to_ratio():
    source = "연결기준 매출액 10억원, 부채비율 150% 이하 유지"
    candidate = "회사는 부채비율을 150% 이하로 유지한다."
    assert grounding_problem(candidate, {"source":source}, _proof(candidate, source, value="150%")) == ""


def test_conflicting_adjacent_bases_cannot_be_used_as_proof():
    source = "연결기준 별도기준 부채비율 150% 이하 유지"
    candidate = "회사는 별도기준 부채비율을 150% 이하로 유지한다."
    assert grounding_problem(candidate, {"source":source}, _proof(candidate, source, value="150%")) == "semantic_grounding_invalid"


@pytest.mark.parametrize("basis", ("별도기준", "개별기준", ""))
def test_basis_before_leading_ratio_cannot_be_changed_or_dropped(basis):
    source = "연결기준 175% 부채비율"
    candidate = f"{basis} 175% 부채비율"
    assert grounding_problem(candidate, {"source": source}, _proof(
        candidate, source, value="175%",
    )) == "semantic_grounding_invalid"


def test_same_basis_before_leading_ratio_is_preserved():
    source = "연결기준 175% 부채비율"
    candidate = "회사는 연결기준 175% 부채비율을 기록했다."
    assert grounding_problem(candidate, {"source": source}, _proof(
        candidate, source, value="175%",
    )) == ""


@pytest.mark.parametrize("prefix", ("연결기준 매출액 9억원, ", "연결기준 매출액. ", "연결기준 20%, "))
def test_other_metric_or_number_or_sentence_cannot_lend_basis_to_leading_ratio(prefix):
    source = prefix + "175% 부채비율"
    candidate = "회사는 175% 부채비율을 기록했다."
    assert grounding_problem(candidate, {"source": source}, _proof(
        candidate, source, value="175%",
    )) == ""


def test_leading_money_value_keeps_existing_metric_grammar():
    source = "8억원 가입금액"
    candidate = "회사는 8억원 가입금액을 정했다."
    assert grounding_problem(candidate, {"source": source}, _proof(
        candidate, source, metric="가입금액", value="8억원",
    )) == ""
