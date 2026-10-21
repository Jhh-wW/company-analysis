"""실제 6차 잘린 입력의 참 판정은 재승인 없이 거절하고 날짜를 보존한다."""

from dataclasses import replace
from pathlib import Path
import json
import re

import pytest

from src.features.composer.litigation_timing import litigation_timing_problem
from src.features.composer.litigation_timing_constants import LITIGATION_TIMING_UNSUPPORTED
from src.features.composer.logic import build_section_prompt
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.composer.tests.test_pipeline import _FakeReviewer
from src.features.composer.tests.test_prose_facts import _source
from src.features.composer.verify import _apply_grounding, _numeric_disposal, _review_fragment_metadata, verify_report
from src.features.composer.verify import NUMERIC_PASS


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/sixth_litigation.json').read_text(encoding='utf-8'))
SOURCE = re.search(r'<P>(\(4\) 당기말.*?</P>)', FIXTURE['xml_excerpt']).group(1).removesuffix('</P>')
PERIOD = '2025-01-01/2025-12-31'
CURRENT = FIXTURE['writer_response']['문장들'][0]['글']
DATED = '2025년 12월 31일 기준 ' + CURRENT


def _fragment(source=SOURCE, period=PERIOD):
    return CollectedFragment('6', '소송·분쟁', source, document_date='2026-03-31',
        document_title='감사보고서 (2025.12)', reporting_period=period, source_publisher='예시회사')


def _problem(claim, source=SOURCE, period=PERIOD):
    return litigation_timing_problem(claim, {'6': source}, {'6': _fragment(source, period)}, section_id='current_challenges')


@pytest.mark.parametrize('claim', [CURRENT, '당기말 현재 ' + CURRENT,
    '2026년 10월 22일 기준 ' + CURRENT, '2026년 3월 31일 기준 ' + CURRENT,
    DATED + ' 현재도 소송이 계류 중이다.', '2025년 12월 32일 기준 ' + CURRENT,
    '2025년 12월 31일 이후에도 계류 중인 소송사건은 9건이다.',
    '2025년 12월 31일 기준일을 지나 현재 계류 중인 소송사건은 9건이다.'])
def test_missing_wrong_or_reaffirmed_current_date_is_rejected(claim):
    assert _problem(claim) == LITIGATION_TIMING_UNSUPPORTED


@pytest.mark.parametrize('prefix', ['2025년 말 기준 ', '2025-12-31 현재 ', '2025.12.31 기준 ', '2025년 12월 31일 기준 '])
def test_explicit_same_period_end_is_preserved(prefix):
    assert _problem(prefix + CURRENT) == ''


@pytest.mark.parametrize('claim', [
    '계류 중인 소송사건은 2025년 12월 31일 기준 9건이다.',
    '회사가 피고로 계류 중인 소송사건은 2025년 말 기준 9건이다.',
    '2025년 말 기준으로 회사가 피고로 계류 중인 소송은 9건이다.',
    '2025년 말 기준 총 9건의 소송이 계류 중이다.',
])
def test_date_directly_limits_postposed_case_count(claim):
    assert _problem(claim) == ''


@pytest.mark.parametrize('period', ['', '2026-01-01/2026-12-31', '2025-12-31/2025-01-01', '잘못된 기간'])
def test_unknown_or_different_document_period_cannot_supply_claim_date(period):
    assert _problem(DATED, period=period) == LITIGATION_TIMING_UNSUPPORTED


def test_prior_year_not_inferred_and_new_current_news_is_not_reclassified():
    assert _problem(DATED, source=SOURCE.replace('당기말', '전기말')) == LITIGATION_TIMING_UNSUPPORTED
    news = CollectedFragment('n', 'news', CURRENT, document_date='2026-10-22')
    assert litigation_timing_problem(CURRENT, {'n': CURRENT}, {'n': news}, section_id='current_challenges') == ''


def test_actual_sixth_saved_true_verdicts_are_rejected_without_reapproval():
    old_source = FIXTURE['legacy_fragment_text']
    cells = FIXTURE['writer_response']['경로표'][0]['칸']
    raw = json.dumps(FIXTURE['review_response'], ensure_ascii=False)
    problems = {}
    verdicts = _apply_grounding(raw, {37: '참', 57: '참'},
        {37: (CURRENT, {'6': old_source}), 57: (' | '.join(cells), {'6': old_source})},
        diagnostic_contexts={37: ('current_challenges', '본문', CURRENT), 57: ('current_challenges', '도식', ' | '.join(cells))},
        flow_cells_by_number={57: cells}, source_fragments_by_id={'6': _fragment(old_source, '')},
        grounding_problems=problems, baseline_date='2026-10-22')
    assert verdicts[37] != '참' and verdicts[57] != '참'
    assert problems == {37: LITIGATION_TIMING_UNSUPPORTED, 57: LITIGATION_TIMING_UNSUPPORTED}


def test_period_and_unmodified_source_reach_writer_and_independent_reviewer():
    fragment = _fragment()
    writer = build_section_prompt('예시회사', 'current_challenges', (fragment,), None)
    reviewer = _review_fragment_metadata(fragment)
    for prompt in (writer, reviewer):
        assert PERIOD in prompt
        assert '본문과 도식 모두 이 말일을 명시' in prompt
    assert SOURCE in writer
    assert '2026-03-31' in reviewer


def test_actual_review_path_keeps_explicit_historical_prose_only():
    fragment = _fragment()
    report = ComposedReport((ComposedSection('current_challenges', tuple(
        ComposedSentence(text, ('6',), '확인', planned_claim_slot='current_challenges:issue')
        for text in (CURRENT, DATED)
    )),))
    result = verify_report(report, (fragment,), None, _FakeReviewer(), baseline_date='2026-10-22')
    assert [s.text for s in result.sections[0].sentences] == [DATED]
    assert result.sections[0].sentences[0].grade == '확인'


def test_date_exemption_does_not_supply_case_count_or_money():
    sentence = ComposedSentence(DATED, ('6',), '확인', planned_claim_slot='current_challenges:issue')
    assert _numeric_disposal(sentence, {'6': _fragment()}, ()) == NUMERIC_PASS
    for claim in (DATED.replace('9건', '31건'), DATED.replace('9건', '2025건'),
                  DATED.replace('9건', '9건이며 소송금액은 2025억원')):
        assert _numeric_disposal(replace(sentence, text=claim), {'6': _fragment()}, ()) != NUMERIC_PASS
    assert _numeric_disposal(sentence, {'6': _fragment(period='')}, ()) != NUMERIC_PASS


@pytest.mark.parametrize('strict', [False, True])
def test_flow_review_uses_same_snapshot_date_boundary(strict):
    actual = tuple(FIXTURE['writer_response']['경로표'][0]['칸'])
    dated = ('2025년 12월 31일 기준 ' + actual[0], actual[1])
    report = ComposedReport((ComposedSection('current_challenges', (), flow_rows=(
        FlowRow(actual, ('6',)), FlowRow(dated, ('6',)),
    )),))
    result = verify_report(report, (_fragment(),), None, _FakeReviewer(), baseline_date='2026-10-22',
        allowed_fragment_ids_by_section={'current_challenges': frozenset({'6'})} if strict else None)
    assert [r.cells for r in result.sections[0].flow_rows] == [dated]
    if strict:
        assert result.sections[0].flow_rows[0].review_binding is not None


def test_public_fact_date_matches_explicit_source_period_and_old_claim_is_not_sealed():
    source = replace(_source(6, SOURCE), reporting_period=PERIOD, location='소송·분쟁')
    sentence = ComposedSentence(DATED, ('6',), '확인', planned_claim_slot='current_challenges:issue', verification_state='verified')
    kwargs = dict(section_id='current_challenges', company_name='예시회사', as_of_date='2026-10-22', evidence=(ProseEvidence('6', source, SOURCE),))
    result = evaluate_verified_prose_fact(sentence, **kwargs)
    assert result.fact is not None
    assert (result.fact.time_state, result.fact.as_of) == ('past', '2025-12-31')
    denied = evaluate_verified_prose_fact(replace(sentence, text=CURRENT), **kwargs)
    assert denied.fact is None and denied.reason_code == LITIGATION_TIMING_UNSUPPORTED
