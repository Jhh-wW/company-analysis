"""9차 실제 동일 정산은 요약에서만 합치고 다른 사실·원문·시점은 보존한다."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.features.composer.extractive_summary import select_extractive_summary
from src.features.composer.logic import SummaryCandidate
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.summary_settlement import distinct_settlement_summaries
from src.features.pipeline.port import FactRecord


SAVED = json.loads((Path(__file__).parent / 'fixtures/ninth_summary_settlement.json').read_text(encoding='utf-8'))
FACTS = tuple(FactRecord(**row) for row in SAVED['facts'])


def candidates(facts=FACTS):
    return tuple(SummaryCandidate(f.section_owner, ComposedSentence(
        f.claim, tuple(s.removeprefix('v2-frag-') for s in f.supporting_source_ids),
        '확인', planned_claim_slot=f.claim_slot, verification_state='verified',
    )) for f in facts)


def distinct(items, facts=FACTS, company=SAVED['company']):
    return distinct_settlement_summaries(items, company_name=company,
        facts_by_sentence_id={id(c.sentence): f for c, f in zip(items, facts)})


def test_actual_different_fragment_numbers_of_same_settlement_keep_one_business_summary():
    before = deepcopy(SAVED)
    items = candidates()
    assert len(items) == 3 and not set(items[0].sentence.citations) & set(items[1].sentence.citations)
    assert FACTS[0].supporting_evidence_hashes == FACTS[1].supporting_evidence_hashes
    assert distinct(items) == (items[1],)
    assert SAVED == before


def test_actual_summary_selector_keeps_bound_original_fact_without_editing_body():
    items = candidates()
    report = ComposedReport((
        ComposedSection('identity', (items[0].sentence,)),
        ComposedSection('business_model', tuple(i.sentence for i in items[1:])),
    ))
    before = deepcopy(report)
    summary = select_extractive_summary(report, FACTS)
    assert summary.fact_ids == (FACTS[1].fact_id,)
    assert summary.sentences == (items[1].sentence,)
    assert report == before and summary.release_ready is False


def test_removed_duplicate_is_not_replaced_by_accounting_presentation_interpretation():
    accounting = FactRecord(**SAVED['accounting_fact'])
    facts = FACTS + (accounting,)
    items = candidates(facts)
    interpretation = replace(items[-1].sentence, grade='해석')
    report = ComposedReport((
        ComposedSection('identity', (items[0].sentence, interpretation)),
        ComposedSection('business_model', tuple(i.sentence for i in items[1:3])),
    ))
    before = deepcopy(report)
    summary = select_extractive_summary(report, facts)
    assert summary.fact_ids == (FACTS[1].fact_id,)
    assert report == before and report.sections[0].sentences[-1] is interpretation


@pytest.mark.parametrize('field,value', [
    ('legal_entity', '다른회사'), ('subject_scope', '연결집단'),
    ('as_of', '2025-12-31'), ('time_state', 'past'), ('fiscal_year', 2025),
    ('event_date', '2025-01-01'), ('period_start', '2025-01-01'), ('period_end', '2025-12-31'),
    ('supporting_source_identities', ['다른문서1', '다른문서2']),
    ('supporting_evidence_hashes', ['0' * 64, '1' * 64]),
    ('supporting_evidence_hashes', []),
])
def test_other_scope_or_source_is_not_collapsed(field, value):
    facts = (replace(FACTS[0], **{field: value}), FACTS[1])
    items = candidates(facts)
    assert distinct(items, facts) == items


@pytest.mark.parametrize('old,new', [
    ('가맹점에', '은행에'), ('원천사로부터', '가맹점으로부터'),
    ('수수료와 부가가치세를', '수수료를'), ('제외하여', '더하여'),
    ('지급하는 구조를 운영한다.', '지급하지 않는다.'),
    ('지급하는 구조를 운영한다.', '지급할 계획이다.'),
    ('지급하는 구조를 운영한다.', '지급한다. 수수료율은 3%이다.'),
    ('최종소비자가', '해외 최종소비자가'),
    ('회사는', '경쟁회사는'), ('회사는', '가상의 회사는'),
])
def test_different_relation_condition_or_additional_fact_is_not_collapsed(old, new):
    facts = (replace(FACTS[0], claim=FACTS[0].claim.replace(old, new)), FACTS[1])
    assert facts[0].claim != FACTS[0].claim
    items = candidates(facts)
    assert distinct(items, facts) == items


def test_missing_fact_evidence_and_unverified_candidates_are_unchanged():
    items = candidates(FACTS[:2])
    assert distinct_settlement_summaries(items, company_name=SAVED['company'], facts_by_sentence_id=None) == items
    assert distinct(items, FACTS[:2], company='') == items
    unverified = (replace(items[0], sentence=replace(items[0].sentence, verification_state='unverified')), items[1])
    assert distinct(unverified, FACTS[:2]) == unverified
