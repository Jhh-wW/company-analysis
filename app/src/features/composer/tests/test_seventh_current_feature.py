"""사용자 절차 완료 안내를 회사의 완료 이력으로 재승인하지 않는다."""

from copy import deepcopy
from pathlib import Path
import json

import pytest

from src.features.composer.logic import parse_section_response
from src.features.composer.plan_status_constants import (
    COMPLETED_EXECUTION_SLOT, COMPLETED_EXECUTION_STATE_MISMATCH,
)
from src.features.composer.plan_status_scope import completed_execution_status_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import _apply_grounding, verify_report


SAVED = json.loads((Path(__file__).parent / 'fixtures/seventh_current_feature.json').read_text(encoding='utf-8'))
CLAIM = SAVED['writer_sentence']['글']


def test_actual_seventh_saved_true_is_rejected_by_the_same_grounding_boundary():
    before = deepcopy(SAVED)
    entry = SAVED['review_entry']
    problems = {}
    verdicts = _apply_grounding(
        json.dumps({'판정': [entry]}, ensure_ascii=False), {35: '참'},
        {35: (CLAIM, {'92': SAVED['source_text']})},
        diagnostic_contexts={35: ('past_changes', '본문', CLAIM)},
        claim_slots_by_number={35: COMPLETED_EXECUTION_SLOT},
        grounding_problems=problems, baseline_date='2026-10-22',
    )
    assert verdicts[35] != '참'
    assert problems[35] == COMPLETED_EXECUTION_STATE_MISMATCH
    assert SAVED == before


def test_actual_writer_review_to_public_report_does_not_create_a_completed_fact():
    sentences = parse_section_response(json.dumps({'문장들': [SAVED['writer_sentence']]}, ensure_ascii=False), 'past_changes')
    report = ComposedReport((ComposedSection('past_changes', sentences),))
    fragments = (CollectedFragment('92', '홈페이지', SAVED['source_text'],
        source_url='https://www.tosspayments.com/blog/business-registration'),)

    def replay(prompt):
        # 발췌 회귀의 번호만 현재 검수 요청 번호에 대응한다. 다른 문장은 승인하지 않는다.
        items = review_items(prompt)
        assert items and all(item.text == CLAIM for item in items)
        return json.dumps({'판정': [dict(SAVED['review_entry'], 번호=item.number) for item in items]}, ensure_ascii=False)

    diagnostics = []
    verified = verify_report(report, fragments, None, replay,
        baseline_date='2026-10-22', diagnostics=diagnostics, allow_sentence_rewrite=False)
    assert not verified.sections[0].sentences
    assert any(item.get('reason_code') == COMPLETED_EXECUTION_STATE_MISMATCH for item in diagnostics)
    public = render_report('토스페이먼츠', verified, fragments, None, as_of_date='2026-10-22')
    assert not public.fact_records
    assert not any(CLAIM in line[0] for section in public.sections for line in section.lines)


@pytest.mark.parametrize('claim', [
    '회사는 사업자등록 바로신청 기능을 제공하고 있으며 신청 완료 메시지를 안내하고 있다.',
    '회사는 신청을 완료한 고객에게 상담 기능을 제공하고 있다.',
    '회사는 고객이 등록을 완료했으며 현재 알림 기능을 제공하고 있다고 안내한다.',
])
def test_current_feature_and_customer_completion_are_not_company_execution(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == COMPLETED_EXECUTION_STATE_MISMATCH
    assert completed_execution_status_problem(claim, 'portfolio:product_role') == ''


@pytest.mark.parametrize('claim', [
    '회사는 상담 서비스를 출시했고 현재 상담 기능을 제공하고 있다.',
    '회사는 지난해 결제 기능을 도입했으며 현재 기능을 제공하고 있다.',
    '회사는 거래처와 공급계약을 체결했다.',
    '회사는 거래처와 공급계약을 체결했고 현재 결제 기능을 제공하고 있다.',
])
def test_actual_company_launch_or_contract_is_preserved(claim):
    assert completed_execution_status_problem(claim, COMPLETED_EXECUTION_SLOT) == ''


@pytest.mark.parametrize('noun', ['도구', '솔루션', '플랫폼', '제품'])
def test_current_user_procedure_variants_keep_the_company_completion_boundary(noun):
    assert completed_execution_status_problem(CLAIM.replace('기능', noun), COMPLETED_EXECUTION_SLOT) == COMPLETED_EXECUTION_STATE_MISMATCH
    launched = f'회사는 상담 {noun}을 출시했고 현재 {noun}을 제공하고 있다.'
    assert completed_execution_status_problem(launched, COMPLETED_EXECUTION_SLOT) == ''
    assert completed_execution_status_problem(CLAIM.replace('기능', noun), 'portfolio:product_role') == ''
