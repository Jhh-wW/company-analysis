"""제재표의 대책 명사구와 같은 조치의 명시 완료를 구분한다."""
import json

import pytest

from src.features.composer.challenge_event_scope import sanction_response_completed_problem
from src.features.composer.verify import _apply_grounding
from src.features.composer.logic import build_section_prompt
from src.features.composer.port import CollectedFragment


SLOT = 'past_changes:completed_execution'


def _table(response, *, header='이행 및 재발방지 대책', date='2024.01.16', actor='당사'):
    return f'제재일 | 조치대상자 | 조치내용 | {header}; {date} | {actor} | 과태료 부과 | {response}'


def _problem(claim, source):
    return sanction_response_completed_problem(claim, {'1': source}, claim_slot=SLOT)


@pytest.mark.parametrize(('response', 'claim'), [
    ('관리감독 및 공시교육 강화', '당사는 관리감독 및 공시교육 강화 조치를 이행하였다.'),
    ('증빙서류 보완 및 검증 절차 강화', '당사는 검증 절차를 강화하였다.'),
    ('과태료 납부 완료관련 프로세스 개선', '당사는 과태료를 납부 완료하고 관련 프로세스를 개선하였다.'),
    ('과태료 납부 완료. 관련 프로세스 개선', '당사는 관련 프로세스를 개선하였다.'),
    ('안전설비 설치 예정', '당사는 안전설비를 설치하였다.'),
    ('안전설비 설치 완료 예정', '당사는 안전설비 설치를 완료했다.'),
    ('교육을 실시하지 않았다', '당사는 교육을 실시하였다.'),
    ('교육을 강화했을 경우', '당사는 교육을 강화하였다.'),
    ('교육 강화 완료 여부 미확인', '당사는 교육을 강화하였다.'),
    ('교육 강화 완료 확인 중', '당사는 교육을 강화하였다.'),
    ('교육 강화 완료 추정', '당사는 교육을 강화하였다.'),
    ('교육 강화', '당사는 재발방지 대책을 이행하였다.'),
])
def test_nominal_or_unreal_response_cannot_be_completed(response, claim):
    assert _problem(claim, _table(response)) == 'time_invalid'


@pytest.mark.parametrize(('response', 'claim'), [
    ('안전설비 설치 및 적용 완료', '당사는 안전설비 설치 및 적용을 완료했다.'),
    ('관련 프로세스를 개선하였다', '당사는 관련 프로세스를 개선하였다.'),
    ('공시교육을 실시하였다', '당사는 공시교육을 실시하였다.'),
    ('교육 실시 완료', '당사는 교육 실시를 완료하였다.'),
    ('교육 강화 완료', '당사는 재발방지 대책을 이행하였다.'),
    ('관리감독 및 공시교육 강화 조치를 이행하였다', '당사는 관리감독 및 공시교육 강화 조치를 이행하였다.'),
    ('안전관리 제도 도입 완료', '당사는 안전관리 제도를 도입 완료했다.'),
    ('과태료 납부 완료관련 프로세스 개선', '당사는 과태료를 납부 완료했다.'),
    ('제재금 5백만원 납부 완료관련 프로세스 개선', '당사는 제재금 납부를 완료했으며, 공시표에 관련 프로세스 개선을 대책으로 제시했다.'),
    ('프로세스 개선', '당사는 프로세스 개선을 대책으로 밝혔다.'),
    ('교육 강화', '당사는 교육 강화를 대책으로 제시했다고 밝혔다.'),
])
def test_explicit_same_action_completion_and_nominal_attribution_are_preserved(response, claim):
    assert _problem(claim, _table(response)) == ''


def test_implementation_header_and_other_response_cell_do_not_supply_completion():
    source = ('제재일 | 조치대상자 | 조치내용 | 처벌 또는 조치에 대한 회사의 이행현황 | 재발방지를 위한 회사의 대책; '
              '2024.01.16 | 당사 | 과태료 부과 | 과태료 납부 완료 | 공시교육 강화')
    assert _problem('당사는 공시교육을 강화하였다.', source) == 'time_invalid'
    assert _problem('당사는 과태료를 납부 완료했다.', source) == ''
    assert _problem('당사는 교육을 강화하였다.', _table('교육 강화', header='처벌 또는 조치에 대한 회사의 이행현황')) == 'time_invalid'


def test_other_row_completion_cannot_supply_selected_event():
    source = _table('교육 강화') + '; 2025.02.17 | 당사 | 과태료 부과 | 교육 강화 완료'
    assert _problem('당사는 2024년 1월 16일 교육을 강화하였다.', source) == 'time_invalid'
    assert _problem('당사는 2025년 2월 17일 교육을 강화하였다.', source) == ''
    assert _problem('당사는 교육을 강화하였다.', source) == 'time_invalid'


def test_other_actor_and_activity_cannot_supply_completion():
    source = _table('안전교육 강화') + '; 2024.01.16 | 종속기업 | 과태료 부과 | 공시교육 강화 완료'
    assert _problem('당사는 공시교육을 강화하였다.', source) == 'time_invalid'
    assert _problem('당사는 공시교육을 강화하였다.', _table('안전교육 강화 완료; 공시교육 강화')) == 'time_invalid'


@pytest.mark.parametrize('source', [
    '당사는 안전교육을 강화하였다.',
    '일자 | 조치대상자 | 조치내용 | 비고; 2024.01.16 | 당사 | 교육 안내 | 교육 강화',
    '연구과제 | 연구기간; 제어기 개선 | 2024.01~2030.12',
])
def test_other_documents_and_non_sanction_tables_are_unchanged(source):
    assert _problem('당사는 교육을 강화하였다.', source) == ''


@pytest.mark.parametrize('slot', ['', 'current_challenges:response', 'future_strategy:stated_plan', 'culture:work_culture'])
def test_guard_is_limited_to_completed_execution(slot):
    assert sanction_response_completed_problem('당사는 교육을 강화하였다.', {'1': _table('교육 강화')}, claim_slot=slot) == ''


def test_model_true_does_not_bypass_response_state():
    claim = '당사는 과태료를 납부 완료하고 프로세스를 개선하였다.'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': {}}]}, ensure_ascii=False)
    diagnostics = []
    result = _apply_grounding(raw, {1: '참'}, {1: (claim, {'1': _table('과태료 납부 완료프로세스 개선')})},
                             diagnostics=diagnostics, claim_slots_by_number={1: SLOT},
                             diagnostic_contexts={1: ('past_changes', '본문', claim)})
    assert result[1] != '참'
    assert any(item.get('reason_code') == 'time_invalid' for item in diagnostics)


def test_past_writer_keeps_nominal_response_raw_and_explains_attribution():
    raw = _table('과태료 납부 완료관련 프로세스 개선')
    fragment = CollectedFragment('1', '공시', raw, supported_claim_slots=(SLOT,))
    prompt = build_section_prompt('회사', 'past_changes', [fragment], None, show_supported_claim_slots=True, shared_evidence_prefix=True)
    assert raw in prompt
    assert '대책으로 제시됐다고 쓴다' in prompt
    assert '납부 완료는 교육·개선의 완료가 아니며' in prompt
    assert '연구과제명' in prompt
    assert fragment.text == raw
