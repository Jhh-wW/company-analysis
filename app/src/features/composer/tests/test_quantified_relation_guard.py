"""개별로 맞는 두 사실을 합쳐 전체 배당 관계를 만드는 회귀를 막는다."""
import pytest

from src.features.composer.grounding import grounding_problem
from src.features.composer.quantified_relation_constants import QUANTIFIED_DIVIDEND_UNBOUND
from src.features.composer.quantified_relation_guard import quantified_dividend_problem


CLAIM = "투자부문은 577개 종속회사로부터 배당수익을 수취한다."


@pytest.mark.parametrize("source", [
    "연결대상 종속회사는 총 577개사입니다. 투자부문은 종속회사와 기타 투자사로부터 배당수익을 수취합니다.",
    "회사는 577개 종속회사로부터 정보를 수집합니다. 배당수익을 수취합니다.",
    "회사는 577개 종속회사 중 5개사로부터 배당수익을 수취합니다.",
    "회사는 577개 종속회사로부터 배당수익을 수취하지 않습니다.",
    "회사는 577개 종속회사로부터 배당수익을 수취할 예정입니다.",
    "회사는 577개 자회사로부터 배당수익을 수취합니다.",
    "회사는 50개 종속회사로부터 배당수익을 수취합니다.",
    "577개 종속회사로부터 배당수익을 다른 회사가 수취합니다.",
    "577개 종속회사로부터 배당수익을 수취하는 것은 아니다.",
    "577개 종속회사로부터 배당수익을 수취할 것으로 예상한다.",
    "577개 종속회사가 다른 회사에 배당금을 지급했다.",
])
def test_count_and_dividend_must_bind_in_own_source(source):
    assert quantified_dividend_problem(CLAIM, {"1": source}) == QUANTIFIED_DIVIDEND_UNBOUND
    assert grounding_problem(CLAIM, {"1": source}, {"결과": "참"}) == QUANTIFIED_DIVIDEND_UNBOUND


@pytest.mark.parametrize("source", [
    "당사는 577개 종속회사로부터 배당수익을 수취합니다.",
    "당사는 종속회사 577개사로부터 배당금을 받았습니다.",
    "577개 종속회사가 당사에 배당금을 지급했습니다.",
    "당사는 577개 종속회사로부터 배당수익을 수취했고, 향후 배당 계획을 수립했다.",
])
def test_explicit_same_group_receipt_or_payment_is_not_rejected(source):
    assert quantified_dividend_problem(CLAIM, {"1": source}) == ""
    # 이 좁은 검사의 통과가 기존 수치 검증을 면제하지 않는다.
    assert grounding_problem(CLAIM + " 배당수익은 100억원이다.", {"1": source}, {}) != ""


@pytest.mark.parametrize("text", [
    "연결대상 종속회사는 총 577개사이다.",
    "회사는 종속회사와 기타 투자사로부터 배당수익을 수취한다.",
    "577개 종속회사를 통해 여러 사업을 영위한다.",
    "577개 종속회사로부터 배당수익을 수취하지 않는다.",
    "577개 종속회사로부터 배당수익을 수취할 예정이다.",
])
def test_separate_supported_claims_remain_in_scope_of_existing_checks(text):
    assert quantified_dividend_problem(text, {"1": text}) == ""


def test_split_citations_do_not_invent_quantified_relationship():
    assert quantified_dividend_problem(CLAIM, {
        "1": "연결대상 종속회사는 577개사입니다.",
        "2": "종속회사로부터 배당수익을 수취합니다.",
    }) == QUANTIFIED_DIVIDEND_UNBOUND


def test_later_plan_does_not_hide_earlier_unsupported_actual_receipt():
    text = "577개 종속회사로부터 배당수익을 수취했고, 향후 배당 계획을 수립했다."
    source = "연결대상 회사는 577개사다. 투자부문은 배당수익을 수취한다."
    assert quantified_dividend_problem(text, {"1": source}) == QUANTIFIED_DIVIDEND_UNBOUND
