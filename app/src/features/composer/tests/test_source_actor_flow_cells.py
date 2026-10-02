"""명시된 표 행위자의 명사형 칸은 살리고 새로운 주어·서술은 독립 검사한다."""
import pytest

from src.features.composer.direct_support_constants import FLOW_CELL_JOIN
from src.features.composer.source_actor_scope import source_actor_problem
from src.features.composer.tests.test_flow_review_binding import _source_context


ACTOR = '다온설비주식회사'
CONTEXT = _source_context(actor=ACTOR, status='개발 완료, 양산 예정')
DECLARATION = ACTOR + '는 산업장비 개발을 완료했다.'


@pytest.mark.parametrize('cells', [
    (DECLARATION, '산업장비', '개발'),
    ('산업장비', ACTOR, '개발 완료'),
    (DECLARATION, '설계 → 시제품', '개발 완료'),
])
def test_explicit_actor_row_preserves_subjectless_labels(cells):
    assert not source_actor_problem(FLOW_CELL_JOIN.join(cells), CONTEXT, cells=cells)


@pytest.mark.parametrize('last', [
    '회사는 산업장비 개발을 완료했다.',
    '별빛제조주식회사는 산업장비 개발을 완료했다.',
    '별빛제조주식회사는 제조',
    '산업장비 양산 완료',
    '양산 완료',
])
def test_named_actor_does_not_exempt_another_claim_or_later_stage(last):
    cells = (DECLARATION, '산업장비', last)
    assert source_actor_problem(FLOW_CELL_JOIN.join(cells), CONTEXT, cells=cells)


def test_prose_does_not_inherit_table_label_exception():
    text = DECLARATION + ' 산업장비 개발'
    assert source_actor_problem(text, CONTEXT)
    assert source_actor_problem('산업장비 | 개발 | 완료', CONTEXT, cells=('산업장비', '개발', '완료'))
