"""뉴스 귀속을 포함한 선택지와 검수 몸통의 정확한 대응을 검증한다."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from src.features.composer import verify
from src.features.composer.future_plan_constants import STATED_PLAN_SLOT
from src.features.composer.future_proof_selection import (
    FutureProofCandidate, prepare_future_proof_options, restore_future_proof_selections,
)
from src.features.composer.news_block import SOURCE_KIND_NEWS
from src.features.composer.news_usage import attribution_prefix
from src.features.composer.port import CollectedFragment


SOURCE = '회사는 신공장을 증설할 계획이다.'


def inputs():
    fragment = CollectedFragment(
        '1', SOURCE_KIND_NEWS, SOURCE, source_url='https://example.com/article',
        document_date='2026-07-01', source_publisher='example.com',
        formal_source_kind=SOURCE_KIND_NEWS,
        document_identity='document:example.com:article',
    )
    fragments = {'1': fragment}
    full = attribution_prefix(fragment) + SOURCE
    options = prepare_future_proof_options({
        1: FutureProofCandidate(1, 'future_strategy', STATED_PLAN_SLOT, full, (fragment,)),
    })
    candidates = {1: verify._grounding_candidate(full, ('1',), fragments)}
    contexts = {1: ('future_strategy', '본문', full)}
    raw = json.dumps({'판정':[{'번호':1, '장':'future_strategy', '근거':['1'],
        '근거대조':'동일 원문 계획을 확인했다.', '결과':'참',
        '검증근거':{'미래증명선택':options[1][0].option_id}}]}, ensure_ascii=False)
    return full, options, candidates, contexts, fragments, raw


def restored(options, candidates, contexts, fragments, raw):
    bound = verify._future_selection_candidates(candidates, contexts, fragments)
    return restore_future_proof_selections(
        raw, options, bound, {1:'future_strategy'}, {1:STATED_PLAN_SLOT}, fragments,
    )


def test_news_option_keeps_full_candidate_and_numeric_body():
    full, options, candidates, contexts, fragments, raw = inputs()
    before = deepcopy(candidates)
    assert candidates[1][0] == SOURCE
    # 기존 경로의 실제 손실을 고정한다. 전체 후보 SHA와 몸통 SHA가 다르다.
    assert restore_future_proof_selections(raw, options, candidates,
        {1:'future_strategy'}, {1:STATED_PLAN_SLOT}, fragments)[1] == {1}
    bound = verify._future_selection_candidates(candidates, contexts, fragments)
    assert bound[1][0] == full and candidates == before
    assert not restored(options, candidates, contexts, fragments, raw)[1]
    problems = {}
    assert verify._apply_grounding(raw, {1:'참'}, candidates,
        future_options_by_number=options, source_fragments_by_id=fragments,
        diagnostic_contexts=contexts, claim_slots_by_number={1:STATED_PLAN_SLOT},
        prose_numbers=frozenset({1}), grounding_problems=problems) == {1:'참'}
    assert not problems and candidates == before


@pytest.mark.parametrize('replacement', ['2026-07-02', 'other.example'])
def test_changed_date_or_publisher_prefix_cannot_reuse_option(replacement):
    full, options, candidates, contexts, fragments, raw = inputs()
    old = '2026-07-01' if replacement.startswith('2026') else 'example.com'
    contexts = {1:('future_strategy','본문',full.replace(old,replacement))}
    assert restored(options, candidates, contexts, fragments, raw)[1] == {1}


def test_changed_body_or_activity_cannot_reuse_option():
    full, options, candidates, contexts, fragments, raw = inputs()
    changed = full.replace('증설', '철거')
    assert restored(options, candidates,
        {1:('future_strategy','본문',changed)}, fragments, raw)[1] == {1}
    changed_candidates = {1:(SOURCE.replace('증설','철거'), candidates[1][1])}
    assert restored(options, changed_candidates,
        {1:('future_strategy','본문',changed)}, fragments, raw)[1] == {1}


@pytest.mark.parametrize('field,value', [
    ('document_date','2026-07-02'), ('source_publisher','other.example'),
    ('source_url','https://example.com/other'), ('text','회사는 신공장을 철거할 계획이다.'),
])
def test_changed_source_snapshot_cannot_reuse_option(field, value):
    full, options, candidates, contexts, fragments, raw = inputs()
    fragments = {'1':replace(fragments['1'], **{field:value})}
    changed = attribution_prefix(fragments['1']) + fragments['1'].text
    candidates = {1:verify._grounding_candidate(changed, ('1',), fragments)}
    contexts = {1:('future_strategy','본문',changed)}
    assert restored(options, candidates, contexts, fragments, raw)[1] == {1}


def test_wrong_number_or_cited_source_cannot_reuse_option():
    full, options, candidates, contexts, fragments, raw = inputs()
    wrong_number = json.loads(raw)
    wrong_number['판정'][0]['번호'] = 2
    assert restore_future_proof_selections(json.dumps(wrong_number,ensure_ascii=False),
        options, {2:(full,candidates[1][1])}, {2:'future_strategy'},
        {2:STATED_PLAN_SLOT}, fragments)[1] == {2}
    changed_sources = {1:(candidates[1][0], {'2':SOURCE})}
    changed_fragments = {'2':replace(fragments['1'],fragment_id='2')}
    assert restored(options, changed_sources, contexts, changed_fragments, raw)[1] == {1}


def test_missing_original_context_does_not_guess_full_candidate():
    full, options, candidates, contexts, fragments, raw = inputs()
    assert restored(options, candidates, {}, fragments, raw)[1] == {1}
    assert restored(options, candidates, {1:('future_strategy','본문','다른 문장')}, fragments, raw)[1] == {1}
