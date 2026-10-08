"""현재 수행 술어를 명사형 목표나 다른 활동의 상태로 승인하지 않는다."""
import json

import pytest

from src.features.composer import verify
from src.features.composer.challenge_event_scope import response_current_activity_problem


@pytest.mark.parametrize('source,claim', [
    ('환경규제에 능동적 대응', '회사는 환경규제에 대응하고 있다.'),
    ('부품 수명 향상; 환경규제에 능동적 대응',
     '회사는 부품 수명을 향상시키는 방향으로 대응하고 있다.'),
    ('안전관리', '회사는 안전관리하고 있다.'),
    ('환경규제 대응', '회사는 환경규제에 대응하는 중이다.'),
    ('환경규제에 대응하기 위해 설비 개선을 진행 중이다.',
     '회사는 환경규제에 대응하고 있다.'),
    ('환경규제 대응; 협력사는 환경규제에 대응하고 있다.',
     '회사는 환경규제에 대응하고 있다.'),
    ('환경규제 대응; 제품 결함에 대응하고 있다.',
     '회사는 환경규제에 대응하고 있다.'),
    ('환경규제 대응 예정', '회사는 환경규제에 대응하고 있다.'),
    ('환경규제 대응 완료', '회사는 환경규제에 대응하고 있다.'),
    ('회사는 과거 환경규제에 대응하고 있었다.',
     '회사는 환경규제에 대응하고 있다.'),
    ('향후 환경규제에 대응하고 있다.', '회사는 환경규제에 대응하고 있다.'),
    ('진행 중인 대응: 환경규제 대응 완료', '회사는 환경규제에 대응하고 있다.'),
    ('환경규제 대응; 협력사는 환경규제에 대응하고 있다.',
     '회사는 환경규제에 대응하고 있다. 협력사는 설비 개선을 계획하고 있다.'),
    ('환경규제 대응 예정; 설비 개선 예정',
     '회사는 환경규제에 대응하고 있다. 설비 개선을 계획하고 있다.'),
    ('규제 대응 및 협력사는 생산설비 개선을 진행 중이다.',
     '회사는 규제에 대응하고 있다.'),
    ('회사는 규제에 대응하고 있지 않다.', '회사는 규제에 대응하고 있다.'),
    ('연구 목적은 규제에 대응하는 것이다.', '회사는 규제에 대응하고 있다.'),
    ('연구 목표는 안전관리하는 것이다.', '회사는 안전관리하고 있다.'),
])
def test_current_performance_requires_own_activity_state(source, claim):
    assert response_current_activity_problem(claim, {'a': source}) == 'time_invalid'


@pytest.mark.parametrize('source,claim', [
    ('회사는 환경규제에 대응하고 있다.', '회사는 환경규제에 대응하고 있다.'),
    ('환경규제 대응을 진행 중이다.', '회사는 환경규제에 대응하고 있다.'),
    ('현재 진행 중인 대응: 환경규제 대응', '회사는 환경규제에 대응하고 있다.'),
    ('진행 중인 활동: 안전관리', '회사는 안전관리하고 있다.'),
    ('회사는 환경규제에 대응하는 중이다.', '회사는 환경규제에 대응하고 있다.'),
    ('환경규제 대응', '회사는 환경규제에 대응하기 위해 설비를 개선할 계획이다.'),
    ('환경규제 대응', '대책에는 환경규제 대응이 기재되어 있다.'),
    ('환경규제 대응 완료', '회사는 환경규제 대응을 완료했다.'),
    ('환경규제 대응 예정', '회사는 환경규제에 대응할 예정이다.'),
    ('회사는 환경규제에 대응하고 있었다.', '회사는 환경규제에 대응하고 있었다.'),
    ('회사는 설비 개선 후 직원 교육을 추진하고 있다.',
     '회사는 설비 개선 후 직원 교육을 추진하고 있다.'),
    ('회사는 공급차질에 대응하고 있다. 고객사는 설비를 개선하고 있다.',
     '회사는 공급차질에 대응하고 있다.'),
    ('환경규제 대응 예정', '회사는 환경규제 대응을 계획하고 있다.'),
    ('회사는 환경규제에 대응하고 있다. 설비 개선 예정',
     '회사는 환경규제에 대응하고 있다. 설비 개선을 계획하고 있다.'),
    ('협력사는 환경규제에 대응하고 있다.', '협력사는 환경규제에 대응하고 있다.'),
    ('회사는 규제에 대응하고 있으며 해외 납품은 내년 시작할 예정이다.',
     '회사는 규제에 대응하고 있다.'),
    ('회사는 규제에 대응하고 있지만 해외 납품은 이미 완료했다.',
     '회사는 규제에 대응하고 있다.'),
    ('회사는 공급처를 다변화하는 대책으로 대응하고 있습니다.',
     '회사는 공급처 다변화로 대응하고 있다.'),
    ('회사는 원자재 부족에 대응한다.', '회사는 원자재 부족에 대응하고 있다.'),
    ('회사는 위험에 대응하는 것입니다.', '회사는 위험에 대응하고 있다.'),
    ('이사회 규정에 따라 필요한 사항을 정한다.',
     '이사회 규정에 따라 필요한 사항을 정하고 있다.'),
])
def test_same_current_activity_and_other_states_are_preserved(source, claim):
    assert not response_current_activity_problem(claim, {'a': source})


def test_current_performance_binds_the_same_event_row():
    source = ('제재조치일 | 조치대상자 | 이행 및 재발방지대책 ; '
              '2026.02.20 | 합성법인 | 환경규제 대응하고 있다')
    claim = '합성법인은 환경규제에 대응하고 있다.'
    assert not response_current_activity_problem(claim, {'a': source})
    nominal = source.replace('대응하고 있다', '대응')
    foreign = nominal + '; 2026.03.20 | 다른법인 | 환경규제 대응하고 있다'
    assert response_current_activity_problem(claim, {'a': foreign}) == 'time_invalid'


def test_model_true_does_not_approve_nominal_current_performance():
    claim = '회사는 환경규제에 대응하고 있다.'
    raw = json.dumps([{'번호': 1, '장': 'current_challenges', '근거': ['a'],
                       '결과': '참', '검증근거': {}}], ensure_ascii=False)
    problems = {}
    result = verify._apply_grounding(
        raw, {1: '참'}, {1: (claim, {'a': '환경규제에 능동적 대응'})},
        diagnostic_contexts={1: ('current_challenges', '본문', '검수')},
        claim_slots_by_number={1: 'current_challenges:response'},
        grounding_problems=problems,
    )
    assert result[1] != '참' and problems[1] == 'time_invalid'
