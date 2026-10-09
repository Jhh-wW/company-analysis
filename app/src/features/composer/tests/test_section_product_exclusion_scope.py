"""자기 제품표에 속한 부문을 외부 부문으로 뒤집지 않는다."""
import json

import pytest

from src.features.composer.business_population_scope import section_product_exclusion_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.scope_guard import scope_problem
from src.features.composer.verify import _apply_grounding


SOURCE = '[장비 부문]\n사업부문 | 품 목 | 구체적 용도\n장비 등 | 센서 모듈 | 산업설비용'


@pytest.mark.parametrize('claim', [
    '장비 부문 외에도 센서를 공급한다.',
    '장비 부문 외의 상품인 모듈을 판매한다.',
    '장비 부문이 아닌 센서를 공급한다.',
    '장비 부문 밖에서 모듈을 판매한다.',
    '장비 부문에 속하지 않는 센서를 판매한다.',
    '장비 부문에서 센서를 판매하며 장비 부문 외에도 모듈을 판매한다.',
])
def test_same_section_product_cannot_be_placed_outside(claim):
    assert section_product_exclusion_problem(claim, {'1': SOURCE}) == SCOPE_CONDITION_UNBOUND
    assert scope_problem(claim, {'1': SOURCE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('claim', [
    '장비 부문에서 센서와 모듈을 공급한다.',
    '장비 부문 외에도 서비스 부문에서 유지보수를 제공한다.',
    '장비 부문 외에는 센서를 공급하지 않는다.',
    '장비 부문 외에도 센서를 공급한다는 설명은 사실이 아니다.',
    '센서는 장비 부문의 상품이며 서비스 부문과 비교된다.',
    '장비 부문에서 센서를 판매하며 서비스 부문에서는 유지보수를 제공한다.',
])
def test_valid_section_and_other_products_keep_existing_contract(claim):
    assert section_product_exclusion_problem(claim, {'1': SOURCE}) == ''


def test_same_product_with_another_explicit_section_is_preserved():
    other = '[서비스 부문]\n품목 | 용도\n센서 | 유지보수용'
    assert section_product_exclusion_problem('장비 부문 외에도 센서를 공급한다.', {'1': SOURCE, '2': other}) == ''


def test_heading_without_product_table_cannot_create_a_constraint():
    assert section_product_exclusion_problem('장비 부문 외에도 센서를 공급한다.', {'1': '[장비 부문]\n센서 산업 전망'}) == ''


def test_product_type_named_goods_does_not_replace_item_header():
    source = '[장비 부문]\n사업부문 | 매출유형 | 품 목 | 용도\n장비 등 | 상 품 | 센서 모듈 등 | 산업설비용'
    assert section_product_exclusion_problem('장비 부문 외에도 센서를 판매한다.', {'1': source}) == SCOPE_CONDITION_UNBOUND


def test_model_true_is_rejected_without_changing_own_source():
    claim = '장비 부문 외에도 센서를 공급한다.'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]}, ensure_ascii=False)
    diagnostics = []
    sources = {'1': SOURCE}
    result = _apply_grounding(
        raw, {1: '참'}, {1: (claim, sources)}, diagnostics=diagnostics,
        diagnostic_contexts={1: ('portfolio', '본문', claim)},
        claim_slots_by_number={1: 'portfolio:product_role'},
    )
    assert result[1] != '참'
    assert sources == {'1': SOURCE}
    assert any(item.get('reason_code') == SCOPE_CONDITION_UNBOUND for item in diagnostics)


@pytest.mark.parametrize('claim', [
    '장비 부문 외에 센서를 공급한다.',
    '장비 부문 이외에 모듈을 판매한다.',
    '장비 부문 이외의 센서를 공급한다.',
    '장비 부문을 제외한 센서를 판매한다.',
    '장비 부문 제외한 센서를 공급한다.',
    '장비 부문과 별개로 센서를 판매한다.',
    '장비 부문과는 별개의 센서를 공급한다.',
    '비장비 부문에서 센서를 공급한다.',
    '비장비 부문의 센서를 판매한다.',
    '회사는 비장비 부문에서도 모듈을 공급한다.',
])
def test_explicit_outside_variants_keep_same_product_section_constraint(claim):
    assert section_product_exclusion_problem(claim, {'1': SOURCE}) == SCOPE_CONDITION_UNBOUND
    assert scope_problem(claim, {'1': SOURCE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('claim', [
    '장비 부문 외에 서비스 부문에서 유지보수를 제공한다.',
    '장비 부문 이외에 센서를 공급하지 않는다.',
    '장비 부문과 별개로 센서를 판매한다는 설명은 사실이 아니다.',
    '비전 부문에서 센서를 공급한다.',
    '예비장비 부문에서 모듈을 공급한다.',
    '장비 부문에서 센서를 공급한다.',
])
def test_outside_variants_do_not_create_other_product_or_word_constraints(claim):
    assert section_product_exclusion_problem(claim, {'1': SOURCE}) == ''


@pytest.mark.parametrize('claim', [
    '장비 부문 외에 센서를 공급한다.',
    '장비 부문 이외의 센서를 공급한다.',
    '장비 부문과 별개로 센서를 공급한다.',
    '비장비 부문에서 센서를 공급한다.',
])
def test_outside_product_supported_by_a_different_explicit_section_is_preserved(claim):
    other = '[서비스 부문]\n품목 | 용도\n센서 | 유지보수용'
    assert section_product_exclusion_problem(claim, {'1': SOURCE, '2': other}) == ''


def test_outside_clause_does_not_borrow_a_product_from_the_next_sentence():
    assert section_product_exclusion_problem(
        '장비 부문 외에 유지보수 사업을 한다. 장비 부문에서는 센서를 공급한다.',
        {'1': SOURCE},
    ) == ''


def test_nested_table_row_does_not_move_products_outside_its_section_heading():
    sources = {
        '1': '[장비 부문]\n사업부문 | 매출유형 | 품 목 | 당기\n판매, 서비스 및 부동산 | 상품 | 센서, 모듈 등 | 100',
        '2': '[장비 부문]\n사업부문 | 매출유형 | 품 목 | 용도\n기 타 | 임대업 | 연구용역, 임대 등 | 사무실 등',
    }
    claim = '장비 부문 외에 판매·서비스 및 부동산 사업부문에서는 센서·모듈 등 상품을 취급하고 있으며, 기타 부문에서는 연구용역 및 임대 서비스도 제공한다.'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]}, ensure_ascii=False)
    assert section_product_exclusion_problem(claim, sources) == SCOPE_CONDITION_UNBOUND
    result = _apply_grounding(
        raw, {1: '참'}, {1: (claim, sources)},
        diagnostic_contexts={1: ('portfolio', '본문', claim)},
        claim_slots_by_number={1: 'portfolio:product_role'},
    )
    assert result[1] != '참'
