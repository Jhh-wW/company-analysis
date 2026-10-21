"""저장 XML → 정상 수집 → 작성·검수 입력 → 공개 Source/Fact의 시점 연결."""

from dataclasses import replace
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from src.features.pipeline import real
from src.features.pipeline.evidence_transport import typed_fragments_from_raw
from src.features.composer.logic import build_section_prompt
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence, filing_meta_from_raw
from src.features.composer.render import render_report
from src.features.composer.tests.test_pipeline import _FakeReviewer
from src.features.composer.verify import _review_fragment_metadata, verify_report
from src.features.provenance.sources import has_valid_provenance_seal
from src.shared.report_quality.fact_binding import fact_primary_source_metadata_mismatches


XML = (Path(__file__).parent / 'fixtures/sixth_litigation.xml').read_text(encoding='utf-8')
RECEIPT = '20260331000296'
CORP = '01492697'
COMPANY = '토스페이먼츠 주식회사'
PERIOD = '2025-01-01/2025-12-31'
CLAIM = '2025년 12월 31일 기준 회사가 피고로 계류 중인 소송사건은 9건이며, 소송 결과를 합리적으로 예측할 수 없어 관련 충당부채를 계상하지 않았다.'


def collect_saved_xml(tmp_path, monkeypatch, *, xml=XML, corp=CORP):
    path = tmp_path / f'{RECEIPT}.xml'
    path.write_text(xml, encoding='utf-8')
    engine = SimpleNamespace(
        RAW_DIR=str(tmp_path), download_document=lambda *_: path,
        read_filing_text=lambda path: re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', path.read_text(encoding='utf-8'))),
        make_fragments=lambda *_: {},
    )
    missing = SimpleNamespace(state='none', fragments=())
    monkeypatch.setattr(real, 'collect_homepage_fragments', lambda *_a, **_k: missing)
    monkeypatch.setattr(real, 'collect_official_ir_fragments', lambda *_a, **_k: missing)
    monkeypatch.setattr(real, '_official_web_collection_step', lambda _: {})
    monkeypatch.setattr(real, '_official_ir_collection_step', lambda _: {})
    monkeypatch.setattr(real, '_attach_name_candidate_fragments', lambda frags, **_: (frags, 0))
    monkeypatch.setattr(real, '_typed_dart_collection_enabled', lambda _: False)
    fragments, _, _ = real._collect(engine, None, {'corp_name': COMPANY}, None, None, [],
        financials=None, fin_years=[], corp_code=corp,
        filing={'corp_code': corp, 'rcept_no': RECEIPT, 'rcept_dt': '20260331', 'report_nm': '감사보고서 (2025.12)'})
    return fragments


def test_saved_xml_reaches_writer_review_and_sealed_public_fact(tmp_path, monkeypatch):
    raw = collect_saved_xml(tmp_path, monkeypatch)
    packet = typed_fragments_from_raw(corp_id=CORP, frags=raw, filing_meta=None)
    fragment = next(f for f in packet.fragments if f.kind == '소송·분쟁')
    assert fragment.text.startswith('(4) 당기말 현재 회사가 피고로')
    assert fragment.reporting_period == PERIOD
    assert fragment.source_publisher == COMPANY
    assert fragment.document_date == '2026-03-31'
    assert fragment.formal_source_kind == '' and fragment.identity_binding == ''
    for prompt in (build_section_prompt(COMPANY, 'current_challenges', (fragment,), None), _review_fragment_metadata(fragment)):
        assert PERIOD in prompt
        assert '본문과 도식 모두 이 말일을 명시' in prompt
    sentence = ComposedSentence(CLAIM, (fragment.fragment_id,), '확인', planned_claim_slot='current_challenges:issue')
    composed = ComposedReport((ComposedSection('current_challenges', (sentence,)),))
    checked = verify_report(composed, (fragment,), None, _FakeReviewer(), baseline_date='2026-10-22')
    assert len(checked.sections[0].sentences) == 1  # 가짜 검수기 통합 회귀이며 모델 승인 아님.
    public = render_report(COMPANY, checked, (fragment,), None, as_of_date='2026-10-22',
        filing_meta=filing_meta_from_raw({'rcept_no': RECEIPT, 'rcept_dt': '20260331', 'report_nm': '감사보고서 (2025.12)'}))
    fact, = public.fact_records
    source = next(s for s in public.citations if s.source_id == fact.source_id)
    assert fact.claim == CLAIM and (fact.time_state, fact.as_of) == ('past', '2025-12-31')
    assert source.reporting_period == PERIOD
    assert source.document_id == RECEIPT
    assert has_valid_provenance_seal(source)
    assert fact_primary_source_metadata_mismatches(fact, source) == ()
    assert not has_valid_provenance_seal(replace(source, reporting_period='2026-01-01/2026-12-31'))


@pytest.mark.parametrize('corp', ['00000001', ''])
def test_xml_of_different_or_unidentified_company_cannot_supply_period(tmp_path, monkeypatch, corp):
    raw = collect_saved_xml(tmp_path, monkeypatch, corp=corp)
    fragment = next(f for f in raw.values() if f['종류'] == '소송·분쟁')
    assert '보고기간' not in fragment and '문서ID' not in fragment
