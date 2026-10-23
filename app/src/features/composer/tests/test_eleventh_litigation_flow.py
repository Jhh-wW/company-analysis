"""실제11 축약 소송 도식을 새 의미 검수 직전까지만 보존하는 회귀."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.diagram_check import check_diagram_numbers
from src.features.composer.direct_support_constants import FLOW_CELL_JOIN
from src.features.composer.litigation_timing import litigation_numeric_text, litigation_snapshot_date
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow


SAVED = json.loads((Path(__file__).parent / 'fixtures/eleventh_litigation_flow.json')
                   .read_text(encoding='utf-8'))
SOURCE = SAVED['source']
CELLS = tuple(SAVED['flow']['칸'])
PERIOD = SAVED['reporting_period']
COMPANY = SAVED['source_publisher']


def fragment(**changes):
    return replace(CollectedFragment('6', '소송·분쟁', SOURCE,
        document_date='2026-03-31', document_title='감사보고서 (2025.12)',
        source_document_id=SAVED['document_id'], source_publisher=COMPANY,
        reporting_period=PERIOD), **changes)


def precheck(cells=CELLS, **changes):
    row = FlowRow(cells, ('6',))
    original = ComposedReport((ComposedSection('current_challenges', (), flow_rows=(row,)),))
    checked, problems = check_diagram_numbers(original, (fragment(**changes),))
    return checked.sections[0].flow_rows, problems


def test_actual_writer_and_source_reach_pre_review_without_new_approval():
    before = deepcopy(SAVED)
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == SAVED['source_sha256']
    assert hashlib.sha256(SAVED['writer_response'].encode()).hexdigest() == SAVED['writer_response_sha256']
    raw = SAVED['writer_response']
    payload = json.JSONDecoder().raw_decode(raw[raw.index('{'):])[0]
    assert payload['경로표'][1] == SAVED['flow']
    assert SAVED['actual_flow_review_present'] is False
    rows, problems = precheck()
    assert not problems
    assert len(rows) == 1 and rows[0] == FlowRow(CELLS, ('6',))
    assert SAVED == before


@pytest.mark.parametrize('head', [
    '2025년 12월 31일 기준 피고 소송 9건 계류 중',
    '2025년 말 기준 피고 소송사건 9건 계류 중',
    '2025-12-31 현재 피고 소송 총 9건 계류 중',
])
def test_compact_role_same_source_date_and_count_are_preserved(head):
    cells = (head + ' / 소송금액 약 4억원', CELLS[1])
    rows, problems = precheck(cells)
    assert rows and not problems
    text = FLOW_CELL_JOIN.join(cells)
    masked = litigation_numeric_text(text, ((SOURCE, PERIOD, COMPANY),), preserve_offsets=True)
    assert len(masked) == len(text)
    assert masked.index('9건') == text.index('9건')
    assert masked.index('4억원') == text.index('4억원')


@pytest.mark.parametrize('old,new', [
    ('2025년', '2024년'), ('2025년', '2026년'),
    ('피고 소송', '원고 소송'), ('9건', '8건'), ('9건', '19건'),
    ('약 4억원', '약 5억원'),
    ('기준 피고', '이후에도 피고'), ('계류 중', '현재까지 계류 중'),
    ('기준 피고', '기준 한빛회사가 피고로 계류 중인'),
])
def test_changed_period_role_count_amount_or_named_company_is_not_prepared(old, new):
    cells = (CELLS[0].replace(old, new), CELLS[1])
    rows, problems = precheck(cells)
    assert not rows and problems


@pytest.mark.parametrize('source', [
    SOURCE.replace('회사가 피고로', '경쟁사가 피고로'),
    SOURCE.replace('회사가 피고로', '한빛회사가 피고로'),
    SOURCE.replace('회사가 피고로', '가맹점이 피고로'),
    SOURCE.replace('회사가 피고로', '회사가 원고로'),
    SOURCE.replace('소송사건은 9건', '소송사건은 8건'),
    SOURCE.replace('(4) 당기말 현재', '(4) 전기말 현재'),
    SOURCE.replace('(4) 당기말 현재', '가상의 사례로 당기말 현재'),
])
def test_current_generic_company_role_and_same_count_must_be_in_own_source(source):
    rows, problems = precheck(text=source)
    assert not rows and problems


@pytest.mark.parametrize('period', ['', '2024-01-01/2024-12-31', '2026-01-01/2026-12-31'])
def test_missing_or_other_document_period_cannot_supply_the_date(period):
    rows, problems = precheck(reporting_period=period)
    assert not rows and problems


def test_another_sentence_date_does_not_qualify_the_compact_snapshot():
    text = '2025년 12월 31일 기준 다른 내용이다. 피고 소송 9건 계류 중'
    assert litigation_snapshot_date(text, ((SOURCE, PERIOD, COMPANY),)) == ''


@pytest.mark.parametrize('prefix', ['한빛회사는 ', '가상 예시: ', '회사가 사업에 진출한다면 '])
def test_compact_snapshot_does_not_borrow_a_date_after_an_unknown_prefix(prefix):
    rows, problems = precheck((prefix + CELLS[0], CELLS[1]))
    assert not rows and problems


def test_matching_plaintiff_source_is_allowed_without_defendant_inference():
    source = SOURCE.replace('회사가 피고로', '회사가 원고로')
    cells = (CELLS[0].replace('피고 소송', '원고 소송'), CELLS[1])
    rows, problems = precheck(cells, text=source)
    assert rows and not problems


def test_precheck_without_own_source_cannot_supply_a_date_or_review_binding():
    row = FlowRow(CELLS, ('other',))
    report = ComposedReport((ComposedSection('current_challenges', (), flow_rows=(row,)),))
    checked, problems = check_diagram_numbers(report, (fragment(),))
    # 인용 존재 검사는 별도 단계다. 이 숫자 사전 검사에서 남은 행도 승인되지 않는다.
    assert not problems
    assert checked.sections[0].flow_rows == (row,)
    assert row.review_binding is None
    text = FLOW_CELL_JOIN.join(CELLS)
    assert litigation_numeric_text(text, (), preserve_offsets=True) == text


def test_precheck_is_not_a_currency_semantic_review():
    # 기존 숫자 사전 검사는 외화 어휘까지 검수하지 않는다. 이 변경도 의미 승인이나
    # 외화 수치 증명을 만들지 않으며 후단 단위·원문 결속 및 새 독립 검수는 필수다.
    cells = (CELLS[0].replace('4억원', '4억달러'), CELLS[1])
    rows, problems = precheck(cells)
    assert rows and not problems
    assert rows[0].cells == cells and rows[0].review_binding is None
