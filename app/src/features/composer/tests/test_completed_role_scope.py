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
