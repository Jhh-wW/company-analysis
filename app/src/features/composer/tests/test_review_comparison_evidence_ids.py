"""검수 설명의 명시적 근거 차용은 재요청 없이 기존 소유권 검사에서 닫는다."""
from copy import deepcopy
import json

import pytest

from src.features.composer import verify
from src.features.composer.grounding_constants import REVIEW_GROUNDING_REJECTED
from src.features.composer.review_evidence_ids import ReviewEvidenceContext, normalize_review_entry


def _context(citations=('1',), proofs=('1',)):
    return ReviewEvidenceContext(frozenset({'1', '2', '3', '59', '2025'}),
                                 {1: frozenset(citations)}, {1: frozenset(proofs)})


def _entry(comparison, citations=('1',)):
    return {'번호': 1, '장': 'business_model', '근거': list(citations), '결과': '참',
            '근거대조': comparison, '검증근거': {}}


@pytest.mark.parametrize('comparison', [
    '1: 기타 항목 일치·상품은 조각59 근거', '조각 2의 관계를 함께 사용',
    '근거 ID 2로 보충', '근거 [2]로 보충', '2: 상품 관계 일치',
    '1: 일부 일치; 2: 다른 항목 근거', '조각 999에 설명이 있다',
    '1, 2: 사업 구성', '1·2: 사업 구성', '조각1 및2 관계 일치',
])
def test_자기근거밖의_명시적ID차용은_참이어도_최종거절한다(comparison):
    row = _entry(comparison)
    before = deepcopy(row)
    context = _context()
    assert not normalize_review_entry(row, context)[1]
    assert row == before
    raw = json.dumps({'판정': [row]}, ensure_ascii=False)
    # 의미 실패는 형식 재요청이나 다른 후보 손실로 바꾸지 않는다.
    assert verify._parse_grouped_verdicts(raw, {1: 'business_model'}, {1: frozenset({'1'})}, evidence_context=context) == {1: '참'}
    text = '회사는 장비를 판매한다.'
    checked = verify._apply_grounding(raw, {1: '참'}, {1: (text, {'1': text})}, review_evidence_context=context)
    assert checked[1] == REVIEW_GROUNDING_REJECTED


@pytest.mark.parametrize('comparison', [
    '1: 조각 1의 판매 관계 일치', '59억원·2025년·2개 제품·3명 고객',
    '1: 매출 2.5% 증가', '1: 2025-10-08 기준', '1: 오후 2:30 발표',
    '1: 생산량 59:41 비율', '1: 조각 2개에서 확인', '1: 근거 2025년 공시',
    '2025: 매출 기간', '1: 조각 2 및59개를 집계', '1: 근거 1,000억원',
])
def test_일반수치_날짜_수량은_외부ID로_읽지않는다(comparison):
    assert normalize_review_entry(_entry(comparison), _context())[1]


def test_정상멀티인용과_배정된_보조수치증명은_보존한다():
    assert normalize_review_entry(_entry('1: 판매·조각 2 관계 일치', ('1','2')), _context(('1','2'), ('1','2')))[1]
    assert normalize_review_entry(_entry('1: 설명·근거 ID 3 수치 대조'), _context(proofs=('1','3')))[1]
    assert not normalize_review_entry(_entry('1: 설명·근거 ID 3 수치 대조'), _context())[1]


def test_무효인용하나가_다른정상판정을_버리지않는다():
    rows = [_entry('1: 설명·조각2 차용'), {**_entry('2: 자기 판매 관계'), '번호': 2, '근거': ['2']}]
    context = ReviewEvidenceContext(frozenset({'1','2'}), {1: frozenset({'1'}), 2: frozenset({'2'})}, {1: frozenset({'1'}), 2: frozenset({'2'})})
    raw = json.dumps({'판정': rows}, ensure_ascii=False)
    text = '회사는 장비를 판매한다.'
    checked = verify._apply_grounding(raw, {1:'참',2:'참'}, {1:(text, {'1':text}),2:(text,{'2':text})}, review_evidence_context=context)
    assert checked == {1:REVIEW_GROUNDING_REJECTED,2:'참'}


def test_평문legacy도_상위근거신설없이_명시차용을_거절한다():
    text = '회사는 장비를 판매한다.'
    row = _entry('1: 설명·조각2 근거')
    del row['근거']
    raw = json.dumps({'판정':[row]}, ensure_ascii=False)
    checked = verify._apply_grounding(raw, {1:'참'}, {1:(text, {'1':text})})
    assert checked[1] == REVIEW_GROUNDING_REJECTED
    row['근거대조'] = '1: 판매 관계 일치'
    assert verify._apply_grounding(json.dumps({'판정':[row]},ensure_ascii=False), {1:'참'}, {1:(text,{'1':text})}) == {1:'참'}
