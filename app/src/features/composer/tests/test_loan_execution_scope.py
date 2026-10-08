"""대출 한도·실행과 용도·금융기관의 자기 원문 결속을 확인한다."""
import json
import pytest

from src.features.composer.loan_execution_scope import loan_execution_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer import verify

TABLE = (
    '금융기관 | 약정내용 | 한도금액 | 실행금액 ; '
    '가온은행 | 운영자금대출 | 100 | - ; '
    '가온은행 | 시설자금대출 | - | 90 ; '
    '나래은행 | 운영자금대출 | 200 | 70'
)


@pytest.mark.parametrize('claim', [
    '회사는 가온은행에서 운영자금대출을 받고 있다.',
    '회사는 가온은행에서 운영자금대출 및 시설자금대출을 받고 있다.',
    '회사는 가온은행의 운영자금대출을 실제 차입했다.',
    '회사는 가온은행과 나래은행에서 운영자금대출을 받고 있다.',
    '회사는 별빛은행으로부터 운영자금대출을 받고 있다.',
    '가온은행의 운영자금대출 실행액은 80이다.',
])
def test_limit_and_other_rows_do_not_prove_specific_execution(claim):
    assert loan_execution_problem(claim, {'1': TABLE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('claim', [
    '회사는 나래은행에서 운영자금대출을 받고 있다.',
    '회사는나래은행에서운영자금대출을받고있다.',
    '당사는 가온은행에서 시설자금대출을 받고 있다.',
    '당사는가온은행에서시설자금대출을받고있다.',
    '나래은행에서 운영자금대출을 받고 가온은행에서 시설자금대출을 받고 있다.',
    '가온은행에서 시설자금대출을 받고 있으며 나래은행에서 운영자금대출을 받고 있다.',
    '금융기관으로부터 운영자금대출을 받고 있다.',
    '가온은행의 운영자금대출 한도는 100이다.',
    '가온은행의 운영자금대출 약정한도를 보유하고 있다.',
    '가온은행의 운영자금대출을 받을 계획이다.',
    '가온은행의 운영자금대출을 받고 있지 않다.',
    '운영자금대출 실행액은 0이다.',
    '은행은 고객에게 운영자금대출 서비스를 제공하고 있다.',
])
def test_actual_execution_limit_modality_and_financial_service_are_preserved(claim):
    assert not loan_execution_problem(claim, {'1': TABLE})


@pytest.mark.parametrize('value', ['0', '0.00', '-', '–', '—'])
def test_zero_and_dash_are_not_positive_execution(value):
    source = TABLE.replace('100 | -', f'100 | {value}')
    assert loan_execution_problem('가온은행에서 운영자금대출을 받고 있다.', {'1': source})


@pytest.mark.parametrize('source', [
    '회사는 가온은행에서 운영자금대출을 받고 있다.',
    '회사는 가온은행에서 운영자금대출을 실제 차입하고 있다.',
    '한편 회사는 가온은행에서 운영자금대출을 받고 있다.',
    '가온은행에서 당사는 운영자금대출을 받고 있다.',
])
def test_same_bank_and_purpose_direct_own_source_is_preserved(source):
    assert not loan_execution_problem(
        '회사는 가온은행에서 운영자금대출을 받고 있다.', {'1': TABLE, '2': source},
    )
    assert not loan_execution_problem('금융기관에서 운영자금대출을 받고 있다.', {'1': TABLE, '2': source})


@pytest.mark.parametrize('source', [
    '회사는 가온은행에서 시설자금대출을 받고 있다.',
    '회사는 별빛은행에서 운영자금대출을 받고 있다.',
    '회사는 가온은행에서 운영자금대출을 받을 계획이다.',
    '회사는 가온은행에서 운영자금대출을 받고 있지 않다.',
    '회사는 가온은행에서 운영자금대출을 받았다. 현재 전액 상환해 해당 차입금은 없다.',
    '고객사는 가온은행에서 운영자금대출을 받고 있다.',
    '자회사는 가온은행에서 운영자금대출을 받고 있다.',
    '가온은행에서 고객사는 운영자금대출을 받고 있다.',
    '가온은행의 운영자금대출을 고객사는 받고 있다.',
    '회사는 가온은행의 운영자금대출에 대한 지급보증을 받고 있다.',
])
def test_guarantee_foreign_owner_purpose_bank_or_modality_is_not_execution(source):
    assert loan_execution_problem('가온은행에서 운영자금대출을 받고 있다.', {'1': TABLE, '2': source})


def test_one_bank_direct_execution_does_not_cover_another_named_bank():
    source = '회사는 나래은행에서 운영자금대출을 받고 있다.'
    assert loan_execution_problem('가온은행과 나래은행에서 운영자금대출을 받고 있다.', {'1': TABLE, '2': source})


def test_direct_past_receipt_preserves_past_claim_without_proving_current_receipt():
    source = '회사는 가온은행에서 운영자금대출을 받았다.'
    assert not loan_execution_problem(source, {'1': TABLE, '2': source})
    assert loan_execution_problem(
        '회사는 가온은행에서 운영자금대출을 받고 있다.', {'1': TABLE, '2': source},
    )


@pytest.mark.parametrize('direct_source, expected_problem', [
    ('회사는 가온은행에서 운영자금대출을 받았다.', True),
    ('회사는 가온은행에서 운영자금대출을 받고 있다.', False),
])
def test_coordinated_current_receipt_binds_its_direct_source_tense(
        direct_source, expected_problem):
    table = ('금융기관 | 약정내용 | 한도금액 | 실행금액\n'
             '가온은행 | 운영자금대출 | 100 | -\n'
             '나래은행 | 시설자금대출 | 100 | 80')
    claim = ('회사는 가온은행에서 운영자금대출을 받고 '
             '나래은행에서 시설자금대출을 받고 있다.')
    assert bool(loan_execution_problem(claim, {'1': table, '2': direct_source})) == expected_problem


def test_same_source_mixed_table_and_direct_execution_is_preserved():
    claim = '회사는 가온은행에서 운영자금대출을 받고 있다.'
    assert not loan_execution_problem(claim, {'1': TABLE + '\n' + claim})


def test_unknown_execution_format_is_left_to_existing_numeric_review():
    source = TABLE.replace('100 | -', '100 | USD 70')
    assert not loan_execution_problem('가온은행에서 운영자금대출을 받고 있다.', {'1': source})


@pytest.mark.parametrize('wrapped', [False, True])
def test_model_true_is_not_execution_proof_in_common_grounding(wrapped):
    entries = [{'번호': 1, '결과': '참', '근거': ['1']}]
    raw = json.dumps({'판정': entries} if wrapped else entries, ensure_ascii=False)
    problems = {}
    original = {'1': TABLE}
    result = verify._apply_grounding(
        raw, {1: '참'}, {1: ('가온은행에서 운영자금대출을 받고 있다.', original)},
        diagnostic_contexts={1: ('operations_partners', '본문', '검수')},
        grounding_problems=problems,
    )
    assert result[1] != '참'
    assert problems[1] == SCOPE_CONDITION_UNBOUND
    assert original == {'1': TABLE}
