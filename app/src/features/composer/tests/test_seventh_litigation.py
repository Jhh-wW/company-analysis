"""실제 7차 문형의 오프라인 후단 소비와 새 승인 없는 경계를 고정한다."""

from dataclasses import replace
from copy import deepcopy
from pathlib import Path
import hashlib
import json

import pytest

from src.features.composer.litigation_timing import litigation_timing_problem
from src.features.composer.grounding import grounding_problem
from src.features.composer.litigation_timing_constants import LITIGATION_TIMING_UNSUPPORTED
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow, filing_meta_from_raw
from src.features.composer.render import render_report
from src.features.composer.tests.test_pipeline import _FakeReviewer
from src.features.composer.verify import NUMERIC_PASS, _apply_grounding, _numeric_disposal, verify_report
from src.features.provenance.sources import has_valid_provenance_seal
from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.features.pipeline.supplementary_research_filter import filter_supplementary_research_report
from src.features.provenance.tests.test_sources import _formal_source_registry
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.models import ChapterEvidenceCandidates, EvidenceReadiness
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_quality.fact_binding import fact_primary_source_metadata_mismatches


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/seventh_litigation.json').read_text(encoding='utf-8'))
SOURCE = FIXTURE['source']
BODY = FIXTURE['body']['글']
CELLS = tuple(FIXTURE['flow']['칸'])
PERIOD = FIXTURE['reporting_period']
COMPANY = FIXTURE['source_publisher']


def _fragment(**kwargs):
    base = CollectedFragment('6', '소송·분쟁', SOURCE, document_date='2026-03-31',
        document_title='감사보고서 (2025.12)', source_document_id='20260331000296',
        reporting_period=PERIOD, source_publisher=COMPANY)
    return replace(base, **kwargs)


def _problem(claim, fragment=None):
    fragment = fragment or _fragment()
    return litigation_timing_problem(claim, {'6': fragment.text}, {'6': fragment}, section_id='current_challenges')


@pytest.mark.parametrize('claim', [BODY, ' | '.join(CELLS),
    BODY.replace('토스페이먼츠가', '토스페이먼츠 주식회사가'),
    BODY.replace('토스페이먼츠가', '(주)토스페이먼츠가')])
def test_same_publisher_and_same_count_parenthesis_keep_explicit_snapshot(claim):
    assert _problem(claim) == ''


@pytest.mark.parametrize('claim', [
    BODY.replace('토스페이먼츠가', '다른회사가'),
    BODY.replace('토스페이먼츠가', '토스페이먼츠 자회사가'),
    BODY.replace('피고', '원고'),
    '2025년 12월 31일 기준 회사가 원고로 계류 중인 소송은 9건이다.',
    '회사가 원고로 계류 중인 소송사건은 2025년 말 기준 9건이다.',
    BODY.replace('9건', '31건'),
    BODY + ' 이후에도 계류 중이다.',
    BODY + ' 현재까지 계류 중이다.',
    BODY.replace('기준 토스페이먼츠가', '기준일을 지나 현재 토스페이먼츠가'),
    '2025년 12월 31일 기준 공시를 작성했다. 피고 계류 소송은 9건이다.',
    '다른회사가 피고인 소송 9건 (2025-12-31 기준)',
    '피고 계류 소송 9건. 다른 문서 (2025-12-31 기준)',
    CELLS[0] + ' 이후에도 계류 중이다.',
    CELLS[0].replace('2025-12-31', '2026-03-31'),
    CELLS[0].replace('피고', '원고'),
    CELLS[0].replace('9건', '31건'),
])
def test_other_subject_date_sentence_and_persistent_claims_are_rejected(claim):
    assert _problem(claim) == LITIGATION_TIMING_UNSUPPORTED


@pytest.mark.parametrize('publisher', ['', '다른회사 주식회사'])
def test_named_subject_requires_this_source_publisher(publisher):
    assert _problem(BODY, _fragment(source_publisher=publisher)) == LITIGATION_TIMING_UNSUPPORTED


@pytest.mark.parametrize('period', ['', '2024-01-01/2024-12-31'])
def test_other_document_period_cannot_support_either_actual_form(period):
    for claim in (BODY, ' | '.join(CELLS)):
        assert _problem(claim, _fragment(reporting_period=period)) == LITIGATION_TIMING_UNSUPPORTED


def test_date_exemption_does_not_widen_count_or_money_pool():
    for claim in (BODY, ' | '.join(CELLS)):
        sentence = ComposedSentence(claim, ('6',), '확인', planned_claim_slot='current_challenges:issue')
        assert _numeric_disposal(sentence, {'6': _fragment()}, ()) == NUMERIC_PASS
        for altered in (claim.replace('9건', '31건'), claim.replace('9건', '2025건'),
                        claim.replace('4억원', '7억원')):
            assert _numeric_disposal(replace(sentence, text=altered), {'6': _fragment()}, ()) != NUMERIC_PASS


def test_actual_seventh_body_verdict_is_reconsumed_unchanged_offline():
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == FIXTURE['source_sha256']
    item = FIXTURE['review_body']
    assert item['번호'] == 40 and item['결과'] == '참' and item['근거'] == ['6']
    problems = {}
    verdicts = _apply_grounding(json.dumps({'판정': [item]}, ensure_ascii=False), {40: '참'},
        {40: (BODY, {'6': SOURCE})}, diagnostic_contexts={40: ('current_challenges', '본문', BODY)},
        source_fragments_by_id={'6': _fragment()}, grounding_problems=problems, baseline_date='2026-10-22')
    assert verdicts[40] == '참' and not problems


def _public(checked):
    return render_report(COMPANY, checked, (_fragment(),), None, as_of_date='2026-10-22',
        filing_meta=filing_meta_from_raw({'rcept_no': '20260331000296', 'rcept_dt': '20260331',
                                        'report_nm': '감사보고서 (2025.12)'}))


def _final_legacy_filter(public):
    # 정본 출처가 함께 있는 실제 형태의 등록부에서 legacy 정책을 검사한다.
    # 별도 fixture 출처는 소송 근거로 참조하지 않으며 source 6을 정본으로 승격하지 않는다.
    canonical, = _formal_source_registry(SOURCE_KIND_DART_BUSINESS_REPORT)
    assert canonical.number != public.citations[0].number
    mixed = replace(public, citations=[*public.citations, canonical])
    verifier = supplementary_research_source_verifier()
    assert verifier(public.citations[0], tuple(mixed.citations),
                    reference_date=public.as_of_date, evidence_text='') is None
    evidence = OfficialEvidenceCollectionResult('01492697', tuple(
        ChapterEvidenceCandidates('01492697', section, (), (), (), EvidenceReadiness.INSUFFICIENT,
                                  (), 0, 10000, 10000)
        for section in REQUIRED_EVIDENCE_SECTION_IDS
    ))
    return filter_supplementary_research_report(mixed, official_evidence=evidence, source_verifier=verifier)


def test_actual_body_keeps_date_through_review_render_and_source_fact_binding():
    sentence = ComposedSentence(BODY, ('6',), '확인', planned_claim_slot='current_challenges:issue')

    def captured_body_only(prompt):
        # 동일 주장·동일 원문·동일 기간만 소비하는 오프라인 시험. 새 모델 승인이 아니다.
        assert BODY in prompt and SOURCE in prompt and PERIOD in prompt and COMPANY in prompt
        result = json.loads(_FakeReviewer()(prompt))
        assert len(result['판정']) == 1
        row = dict(FIXTURE['review_body'], 번호=result['판정'][0]['번호'])
        return json.dumps({'판정': [row]}, ensure_ascii=False)

    report = ComposedReport((ComposedSection('current_challenges', (sentence,)),))
    checked = verify_report(report, (_fragment(),), None, captured_body_only, baseline_date='2026-10-22',
        allowed_fragment_ids_by_section={'current_challenges': frozenset({'6'})})
    assert [s.text for s in checked.sections[0].sentences] == [BODY]
    assert checked.sections[0].sentences[0].grade == '확인'
    public = _public(checked)
    fact, = public.fact_records
    source = next(s for s in public.citations if s.source_id == fact.source_id)
    assert fact.claim == BODY and (fact.time_state, fact.as_of) == ('past', '2025-12-31')
    assert source.publisher == COMPANY and source.reporting_period == PERIOD
    assert source.document_id == '20260331000296' and has_valid_provenance_seal(source)
    assert fact_primary_source_metadata_mismatches(fact, source) == ()
    assert not has_valid_provenance_seal(replace(source, reporting_period='2026-01-01/2026-12-31'))
    assert any(BODY in line for line, _ in public.sections[0].prose_lines)
    final = _final_legacy_filter(public)
    assert final.fact_records == public.fact_records
    assert final.sections[0].prose_lines == public.sections[0].prose_lines


def test_actual_flow_enters_review_and_renders_only_with_test_reviewer_approval():
    seen = []

    def synthetic_flow_review(prompt):
        # 실제 7차 도식은 사전검사에서 탈락하여 승인 응답이 없다. 이 부분은 기술 회귀다.
        assert CELLS[0] in prompt and SOURCE in prompt and PERIOD in prompt
        seen.append(prompt)
        result = json.loads(_FakeReviewer()(prompt))
        for row in result['판정']:
            row['검증근거'] = {'수치': [dict(FIXTURE['review_body']['검증근거']['수치'][0],
                                        표현='소송금액 약 4억원')]}
        return json.dumps(result, ensure_ascii=False)

    report = ComposedReport((ComposedSection('current_challenges', (), flow_rows=(FlowRow(CELLS, ('6',)),)),))
    checked = verify_report(report, (_fragment(),), None, synthetic_flow_review, baseline_date='2026-10-22',
        allowed_fragment_ids_by_section={'current_challenges': frozenset({'6'})})
    assert seen and [r.cells for r in checked.sections[0].flow_rows] == [CELLS]
    assert checked.sections[0].flow_rows[0].review_binding is not None
    public = _public(checked)
    assert public.sections[0].tables[0].rows == [list(CELLS)]
    assert public.citations[0].reporting_period == PERIOD
    final = _final_legacy_filter(public)
    assert final.sections[0].tables[0].rows == [list(CELLS)]
    denied = verify_report(report, (_fragment(),), None, lambda _: '{"판정":[{"번호":1,"결과":"거짓"}]}',
        baseline_date='2026-10-22', allowed_fragment_ids_by_section={'current_challenges': frozenset({'6'})})
    assert not denied.sections[0].flow_rows


def _ground(claim, source, row):
    return grounding_problem(claim, {'6': source}, row,
        litigation_sources=((source, PERIOD, COMPANY),))


@pytest.mark.parametrize('changed', ['약 7억원', '약 4%', '약 4만달러'])
def test_approximate_marker_cannot_change_value_dimension_or_currency(changed):
    row = deepcopy(FIXTURE['review_body'])
    proof = row['검증근거']['수치'][0]
    proof['표현'] = proof['표현'].replace('약 4억원', changed)
    proof['후보값'] = changed
    assert _ground(BODY.replace('약 4억원', changed), SOURCE, row)


def test_approximate_proof_requires_same_marker_at_actual_candidate_value():
    row = deepcopy(FIXTURE['review_body'])
    row['검증근거']['수치'][0]['표현'] = '소송금액은 4억원 규모'
    assert _ground(BODY.replace('약 4억원', '4억원'), SOURCE, row)


def test_original_offsets_bind_two_metrics_and_reject_duplicate_proof():
    claim = BODY + ' 보험료는 약 4억원이다.'
    source = SOURCE + ' 2025년 보험료는 405,535천원입니다.'
    row = deepcopy(FIXTURE['review_body'])
    proof = row['검증근거']['수치'][0]
    row['검증근거']['수치'].append(dict(proof, 표현='보험료는 약 4억원', 항목='보험료',
        원문='2025년 보험료는 405,535천원입니다.', 원문항목='보험료'))
    assert _ground(claim, source, row) == ''
    row['검증근거']['수치'][1] = deepcopy(proof)
    assert _ground(claim, source, row)
    assert _ground(BODY, SOURCE, {'검증근거': {'수치': [proof, deepcopy(proof)]}})


def test_date_exemption_does_not_relabel_other_metric_or_other_period_quote():
    claim = BODY + ' 보험료는 약 4억원이다.'
    source = SOURCE + ' 2024년 보험료는 405,535천원입니다.'
    row = deepcopy(FIXTURE['review_body'])
    proof = row['검증근거']['수치'][0]
    row['검증근거']['수치'].append(dict(proof, 표현='보험료는 약 4억원', 항목='보험료',
        원문='2024년 보험료는 405,535천원입니다.', 원문항목='보험료'))
    assert _ground(claim, source, row)
    row = deepcopy(FIXTURE['review_body'])
    row['검증근거']['수치'][0]['원문'] = '2024년 소송금액은 405,535천원입니다.'
    assert _ground(BODY, SOURCE + ' 2024년 소송금액은 405,535천원입니다.', row)


def test_full_expression_retains_original_date_and_amount_positions():
    row = deepcopy(FIXTURE['review_body'])
    row['검증근거']['수치'][0]['표현'] = BODY
    assert _ground(BODY, SOURCE, row) == ''


def test_generic_wrong_party_role_cannot_pass_later_grounding():
    claim = '2025년 12월 31일 기준 회사가 원고로 계류 중인 소송은 9건이다.'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '장': 'current_challenges', '근거': ['6']}]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: '참'}, {1: (claim, {'6': SOURCE})},
        diagnostic_contexts={1: ('current_challenges', '본문', claim)},
        source_fragments_by_id={'6': _fragment()}, grounding_problems=problems)
    assert result[1] != '참' and problems[1] == LITIGATION_TIMING_UNSUPPORTED
