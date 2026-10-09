"""현재 정의와 명시 사업 연혁을 구분하는 무료 회귀."""
import json
import pytest

from src.features.composer.business_origin_scope import business_origin_scope_problem
from src.features.composer.scope_guard import flow_scope_problem, scope_problem
from src.features.composer.verify import (
    REVIEW_EVIDENCE_IDS_KEY, REVIEW_SECTION_KEY,
    _apply_grounding, _parse_grouped_verdicts, _parse_verdicts,
)


@pytest.mark.parametrize(('claim', 'sources', 'expected'), [
    ('당사는 센서라는 단일 품목 제조사에서 출발하여 현재 부품 사업을 영위한다.', {'1': '당사는 센서 사업과 부품 사업으로 구성된다.'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 출발했다.', {'1': '당사는 센서 사업에서 출발했다.'}, ''),
    ('당사는 센서라는 단일 품목 제조사에서 출발했다.', {'1': '당사는 센서라는 단일 품목 제조사에서 출발했다.'}, ''),
    ('당사는 센서라는 단일 품목 제조사에서 출발했다.', {'1': '당사는 센서 사업에서 출발했다.'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 부품 사업으로 확장하였다.', {'1': '당사는 센서 사업에서 부품 사업으로 확장하였다.'}, ''),
    ('당사는 센서 사업에서 부품 사업으로 확장하였다.', {'1': '당사는 센서 사업과 부품 사업을 영위한다.'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 출발했다.', {'1': '경쟁사는 센서 사업에서 출발했다.'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 출발했다.', {'1': '당사는 부품 사업에서 출발했다.'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 출발할 계획이다.', {'1': '현재 당사는 부품 사업을 영위한다.'}, ''),
    ('당사는 물류 공정에서 출발해 제품을 운송한다.', {'1': '물류 공정 설명이다.'}, ''),
    ('당사는 현재 센서와 부품 사업을 영위하는 기업으로 읽힌다.', {'1': '당사는 센서와 부품 사업을 영위한다.'}, ''),
    ('당사는 센서 사업에서 출발했다.', {'1': '1990 | 창업 | 센서 사업'}, ''),
    ('당사는 센서 사업에서 부품 사업으로 확장했다.', {'1': '당사는 창업 당시 센서 사업을 영위했다.', '2': '당사는 이후 부품 사업으로 확장했다.'}, ''),
    ('당사는 센서 사업에서 부품 사업으로 확장했다.', {'1': '당사는 창업 당시 센서 사업을 영위했다.', '2': '경쟁사는 이후 부품 사업으로 확장했다.'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 출발했다.', {'1': '당사는 설립되었으며 현재 센서 사업을 영위한다.'}, 'scope_condition_unbound'),
    ('당사는 센서라는 단일 품목 제조사에서 출발했다.', {'1': '당사는 창업 당시 센서 사업만 영위했다.'}, ''),
    ('당사는 센서 사업에서 출발했다.', {'1': '당사는 센서 사업에서 출발하지 않았다.'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 출발했다.', {'1': '경쟁사 연혁\n1990 | 창업 | 센서 사업'}, 'scope_condition_unbound'),
    ('당사는 센서 사업에서 출발했다.', {'1': '당사는 센서 사업부문을 인적분할하여 설립되었다.'}, ''),
    ('당사는 센서라는 단일 품목 제조사에서 출발했다.', {'1': '당사는 센서 사업부문을 인적분할하여 설립되었다.'}, 'scope_condition_unbound'),
    ('당사는 센서 제조사에서 출발하여 부품 사업으로 확장했다.', {'1': '당사는 센서 제조사로 출발했으며 이후 부품 사업으로 확장했다.'}, ''),
    ('당사는 센서 제조사에서 출발하여 부품 사업으로 확장했다.', {'1': '당사는 설립 당시 센서 제조 사업만 영위했다.', '2': '당사는 이후 부품 사업을 새로 추가했다.'}, ''),
])
def test_business_origin_own_history(claim, sources, expected):
    assert business_origin_scope_problem(claim, sources) == expected


def test_scope_dispatch_rejects_current_definition_as_origin():
    assert scope_problem('당사는 센서라는 단일 품목 제조사에서 출발했다.', {'1': '당사는 센서 및 부품 사업으로 구성된다.'}) == 'scope_condition_unbound'


@pytest.mark.parametrize('grouped', [False, True])
def test_interpreted_origin_model_true_still_needs_own_history(grouped):
    claim = '당사는 센서라는 단일 품목 제조사에서 출발하여 여러 사업으로 구성된 기업이 되었다.'
    sources = {'own': '당사는 센서 및 부품 사업으로 구성된다.'}
    row = {'번호': 1, '결과': '참', '근거': ['own']}
    if grouped:
        row.update({REVIEW_SECTION_KEY: 'identity', REVIEW_EVIDENCE_IDS_KEY: ['own']})
    raw = json.dumps({'판정': [row]}, ensure_ascii=False)
    parsed = (_parse_grouped_verdicts(raw, {1: 'identity'}, {1: frozenset({'own'})})
              if grouped else _parse_verdicts(raw))
    assert parsed == {1: '참'}
    problems = {}
    verdicts = _apply_grounding(raw, parsed, {1: (claim, sources)},
        diagnostic_contexts={1: ('identity', '본문', '해석')}, grounding_problems=problems)
    assert verdicts[1] != '참'
    assert problems[1] == 'scope_condition_unbound'


def test_origin_scope_in_summary_and_diagram():
    claim = '당사는 센서 사업에서 출발했다.'
    own = {'own': '당사는 센서 사업과 부품 사업을 영위한다.'}
    assert scope_problem(claim, own) == 'scope_condition_unbound'
    assert flow_scope_problem(('사업 기원', claim), own) == 'scope_condition_unbound'
