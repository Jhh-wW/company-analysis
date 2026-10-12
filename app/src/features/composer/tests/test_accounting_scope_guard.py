"""회계 범위 한정의 자기 인용과 검증된 실적표 경계."""
import pytest

from src.features.composer.accounting_scope_guard import accounting_scope_problem
from src.features.composer.grounding_constants import TABLE_SOURCE_ID


@pytest.mark.parametrize('basis', ['별도 기준', '연결 기준', '개별 재무제표 기준'])
def test_added_scope_is_rejected_for_product_description(basis):
    assert accounting_scope_problem(
        f'{basis} 매출 구성표에서 타이어 제품은 승용차용으로 공급된다.',
        {'123': '품목 | 구체적용도 ; 타이어 | 승용차용'},
    ) == 'accounting_scope_unbound'


@pytest.mark.parametrize('basis', ['별도 기준', '연결 기준', '개별 재무제표 기준'])
def test_literal_scope_preserves_the_same_activity(basis):
    text = f'{basis} 매출 구성표에서 타이어 제품은 승용차용으로 공급된다.'
    assert accounting_scope_problem(text, {'1': f'{basis}\n품목 | 용도 ; 타이어 | 승용차용'}) == ''


@pytest.mark.parametrize('claim', [
    '연결 기준으로 제품 판매대가를 현금으로 수취한다.',
    '연결 매출액은 100억원이다.',
])
def test_consolidated_company_subject_preserves_its_own_activity(claim):
    source = '연결회사는 제품 판매대가를 현금으로 수취한다. 연결회사 매출액은 100억원이다.'
    assert accounting_scope_problem(claim, {'1': source}) == ''


def test_consolidated_source_cannot_be_replaced_with_separate_scope_in_product_table():
    assert accounting_scope_problem('별도 기준으로 제품 판매대가를 현금으로 수취한다.', {
        '1': '품목 | 매출유형 ; 제품 | 판매\n연결회사는 제품 판매대가를 현금으로 수취한다.',
    }) == 'accounting_scope_unbound'


@pytest.mark.parametrize('claim', [
    '회사는 고객에게 별도로 유지보수 비용을 청구한다.',
    '회사는 별도 계약으로 정비 서비스를 제공한다.',
    '회사는 별도 서비스를 제공한다.',
    '정비 사업부문에서는 서비스 대가를 현금으로 수취한다.',
    '연결회사는 부품회사 지분을 취득하였다.',
])
def test_nonaccounting_words_do_not_create_scope_requirements(claim):
    assert accounting_scope_problem(claim, {'1': claim}) == ''


def test_division_heading_is_not_separate_financial_scope():
    assert accounting_scope_problem('별도 기준으로 정비 대가를 현금으로 수취한다.', {
        '1': '[정비 사업부문]\n품목 | 매출유형 ; 정비 | 서비스\n정비 대가를 현금으로 수취한다.',
    }) == 'accounting_scope_unbound'


def test_unrelated_own_source_does_not_lend_accounting_scope():
    assert accounting_scope_problem('별도 기준으로 타이어를 승용차용으로 제공한다.', {
        '1': '품목 | 용도 ; 타이어 | 승용차용\n타이어를 승용차용으로 제공한다.',
        '2': '별도 재무제표에는 차입금이 기재된다.',
    }) == 'accounting_scope_unbound'


def test_unrelated_separate_scope_does_not_remove_direct_consolidated_scope():
    assert accounting_scope_problem('연결 기준으로 제품 판매대가를 현금으로 수취한다.', {
        '1': '연결회사는 제품 판매대가를 현금으로 수취한다.',
        '2': '별도 재무제표에는 차입금이 기재된다.',
    }) == ''


@pytest.mark.parametrize('scope', ['연결', '별도', '개별'])
def test_financial_header_or_postfix_is_literal_scope(scope):
    assert accounting_scope_problem(f'{scope} 매출액은 100억원이다.', {
        '1': f'{scope} 재무제표\n매출액은 100억원이다.',
    }) == ''
    assert accounting_scope_problem(f'{scope} 매출액은 100억원이다.', {
        '1': f'매출액({scope}) 100억원',
    }) == ''


def test_global_table_cannot_lend_scope_to_an_unbound_product_claim():
    claim = '별도 기준 매출 구성표에서 타이어 제품은 승용차용으로 공급된다.'
    source = '품목 | 구체적용도 ; 타이어 | 승용차용'
    assert accounting_scope_problem(claim, {'1': source, TABLE_SOURCE_ID: '별도 재무제표\n매출액100억원'}) == 'accounting_scope_unbound'


@pytest.mark.parametrize('metric', ['매출액', '영업이익', '당기순이익'])
def test_only_verified_numeric_table_binding_allows_its_scope(metric):
    claim = f'연결 {metric}의 누적 증가율은 10%이다.'
    sources = {'1': f'구분 | 2023년 | 2025년 ; {metric} | 100 | 110',
               TABLE_SOURCE_ID: f'연결 재무제표\n{metric} 2023년100 2025년110 증가율10%'}
    assert accounting_scope_problem(claim, sources) == 'accounting_scope_unbound'
    assert accounting_scope_problem(claim, sources, allow_bound_table=True) == ''


def test_explicit_separate_scope_is_not_consolidated_even_when_table_is_bound():
    assert accounting_scope_problem('별도 매출액 증가율은 10%이다.', {
        TABLE_SOURCE_ID: '연결 재무제표\n매출액 증가율10%',
    }, allow_bound_table=True) == 'accounting_scope_unbound'


def test_scope_free_product_information_remains_unchanged():
    assert accounting_scope_problem('타이어 제품은 승용차용으로 공급된다.', {
        '123': '품목 | 구체적용도 ; 타이어 | 승용차용',
    }) == ''


@pytest.mark.parametrize('claim,source', [
    ('별도 기준으로 원재료 구매 계약을 체결한다.', '회사는 원재료 구매 계약을 체결한다.'),
    ('연결 및 별도 재무제표에서 파생부채 결제완료가 확인된다.', '연결회사는 파생부채를 결제했다. 회사는 파생부채를 결제했다.'),
])
def test_financial_contract_prose_parent_scope_is_outside_new_table_guard(claim, source):
    # 부모 금융 절의 scope 운송이 없는 산문은 기존 의미검수가 계속 맡는다.
    assert accounting_scope_problem(claim, {'1': source}) == ''


@pytest.mark.parametrize('table', [
    '품목 | 용도 ; 압축기 | 냉각용',
    '품목 | 구체적용도 ; 감지기 | 상태 측정',
    '제품명 | 적용용도 ; 제어기 | 온도 제어',
    '품목 | 매출액 ; 포장지 | 100',
    '구분 | 2024년 | 2025년 ; 매출액 | 100 | 110',
])
def test_table_scope_cannot_be_invented_without_brand_or_division_name(table):
    assert accounting_scope_problem('별도 기준으로 제품 현황을 공시했다.', {'1': table}) == 'accounting_scope_unbound'


def test_table_words_in_prose_are_not_structured_table():
    source = '회사 설명에서 품목과 용도, 매출액을 확인한다.'
    assert accounting_scope_problem('별도 기준으로 회사 설명을 확인한다.', {'1': source}) == ''


def test_product_service_revenue_header_is_a_product_table():
    assert accounting_scope_problem('별도 기준으로 정비 서비스 매출이 발생한다.', {
        '1': '제품·서비스 | 매출액 ; 정비 서비스 | 20',
        '2': '별도 서비스 계약으로 장치 유지보수를 제공한다.',
    }) == 'accounting_scope_unbound'


def test_valid_total_revenue_number_cannot_lend_scope_to_product_table():
    assert accounting_scope_problem('별도 기준으로 타이어 매출액은 100억원이다.', {
        '1': '품목 | 매출액 ; 타이어 | 100억원',
        TABLE_SOURCE_ID: '별도 재무제표\n매출액 | 2025년 | 100억원',
    }, allow_bound_table=True) == 'accounting_scope_unbound'


def test_direct_product_table_scope_is_preserved_despite_global_other_scope():
    assert accounting_scope_problem('별도 기준으로 타이어 매출액은 100억원이다.', {
        '1': '별도 재무제표\n품목 | 매출액 ; 타이어 | 100억원',
        TABLE_SOURCE_ID: '연결 재무제표\n매출액 | 2025년 | 100억원',
    }, allow_bound_table=True) == ''
