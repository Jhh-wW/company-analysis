"""계획 상태의 좁은 배치 예외와 자기 원문·시점 운반을 검사한다."""
from copy import deepcopy
import json

import pytest

from src.features.composer.plan_status_constants import PLAN_STATUS_KEY, PLAN_STATUS_SLOT
from src.features.composer.plan_status_scope import (
    completed_execution_status_problem, plan_status_fact_state, plan_status_prose_problem,
)
from src.features.composer.review_evidence_ids import ReviewEvidenceContext, normalize_review_entry
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA
from src.features.composer.verify import _apply_grounding

CURRENT = '회사는 신공장 증설을 추진 중이다.'
PLAN = '회사는 신공장 증설을 내년까지 완료할 계획이다.'
SOURCE = CURRENT + ' ' + PLAN


def proof(progress=CURRENT, plan=PLAN, identifier='1'):
    return {PLAN_STATUS_KEY: [{'근거': identifier, '대상': '신공장', '활동': '증설',
                              '진행원문': progress, '계획원문': plan}]}


def check(text=CURRENT, source=SOURCE, evidence=None):
    return plan_status_prose_problem(text, {'1': source}, proof() if evidence is None else evidence,
                                     claim_slot=PLAN_STATUS_SLOT)


def test_same_plan_progress_preserves_punctuation_and_subject():
    assert check() == ''
    for owner in ('예시제조사는', '회사는'):
        current, plan = CURRENT.replace('회사는', owner), PLAN.replace('회사는', owner)
        assert check(current, current + ' ' + plan, proof(current, plan)) == ''


@pytest.mark.parametrize('progress', [
    '회사는 신공장 증설 예산을 확정했고, 별도 교육 사업을 추진 중이다.',
    '회사는 신공장 증설 예산을 확정했고 별도 교육 사업을 추진 중이다.',
    '지난해 회사는 신공장 증설을 추진 중이었다.',
    '회사는 신공장 증설을 완료했다.',
    '회사는 신공장 증설을 취소했다.',
    '경쟁사는 신공장 증설을 추진 중이다.',
    '회사는 신공장 증설을 추진하고 있지 않다.',
    '조건을 충족하는 경우 회사는 신공장 증설을 추진 중이다.',
])
def test_other_activity_period_actor_or_state_cannot_support_progress(progress):
    assert check(source=progress + ' ' + PLAN, evidence=proof(progress))


@pytest.mark.parametrize('plan', [
    '회사는 신공장 증설을 내년까지 완료할 계획이었으나 취소했다.',
    '회사는 신공장 증설을 내년까지 완료할 계획은 없다.',
    '회사는 다른 공장 증설을 내년까지 완료할 계획이다.',
    '인수가 완료될 경우 회사는 신공장 증설을 완료할 계획이다.',
])
def test_other_target_conditional_or_cancelled_plan_cannot_support_progress(plan):
    assert check(source=CURRENT + ' ' + plan, evidence=proof(plan=plan))


@pytest.mark.parametrize('change', [
    {'근거': '2'}, {'진행원문': '조작된 원문'}, {'계획원문': CURRENT},
    {'알수없는필드': '값'}, {'대상': '다른공장'}, {'활동': '판매'},
])
def test_closed_proof_checks_exact_quotes_and_own_citation(change):
    evidence = proof()
    evidence[PLAN_STATUS_KEY][0].update(change)
    assert check(evidence=evidence)


def test_conditional_refund_and_routine_work_are_not_plan_status():
    assert check('회사는 의무 미이행시 보조금을 상환해야 한다.')
    assert check('회사는 고객 상담 업무를 수행 중이다.')
    assert check(evidence={})


def test_other_slots_and_future_claims_keep_existing_contract():
    assert plan_status_prose_problem(CURRENT, {}, {}, claim_slot='future_strategy:stated_plan') == ''
    assert plan_status_prose_problem(PLAN, {}, {}, claim_slot=PLAN_STATUS_SLOT) == ''


@pytest.mark.parametrize('state,word,time', [
    ('in_progress', '추진 중이다', 'present'), ('paused', '보류한 상태다', 'present'),
    ('cancelled', '취소했다', 'present'), ('completed', '완료했다', 'present'),
])
def test_explicit_current_plan_lifecycle_state_and_time_are_preserved(state, word, time):
    claim = '현재 회사는 신공장 증설을 ' + word + '.'
    assert check(claim, claim + ' ' + PLAN, proof(claim)) == ''
    assert plan_status_fact_state(claim, PLAN_STATUS_SLOT) == (time, state)


def test_completed_execution_rejects_progress_only_and_preserves_completion():
    assert completed_execution_status_problem(CURRENT, 'past_changes:completed_execution')
    assert completed_execution_status_problem('회사는 공장을 설립했고 신공장 증설을 추진 중이다.',
                                              'past_changes:completed_execution') == ''


def test_nested_status_proof_checks_owned_ids_and_display_aliases():
    context = ReviewEvidenceContext(frozenset({'1', '2'}), {1: frozenset({'1'})}, {1: frozenset({'1'})})
    row = {'번호': 1, '근거': ['1'], '검증근거': proof(identifier='조각 1')}
    normalized, valid = normalize_review_entry(row, context)
    assert valid and normalized['검증근거'][PLAN_STATUS_KEY][0]['근거'] == '1'
    row['검증근거'] = proof(identifier='2')
    assert normalize_review_entry(row, context)[1] is False
    schema = FLAT_REVIEW_SCHEMA['$defs']['grounding']
    assert PLAN_STATUS_KEY in schema['properties'] and PLAN_STATUS_KEY not in schema['required']


def test_grounding_preserves_supported_status_and_rejects_refund_condition():
    evidence = proof()
    before = deepcopy(evidence)
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': evidence}]}, ensure_ascii=False)
    result = _apply_grounding(raw, {1: '참'}, {1: (CURRENT, {'1': SOURCE})},
                             diagnostic_contexts={1: ('future_strategy', '본문', CURRENT)},
                             claim_slots_by_number={1: PLAN_STATUS_SLOT})
    assert result[1] == '참' and evidence == before
    result = _apply_grounding(raw, {1: '참'}, {1: ('의무 미이행시 보조금은 상환 대상이 된다.', {'1': SOURCE})},
                             diagnostic_contexts={1: ('future_strategy', '본문', '의무 미이행시 보조금은 상환 대상이 된다.')},
                             claim_slots_by_number={1: PLAN_STATUS_SLOT})
    assert result[1] != '참'


def test_one_proof_cannot_cover_another_activity_with_same_state():
    assert check('회사는 신공장 증설을 추진 중이며 물류센터 매각을 추진 중이다.')


def test_completion_mismatch_diagnostic_survives_transport():
    from hashlib import sha256
    from src.shared.report_quality.review_diagnostics import observed_review_outcomes
    diagnostics = []
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]})
    result = _apply_grounding(raw, {1: '참'}, {1: (CURRENT, {'1': SOURCE})},
                             diagnostics=diagnostics,
                             diagnostic_contexts={1: ('past_changes', '본문', CURRENT)},
                             claim_slots_by_number={1: 'past_changes:completed_execution'})
    assert result[1] != '참'
    observed = observed_review_outcomes(diagnostics)
    assert observed[0]['reason_code'] == 'completed_execution_state_mismatch'
    assert observed[0]['candidate_sha256'] == sha256(CURRENT.encode()).hexdigest()


def test_verified_status_fact_preserves_time_and_plan_status():
    from dataclasses import replace
    from src.features.composer.tests.test_prose_facts import _sentence, _source
    from src.features.composer.prose_facts import ProseEvidence, build_verified_prose_fact
    sentence = replace(_sentence(), text=CURRENT, citations=('1',), planned_claim_slot=PLAN_STATUS_SLOT)
    fact = build_verified_prose_fact(sentence, section_id='future_strategy', company_name='예시회사', as_of_date='2026-09-30',
                                   evidence=(ProseEvidence('1', _source(1, SOURCE), SOURCE),))
    assert fact is not None and fact.time_state == 'present' and fact.plan_status == 'in_progress'
