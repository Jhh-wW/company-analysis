"""회사·상품 이름 목록 없이 조건의 같은 행/대상 결속을 대조한다."""

import pytest

from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.scope_guard import scope_problem


@pytest.mark.parametrize("name, condition", [
    ("가온운전자금", "신용등급 QX+ 이상 기업"),
    ("별빛운전자금", "연매출 10억원 이상 기업"),
    ("새싹정산서비스", "수출기업 전용"),
])
def test_same_product_and_explicit_channel_example_keep_condition(name, condition):
    sources = {"공시": f"상품명 | 주요 내용\n{name} | {condition}을 대상으로 하는 상품"}
    assert scope_problem(f"{name}는 {condition}을 대상으로 한다.", sources) == ""
    assert scope_problem(f"기업금융 채널은 예를 들어 {name}는 {condition}을 대상으로 한다고 설명한다.", sources) == ""
    assert scope_problem(f"기업금융 채널은 {condition}을 대상으로 하는 {name} 등을 제공한다.", sources) == ""


@pytest.mark.parametrize("condition", ["신용등급 QX+ 이상 기업", "연매출 10억원 이상 기업", "수출기업 전용"])
def test_product_condition_cannot_become_whole_channel_condition(condition):
    sources = {"공시": f"한빛상품 | {condition}을 대상으로 하는 상품"}
    assert scope_problem(f"기업금융 채널은 {condition}으로 운영된다.", sources) == SCOPE_CONDITION_UNBOUND
    assert scope_problem(f"기업금융 채널은 한빛상품을 포함해 {condition}으로 운영된다.", sources) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("condition", ["신용등급 QX+ 이상 기업", "연매출 10억원 이상 기업", "수출기업 전용"])
def test_direct_official_whole_channel_statement_is_not_removed(condition):
    sources = {"상품행": f"한빛상품 | {condition}을 대상으로 하는 상품",
               "공통조건": f"기업금융 채널의 공통 조건은 {condition}으로 정해져 있다."}
    assert scope_problem(f"기업금융 채널은 {condition}으로 운영된다.", sources) == ""


@pytest.mark.parametrize("first, second", [
    ("신용등급 QX+ 이상 기업", "신용등급 ZX- 이상 기업"),
    ("연매출 10억원 이상 기업", "연매출 30억원 이상 기업"),
    ("수출기업 전용", "내수기업 전용"),
])
def test_two_products_in_same_source_do_not_lend_conditions(first, second):
    sources = {"공시": f"가온상품 | {first}\n나래상품 | {second}"}
    assert scope_problem(f"나래상품은 {second}을 대상으로 한다.", sources) == ""
    assert scope_problem(f"나래상품은 {first}을 대상으로 한다.", sources) == SCOPE_CONDITION_UNBOUND
    assert scope_problem(f"가온상품이 있으며 나래상품은 {first}을 대상으로 한다.", sources) == SCOPE_CONDITION_UNBOUND


def test_matching_condition_on_unrelated_channel_is_not_global_support():
    sources = {"상품": "가온상품 | 신용등급 QX+ 이상 기업을 위한 상품",
               "다른채널": "개인금융 채널의 공통 조건은 신용등급 QX+ 이상이다."}
    assert scope_problem("기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 한다.", sources) == SCOPE_CONDITION_UNBOUND


def test_row_title_is_bound_only_within_same_source():
    source = "상품명\n주요 내용\n한빛상품\n- 신용등급 QX+ 이상 기업을 대상으로 하는 상품"
    assert scope_problem("한빛상품은 신용등급 QX+ 이상 기업을 대상으로 한다.", {"공시": source}) == ""
    # 제목이 조각 밖에 있으면 guard가 그 이름을 복원하거나 승인하지 않는다.
    assert scope_problem("기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 한다.",
                         {"제목": "한빛상품", "조건": "- 신용등급 QX+ 이상 기업을 대상으로 하는 상품"}) == SCOPE_CONDITION_UNBOUND


def test_same_value_in_different_dimensions_is_not_a_scope_match():
    sources = {"공시": "가온상품 | 가입기간 3년 이상\n나래상품 | 평가등급 QX+ 이상"}
    assert scope_problem("나래상품은 평가등급 QX+ 이상을 대상으로 한다.", sources) == ""
    assert scope_problem("나래상품은 가입기간 3년 이상을 대상으로 한다.", sources) == SCOPE_CONDITION_UNBOUND
    # 숫자값/단위의 진위는 기존 수치 검사가 맡으며 이 검사에서 지어내지 않는다.
    assert scope_problem("기업금융 채널은 가입기간 3개월 이상이다.", sources) == ""


@pytest.mark.parametrize("text", [
    "기업금융 채널은 다양한 상품을 제공한다.",
    "신용등급은 상품을 선택할 때 고려하는 요소다.",
    "전체 매출은 10억원이다.",
    "가온상품이라는 이름을 소개한다.",
])
def test_keywords_alone_do_not_remove_content(text):
    assert scope_problem(text, {"공시": "가온상품 | 신용등급 QX+ 이상 기업을 위한 상품"}) == ""


def test_real_woori_composer_fragment_lost_product_title_but_kept_local_condition():
    sources = {
        "197": "개인 및 기업·기관 대상 대출상품, 해외송금·수출입·무역금융과 같은 외환상품 및 각종 서비스를 제공한다.",
        "203": "- 당행 신용등급 BB+(SOHO 5)등급 이상 법인 및 개인사업자를 대상으로 운전 및 시설자금을 지원하는 범용 대출상품",
        "204": "- 당행 신용등급 BB+(SOHO 5)등급 이상 아래의 조건을 충족하는 법인 및 개인사업자를 대상 으로 운전 및 시설자금을 지원하는 대출상품",
    }
    bad = "기업금융 채널에서는 신용등급 BB+ 이상의 법인 및 개인사업자를 대상으로 운전자금·시설자금 대출, 무역금융, 지급보증 등의 상품을 제공한다."
    assert scope_problem(bad, sources) == SCOPE_CONDITION_UNBOUND
    assert scope_problem("기업금융 채널에서는 기업대출과 무역금융을 제공한다.", sources) == ""


def test_precise_product_claim_cannot_be_proven_without_its_missing_heading():
    sources = {"조각": "- 신용등급 QX+ 이상 기업을 대상으로 하는 상품"}
    # 빈 반환값은 검증 완료가 아니다. 없는 상품 주어는 기존 의미 검수 계약에서 다룬다.
    assert scope_problem("임의상품은 신용등급 QX+ 이상 기업을 대상으로 한다.", sources) == ""


def test_product_type_example_survives_missing_product_title():
    sources = {"조각": "- 신용등급 QX+ 이상 기업을 대상으로 운전자금을 지원하는 범용 대출상품"}
    assert scope_problem("기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 하는 범용 대출상품을 제공한다.", sources) == ""
    assert scope_problem("기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 모든 대출상품을 제공한다.", sources) == SCOPE_CONDITION_UNBOUND
    assert scope_problem("기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 하는 전체 대출상품을 제공한다.", sources) == SCOPE_CONDITION_UNBOUND


def test_bulleted_official_channel_statement_keeps_its_scope():
    sources = {"상품": "가온상품 | 신용등급 QX+ 이상 기업을 위한 상품",
               "공통": "- 기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 모든 상품을 제공한다."}
    assert scope_problem("기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 상품을 제공한다.", sources) == ""


def test_other_product_after_relative_condition_does_not_approve_it():
    sources = {"공시": "가온상품 | 신용등급 QX+ 이상\n나래상품 | 신용등급 ZX- 이상"}
    assert scope_problem("기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 하는 나래상품과 가온상품을 제공한다.", sources) == SCOPE_CONDITION_UNBOUND
