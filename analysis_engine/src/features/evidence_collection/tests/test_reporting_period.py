"""원문 법인·표시날짜·선언값이 결속된 공시기간만 수집·직렬화한다."""

from dataclasses import replace
import hashlib
import json

import pytest

from core.dart_client import UsageCounter
from features.evidence_collection import constants as c
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.dart_fetcher import DartRuntimeFetcher
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow
from features.evidence_collection.models import EvidenceCollectionError
from features.evidence_collection.reporting_period import reporting_period_from_xml
from features.evidence_collection.serialize import _document_to_mapping, harvest_to_mapping
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher
from features.evidence_collection.tests.test_serialize import _document


COMPANY = '01492697'
RECEIPT = '20260331000296'
PERIOD = '2025-01-01/2025-12-31'
START_NODE = '<TU AUNIT="PERIODFROM" AUNITVALUE="20250101">2025년 01월 01일</TU>'
END_NODE = '<TU AUNIT="PERIODTO" AUNITVALUE="20251231">2025년 12월 31일</TU>'
HEADER = '<COMPANY-NAME AREGCIK="01492697">토스페이먼츠 주식회사</COMPANY-NAME>'
BODY = ('<P>I. 회사의 개요</P><P>당사는 전자지급결제대행업을 영위하는 법인이다.</P>'
        '<P>II. 참고 사항</P><P>오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.</P>')
XML = f'<DOCUMENT>{HEADER}<BODY>{START_NODE}{END_NODE}{BODY}</BODY></DOCUMENT>'


def _period(xml=XML, company=COMPANY):
    return reporting_period_from_xml(xml, document_corp_code=company)


def test_exact_annual_and_current_quarter_and_leap_day_dates_are_not_inferred():
    assert _period() == PERIOD
    quarter = XML.replace('20250101', '20260701').replace('2025년 01월 01일', '2026년 7월 1일')
    quarter = quarter.replace('20251231', '20260930').replace('2025년 12월 31일', '2026년 9월 30일')
    assert _period(quarter) == '2026-07-01/2026-09-30'
    leap = XML.replace('20250101', '20240229').replace('2025년 01월 01일', '2024년 02월 29일')
    assert _period(leap) == '2024-02-29/2025-12-31'


@pytest.mark.parametrize('xml,company', [
    (XML, '00126380'), (XML, ''), (XML, '1492697'),
    (XML.replace(HEADER, ''), COMPANY),
    (XML.replace(HEADER, HEADER + HEADER), COMPANY),
    (XML.replace(HEADER, '<SECTION>' + HEADER + '</SECTION>'), COMPANY),
    (XML.replace('AREGCIK="01492697"', ''), COMPANY),
    (XML.replace(START_NODE, ''), COMPANY),
    (XML.replace(END_NODE, ''), COMPANY),
    (XML.replace(START_NODE, START_NODE + START_NODE), COMPANY),
    (XML.replace(END_NODE, END_NODE + END_NODE), COMPANY),
    (XML.replace('20250101', '20250230').replace('2025년 01월 01일', '2025년 02월 30일'), COMPANY),
    (XML.replace('20251231', '20251331').replace('2025년 12월 31일', '2025년 13월 31일'), COMPANY),
    (XML.replace('2025년 12월 31일', '2024년 12월 31일'), COMPANY),
    (XML.replace('20250101', '20260101').replace('2025년 01월 01일', '2026년 01월 01일'), COMPANY),
    (XML.replace('20250101', '2025-01-01'), COMPANY),
    (XML.replace('2025년 01월 01일', '2025-01-01'), COMPANY),
    (XML.replace('20250101', '２０２５０１０１'), COMPANY),
    (XML.replace('PERIODFROM"', 'PERIODFROM2"'), COMPANY),
    (XML[:-5], COMPANY),
    ('<!DOCTYPE DOCUMENT [<!ENTITY x "2025">]>' + XML, COMPANY),
    (XML.replace('<DOCUMENT>', '<OTHER>').replace('</DOCUMENT>', '</OTHER>'), COMPANY),
])
def test_foreign_ambiguous_absent_or_malformed_period_is_empty(xml, company):
    assert _period(xml, company) == ''


@pytest.mark.parametrize('value', ['2025-01-01', '2025-01-01/2025-02-30',
    '2025-13-01/2025-12-31', '2025-12-31/2025-01-01',
    ' 2025-01-01/2025-12-31', '2025-1-1/2025-12-31', None, 2025])
def test_document_and_fetch_models_reject_invalid_declared_period(value):
    with pytest.raises(EvidenceCollectionError):
        replace(_document('audit:period'), reporting_period=value)
    with pytest.raises((TypeError, ValueError)):
        DocumentFetchResult(state='OK', reporting_period=value)


def test_empty_defaults_and_failure_payload_contract_are_preserved():
    assert _document('audit:period').reporting_period == ''
    assert DocumentFetchResult(state='OK').reporting_period == ''
    assert replace(_document('audit:period'), reporting_period=PERIOD).reporting_period == PERIOD
    with pytest.raises(ValueError):
        DocumentFetchResult(state='FAILED', reporting_period=PERIOD)


def test_runtime_fetch_uses_period_and_identity_from_same_xml_without_list_state(tmp_path):
    path = tmp_path / f'{RECEIPT}.xml'
    path.write_text(XML, encoding='utf-8')
    fetcher = DartRuntimeFetcher(document_cache_dir=tmp_path,
        counter=UsageCounter(path=tmp_path / 'usage.json'),
        get_json_fn=lambda *_args: pytest.fail('네트워크 목록 조회를 호출하면 안 됩니다'),
        download_document_fn=lambda *_args: path)
    first = fetcher.fetch_document_text(RECEIPT)
    second = fetcher.fetch_document_text(RECEIPT)
    assert first.corp_code == COMPANY
    assert first.reporting_period == PERIOD == second.reporting_period
    assert first.text == second.text
    path.write_text(XML.replace(START_NODE, ''), encoding='utf-8')
    assert fetcher.fetch_document_text(RECEIPT).reporting_period == ''


def _harvest(*, document_company=COMPANY, period=PERIOD):
    row = RawFilingRow(RECEIPT, '감사보고서 (2025.12)', '20260331', COMPANY)
    text = ('I. 회사의 개요\n당사는 전자지급결제대행업을 영위하는 법인이다.\n\n'
            'II. 참고 사항\n오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.')
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={'F': FilingListResult(state='OK', rows=(row,))},
        document_responses_by_rcept_no={RECEIPT: DocumentFetchResult(state='OK', text=text,
            corp_code=document_company, reporting_period=period)})
    return collect_dart_evidence(fetcher, COMPANY, now='2026-10-23T00:00:00+09:00')


def test_collector_transmits_same_period_to_scored_and_unclassified_documents():
    harvest = _harvest()
    assert harvest.documents and harvest.unclassified_documents
    assert {doc.reporting_period for doc in (*harvest.documents, *harvest.unclassified_documents)} == {PERIOD}
    mapping = harvest_to_mapping(harvest)
    assert all(doc['reporting_period'] == PERIOD for doc in mapping['documents'] + mapping['unclassified_documents'])


def test_foreign_document_is_rejected_and_unbound_document_period_is_not_transmitted():
    foreign = _harvest(document_company='00126380')
    assert not foreign.documents and not foreign.unclassified_documents
    unknown = _harvest(document_company='')
    assert unknown.documents
    assert all(doc.reporting_period == '' for doc in (*unknown.documents, *unknown.unclassified_documents))


def test_distinct_documents_keep_their_own_period_in_both_serialized_lanes():
    previous_receipt = '20250331000001'
    text = ('I. 회사의 개요\n당사는 전자지급결제대행업을 영위하는 법인이다.\n\n'
            'II. 참고 사항\n오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.')
    periods = {RECEIPT: PERIOD, previous_receipt: '2024-01-01/2024-12-31'}
    rows = (RawFilingRow(RECEIPT, '감사보고서 (2025.12)', '20260331', COMPANY),
            RawFilingRow(previous_receipt, '감사보고서 (2024.12)', '20250331', COMPANY))
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={'F': FilingListResult(state='OK', rows=rows)},
        document_responses_by_rcept_no={receipt: DocumentFetchResult(state='OK',
            text=text + '\n' + receipt, corp_code=COMPANY, reporting_period=period)
            for receipt, period in periods.items()})
    harvest = collect_dart_evidence(fetcher, COMPANY, now='2026-10-23T00:00:00+09:00')
    mapping = harvest_to_mapping(harvest)
    assert len(mapping['documents']) == len(mapping['unclassified_documents']) == 2
    for document in mapping['documents'] + mapping['unclassified_documents']:
        receipt = document['document_id'].rsplit(':', 1)[-1]
        assert document['reporting_period'] == periods[receipt]


def test_empty_period_serialization_is_byte_compatible_and_nonempty_adds_only_one_field():
    legacy = _document_to_mapping(_document('audit:period'), ['a' * 64], [])
    assert 'reporting_period' not in legacy
    with_period = _document_to_mapping(replace(_document('audit:period'), reporting_period=PERIOD), ['a' * 64], [])
    assert with_period.pop('reporting_period') == PERIOD
    assert json.dumps(with_period, ensure_ascii=False).encode() == json.dumps(legacy, ensure_ascii=False).encode()
    assert hashlib.sha256(json.dumps(legacy, ensure_ascii=False).encode()).hexdigest() == 'd2bbb6cb4162b9076496f19e8641df9a984ed5488f4069c6cfc26627bdb9e82b'
