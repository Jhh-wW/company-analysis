"""5장 표의 재무 과제 우회와 장 이동에서의 범위 차이를 재현한다."""
from dataclasses import replace

import pytest

from src.features.composer import verify as v
from src.features.composer.accounting_policy_constants import ACCOUNTING_POLICY_BOILERPLATE
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.constants import CHALLENGE_FLOW_SECTION_ID
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.shared.report_quality.composition_diagnostic_constants import SECTION_MOVE_BLOCKED_TARGET_RULE


def _report(row, section_id=CHALLENGE_FLOW_SECTION_ID):
    return ComposedReport(sections=(ComposedSection(section_id=section_id, sentences=(), flow_rows=(row,)),))


@pytest.mark.parametrize('issue,source', [
    ('환율 변동 위험', '회사는 외화 거래를 수행하므로 환율변동 위험에 노출되어 있다.'),
    ('운송 수익 인식과 원가 배분의 복잡성', '운송용역의 거래가격을 배분하며, 발생한 원가를 기준으로 진행률을 산정한다.'),
    ('반품 및 할인 추정의 불확실성', '고객은 불량재화를 반품할 권리를 가지며 반품액은 과거 경험에 따라 추정한다.'),
    ('계속 관리해야 하는 복잡한 문제', '회사는 금융부채의 유동성 위험을 관리한다.'),
])
def test_legacy_challenge_table_cannot_restore_policy_as_business_issue(monkeypatch, issue, source):
    monkeypatch.setattr(v, '_semantic_review', lambda groups, *args, **kwargs: groups)
    fragment = CollectedFragment('1', 'DART', source)
    report = _report(FlowRow((issue, '정기 점검'), ('1',)))
    diagnostics = []
    result = v.verify_report(report, (fragment,), None, lambda *args: '', diagnostics=diagnostics)
    assert result.sections[0].flow_rows == ()
    assert diagnostics[0]['reason_code'] == ACCOUNTING_POLICY_BOILERPLATE
    assert report.sections[0].flow_rows and fragment.text == source


@pytest.mark.parametrize('source', [
    '원재료 조달이 중단되어 고객 납품이 지연되었다. 회사는 대출을 받아 대체 원재료를 확보했다.',
    '환율 위험을 정기적으로 관리하며, 고객 납품이 중단되었다.',
])
def test_actual_business_issue_survives_with_financial_response(monkeypatch, source):
    monkeypatch.setattr(v, '_semantic_review', lambda groups, *args, **kwargs: groups)
    row = FlowRow(('원재료 조달 중단과 납품 지연', '대출을 통한 대체 원재료 확보'), ('1',))
    report = _report(row)
    result = v.verify_report(report, (CollectedFragment('1', 'DART', source),), None, lambda *args: '')
    assert result.sections[0].flow_rows == (row,)


def test_other_sections_keep_their_existing_table_contract(monkeypatch):
    monkeypatch.setattr(v, '_semantic_review', lambda groups, *args, **kwargs: groups)
    row = FlowRow(('수익 인식', '거래가격 배분'), ('1',))
    report = _report(row, 'business_model')
    result = v.verify_report(report, (CollectedFragment('1', 'DART', '거래가격을 배분한다.'),), None, lambda *args: '')
    assert result == report


def test_internal_failure_does_not_restore_unchecked_challenge_rows(monkeypatch):
    def explode(*args, **kwargs):
        raise ValueError('검수기 실패 대역')
    monkeypatch.setattr(v, '_verify_report_inner', explode)
    row = FlowRow(('공급 지연', '대체 공급처 확보'), ('1',))
    report = _report(row)
    other = replace(report.sections[0], section_id='operations_partners')
    report = replace(report, sections=report.sections + (other,))
    result = v.verify_report(report, (CollectedFragment('1', 'DART', '공급이 지연되었다.'),), None, lambda *args: '')
    assert result.sections[0].flow_rows == ()
    assert result.sections[1].flow_rows == (row,)


def test_relocation_applies_challenge_only_financial_scope():
    text = '회사는 환율변동 위험에 노출되어 있다.'
    sources = {'1': text}
    assert v._challenge_section_prose_problem(text, sources, culture_candidate=False)
    assert v._relocation_blocker(text, sources, citations=frozenset({'1'}), allowed=None,
                                 culture_candidate=False, source_binding_problem='') == SECTION_MOVE_BLOCKED_TARGET_RULE
    business = '제품 결함으로 리콜을 실시했다.'
    assert not v._relocation_blocker(business, {'1': business}, citations=frozenset({'1'}), allowed=None,
                                     culture_candidate=False, source_binding_problem='')


def test_mixed_original_and_actual_financial_service_are_preserved():
    assert not challenge_business_problem('제품 결함으로 반품이 급증했다.', {
        '1': '반품액은 과거 경험에 따라 추정한다. 제품 결함으로 반품이 급증했다.',
    })
    assert not challenge_business_problem('대출 고객의 연체율이 상승하였다.', {
        '1': '회사는 고객에게 대출 관리 서비스를 제공한다. 대출 고객의 연체율이 상승하였다.',
    })
