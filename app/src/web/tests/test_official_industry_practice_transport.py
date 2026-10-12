"""산업 조사 전용 원문도 예시 문맥과 정확한 원조각 결속을 보존한다."""

import copy
import hashlib
from dataclasses import replace

import pytest

from src.shared.report_evidence.practice_context import build_practice_context
from src.web.tests.test_official_industry_evidence import envelope, convert


def _bound():
    raw = envelope()
    document = raw['unclassified_documents'][0]
    fragment = raw['unclassified_fragments'][0]
    ranges = ('예를 들어 이렇게 요청합니다.', fragment['text'])
    full = '\n'.join(ranges)
    start = len(ranges[0]) + 1
    document['content_sha256'] = hashlib.sha256(full.encode()).hexdigest()
    document['usable_ranges'] = [{'start': start, 'end': len(full)}]
    fragment['location'] = f'{start}-{len(full)}'
    fragment['practice_context_json'] = build_practice_context(ranges=ranges,
        document_id=document['document_id'], document_sha256=document['content_sha256'],
        fragment_index=1, fragment_location=fragment['location'])
    return raw


def test_industry_candidate_preserves_context_without_mutating_observation():
    raw = _bound()
    before = copy.deepcopy(raw)
    candidate, = convert(raw)
    assert raw == before
    assert candidate.practice_context_json == raw['unclassified_fragments'][0]['practice_context_json']
    assert candidate.text == raw['unclassified_fragments'][0]['text']
    with pytest.raises(ValueError, match='사용 구간'):
        replace(candidate, practice_context_json=candidate.practice_context_json,
                location=f'0-{len(candidate.text)}')


def test_industry_candidate_context_cannot_borrow_another_document_hash():
    raw = _bound()
    raw['unclassified_documents'][0]['content_sha256'] = 'a' * 64
    with pytest.raises(ValueError, match='실습 문맥'):
        convert(raw)
