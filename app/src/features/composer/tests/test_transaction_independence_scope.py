"""계약 사실과 추가·독립 관계를 구분하며 실제 생산 진단을 확인한다."""

import json
from hashlib import sha256

import pytest

from src.features.composer.grounding_constants import TABLE_SOURCE_ID
from src.features.composer.transaction_independence_constants import TRANSACTION_INDEPENDENCE_UNBOUND
from src.features.composer.transaction_independence_scope import transaction_independence_problem
from src.features.composer.verify import _apply_grounding
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


@pytest.mark.parametrize('modifier', ['별도로도', '별개로', '추가로'])
def test_contract_existence_does_not_prove_additional_contract(modifier):
    claim = f'회사는 원재료 구매 계약을 {modifier} 체결하고 있다.'
    assert transaction_independence_problem(claim, {'a': '회사는 원재료 구매 계약을 체결하고 있다.'}) == TRANSACTION_INDEPENDENCE_UNBOUND


@pytest.mark.parametrize('claim,source', [
    ('회사는 별도의 원재료 구매 계약을 체결하였다.', '회사는 별도의 원재료 구매 계약을 체결하였다.'),
    ('회사는 원재료 구매 계약을 추가로 체결하였다.', '회사는 원재료 구매 계약을 추가로 체결하였다.'),
    ('회사는 원재료 구매 약정을 별개로 유지한다.', '회사는 원재료 구매 약정을 별개로 유지한다.'),
    ('회사는 원재료 구매 계약을 체결하고 있다.', '회사는 원재료 구매 계약을 체결하고 있다.'),
    ('회사는 별도 재무제표 기준의 원재료 구매 계약을 유지한다.', '회사는 원재료 구매 계약을 유지한다.'),
    ('회사는 연결 기준으로 원재료 구매 계약을 유지한다.', '회사는 원재료 구매 계약을 유지한다.'),
    ('회사는 별도 서비스를 제공한다.', '회사는 서비스를 제공한다.'),
])
def test_direct_relation_and_nontransaction_scope_are_preserved(claim, source):
    assert transaction_independence_problem(claim, {'a': source}) == ''


@pytest.mark.parametrize('sources', [
    {'a': '회사는 원재료 구매 계약을 체결한다. 회사는 시설 임대 계약을 별도로 체결한다.'},
    {'a': '회사는 원재료 구매 계약을 체결한다.', 'b': '회사는 시설 임대 계약을 추가로 체결한다.'},
    {'a': '회사는 원재료 구매 계약을 체결한다.', TABLE_SOURCE_ID: '회사는 원재료 구매 계약을 별도로 체결한다.'},
    {'a': '협력회사는 원재료 구매 계약을 별도로 체결한다.'},
    {'a': '회사는 원재료 구매 계약을 체결한다. 경쟁사는 원재료 구매 계약을 별도로 체결한다.'},
    {'a': '회사는 원재료 구매 계약을 별도로 체결하지 않았다.'},
])
def test_other_activity_actor_table_and_nonactual_relation_are_rejected(sources):
    assert transaction_independence_problem('회사는 원재료 구매 계약을 별도로도 체결한다.', sources) == TRANSACTION_INDEPENDENCE_UNBOUND


@pytest.mark.parametrize('claim,source', [
    ('푸른기업은 원재료 구매 계약을 추가로 체결할 예정이다.', '당사는 원재료 구매 계약을 추가로 체결할 예정입니다.'),
    ('푸른기업이 별도의 원재료 구매 계약을 유지한다.', '푸른기업은 별도의 원재료 구매 계약을 유지합니다.'),
    ('회사는 별도로 공시한 원재료 구매 계약을 체결한다.', '회사는 원재료 구매 계약을 체결한다.'),
    ('회사는 시설 투자가 진행되는 미래시점까지 다수의 추가 약정이 체결될 예정이다.', '회사는 시설 투자가 진행되는 미래시점까지 다수의 추가 약정이 체결될 예정입니다.'),
])
def test_future_relation_subject_variation_and_reporting_modifier(claim, source):
    assert transaction_independence_problem(claim, {'a': source}) == ''


@pytest.mark.parametrize('claim,source', [
    ('회사는 추가 계약을 체결했다.', '회사는 추가 계약을 체결했다.'),
    ('회사는 원재료 구매 계약을 추가로 체결한다.', '푸른기업은 원재료 구매 계약을 추가로 체결한다.'),
    ('푸른기업은 원재료 구매 계약을 추가로 체결한다.', '회사는 원재료 구매 계약을 추가로 체결한다.'),
])
def test_direct_relation_generic_actor_alias_and_unnamed_contract(claim, source):
    assert transaction_independence_problem(claim, {'a': source}) == ''


def test_different_named_actor_cannot_lend_relation():
    assert transaction_independence_problem(
        '푸른기업은 원재료 구매 계약을 추가로 체결한다.',
        {'a': '새봄기업은 원재료 구매 계약을 추가로 체결한다.'},
    ) == TRANSACTION_INDEPENDENCE_UNBOUND


@pytest.mark.parametrize('source,expected', [
    ('회사는 가정용 정수기 공급 계약을 추가로 체결했다.', ''),
    ('회사는 가정용 정수기 공급 계약을 추가로 체결했다고 가정한다.', TRANSACTION_INDEPENDENCE_UNBOUND),
    ('회사는 가정용 정수기 공급 계약을 추가로 체결한 것으로 추정된다.', TRANSACTION_INDEPENDENCE_UNBOUND),
])
def test_household_product_does_not_mean_hypothetical_contract(source, expected):
    assert transaction_independence_problem(
        '회사는 가정용 정수기 공급 계약을 추가로 체결했다.', {'a': source},
    ) == expected


def test_separate_disclosure_does_not_prove_separate_contract():
    claim = '회사는 원재료 구매 계약을 별도로 체결한다.'
    source = '회사는 별도로 공시한 원재료 구매 계약을 체결한다.'
    assert transaction_independence_problem(claim, {'a': source}) == TRANSACTION_INDEPENDENCE_UNBOUND


def test_future_additional_commitment_keeps_omitted_actor_and_spaced_particle():
    claim = '연결회사는 해외 생산공장의 증설을 추진하고 있으며, 증설과정 및 투자가 진행되는 미래시점까지 다수의 추가 약정이 체결될 예정이다.'
    source = '연결회사는 해외 생산공장의 증설을 추진하고 있습니다. 당기말 현재 체결된 유형자산 취득 약정은 증설을 위해 지출될 금액의 일부이며, 증설과정 및 투자가 진행되는 미래시점까지 다수 의 추가 약정이 체결될 예정입니다.'
    assert transaction_independence_problem(claim, {'a': source}) == ''
    spaced_claim = claim.replace('증설과정', '증설 과정').replace('미래시점', '미래 시점')
    assert transaction_independence_problem(spaced_claim, {'a': source}) == ''


@pytest.mark.parametrize('kind', ['본문', '요약'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_real_body_summary_producer_keeps_rejection_diagnostic(kind, wrapped):
    claim = '회사는 원재료 구매 계약을 별도로도 체결하고 있다.'
    source = '회사는 원재료 구매 계약을 체결하고 있으며, 매년 갱신한다.'
    section = 'summary' if kind == '요약' else 'operations_partners'
    row = {'번호': 1, '결과': '참', '근거': ['a']}
    raw = json.dumps({'검수결과': {section: [row]}} if wrapped else {'판정': [row]}, ensure_ascii=False)
    diagnostics, problems = [], {}
    result = _apply_grounding(raw, {1: '참'}, {1: (claim, {'a': source})},
                             diagnostics=diagnostics, diagnostic_contexts={1: (section, kind, claim)},
                             grounding_problems=problems)
    assert result[1] != '참'
    assert problems == {1: TRANSACTION_INDEPENDENCE_UNBOUND}
    observed = observed_review_outcomes(diagnostics)
    assert len(observed) == 1
    assert observed[0]['reason_code'] == TRANSACTION_INDEPENDENCE_UNBOUND
    assert observed[0]['verification_items'] == ('거래 독립성',)
    assert observed[0]['candidate_sha256'] == sha256(claim.encode()).hexdigest()
    assert observed[0]['section_id'] == section
    assert observed[0]['kind'] == kind
    assert 'candidate_text' not in observed[0]
