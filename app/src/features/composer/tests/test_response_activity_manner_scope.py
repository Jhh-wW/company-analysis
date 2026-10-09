"""명시 목적어·행동의 방식 부사는 진행 상태나 다른 대상을 대신하지 않는다."""

import json

import pytest

from src.features.composer import verify
from src.features.composer.challenge_event_scope import response_current_activity_problem


CLAIM = '회사는 광고 사업을 이행하고 있다.'


@pytest.mark.parametrize('claim,source', [
    (CLAIM, '당사는 광고 사업을 성공적으로 이행하고 있습니다.'),
    ('회사는 광고 사업을 성공적으로 이행하고 있다.', '당사는 광고 사업을 이행하고 있습니다.'),
    ('회사는 제품 공급을 이행하고 있다.', '당사는 제품 공급을 성공적으로 이행하고 있습니다.'),
    ('회사는 관련 인프라를 자체적으로 구축함으로써 광고 사업을 이행하고 있다.',
     '당사는 관련 인프라를 자체적으로 구축함으로써 광고 사업을 성공적으로 이행하고 있습니다.'),
])
def test_same_explicit_object_and_current_action_preserve_manner(claim, source):
    assert response_current_activity_problem(claim, {'a': source}) == ''


@pytest.mark.parametrize('source', [
    '당사는 콘텐츠 사업을 성공적으로 이행하고 있습니다.',
    '당사는 제품 공급을 성공적으로 이행하고 있습니다.',
    '당사는 광고 사업을 성공적으로 이행하지 않고 있습니다.',
    '당사는 광고 사업을 성공적으로 이행하지 못하고 있습니다.',
    '당사는 광고 사업을 성공적으로 이행할 예정입니다.',
    '향후 당사는 광고 사업을 성공적으로 이행하고 있습니다.',
    '당사는 광고 사업을 성공적으로 이행했습니다.',
    '당사는 광고 사업을 성공적으로 이행할 방침입니다.',
    '고객사는 광고 사업을 성공적으로 이행하고 있습니다.',
    '당사는 광고 사업을 2배 성공적으로 이행하고 있습니다.',
    '당사는 성공적으로 이행하고 있습니다.',
])
def test_manner_cannot_loan_object_actor_or_current_state(source):
    assert response_current_activity_problem(CLAIM, {'a': source}) == 'time_invalid'


def test_manner_does_not_waive_other_activity_semantic_review():
    assert response_current_activity_problem(
        '회사는 광고 사업을 구축하고 있다.',
        {'a': '당사는 광고 사업을 성공적으로 이행하고 있습니다.'},
    ) == ''


def test_model_true_keeps_same_current_object_without_manner_word():
    own = '당사는 광고 사업을 성공적으로 이행하고 있습니다.'
    raw = json.dumps([{'번호': 1, '근거': ['a'], '결과': '참', '검증근거': {}}], ensure_ascii=False)
    problems = {}
    result = verify._apply_grounding(
        raw, {1: '참'}, {1: (CLAIM, {'a': own})},
        diagnostic_contexts={1: ('current_challenges', '본문', '검수')},
        claim_slots_by_number={1: 'current_challenges:response'},
        grounding_problems=problems,
    )
    assert result[1] == '참'
    assert problems == {}
