"""회계정책의 슬롯 제외가 약신호 보충에서 되살아나지 않고 원문은 유지된다."""
import hashlib

import pytest

from features.evidence_collection.business_slot_scope import business_slot_scope
from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.tests.test_streaming_collection import _collect


@pytest.mark.parametrize('slot', ('business_model:revenue_model', 'business_model:value_exchange'))
@pytest.mark.parametrize('text', [
    '재화의 판매에 따른 수익은 구매자에게 인도할 때 인식한다.',
    '수익인식 정책상 구매자가 재화의 수령을 승인한 때 수익을 인식한다.',
    '이전시점과 대가지급시점이 1년 이내여서 유의적인 금융요소를 조정하지 않는 실무적 간편법을 사용한다.',
])
def test_정책제외_신호는_보조채점이나_무신호_재분류에서_승격하지_않는다(text, slot):
    scores, observed = score_fragment_slots_with_signal(text, allowed_slot_ids=frozenset({slot}))
    assert observed
    assert not scores
    assert not _collect(text).unclassified_fragments


@pytest.mark.parametrize('actual', [
    '회사는 고객에게 장비를 판매하고 판매대금을 현금이나 어음으로 회수한다.',
    '고객에게 서비스 이용료를 월정액으로 청구한다.',
    '회사는 고객에게 서비스를 제공했고 대가 15억원을 수익으로 인식했습니다.',
])
def test_정책과_섞인_실제거래_원문좌표와_수집지문은_유지한다(actual):
    policy = '재화의 판매에 따른 수익은 구매자 인도 시 인식한다.'
    text = policy + ' ' + actual
    before = hashlib.sha256(text.encode()).hexdigest()
    for slot in ('business_model:revenue_model', 'business_model:value_exchange'):
        scope = business_slot_scope(text, slot)
        assert scope.score_text
        assert '구매자 인도' not in scope.score_text
    harvest = _collect(text)
    assert harvest.fragments
    for fragment in harvest.fragments:
        begin, end = map(int, fragment.location.split('-'))
        assert text[begin:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
    assert all(document.content_sha256 == before for document in harvest.documents)
