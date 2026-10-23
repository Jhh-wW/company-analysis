"""실제11의 당기/전기와 두 합병 날짜를 보존하는 무료 시점 회귀."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.disclosure_transaction_timing import disclosure_transaction_timing
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FilingMeta
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.composer.public_manifest import build_public_structure_seal
from src.features.composer.render import render_report
from src.features.composer.quality_projection import (
    bound_summary_fact_id, build_generation_quality_candidate, select_bound_public_sentences,
)
from src.features.provenance.sources import Source, SourceKind, exact_evidence_text_hash
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.assessment import _official_prose_numbers_proven


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/eleventh_disclosure_transaction.json').read_text(encoding='utf-8'))
COMPANY = FIXTURE['company']
BASELINE = FIXTURE['as_of_date']


def source(fid='4', **changes):
    item = FIXTURE['items'][fid]
    meta = item['source']
    return replace(Source(number=int(fid), kind=SourceKind.FILING, label=meta['title'],
        source_id=meta['source_id'], title=meta['title'], publisher=meta['publisher'],
        host=meta['host'], url=meta['url'], document_id=meta['document_id'],
        location=meta['location'], reporting_period=meta['reporting_period'],
        exact_evidence_hashes=[exact_evidence_text_hash(item['source_text'])]), **changes)


def timing(fid='4', claim=None, text=None, **changes):
    item = FIXTURE['items'][fid]
    return disclosure_transaction_timing(claim or item['claim'],
        ((text or item['source_text'], source(fid, **changes)),),
        section_id=item['section_id'], company_name=COMPANY, as_of_date=BASELINE)


@pytest.mark.parametrize('fid,expected', [('4', '2025-12-31'), ('32', '2023-11-21')])
def test_actual_source_claim_and_fact_time_are_bound_without_changing_reviewed_input(fid, expected):
    item = FIXTURE['items'][fid]
    assert hashlib.sha256(item['source_text'].encode()).hexdigest() == item['source_sha256']
    sentence = ComposedSentence(item['claim'], (fid,), '확인', planned_claim_slot=item['claim_slot'], verification_state='verified')
    result = evaluate_verified_prose_fact(sentence, section_id=item['section_id'],
        company_name=COMPANY, as_of_date=BASELINE,
        evidence=(ProseEvidence(fid, source(fid), item['source_text']),), carry_official_exact_text=True)
    assert result.fact is not None, result.reason_code
    fact = result.fact
    assert (fact.time_state, fact.as_of) == ('past', expected)
    assert fact.claim == timing(fid).public_claim
    assert fact.evidence_binding == fact_evidence_binding(fact)
    assert sentence.text == item['claim']
    if fid == '4':
        assert fact.claim.startswith('2025년 12월 31일 종료 회계기간 공시상, ')
        assert '전기 중에는' in fact.claim and '2024' not in fact.claim


@pytest.mark.parametrize('period', ['2025', '2025-01-01/2025-12-31'])
def test_explicit_same_annual_period_is_preserved(period):
    assert timing(reporting_period=period).as_of == '2025-12-31'


@pytest.mark.parametrize('changes', [
    {'publisher': ''}, {'publisher': '다른회사'}, {'host': 'other.example'},
    {'kind': SourceKind.NEWS}, {'title': ''},
    {'reporting_period': '2024-01-01/2024-12-31'},
    {'reporting_period': '2025-01-01/2025-03-31'},
    {'reporting_period': '2025-01-01/2025-01-31'},
    {'reporting_period': '2024'}, {'reporting_period': '2025Q4'},
])
def test_other_unclear_monthly_or_quarterly_source_does_not_get_a_period(changes):
    assert timing(**changes) is None


@pytest.mark.parametrize('claim', [
    '당기 중 회사는 특수관계자와 자금거래를 수행하고 있다.',
    '당기 중 회사는 특수관계자와 자금거래를 수행할 예정이다.',
    '1분기 중 회사는 특수관계자와 자금거래를 수행하였다.',
    '당기 3월 중 회사는 특수관계자와 자금거래를 수행하였다.',
    '“당기 중 회사는 특수관계자와 자금거래를 수행하였다.”',
    '당기 중 자금거래를 수행하였으며 현재도 관계를 유지하고 있다.',
    '당기 중 회사는 자금거래를 확대할 방침이며, 전기 중에는 자금거래가 없었다.',
    '당기 중 회사는 자금거래를 추진한다. 전기 중에는 자금거래가 없었다.',
    '당기 중 회사는 자금거래가 이뤄진다. 전기 중에는 자금거래가 없었다.',
    '당기 중 회사는 자금거래를 확장한다. 전기 중에는 자금거래가 없었다.',
    '회사는 자금거래를 반복한다. ' + FIXTURE['items']['4']['claim'],
])
def test_current_future_quote_and_nonannual_claims_are_unchanged(claim):
    assert timing(claim=claim) is None


def test_no_annual_metadata_or_exact_annual_end_does_not_infer_previous_year():
    item = FIXTURE['items']['4']
    assert timing(text=item['source_text'].split('내부회계관리제도 감사 또는 검토의견')[0]) is None


def test_same_bound_prefix_is_idempotent_but_wrong_prefix_is_not_accepted():
    bound = timing()
    assert timing(claim=bound.public_claim) == bound
    assert timing(claim=bound.public_claim.replace('2025년 12월 31일 종료', '2024년 12월 31일 종료')) is None


@pytest.mark.parametrize('claim', [
    FIXTURE['items']['32']['claim'].replace('2023년 11월 21일에', '2023년 10월 31일에'),
    '회사는 2023년 10월 31일을 간주취득일로 하여 합병을 완료하였다.',
    '다른회사는 2023년 11월 21일에 합병을 완료하였다.',
    '회사는 2023년 11월 21일에 합병을 완료할 예정이다.',
    '“회사는 2023년 11월 21일에 합병을 완료하였다.”',
])
def test_deemed_date_other_actor_future_or_quote_cannot_become_merger_completion_date(claim):
    assert timing('32', claim=claim) is None


def _public(fid, *, text=None, period=''):
    # 같은 원문·문장으로 DTO를 구성하는 기술 회귀이며 새 AI 승인 재생은 아니다.
    item = FIXTURE['items'][fid]
    sentence = ComposedSentence(item['claim'], (fid,), '확인', planned_claim_slot=item['claim_slot'], verification_state='verified')
    composed = ComposedReport(tuple(ComposedSection(s, (sentence,) if s == item['section_id'] else ()) for s in SECTION_IDS), summary=(sentence,))
    doc = item['source']
    fragments = (CollectedFragment(fid, '사업내용', text or item['source_text'],
        document_identity='document:dart.fss.or.kr:' + doc['document_id'], reporting_period=period),)
    filing = FilingMeta(doc['document_id'], doc['title'], '2026-03-31' if fid == '4' else '2025-03-31')
    seal = build_public_structure_seal(composed, fragments, None, filing_meta=filing,
        composition_tables=(), table_presentation='table', company_id='01492697',
        evidence_generation_sha256='a' * 64,
        evidence_packet_sha256s=tuple((key, str(i) * 64) for i, key in enumerate(SECTION_IDS, 1)),
        company_name=COMPANY, corp_type='', generated_at='', as_of_date=BASELINE,
        analysis_period='', latest_performance_period='', citation_style='inline')
    public = render_report(COMPANY, composed, fragments, None, as_of_date=BASELINE,
        company_id='01492697', filing_meta=filing, public_structure_seal=seal, release_mode='FULL')
    return composed, public


@pytest.mark.parametrize('fid', ['4', '32'])
def test_render_manifest_fact_and_legacy_final_filter_keep_same_visible_claim(fid):
    item = FIXTURE['items'][fid]
    composed, public = _public(fid)
    fact, = public.fact_records
    section = next(s for s in public.sections if s.cell == item['section_id'])
    assert section.prose_lines == [(fact.claim + f' [{fid}]', '')]
    assert public.summary_items[0].text == fact.claim + f' [{fid}]'
    assert composed.sections[SECTION_IDS.index(item['section_id'])].sentences[0].text == item['claim']
    selection = select_bound_public_sentences(composed, public)
    assert not selection.excluded and selection.fact_ids == (fact.fact_id,)
    assert bound_summary_fact_id(composed.summary[0], public.fact_records) == fact.fact_id
    candidate = build_generation_quality_candidate(public, composed)
    owned = next(s for s in candidate.sections if s.section_id == item['section_id'])
    assert owned.public_sentence_count == 1 and not owned.has_unbound_public_content
    assert candidate.summary_fact_ids == (fact.fact_id,) and not candidate.has_unbound_summary_content
    assert _official_prose_numbers_proven(candidate.facts[0], {s.source_id: s for s in candidate.sources})
    from src.features.composer.tests.test_seventh_litigation import _final_legacy_filter
    filtered = _final_legacy_filter(public)
    assert filtered.fact_records == public.fact_records
    assert next(s for s in filtered.sections if s.cell == item['section_id']).prose_lines == section.prose_lines


def test_metadata_without_exact_date_preserves_old_limit_instead_of_adding_unproven_numbers():
    text = FIXTURE['items']['4']['source_text'].split('내부회계관리제도 감사 또는 검토의견')[0]
    assert timing(text=text, reporting_period='2025-01-01/2025-12-31') is None
    composed, public = _public('4', text=text, period='2025-01-01/2025-12-31')
    fact, = public.fact_records
    assert fact.claim == FIXTURE['items']['4']['claim']
    assert (fact.time_state, fact.as_of) == ('present', BASELINE)
    candidate = build_generation_quality_candidate(public, composed)
    assert not next(s for s in candidate.sections if s.section_id == 'operations_partners').has_unbound_public_content
    assert not select_bound_public_sentences(composed, public).excluded


@pytest.mark.parametrize('field,value', [('claim', '임의 공개 문장'), ('as_of', '2024-12-31'), ('source_publisher', '다른회사')])
def test_changed_public_claim_date_or_primary_publisher_cannot_reuse_timing_alias(field, value):
    composed, public = _public('4')
    fact, = public.fact_records
    changed = replace(fact, **{field: value})
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    public = replace(public, fact_records=[changed])
    assert select_bound_public_sentences(composed, public).excluded


@pytest.mark.parametrize('changes', [
    {'source_publisher': '다른회사', 'legal_entity': '다른회사'},
    {'source_title': '감사보고서 (2024.12)'}, {'source_host': 'other.example'},
    {'legal_entity': '다른회사'}, {'supporting_evidence_hashes': ['0' * 64]},
])
def test_fixed_real_source_does_not_accept_changed_fact_metadata_or_hash(changes):
    composed, public = _public('4')
    fact, = public.fact_records
    changed = replace(fact, **changes)
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    changed_public = replace(public, fact_records=[changed])
    assert changed_public.citations == public.citations
    assert select_bound_public_sentences(composed, changed_public).excluded


def test_interpretation_keeps_original_claim_and_does_not_use_new_period_alias():
    sentence = ComposedSentence(FIXTURE['items']['4']['claim'], ('4',), '해석',
        planned_claim_slot='operations_partners:operating_role', verification_state='verified')
    composed = ComposedReport((ComposedSection('operations_partners', (sentence,)),))
    meta = FIXTURE['items']['4']['source']
    public = render_report(COMPANY, composed, (CollectedFragment('4', '사업내용', FIXTURE['items']['4']['source_text']),),
        None, as_of_date=BASELINE, filing_meta=FilingMeta(meta['document_id'], meta['title'], '2026-03-31'))
    assert public.fact_records[0].claim == sentence.text
    assert not select_bound_public_sentences(composed, public).excluded


def test_json_escape_key_cannot_skip_fixed_source_metadata_validation():
    composed, public = _public('4')
    fact, = public.fact_records
    escaped = fact.state_evidence.replace('disclosure_reviewed_claim_sha256', r'disclosure\u005freviewed_claim_sha256')
    assert escaped != fact.state_evidence and json.loads(escaped) == json.loads(fact.state_evidence)
    changed = replace(fact, state_evidence=escaped, source_type='뉴스')
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    assert select_bound_public_sentences(composed, replace(public, fact_records=[changed])).excluded
    # 의미가 같은 JSON 표기만 바뀐 정상 Fact는 그대로 허용한다.
    same = replace(fact, state_evidence=escaped)
    same = replace(same, evidence_binding=fact_evidence_binding(same))
    assert not select_bound_public_sentences(composed, replace(public, fact_records=[same])).excluded
