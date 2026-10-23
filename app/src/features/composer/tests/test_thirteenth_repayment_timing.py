"""실제13의 명시 월 상환완료는 사건일 추정 없이 자기 공시일 기준으로 잠근다."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.disclosure_transaction_timing import disclosure_transaction_timing
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FilingMeta
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.composer.quality_projection import build_generation_quality_candidate, select_bound_public_sentences
from src.features.composer.render import render_report
from src.features.provenance.sources import Source, SourceKind, exact_evidence_text_hash
from src.features.storage.reports import report_from_dict, report_to_dict
from src.shared.report_quality.fact_binding import fact_evidence_binding


SAVED = json.loads((Path(__file__).parent / 'fixtures/thirteenth_repayment_timing.json').read_text(encoding='utf-8'))
COMPANY = SAVED['company']
BASELINE = SAVED['as_of_date']
CLAIM = SAVED['claim']
TEXT = SAVED['source_text']


def source(**changes):
    meta = SAVED['source']
    return replace(Source(number=4, kind=SourceKind.FILING, label=meta['title'],
        source_id=meta['source_id'], title=meta['title'], publisher=meta['publisher'],
        host=meta['host'], url=meta['url'], document_id=meta['document_id'],
        location=meta['location'], disclosed_at=meta['disclosed_at'],
        exact_evidence_hashes=[exact_evidence_text_hash(TEXT)]), **changes)


def timing(claim=CLAIM, text=TEXT, **changes):
    return disclosure_transaction_timing(claim, ((text, source(**changes)),),
        section_id=SAVED['section_id'], company_name=COMPANY, as_of_date=BASELINE)


def sentence():
    return ComposedSentence(CLAIM, ('4',), '확인',
        planned_claim_slot=SAVED['claim_slot'], verification_state='verified')


def test_actual_claim_is_unchanged_and_month_is_not_invented_as_a_day():
    assert hashlib.sha256(TEXT.encode()).hexdigest() == SAVED['source_sha256']
    actual = sentence()
    result = evaluate_verified_prose_fact(actual, section_id=SAVED['section_id'],
        company_name=COMPANY, as_of_date=BASELINE,
        evidence=(ProseEvidence('4', source(), TEXT),), carry_official_exact_text=True)
    assert result.fact is not None, result.reason_code
    fact = result.fact
    assert (fact.claim, actual.text) == (CLAIM, CLAIM)
    assert (fact.time_state, fact.as_of, fact.source_date) == ('past', '2026-03-31', '2026-03-31')
    # 사건월은 2월이다. 3월 31일은 완료를 확인한 공시 기준일이며 사건일이 아니다.
    assert '2026년 2월 중' in fact.claim and '2026년 3월 31일' not in fact.claim
    assert not fact.event_date
    assert fact.evidence_binding == fact_evidence_binding(fact)


@pytest.mark.parametrize('changes', [
    {'publisher': '다른회사'}, {'publisher': ''}, {'host': 'other.example'},
    {'kind': SourceKind.NEWS}, {'disclosed_at': ''}, {'disclosed_at': '2026-03'},
    {'disclosed_at': '2026-02-30'}, {'disclosed_at': '2026-3-31'},
    {'disclosed_at': '2026-01-31'}, {'disclosed_at': '2027-03-31'},
])
def test_only_own_actual_disclosure_date_can_confirm_completed_month(changes):
    assert timing(**changes) is None


@pytest.mark.parametrize('old,new', [
    ('토스페이먼츠는', '다른회사는'), ('2026년 2월 중', '2025년 2월 중'),
    ('2026년 2월 중', '2026년 3월 중'), ('2026년 2월 중', '2026년 13월 중'),
    ('2026년 2월 중', '2026년 2월 1일에'),
    ('전액 상환하였으며', '전액 상환할 예정이며'),
    ('전액 상환하였으며', '일부 상환하였으며'),
    ('제공받았다.', '제공받고 있다.'),
    ('제공받았다.', '제공받는다.'),
    ('제공받았다.', '제공받을 것이다.'),
])
def test_other_company_future_partial_different_month_or_mixed_time_claim_is_not_retimed(old, new):
    assert old in CLAIM
    assert timing(claim=CLAIM.replace(old, new)) is None


@pytest.mark.parametrize('old,new', [
    ('당사는 2026년 2월 중', '다른회사는 2026년 2월 중'),
    ('당사는 2026년 2월 중', '당사는 2025년 2월 중'),
    ('당사는 2026년 2월 중', '당사는 2026년 3월 중'),
    ('전액 상환하였습니다.', '전액 상환할 예정입니다.'),
    ('전액 상환하였습니다.', '전액 상환하였으며 현재도 상환을 진행하고 있습니다.'),
    ('전액 상환하였습니다.', '조건이 충족될 경우 전액 상환하였습니다.'),
    ('전액 상환하였습니다.', '전액 상환하였으며 연대보증을 제공받는다.'),
    ('전액 상환하였습니다.', '전액 상환하였으며 연대보증을 제공받을 것이다.'),
    ('당사는 2026년 2월 중', '당사는 2026년 2월 중 2026년 3월 1일에'),
    ('신주인수권부사채는 전액 상환하였습니다.', '전환사채는 전액 상환하였습니다.'),
])
def test_same_own_source_must_carry_same_month_and_unconditional_completed_event(old, new):
    assert old in TEXT
    assert timing(text=TEXT.replace(old, new)) is None


def test_month_and_completion_cannot_be_borrowed_from_another_citation():
    without_month = TEXT.replace('2026년 2월 중', '보고기간 이후')
    assert timing(text=without_month) is None
    assert disclosure_transaction_timing(CLAIM,
        ((without_month, source()), (TEXT, source(source_id='v2-frag-99', number=99))),
        section_id=SAVED['section_id'], company_name=COMPANY, as_of_date=BASELINE) is None


def test_multiple_completed_months_do_not_choose_a_convenient_one():
    assert timing(text=TEXT + ' 당사는 2026년 3월 중 신주인수권부사채를 전액 상환하였습니다.') is None
    assert timing(claim=CLAIM + ' 회사는 2026년 3월 중 신주인수권부사채를 전액 상환하였다.') is None


def test_render_fact_quality_and_storage_keep_original_words_and_supported_date_format():
    original = sentence()
    composed = ComposedReport((ComposedSection(SAVED['section_id'], (original,)),), summary=(original,))
    meta = SAVED['source']
    fragments = (CollectedFragment('4', '특수관계자', TEXT),)
    public = render_report(COMPANY, composed, fragments, None, as_of_date=BASELINE,
        filing_meta=FilingMeta(meta['document_id'], meta['title'], meta['disclosed_at']))
    fact, = public.fact_records
    assert (fact.time_state, fact.as_of) == ('past', '2026-03-31')
    assert public.sections[0].prose_lines == [(CLAIM + ' [4]', '')]
    assert original.text == CLAIM
    assert not select_bound_public_sentences(composed, public).excluded
    candidate = build_generation_quality_candidate(public, composed)
    assert not candidate.has_unbound_summary_content
    restored = report_from_dict(report_to_dict(public))
    assert restored.fact_records == public.fact_records
    assert restored.summary_items == public.summary_items
