"""과제명·기대효과와 실행 상태를 분리하는 독립 경계."""
import pytest

from src.features.composer.research_table_status_scope import research_table_status_problem


HEADER = '연 구 과 제 | 연 구 기 간 | 연구결과 및 기대효과 ; '
TABLE = HEADER + ('친환경 소재 연구 | 19/01~25/12 | 재활용 소재 확보 및 분산 공정 개발 ; '
                  '가상 시제품 연구 | 24/01~30/12 | 가상 시제품 예측 및 검증 프로세스 구축 ; ')


@pytest.mark.parametrize('claim', [
    '회사는 친환경 소재 연구를 추진하고 있다.',
    '회사는 친환경 소재 연구를 완료했다.',
    '회사는 가상 시제품 연구를 개발하고 있다.',
    '회사는 가상 시제품 연구를 완료했다고 공시했다.',
    '회사는 친환경 소재 연구와 가상 시제품 연구를 진행 중이다.',
    '회사는 재활용 소재 확보 및 분산 공정을 개발하고 있다.',
    '회사는 가상 시제품 예측 및 검증 프로세스를 구축했다.',
])
def test_nominal_table_does_not_support_execution(claim):
    assert research_table_status_problem(claim, {'a': TABLE}) == 'time_invalid'


@pytest.mark.parametrize('claim', [
    '회사는 친환경 소재 연구를 연구과제로 기재했다.',
    '회사는 가상 시제품 연구의 기간을 24/01~30/12로 기재했다.',
    '회사는 재활용 소재 확보 및 분산 공정 개발을 기대효과로 제시했다.',
    '회사는 가상 시제품 연구를 연구과제로 제시하고 있다.',
    '연구기간 표에는 2025년 12월 종료일이 있다.',
    '회사는 별도 고객 관리 시스템을 구축했다.',
])
def test_attributed_description_and_unrelated_execution_are_preserved(claim):
    assert research_table_status_problem(claim, {'a': TABLE}) == ''


@pytest.mark.parametrize(('state', 'claim'), [
    ('친환경 소재 연구를 진행 중이다.', '회사는 친환경 소재 연구를 추진하고 있다.'),
    ('개발 완료', '회사는 친환경 소재 연구를 완료했다.'),
    ('분산 공정을 개발하고 있습니다.', '회사는 친환경 소재 연구를 개발하고 있다.'),
])
def test_direct_same_row_state_is_preserved(state, claim):
    source = HEADER + f'친환경 소재 연구 | 19/01~25/12 | {state} ; '
    assert research_table_status_problem(claim, {'a': source}) == ''


@pytest.mark.parametrize('state', ['개발 예정', '개발을 완료할 계획', '개발하고 있지 않다', '개발 중단', '완료하지 않았다', '개발 완료를 기대한다'])
def test_future_negative_and_expected_states_do_not_prove_execution(state):
    source = HEADER + f'친환경 소재 연구 | 19/01~25/12 | {state} ; '
    assert research_table_status_problem('회사는 친환경 소재 연구를 개발하고 있다.', {'a': source}) == 'time_invalid'


def test_other_row_and_other_project_do_not_lend_state():
    source = TABLE + '데이터 분석 과제 | 24/01~30/12 | 개발하고 있다 ; '
    assert research_table_status_problem('회사는 친환경 소재 연구를 개발하고 있다.', {'a': source}) == 'time_invalid'
    assert research_table_status_problem('회사는 친환경 소재 연구를 개발하고 있다.', {'a': TABLE, 'b': '회사는 데이터 분석 과제를 개발하고 있다.'}) == 'time_invalid'


def test_exact_project_in_separate_prose_supports_execution():
    assert research_table_status_problem('회사는 친환경 소재 연구를 추진하고 있다.', {'a': TABLE, 'b': '회사는 친환경 소재 연구를 진행 중이다.'}) == ''


def test_other_project_with_same_effect_words_does_not_lend_state():
    own = {'a': TABLE, 'b': '회사는 다른 연구과제에서 재활용 소재와 분산 공정을 개발하고 있다.'}
    assert research_table_status_problem('회사는 친환경 소재 연구를 추진하고 있다.', own) == 'time_invalid'


def test_two_rows_with_different_states_keep_their_own_state():
    source = HEADER + '친환경 소재 연구 | 19/01~25/12 | 개발 완료 ; 가상 시제품 연구 | 24/01~30/12 | 개발하고 있다 ; '
    assert research_table_status_problem('회사는 친환경 소재 연구를 완료했으며 가상 시제품 연구를 개발하고 있다.', {'a': source}) == ''
    assert research_table_status_problem('회사는 친환경 소재 연구와 가상 시제품 연구를 개발하고 있다.', {'a': source}) == 'time_invalid'


def test_table_syntax_required_and_inputs_unchanged():
    own = {'a': '제품 | 기간 | 효용 ; 친환경 소재 연구 | 2025 | 개발 내용'}
    before = dict(own)
    assert research_table_status_problem('회사는 친환경 소재 연구를 개발하고 있다.', own) == ''
    assert own == before


def test_direct_same_project_statement_after_table_is_preserved():
    source = TABLE + '\n친환경 소재 연구는 현재 진행 중입니다.'
    assert research_table_status_problem('회사는 친환경 소재 연구를 현재 추진하고 있다고 밝혔다.', {'a': source}) == ''


def test_nominal_first_project_does_not_take_second_project_state():
    source = '회사는 가상 시제품 연구를 진행하고 있습니다.\n' + TABLE
    claim = '회사는 친환경 소재 연구를 연구과제로 제시했으며 가상 시제품 연구를 현재 진행하고 있다고 밝혔다.'
    assert research_table_status_problem(claim, {'a': source}) == ''
    assert research_table_status_problem('회사는 친환경 소재 연구를 제시하고 현재 추진하고 있다.', {'a': source}) == 'time_invalid'


def test_nominal_strengthening_effect_is_not_completed_strengthening():
    source = HEADER + '친환경 소재 연구 | 19/01~25/12 | 제품 경쟁력 강화 ; '
    assert research_table_status_problem('회사는 제품 경쟁력을 강화하였다.', {'a': source}) == 'time_invalid'


def test_markdown_border_pipes_keep_the_same_table_contract():
    source = '| 연구과제 | 연구기간 | 연구결과 및 기대효과 |\n| --- | --- | --- |\n| 친환경 소재 연구 | 19/01~25/12 | 분산 공정 개발 |'
    assert research_table_status_problem('회사는 친환경 소재 연구를 개발하고 있다.', {'a': source}) == 'time_invalid'


@pytest.mark.parametrize('heading', ['연구과제 | 기대효과', '연구과제 | 연구기간 | 기대효과', '연구개발 실적 | 연구기간'])
def test_closed_research_header_variants_do_not_create_state(heading):
    if heading.endswith('연구기간'):
        row = '전동 구동장치 기술 개발 | 2024/01~2026/12'
    elif '연구기간' in heading:
        row = '전동 구동장치 기술 개발 | 2024/01~2026/12 | 제품 경쟁력 강화'
    else:
        row = '전동 구동장치 기술 개발 | 제품 경쟁력 강화'
    assert research_table_status_problem('회사는 전동 구동장치 기술을 현재 개발하고 있다.', {'a': heading+' ; '+row}) == 'time_invalid'


@pytest.mark.parametrize('heading', ['연구과제 | 상태', '연구과제 | 연구결과'])
def test_direct_two_column_state_is_preserved(heading):
    source = heading+' ; 전동 구동장치 기술 개발 | 개발 완료'
    assert research_table_status_problem('회사는 전동 구동장치 기술 개발을 완료했다.', {'a': source}) == ''


@pytest.mark.parametrize(('state', 'claim'), [('개발 완료', '완료했다'), ('개발 진행 중', '현재 개발하고 있다')])
def test_two_column_other_row_state_is_not_borrowed(state, claim):
    source = '연구과제 | 상태 ; 전동 구동장치 기술 개발 | 계획 ; 냉각장치 기술 개발 | '+state
    assert research_table_status_problem('회사는 전동 구동장치 기술을 '+claim+'.', {'a': source}) == 'time_invalid'


def test_completion_in_project_title_is_not_status_cell():
    source = '연구과제 | 연구기간 ; 전동 구동장치 기술 개발 완료 | 2024/01~2025/12'
    assert research_table_status_problem('회사는 전동 구동장치 기술 개발을 완료했다.', {'a': source}) == 'time_invalid'
    assert research_table_status_problem('회사는 전동 구동장치 기술 개발 완료를 과제명으로 기재했다.', {'a': source}) == ''


def test_generic_titles_and_dates_are_outside_the_closed_research_headers():
    for source in ['제품 | 기간 | 기대효과 ; 전동 구동장치 기술 개발 | 2024/01~2026/12 | 개발', '연구과제 소개 문장에는 2024/01~2026/12가 있다.']:
        assert research_table_status_problem('회사는 전동 구동장치 기술을 개발하고 있다.', {'a': source}) == ''
