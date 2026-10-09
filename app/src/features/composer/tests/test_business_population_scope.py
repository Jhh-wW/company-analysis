"""상반 매출 추세와 부문 투자 계획의 범위 경계를 확인한다."""
import hashlib
import json

import pytest

from src.features.composer.business_population_scope import (
    opposing_revenue_population_problem, section_investment_plan_problem,
)
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.scope_guard import scope_problem

SOURCE = (
    '장비제작부문 매출액은 전년 대비 감소하였습니다. '
    '유통사업 종료에 따라 매출이 감소하였으나, 센서 매출의 경우 수주량이 증가하여 전년 대비 증가하였습니다.'
)


def _context(source, title='[기타부문-시제품]'):
    digest = lambda s: hashlib.sha256(s.encode()).hexdigest()
    document = title + '\n' + source
    return json.dumps({
        'version': 'source-section-context-v1', 'document_id': 'document-a',
        'document_sha256': digest(document), 'text': title,
        'location': f'0-{len(title)}', 'text_sha256': digest(title),
        'scope_location': f'0-{len(document)}',
        'fragment_location': f'{len(title)+1}-{len(document)}',
        'fragment_sha256': digest(source),
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@pytest.mark.parametrize('candidate', [
    '장비제작 부문은 매출이 감소하였으나, 센서 수주량 증가로 전년 대비 증가하였다.',
    '장비제작부문은 매출이 감소했지만 센서 수주량 증가로 증가했다.',
])
def test_상위매출과하위품목매출의반대추세합치기를제한(candidate):
    assert opposing_revenue_population_problem(candidate, {'a': SOURCE}) == SCOPE_CONDITION_UNBOUND
    assert scope_problem(candidate, {'a': SOURCE}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('candidate', [
    '장비제작 부문은 매출이 감소하였으나, 센서 매출은 전년 대비 증가하였다.',
    '장비제작 부문은 매출이 감소하였으나, 센서의 매출은 전년 대비 증가하였다.',
    '장비제작 부문은 매출이 감소하였으나, 센서 수주량이 증가하였다.',
    '장비제작 부문 총매출은 감소했고 센서 매출은 증가했다.',
    '유통사업 종료 때문에 장비제작 부문 매출이 감소하였다.',
    '다른사업부문은 매출이 감소하였으나, 센서 수주량 증가로 전년 대비 증가하였다.',
])
def test_같은지표정상축약과다른지표다른사업을보존(candidate):
    assert opposing_revenue_population_problem(candidate, {'a': SOURCE}) == ''


def test_명시상반하위매출이없는원문을추정하지않음():
    source = '장비제작부문 매출은 감소하였으나 센서 수주량이 증가하였다.'
    assert opposing_revenue_population_problem(
        '장비제작부문은 매출이 감소하였으나 센서 수주량 증가로 전년 대비 증가하였다.', {'a': source},
    ) == ''


def test_수주량증가예외가뒤총매출증가주장을면제하지않음():
    candidate = '장비제작부문 매출은 감소했지만 센서 수주량은 증가했고 전년 대비 매출이 증가했다.'
    assert opposing_revenue_population_problem(candidate, {'a': SOURCE}) == SCOPE_CONDITION_UNBOUND


def test_앞하위매출증가를뒤상위매출주장이빌리지못함():
    candidate = '장비제작부문 매출은 감소했지만 센서 매출은 증가했고 부문 매출이 증가했다.'
    assert opposing_revenue_population_problem(candidate, {'a': SOURCE}) == SCOPE_CONDITION_UNBOUND


PLAN = '향후 투자 계획은 정보 시스템 고도화 및 생산성 향상을 위해 투자 수행 예정입니다.'


@pytest.mark.parametrize('candidate', [
    '회사는 정보 시스템 고도화 및 생산성 향상을 위해 투자를 수행할 예정이다.',
    '연결회사 전체는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
    '투자를 수행할 예정이다.',
    '시제품 부문은 고객을 지원한다. 회사는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
    '시제품 부문 계획과 달리 회사는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
    '시제품 부문은 고객을 지원하며 장비부문은 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
])
def test_특정부문계획을명시부문없이전체주장으로바꾸지못함(candidate):
    assert section_investment_plan_problem(candidate, {'a': PLAN}, {'a': _context(PLAN)}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('candidate', [
    '시제품 부문은 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
    '회사는 시제품 부문에서 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
    '시제품 사업의 생산성 향상을 위해 투자를 수행할 예정이다.',
    '시제품 사업부는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
])
def test_명시부문의같은투자계획보존(candidate):
    assert section_investment_plan_problem(candidate, {'a': PLAN}, {'a': _context(PLAN)}) == ''


def test_실제연결전체계획을부문표제로과잭제한하지않음():
    source = '연결회사 전체의 투자 계획은 정보 시스템 고도화를 위해 투자 수행 예정입니다.'
    assert section_investment_plan_problem(
        '연결회사 전체는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
        {'a': source}, {'a': _context(source)},
    ) == ''


def test_다른절의연결전체설명을계획범위로빌리지않음():
    source = '연결회사 전체는 세 부문으로 구성된다. ' + PLAN
    assert section_investment_plan_problem(
        '회사는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.',
        {'a': source}, {'a': _context(source)},
    ) == SCOPE_CONDITION_UNBOUND


def test_앞전체계획예외가뒤부문계획절까지끝내지않음():
    whole = '연결회사 전체의 투자 계획은 광고 시스템 고도화를 위해 투자 수행 예정입니다.'
    local = '시제품 부문은 생산성 향상을 위해 투자 수행 예정입니다.'
    candidate = '연결회사 전체는 광고 시스템 고도화를 위해 투자를 수행할 예정이다. 회사는 생산성 향상을 위해 투자를 수행할 예정이다.'
    assert section_investment_plan_problem(candidate, {'a': whole, 'b': local}, {'b': _context(local)}) == SCOPE_CONDITION_UNBOUND


def test_다른활동의전체계획이현재부문계획범위를빌려주지못함():
    whole = '연결회사 전체의 투자 계획은 광고 시스템 고도화를 위해 투자 수행 예정입니다.'
    assert section_investment_plan_problem(
        '회사는 생산성 향상을 위해 투자를 수행할 예정이다.',
        {'a': whole, 'b': PLAN}, {'b': _context(PLAN)},
    ) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('second_subject,expected', [
    ('회사는', SCOPE_CONDITION_UNBOUND), ('시제품 부문은', ''),
])
def test_같은문장의전체계획예외도다음계획을따로검사(second_subject, expected):
    whole = '연결회사 전체의 투자 계획은 광고 시스템 고도화를 위해 투자 수행 예정입니다.'
    local = '시제품 부문은 생산성 향상을 위해 투자 수행 예정입니다.'
    candidate = ('연결회사 전체는 광고 시스템 고도화를 위해 투자를 수행할 예정이며 '
                 + second_subject + ' 생산성 향상을 위해 투자를 수행할 예정이다.')
    assert section_investment_plan_problem(candidate, {'a': whole, 'b': local},
                                           {'b': _context(local)}) == expected


def test_다른부문의투자목적을현재부문에빌리지못함():
    source_a = '향후 투자 계획은 연구 설비 개선을 위해 투자 수행 예정입니다.'
    source_b = '향후 투자 계획은 공장 생산성 향상을 위해 투자 수행 예정입니다.'
    sources = {'a': source_a, 'b': source_b}
    contexts = {'a': _context(source_a), 'b': _context(source_b, '[기타부문-장비]')}
    assert section_investment_plan_problem(
        '시제품 부문은 공장 생산성 향상을 위해 투자를 수행할 예정이다.', sources, contexts,
    ) == SCOPE_CONDITION_UNBOUND
    assert section_investment_plan_problem(
        '시제품 부문은 연구 설비 개선을 위해 투자를 수행할 예정이다. '
        '장비 부문은 공장 생산성 향상을 위해 투자를 수행할 예정이다.', sources, contexts,
    ) == ''


def test_같은문장뒤다른사업주어는앞부문의생략주어가아님():
    candidate = ('시제품 부문은 정보 시스템 고도화를 위해 투자 수행 예정이며 '
                 '장비 사업은 정보 시스템 고도화를 위해 투자 수행 예정이다.')
    assert section_investment_plan_problem(candidate, {'a': PLAN},
                                           {'a': _context(PLAN)}) == SCOPE_CONDITION_UNBOUND


def test_자기인용밖부문문맥이나빈레거시문맥을추가근거로쓰지않음():
    candidate = '회사는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.'
    assert section_investment_plan_problem(candidate, {'a': PLAN}, {'other': _context(PLAN)}) == ''
    assert section_investment_plan_problem(candidate, {'a': PLAN}, {}) == ''


def test_부문없는일반표제와현재행위를계획으로추정하지않음():
    assert section_investment_plan_problem('회사는 투자를 수행할 예정이다.', {'a': PLAN}, {'a': _context(PLAN, '[사업내용]')}) == ''
    assert section_investment_plan_problem('회사는 투자를 수행한다.', {'a': PLAN}, {'a': _context(PLAN)}) == ''


def test_부문문맥의조각SHA불일치는거절():
    assert section_investment_plan_problem(
        '회사는 투자를 수행할 예정이다.', {'a': PLAN}, {'a': _context(PLAN + '다른 원문')},
    ) == SCOPE_CONDITION_UNBOUND


def test_실제검수배선의참을부문제약으로제외하고빈레거시는유지():
    from src.features.composer.verify import _apply_grounding, REVIEW_GROUNDING_REJECTED, VERDICT_TRUE

    candidate = '회사는 정보 시스템 고도화 및 생산성 향상을 위해 투자를 수행할 예정이다.'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '근거': ['a'], '검증근거': {}}]}, ensure_ascii=False)
    candidates = {1: (candidate, {'a': PLAN})}
    before = _apply_grounding(raw, {1: VERDICT_TRUE}, candidates)
    problems = {}
    after = _apply_grounding(raw, {1: VERDICT_TRUE}, candidates,
                             section_context_by_source_id={'a': _context(PLAN)},
                             grounding_problems=problems)
    assert before[1] == VERDICT_TRUE
    assert after[1] == REVIEW_GROUNDING_REJECTED
    assert problems == {1: SCOPE_CONDITION_UNBOUND}


def test_도식의정확한앞칸부문과인접계획은보존():
    cells = ('시제품 부문', '정보 시스템 고도화를 위해 투자 수행 예정', '생산성 향상')
    assert section_investment_plan_problem(' '.join(cells), {'a': PLAN}, {'a': _context(PLAN)}, cells=cells) == ''


@pytest.mark.parametrize('cells', [
    ('장비 부문', '정보 시스템 고도화를 위해 투자 수행 예정', '생산성 향상'),
    ('시제품 부문', '회사 전체는 정보 시스템 고도화를 위해 투자 수행 예정', '생산성 향상'),
    ('시제품 부문', '장비부문은 정보 시스템 고도화를 위해 투자 수행 예정', '생산성 향상'),
])
def test_다른대상이나명시전체계획칸은앞칸으로빌리지않음(cells):
    assert section_investment_plan_problem(' '.join(cells), {'a': PLAN}, {'a': _context(PLAN)}, cells=cells) == SCOPE_CONDITION_UNBOUND
