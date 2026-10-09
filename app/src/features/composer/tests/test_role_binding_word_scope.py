"""역할 낱말과 합성 명사의 우연한 부분 일치를 구분한다."""

import pytest

from src.features.composer.role_binding import (
    role_binding_hint_lines,
    role_binding_problem,
    role_binding_requirements,
)


@pytest.mark.parametrize('text', [
    '회사는 공제조합에 보증 목적의 출자금을 담보로 제공하고 있다.',
    '공제조합은 회사에 계약이행 보증을 제공한다.',
    '회사는 협동조합과 거래하고 공제조합에 출자금을 보유한다.',
])
def test_compound_name_does_not_require_a_manufacturing_role(text):
    requirements = role_binding_requirements(text, {'1': text})
    assert not requirements.required
    assert role_binding_hint_lines(requirements, False) == ''
    assert role_binding_problem(text, {'1': text}, {}) == ''


@pytest.mark.parametrize('cell', ['공제조합', '공제조합에 담보 제공'])
def test_compound_name_in_a_diagram_does_not_require_manufacturing(cell):
    assert not role_binding_requirements(cell, {'1': cell}, [cell]).required


@pytest.mark.parametrize('text', [
    '회사는 센서를 제조합니다.',
    '회사는 센서를 제조합니까?',
    '회사는 센서를 제조하고 있다.',
    '회사는 센서를 제조할 계획이다.',
    '회사는 공제조합에 출자하고 센서를 제조한다.',
    '회사는 센서를 제작하고 생산한다.',
])
def test_actual_role_predicates_still_require_role_binding(text):
    requirements = role_binding_requirements(text, {'1': text})
    assert requirements.required
    assert role_binding_problem(text, {'1': text}, {})


@pytest.mark.parametrize('cell', ['제조', '제조합니다', '제조 및 판매', '제조 합작 투자'])
def test_actual_role_cells_and_separate_words_keep_the_requirement(cell):
    assert role_binding_requirements(cell, {'1': cell}, [cell]).required
