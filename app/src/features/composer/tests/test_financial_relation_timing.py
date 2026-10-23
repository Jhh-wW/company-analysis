"""실제 7차 금융 결산표의 현재화와 정상 당시·현행 관계를 구별한다."""

from dataclasses import replace
from pathlib import Path
import json

import pytest

from src.features.composer.financial_relation_timing import financial_relation_timing_problem
from src.features.composer.financial_relation_timing_constants import FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED
from src.features.composer.logic import parse_section_response
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection
from src.features.composer.render import render_report
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import _apply_grounding, verify_report
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


SAVED = json.loads((Path(__file__).parent / 'fixtures/seventh_financial_relation.json').read_text(encoding='utf-8'))
CLAIM = SAVED['writer_sentence']['글']
FRAGMENTS = {item['fragment_id']: CollectedFragment(**item) for item in SAVED['sources']}
SOURCES = {number: fragment.text for number, fragment in FRAGMENTS.items()}


def _problem(claim, *, section='operations_partners', sources=SOURCES, fragments=FRAGMENTS):
    return financial_relation_timing_problem(claim, sources, fragments,
        section_id=section, baseline_date='2026-10-22')


@pytest.mark.parametrize('claim', [CLAIM,
    '회사는 소프트웨어공제조합과 보증 관계를 이어가고 있다.',
    '소프트웨어공제조합은 보증 제공자이자 금융자산 투자 대상으로 동시에 기능한다.',
    '회사는 소프트웨어공제조합으로부터 보증을 받고 있다.',
    '2025년 말 기준 소프트웨어공제조합의 보증 관계를 현재도 유지하고 있다.',
])
def test_snapshot_tables_do_not_reaffirm_current_financial_relation(claim):
    assert _problem(claim) == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED


@pytest.mark.parametrize('claim', [
    '2025년 말 기준 소프트웨어공제조합은 보증 제공자이자 금융자산 투자 대상으로 기능한다.',
    '2025-12-31 현재 회사는 소프트웨어공제조합과 보증 관계를 유지하고 있다.',
    '당시 회사는 소프트웨어공제조합과 보증 관계를 유지하고 있다.',
    '회사는 소프트웨어공제조합으로부터 보증을 제공받았다.',
    '회사는 보증 계약을 체결했다.',
    '회사는 현재 전자지급결제대행 서비스를 제공하고 있다.',
])
def test_explicit_historical_relation_or_ordinary_company_operation_is_preserved(claim):
    assert _problem(claim) == ''


def test_current_official_contract_text_and_other_sections_keep_existing_review():
    current = '회사는 현재 소프트웨어공제조합으로부터 지급보증을 받고 있다.'
    source = CollectedFragment('n', 'official_identity_verified_web_page', current)
    assert _problem(current, sources={'n': current}, fragments={'n': source}) == ''
    assert _problem(CLAIM, section='identity') == ''
    current_snapshot = {key: replace(value, reporting_period='2026-01-01/2026-10-22')
                        for key, value in FRAGMENTS.items()}
    assert _problem(CLAIM, fragments=current_snapshot) == ''
    mixed = dict(FRAGMENTS, **{'57': current_snapshot['57']})
    assert _problem(CLAIM, fragments=mixed) == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED


@pytest.mark.parametrize('extra_text', [
    '회사는 현재 전자지급결제대행 서비스를 제공하고 있다.',
    '회사는 웹케시와 정산 데이터 연동 업무협약을 체결했다.',
])
def test_unrelated_extra_citation_cannot_release_the_snapshot_boundary(extra_text):
    extra = CollectedFragment('n', 'official_identity_verified_web_page', extra_text,
        source_collected_on='2026-10-22')
    assert _problem(CLAIM, sources=dict(SOURCES, n=extra_text),
        fragments=dict(FRAGMENTS, n=extra)) == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED


def test_only_current_official_same_counterparty_and_both_roles_can_supplement_snapshot():
    current = '현재 소프트웨어공제조합은 보증 제공자이자 금융자산 투자 대상으로 동시에 기능한다.'
    extra = CollectedFragment('n', 'official_identity_verified_web_page', current,
        formal_source_kind='official_identity_verified_web_page', source_collected_on='2026-10-22')
    assert _problem(CLAIM, sources=dict(SOURCES, n=current), fragments=dict(FRAGMENTS, n=extra)) == ''
    for altered in (current.replace('소프트웨어공제조합', '서울보증보험'),
                    current.replace('소프트웨어공제조합', '소프트웨어공제조합지원센터'),
                    current.replace('소프트웨어공제조합', '소프트웨어공제조합의 자회사'),
                    '현재 소프트웨어공제조합은 보증 제공자로 기능한다.'):
        assert _problem(CLAIM, sources=dict(SOURCES, n=altered),
            fragments=dict(FRAGMENTS, n=replace(extra, text=altered))) == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED
    assert _problem(CLAIM, sources=dict(SOURCES, n=current),
        fragments=dict(FRAGMENTS, n=replace(extra, source_collected_on='2025-10-22'))) == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED


def test_two_providers_require_current_same_role_for_each_mentioned_provider():
    snapshot = '보증내역 | 보증금액 ; 서울보험 | 100 ; 미래보험 | 200'
    claim = '서울보험과 미래보험은 보증 제공자로 기능하고 있다.'
    table = CollectedFragment('a', 'dart_audit_report', snapshot,
        reporting_period='2025-01-01/2025-12-31')
    first = '현재 서울보험은 보증 제공자로 기능하고 있다.'
    second = '현재 미래보험은 보증 제공자로 기능하고 있다.'

    def problem(*statements):
        fragments = {'a': table}
        sources = {'a': snapshot}
        for index, statement in enumerate(statements):
            number = str(index)
            fragments[number] = CollectedFragment(number, 'official_identity_verified_web_page', statement,
                formal_source_kind='official_identity_verified_web_page', source_collected_on='2026-10-23')
            sources[number] = statement
        return financial_relation_timing_problem(claim, sources, fragments,
            section_id='operations_partners', baseline_date='2026-10-23')

    assert problem() == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED
    assert problem(first) == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED
    assert problem(first, second) == ''
    assert problem('현재 서울보험과 미래보험은 보증 제공자로 기능하고 있다.') == ''
    assert problem(first, second.replace('보증 제공자', '금융자산 투자 대상')) == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED


def test_actual_saved_true_is_rejected_without_reapproving_other_sources():
    problems = {}
    verdicts = _apply_grounding(json.dumps({'판정': [SAVED['review_entry']]}, ensure_ascii=False),
        {60: '참'}, {60: (CLAIM, SOURCES)},
        diagnostic_contexts={60: ('operations_partners', '본문', CLAIM)},
        claim_slots_by_number={60: 'operations_partners:operating_role'},
        source_fragments_by_id=FRAGMENTS, grounding_problems=problems, baseline_date='2026-10-22')
    assert verdicts[60] != '참'
    assert problems[60] == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED


def test_actual_writer_review_to_public_report_cannot_create_current_financial_fact():
    sentences = parse_section_response(json.dumps({'문장들': [SAVED['writer_sentence']]}, ensure_ascii=False), 'operations_partners')
    report = ComposedReport((ComposedSection('operations_partners', sentences),))

    def replay(prompt):
        items = review_items(prompt)
        assert items and all(item.text == CLAIM for item in items)
        return json.dumps({'판정': [dict(SAVED['review_entry'], 번호=item.number) for item in items]}, ensure_ascii=False)

    diagnostics = []
    verified = verify_report(report, tuple(FRAGMENTS.values()), None, replay,
        baseline_date='2026-10-22', diagnostics=diagnostics, allow_sentence_rewrite=False)
    assert not verified.sections[0].sentences
    assert any(item.get('reason_code') == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED for item in diagnostics)
    observed = observed_review_outcomes(diagnostics)
    assert len(observed) == 1
    assert observed[0]['reason_code'] == FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED
    assert observed[0]['verification_items'] == ('시점',)
    assert final_review_outcomes(verified, diagnostics) == observed
    public = render_report('토스페이먼츠', verified, tuple(FRAGMENTS.values()), None, as_of_date='2026-10-22')
    assert not public.fact_records
    assert public.sections[0].guidance_lines
