"""미래 활동의 명시 조건은 선택형과 자유형에서 같은 절에 남아야 한다."""

import json

import pytest

from src.features.composer import verify
from src.features.composer.future_plan_constants import STATED_PLAN_SLOT
from src.features.composer.future_plan_guard import future_plan_prose_problem
from src.features.composer.future_proof_selection import (
    FutureProofCandidate, prepare_future_proof_options,
)
from src.features.composer.port import CollectedFragment


def proof(source):
    return {'미래근거': [{'근거': '1', '대상': '신공장', '활동': '증설',
                         '원문': source, '양태': '계획'}]}


@pytest.mark.parametrize('source,candidate', [
    ('회사는 허가를 받으면 신공장을 증설할 것이다.',
     '회사는 신공장을 증설할 것이라고 밝혔다.'),
    ('회사는 승인을 얻으면 신공장을 증설할 것이다.',
     '회사는 신공장을 증설할 것이라고 밝혔다.'),
    ('회사는 수요가 충족될 경우 신공장을 증설할 것이다.',
     '회사는 신공장을 증설할 예정이다.'),
    ('회사는 허가를 받으면 신공장을 증설할 것이다.',
     '회사는 자금이 있으면 신공장을 증설할 것이다.'),
    ('회사는 허가를 받으면 신공장을 증설할 것이다.',
     '회사는 허가를 받으면 창고를 매각할 것이며 신공장을 증설할 것이다.'),
])
def test_missing_changed_or_other_activity_condition_is_rejected(source, candidate):
    evidence = proof(source)
    assert future_plan_prose_problem(candidate, {'1': source}, evidence,
                                    claim_slot=STATED_PLAN_SLOT)
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '근거': ['1'],
                              '검증근거': evidence}]}, ensure_ascii=False)
    problems = {}
    assert verify._apply_grounding(raw, {1: '참'}, {1: (candidate, {'1': source})},
        diagnostic_contexts={1: ('future_strategy', '본문', candidate)},
        claim_slots_by_number={1: STATED_PLAN_SLOT}, prose_numbers=frozenset({1}),
        grounding_problems=problems) == {1: '근거결속실패'}
    assert problems
    fragment = CollectedFragment('1', '공시', source)
    assert prepare_future_proof_options({1: FutureProofCandidate(
        1, 'future_strategy', STATED_PLAN_SLOT, candidate, (fragment,))}) == {}


@pytest.mark.parametrize('source,candidate', [
    ('회사는 허가를 받으면 신공장을 증설할 것이다.',
     '회사는 허가를 받으면 신공장을 증설할 것이라고 밝혔다.'),
    ('회사는 허가를 받으면 창고를 매각할 것이며 신공장을 증설할 것이다.',
     '회사는 신공장을 증설할 것이다.'),
    ('회사는 허가를 받았고 신공장을 증설할 것이다.',
     '회사는 신공장을 증설할 것이다.'),
    ('허가 조건은 해제됐다. 회사는 신공장을 증설할 것이다.',
     '회사는 신공장을 증설할 것이다.'),
])
def test_same_condition_or_independent_unconditional_plan_is_preserved(source, candidate):
    assert not future_plan_prose_problem(candidate, {'1': source}, proof(source),
                                        claim_slot=STATED_PLAN_SLOT)


def test_condition_does_not_turn_denied_promise_into_positive_plan():
    source = '회사는 허가를 받으면 신공장을 증설하지 않을 것이다.'
    candidate = '회사는 허가를 받으면 신공장을 증설할 것이다.'
    assert future_plan_prose_problem(candidate, {'1': source}, proof(source),
                                    claim_slot=STATED_PLAN_SLOT)
