"""수집·공개 두 배포본에서 회계정책의 슬롯 경계와 원문을 함께 확인한다."""
import hashlib

import pytest

from src.shared.report_evidence import business_slot_scope as app


SLOTS = ('business_model:revenue_model', 'business_model:value_exchange')
POLICY = '재화의 판매에 따른 수익은 재화가 구매자에게 인도되는 시점에 인식한다.'


@pytest.mark.parametrize('slot', SLOTS)
@pytest.mark.parametrize('text', [
    POLICY,
    '수익인식 정책상 구매자가 재화의 수령을 승인한 때 수익을 인식한다.',
    '일반적으로 이전시점과 대가지급시점이 1년 이내여서 유의적인 금융요소를 조정하지 않는 실무적 간편법을 사용한다.',
])
def test_회계정책의_수집_공개_경계는_같고_다른칸_원문은_보존한다(text, slot):
    assert not app.business_slot_scope(text, slot).score_text
    assert app.business_slot_scope_problem(text, slot) == 'business_slot_scope_unsupported'
    assert app.business_slot_quote_problem(text, slot, 0, len(text))
    assert app.business_slot_scope(text, 'business_model:regional_mix').score_text == text


@pytest.mark.parametrize('slot', SLOTS)
@pytest.mark.parametrize('actual', [
    '회사는 고객에게 장비를 판매하고 판매대금을 현금이나 어음으로 회수한다.',
    '회사는 고객에게 서비스를 제공했고 대가 15억원을 수익으로 인식했습니다.',
    '고객에게 서비스 이용료를 월정액으로 청구한다.',
    '은행은 기업대출 이자수익을 유효이자율법으로 인식한다.',
    '보험계약자가 지급한 보험료를 수익으로 인식한다.',
    '신탁 운용보수를 수익으로 인식한다.',
    '제품별 매출은 소형제품 40%, 대형제품 60%이다.',
])
def test_실제거래가_있는_혼합원문의_거래절과_좌표_지문을_보존한다(actual, slot):
    text = POLICY + ' ' + actual
    before = hashlib.sha256(text.encode()).hexdigest()
    left = app.business_slot_scope(text, slot)
    assert not any(actual.rstrip('.') in piece or piece.strip() in actual for piece in left.excluded_clauses)
    assert '구매자에게 인도' not in left.score_text
    assert not app.business_slot_scope_problem(text, slot)
    assert app.business_slot_quote_problem(text, slot, 0, len(POLICY))
    quote_start = text.index(actual)
    assert not app.business_slot_quote_problem(text, slot, quote_start, len(text))
    for begin, end in left.excluded_spans:
        assert text[begin:end] in left.excluded_clauses
    assert hashlib.sha256(text.encode()).hexdigest() == before
