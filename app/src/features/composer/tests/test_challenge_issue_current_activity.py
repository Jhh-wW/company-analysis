"""문제 설명에 섞인 명사형 현재 대응도 자기 활동 상태를 확인한다."""
import json

import pytest

from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_event_scope import response_current_activity_problem
from src.features.composer import verify


ISSUE = '회사는 환경 규제에 대응해야 하며, 저탄소 시스템 개발이 그 맥락에서 이루어지고 있다.'
NOMINAL_SOURCE = '저탄소 시스템; 환경 규제에 선제적 대응; 양산 적용 예정; 특허 출원 중'


def test_issue_slot_also_checks_passive_current_development():
    assert response_current_activity_problem(ISSUE, {'a': NOMINAL_SOURCE}) == 'time_invalid'
    assert challenge_business_problem(
        ISSUE, {'a': NOMINAL_SOURCE}, claim_slot='current_challenges:issue',
    ) == 'time_invalid'


def test_model_true_issue_with_nominal_development_is_rejected_in_production_entry():
    raw = json.dumps([{'번호': 1, '장': 'current_challenges', '근거': ['a'],
                       '결과': '참', '검증근거': {}}], ensure_ascii=False)
    problems = {}
    result = verify._apply_grounding(
        raw, {1: '참'}, {1: (ISSUE, {'a': NOMINAL_SOURCE})},
        diagnostic_contexts={1: ('current_challenges', '본문', '검수')},
        claim_slots_by_number={1: 'current_challenges:issue'},
        grounding_problems=problems,
    )
    assert result[1] != '참' and problems[1] == 'time_invalid'


@pytest.mark.parametrize('source', [
    '환경 규제 대응을 위해 저탄소 시스템을 개발하고 있다.',
    '환경 규제 대응을 위해 저탄소 시스템 개발이 이루어지고 있다.',
    '환경 규제 대응을 위해 저탄소 시스템 개발이 현재 진행되고 있다.',
    '환경 규제 대응을 위해 저탄소 시스템 개발이 중단 없이 진행되고 있다.',
    '환경 규제 대응을 위해 저탄소 시스템 개발이 중단하지 않고 수행되고 있다.',
    '환경 규제 대응을 위해 저탄소 시스템 개발이 중단 없이 진행 중이다.',
])
def test_explicit_same_current_activity_is_preserved(source):
    assert response_current_activity_problem(ISSUE, {'a': source}) == ''


@pytest.mark.parametrize('source', [
    '저탄소 시스템 개발이 이루어질 예정이다.',
    '저탄소 시스템 개발이 이루어졌다.',
    '저탄소 시스템 개발 완료; 양산 적용 예정',
    '저탄소 시스템 개발이 진행되고 있지 않다.',
    '저탄소 시스템 개발이 중단되었다.',
    '저탄소 시스템 개발 진행 중단',
    '저탄소 시스템 개발이 이루어지고 있지 못하다.',
    '저탄소 시스템 개발하지 않고 진행 중이다.',
    '과거 저탄소 시스템 개발이 이루어지고 있었다.',
])
def test_noncurrent_source_does_not_support_current_activity(source):
    assert response_current_activity_problem(ISSUE, {'a': source}) == 'time_invalid'


@pytest.mark.parametrize('candidate', [
    '회사는 환경 규제에 대응해야 하는 과제를 마주하고 있다.',
    '회사는 환경 규제에 대응해야 하며, 저탄소 시스템 개발이 이루어질 예정이다.',
    '회사는 환경 규제에 대응해야 하며, 저탄소 시스템 개발이 이루어졌다.',
    '회사는 환경 규제에 대응해야 하며, 저탄소 시스템 개발이 진행되고 있지 않다.',
    '회사는 환경 규제에 대응해야 하며, 저탄소 시스템 개발이 이루어지고 있지 못하다.',
])
def test_issue_only_or_noncurrent_candidate_is_not_changed(candidate):
    assert response_current_activity_problem(candidate, {'a': NOMINAL_SOURCE}) == ''


def test_other_activity_current_state_cannot_be_borrowed():
    source = '저탄소 시스템 개발 완료; 양산 적용이 이루어지고 있다.'
    assert response_current_activity_problem(ISSUE, {'a': source}) == 'time_invalid'
