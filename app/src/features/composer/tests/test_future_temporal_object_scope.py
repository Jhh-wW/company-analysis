"""시점 부사를 앞 활동의 미래 양태로 빌리지 못하게 한다."""
import pytest

from src.features.composer.future_plan_constants import FUTURE_KEY, STATED_PLAN_SLOT
from src.features.composer.future_plan_guard import future_plan_prose_problem, has_forward_marker, _source_modality


@pytest.mark.parametrize('source,activity,expected', [
    ('당사는 성과를 경영진에게 보고하고 향후 방향성을 논의하며 위원회를 운영한다.', '성과를 경영진에게 보고하고', False),
    ('당사는 성과를 경영진에게 보고하고 내년 생산시설을 증설할 계획이다.', '성과를 경영진에게 보고하고', False),
    ('당사는 생산시설을 증설할 계획이다.', '생산시설을 증설', True),
    ('당사는 내년 생산시설을 증설한다.', '생산시설을 증설', True),
    ('당사는 향후 생산시설을 증설한다.', '생산시설을 증설', True),
    ('당사는 생산시설을 증설하겠습니다.', '생산시설을 증설', True),
    ('당사는 성과를 보고하고 있으며 향후 방향성을 논의할 계획이다.', '성과를 보고', False),
])
def test_temporal_after_activity_does_not_supply_its_modality(source, activity, expected):
    start = source.index(activity)
    mode, reason, _ = _source_modality(source, (start, start + len(activity)))
    assert bool(mode) is expected
    assert bool(reason) is not expected


def test_present_reporting_does_not_prove_future_discussion():
    source = '전략위원회를 통해 그 성과를 경영진에게 보고하고 향후 방향성을 논의하며 운영위원회를 주축으로 활동한다.'
    candidate = '회사는 전략위원회를 통해 성과를 경영진에게 보고하고 향후 방향성을 논의할 계획이다.'
    proof = {'근거': 'own', '대상': '전략위원회', '활동': '성과를 경영진에게 보고하고', '원문': source, '양태': '계획'}
    assert future_plan_prose_problem(candidate, {'own': source}, {FUTURE_KEY: [proof]}, claim_slot=STATED_PLAN_SLOT)


def test_explicit_future_discussion_is_preserved():
    source = '당사는 방향성을 논의할 계획이다.'
    proof = {'근거': 'own', '대상': '방향성', '활동': '논의', '원문': source, '양태': '계획'}
    assert not future_plan_prose_problem(source, {'own': source}, {FUTURE_KEY: [proof]}, claim_slot=STATED_PLAN_SLOT)


@pytest.mark.parametrize('source,expected', [
    ('당사는 향후 방향성을 논의하며 성과를 보고한다.', False),
    ('당사는 내년 방향성을 논의한다.', True),
    ('당사는 향후 방향성을 논의할 계획이다.', True),
    ('당사는 승인을 얻으면 향후 방향성을 논의할 계획이다.', True),
])
def test_direction_object_does_not_supply_discussion_tense(source, expected):
    activity = '논의'
    start = source.index(activity)
    mode, problem, _ = _source_modality(source, (start, start + len(activity)))
    assert bool(mode) is expected
    assert bool(problem) is not expected


def test_legacy_discussion_proof_does_not_upgrade_current_direction():
    source = '전략위원회를 통해 그 성과를 경영진에게 보고하고 향후 방향성을 논의하며 운영위원회를 주축으로 활동한다.'
    candidate = '회사는 전략위원회를 통해 성과를 경영진에게 보고하고 향후 방향성을 논의할 계획이다.'
    proof = {'근거': 'own', '대상': '방향성', '활동': '논의', '원문': source, '양태': '계획'}
    assert future_plan_prose_problem(candidate, {'own': source}, {FUTURE_KEY: [proof]}, claim_slot=STATED_PLAN_SLOT)


def test_current_direction_discussion_does_not_require_future_proof():
    source = '당사는 향후 방향성을 논의하며 운영위원회를 관리하고 있다.'
    assert not has_forward_marker(source)
    assert not future_plan_prose_problem(source, {'own': source}, None, claim_slot=STATED_PLAN_SLOT)
    assert has_forward_marker('당사는 향후 방향성을 논의할 계획이다.')
