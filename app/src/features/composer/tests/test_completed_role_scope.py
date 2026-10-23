"""현재 운영역할을 완료 이력으로 봉인하지 않는 배치 경계."""
import json

import pytest

from src.features.composer.plan_status_constants import (
    COMPLETED_EXECUTION_SLOT, COMPLETED_EXECUTION_STATE_MISMATCH,
)
from src.features.composer.plan_status_scope import completed_execution_status_problem
from src.features.composer.verify import _apply_grounding


@pytest.mark.parametrize('claim', [
    '회사는 연구위원회를 운영하고 있다.',
    '회사는 연구위원회가 성과를 보고하고 향후 방향성을 논의하는 구조를 운영하고 있다.',
    '회사는 의사결정 체계를 갖추고 있다.',
    '연구위원회는 경영진으로 구성되어 있으며 회사는 이를 운영하고 있다.',
    '회사는 안전관리 제도를 운영 중이다.',
    '회사의 안전관리 체계는 운영되고 있다.',
    '위원회 성과를 경영진에게 보고하는 구조로 운영되고 있다.',
    '회사의 의사결정 체계는 위원회를 통해 수행되고 있으며 향후 방향을 논의한다.',
    '회사는 연구위원회를 운영하며 향후 방향성을 논의하고 있다.',
    '경쟁사는 연구위원회를 설립했고 회사는 연구위원회를 운영하고 있다.',
    '회사는 연구위원회를 설립했다고 가정하며 현재 조직을 운영하고 있다.',
    '회사는 연구위원회를 설립했을 경우 조직을 운영하고 있다.',
    '회사는 연구위원회를 설립하지 않았으며 현재 조직을 운영하고 있다.',
    '경영진으로 구성된 전략위원회와 운영위원회를 통해 성과를 보고하는 구조로 운영되고 있다.',
])
def test_current_role_without_claimed_completion_is_not_completed_execution(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == COMPLETED_EXECUTION_STATE_MISMATCH


@pytest.mark.parametrize('claim', [
    '회사는 연구위원회를 설립했고 현재 연구위원회를 운영하고 있다.',
    '회사는 연구위원회를 설립했으며 앞으로 운영 범위를 확대할 계획이다.',
    '회사는 지난해 안전관리 제도를 도입했으며 현재 이를 운영하고 있다.',
    '회사는 2018년에 설립된 연구위원회를 운영하고 있다.',
    '회사는 공장을 준공했다.',
    '회사는 안전교육을 실시했다.',
    '회사는 제품을 공급하고 있다.',
    '회사는 ESG 대응 원칙을 정하고 있다.',
    '회사는 위원회 운영을 개시했으며 현재 보고 구조로 운영되고 있다.',
    '회사는 위원회를 설립했고 현재 보고 체계가 운영되고 있다.',
    '회사는 위원회 운영을 개시했으며 향후 운영 범위를 확대할 계획이다.',
    '회사는 경영진으로 위원회를 구성했으며 현재 보고 구조로 운영되고 있다.',
    '2018년에 구성된 위원회를 통해 현재 보고 구조로 운영되고 있다.',
])
def test_actual_completion_and_other_present_statements_keep_existing_contract(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == ''


@pytest.mark.parametrize('slot', ['culture:work_culture', 'current_challenges:response', 'future_strategy:plan_status', ''])
def test_other_slots_preserve_current_role(slot):
    assert completed_execution_status_problem('회사는 연구위원회를 운영하고 있다.', slot) == ''
    assert completed_execution_status_problem('회사의 연구위원회는 운영되고 있다.', slot) == ''


def test_passive_current_role_is_rejected_after_model_true():
    claim = '회사의 위원회 성과를 보고하는 구조로 운영되고 있다.'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]}, ensure_ascii=False)
    diagnostics = []
    result = _apply_grounding(
        raw, {1: '참'}, {1: (claim, {'1': '위원회는 성과를 보고하고 방향을 논의한다.'})},
        diagnostics=diagnostics, claim_slots_by_number={1: COMPLETED_EXECUTION_SLOT},
        diagnostic_contexts={1: ('past_changes', '본문', claim)},
    )
    assert result[1] != '참'
    assert any(item.get('reason_code') == COMPLETED_EXECUTION_STATE_MISMATCH for item in diagnostics)


@pytest.mark.parametrize('source', [
    '회사는 연구위원회를 운영하고 있다.',
    '회사는 2018년에 연구위원회를 설립했다. 현재 연구위원회를 운영하고 있다.',
    '회사는 공장을 준공했다. 현재 연구위원회를 운영하고 있다.',
])
def test_own_source_completion_cannot_supply_an_unstated_completed_claim(source):
    claim = '회사는 연구위원회를 운영하고 있다.'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]}, ensure_ascii=False)
    diagnostics = []
    result = _apply_grounding(
        raw, {1: '참'}, {1: (claim, {'1': source})}, diagnostics=diagnostics,
        diagnostic_contexts={1: ('past_changes', '본문', claim)},
        claim_slots_by_number={1: COMPLETED_EXECUTION_SLOT},
    )
    assert result[1] != '참'
    assert any(item.get('reason_code') == COMPLETED_EXECUTION_STATE_MISMATCH for item in diagnostics)


@pytest.mark.parametrize('claim', [
    '회사는 쇼핑몰 창업자를 대상으로 홈택스에 접속하지 않고도 사업자등록 신청을 '
    '바로 할 수 있는 서비스를 제공하고 있으며, 신청 후 최소 3일 뒤 국세청으로부터 '
    '사업자등록 신청 완료 메시지를 받을 수 있다고 안내하고 있다.',
    '회사는 쇼핑몰 운영자를 위한 마케팅 정보 콘텐츠를 공식 채널을 통해 제공하고 '
    '있으며, 하반기 소비 시즌 프로모션 전략 등을 다루는 콘텐츠가 포함되어 있다.',
    '회사의 상담 서비스가 온라인으로 제공되고 있다.',
])
def test_current_service_is_not_a_completed_company_change_after_review(claim):
    """실제 생성에서 참 판정을 받은 현재 소개를 과거 실행으로 승격하지 않는다."""
    diagnostics = []
    result = _apply_grounding(
        json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]}, ensure_ascii=False),
        {1: '참'}, {1: (claim, {'1': claim})}, diagnostics=diagnostics,
        diagnostic_contexts={1: ('past_changes', '본문', claim)},
        claim_slots_by_number={1: COMPLETED_EXECUTION_SLOT},
    )
    assert result[1] != '참'
    assert any(item.get('reason_code') == COMPLETED_EXECUTION_STATE_MISMATCH for item in diagnostics)
    assert completed_execution_status_problem(claim, 'portfolio:product_role') == ''


@pytest.mark.parametrize('claim', [
    '회사는 2025년 상담 서비스를 출시했고 현재 서비스를 제공하고 있다.',
    '회사는 지난해 교육 콘텐츠를 도입했으며 현재 공식 채널로 제공하고 있다.',
    '회사는 상담 서비스를 개시했다. 현재 서비스를 제공하고 있다.',
])
def test_actual_service_launch_is_preserved_with_current_service_description(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == ''


@pytest.mark.parametrize('claim', [
    '회사는 2025년에 출시한 상담 서비스를 현재 제공하고 있다.',
    '회사는 지난해 도입한 교육 콘텐츠를 현재 제공하고 있다.',
    '회사는 상담 서비스 도입을 완료한 뒤 현재 서비스를 제공하고 있다.',
    '당사가 2025년에 출시한 서비스를 현재 제공하고 있다.',
    '회사는 온라인에서 출시한 상담 서비스를 현재 제공하고 있다.',
])
def test_explicit_self_completion_modifier_is_not_just_current_introduction(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == ''


@pytest.mark.parametrize('claim', [
    '회사는 상담 서비스 도입을 완료한 경우 현재 서비스를 제공하고 있다.',
    '회사는 상담 서비스 도입을 완료한 것이 아님을 밝히며 기존 서비스를 제공하고 있다.',
    '회사는 상담 서비스 도입을 완료한 바 없음에도 기존 서비스를 제공하고 있다.',
    '경쟁사가 출시한 서비스를 회사는 현재 제공하고 있다.',
    '회사는 경쟁사가 출시한 서비스를 현재 제공하고 있다.',
    '회사는 경쟁사에서 출시한 서비스를 현재 제공하고 있다.',
    '회사는 자회사에서 도입한 서비스를 현재 제공하고 있다.',
    '회사는 고객이 신청을 완료한 뒤 상담 서비스를 제공하고 있다.',
    '회사는 신청을 완료한 고객에게 상담 서비스를 제공하고 있다.',
])
def test_completion_modifier_keeps_condition_denial_and_other_actor_boundaries(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == COMPLETED_EXECUTION_STATE_MISMATCH
    assert completed_execution_status_problem(claim, 'portfolio:product_role') == ''


def test_completion_only_in_source_does_not_change_current_candidate():
    claim = '회사는 상담 서비스를 제공하고 있다.'
    source = '회사는 2025년에 출시한 상담 서비스를 현재 제공하고 있다.'
    result = _apply_grounding(
        json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]}, ensure_ascii=False),
        {1: '참'}, {1: (claim, {'1': source})},
        diagnostic_contexts={1: ('past_changes', '본문', claim)},
        claim_slots_by_number={1: COMPLETED_EXECUTION_SLOT},
    )
    assert result[1] != '참'


@pytest.mark.parametrize('claim', [
    '가온페이는 2025년에 출시한 상담 서비스를 현재 제공하고 있다.',
    '주식회사 가온은 2025년에 도입한 상담 서비스를 현재 제공하고 있다.',
])
def test_new_service_guard_defers_named_completed_actor_to_existing_review(claim):
    # 회사명 입력이 없는 가드에서 자기 회사로 추정하지 않는다. 이전 의미 검수에 남긴다.
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == ''
    result = _apply_grounding(
        json.dumps({'판정': [{'번호': 1, '결과': '거짓', '검증근거': {}}]}, ensure_ascii=False),
        {1: '거짓'}, {1: (claim, {'1': claim})},
        diagnostic_contexts={1: ('past_changes', '본문', claim)},
        claim_slots_by_number={1: COMPLETED_EXECUTION_SLOT},
    )
    assert result[1] == '거짓'


@pytest.mark.parametrize('actor', ['경쟁사', '자회사', '고객', '관계회사'])
def test_named_subject_deferral_does_not_cover_explicit_other_actors(actor):
    claim = f'{actor}가 출시한 상담 서비스를 현재 제공하고 있다.'
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == COMPLETED_EXECUTION_STATE_MISMATCH


def test_named_modifier_does_not_expand_preexisting_organization_guard():
    claim = '가온페이는 2025년에 설립한 연구위원회를 현재 운영하고 있다.'
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == COMPLETED_EXECUTION_STATE_MISMATCH


@pytest.mark.parametrize('claim', [
    '가온페이는 상담 서비스를 현재 제공하고 있다.',
    '가온페이는 출시한 경우 상담 서비스를 현재 제공하고 있다.',
    '가온페이는 출시한 것이 아니라 기존 서비스를 현재 제공하고 있다.',
])
def test_named_service_guard_keeps_current_only_condition_and_denial(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == COMPLETED_EXECUTION_STATE_MISMATCH
