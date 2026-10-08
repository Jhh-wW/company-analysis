"""6장 최초·보충 작성에서 부문 계획의 주어와 기존 검수 경계를 확인한다."""
import json

import pytest

from src.features.composer.constants import SECTION_GUIDES
from src.features.composer.logic import compose_selected_sections
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.scope_supplement_feedback import collect_scope_supplement_failures
from src.features.composer.tests.test_scope_supplement_feedback import inputs, SOURCE, TITLE
from src.features.composer.tests.test_business_population_scope import _context
from src.features.composer.verify import (
    _apply_grounding, REVIEW_GROUNDING_REJECTED, VERDICT_TRUE,
)


@pytest.mark.parametrize('supplement', [False, True])
def test_writer_and_supplement_keep_original_scope_and_subject_instruction(supplement):
    packets, _, draft, diagnostic = inputs()
    failures = collect_scope_supplement_failures(draft, [diagnostic], packets) if supplement else None
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return json.dumps({'문장들': []})

    compose_selected_sections(
        '합성회사', None, ask, section_evidence_packets=packets,
        section_ids=('future_strategy',), scope_failures_by_section=failures,
    )
    assert len(seen) == 1
    assert SECTION_GUIDES['future_strategy'] in seen[0]
    assert '그 이름을 계획 문장의 주어에 명시하고 회사 전체 계획으로 넓히지 마라.' in seen[0]
    assert TITLE in seen[0] and SOURCE in seen[0]
    assert 'plan_status 산문은 자기 인용 하나에 같은 사업 대상·활동의 명시 계획과' in seen[0]


@pytest.mark.parametrize('subject,expected', [
    ('회사는', REVIEW_GROUNDING_REJECTED), ('시제품 부문은', VERDICT_TRUE),
])
def test_same_true_review_preserves_only_the_bound_division_plan(subject, expected):
    # 실제 실패 구조를 합성한다. 원 검수의 참을 추가 승인으로 사용하지 않는다.
    candidate = f'{subject} 정보 시스템 고도화를 위해 투자를 수행할 예정이다.'
    raw = json.dumps({'판정': [{
        '번호': 1, '결과': '참', '근거': ['a'], '검증근거': {},
    }]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(
        raw, {1: VERDICT_TRUE}, {1: (candidate, {'a': SOURCE})},
        section_context_by_source_id={'a': _context(SOURCE, TITLE)},
        grounding_problems=problems,
    )
    assert result[1] == expected
    assert problems == ({1: SCOPE_CONDITION_UNBOUND} if expected == REVIEW_GROUNDING_REJECTED else {})
