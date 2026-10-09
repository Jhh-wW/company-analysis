"""일반 산업 관계·회계정책의 회사 사업모델 승격과 정상 거래를 함께 대조한다."""
import hashlib
import json
import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding


@pytest.mark.parametrize('source', [
    '자동차 부품 업체들은 완성차 업체와 협력하여 부품을 개발하고 OEM으로 생산한다.',
    '부품 제조업체들은 OEM 방식으로 고객에게 공급한다.',
    '해당 산업의 업체들은 OEM 계약으로 제품을 생산한다.',
    '산업의 특성. 부품의 대부분은 OEM으로 공급되고 일부는 직수출로 판매한다.',
    '산업의 특성. 부품은 OEM으로 공급되는 특성이 있습니다.',
])
def test_산업_일반관계와_회사_생산설명을_합쳐도_회사관계로_승격하지_않는다(source):
    claim='회사는 공조제품을 생산하며 완성차 업체와 OEM 관계로 협력하여 개발한다.'
    assert business_relation_scope_problem(claim, {'a': '회사는 공조제품을 생산한다.', 'b': source},
        section_id='operations_partners') == 'scope_condition_unbound'


@pytest.mark.parametrize('claim,source', [
    ('회사는 OEM으로 부품을 공급한다.', '회사는 OEM 계약으로 부품을 공급한다.'),
    ('자동차 열관리 부문은 OEM으로 공급한다.', '자동차 열관리 부문은 OEM 개발요청에 따라 부품을 공급한다.'),
    ('부품은 OEM 방식으로 공급된다.', 'OEM 개발요청에 따라 부품을 공급한다.'),
    ('회사는 OEM으로 부품을 공급한다.', '자동차 부품 업체들은 OEM으로 공급한다. 회사는 OEM 계약으로 부품을 공급한다.'),
    ('당사의 협력업체들은 OEM으로 부품을 공급한다.', '당사의 협력업체들은 OEM으로 부품을 공급한다.'),
    ('회사의 종속기업들은 OEM으로 공급한다.', '회사의 종속기업들은 OEM으로 부품을 공급한다.'),
    ('자동차 부품 업체들은 OEM으로 공급한다.', '자동차 부품 업체들은 OEM으로 부품을 공급한다.'),
    ('프로토타입 부문은 CNC 가공과 소량생산을 주력으로 한다.', '프로토타입 부문은 CNC 가공과 소량생산을 주력으로 한다.'),
    ('회사는 OEM으로 부품을 공급한다.', '산업의 특성. 부품 업체들은 OEM으로 공급한다. 회사는 OEM 계약으로 부품을 공급한다.'),
    ('열관리 부문은 OEM으로 부품을 공급한다.', '산업의 특성. 열관리 부문은 OEM 개발요청으로 부품을 공급한다.'),
    ('회사는 OEM으로 부품을 공급한다.', '산업의 특성. 새봄주식회사는 OEM으로 부품을 공급한다.'),
])
def test_명시회사부문_주어생략_혼합원문_및_산업설명자체는_보존한다(claim,source):
    assert business_relation_scope_problem(claim, {'a':source}, section_id='operations_partners') == ''


@pytest.mark.parametrize('source', [
    '회사는 OEM 계약을 하지 않는다.',
    '회사는 OEM 공급을 중단했다.',
    '회사는 OEM으로 공급할 계획이다.',
])
def test_산업일반설명이_회사관계의_부정_중단_계획을_덮지_않는다(source):
    assert business_relation_scope_problem('회사는 OEM으로 부품을 공급한다.',
        {'a':'부품 업체들은 OEM으로 공급한다.', 'b':source}, section_id='operations_partners') == 'scope_condition_unbound'


@pytest.mark.parametrize('claim', [
    '연결회사의 수익 인식은 재화가 구매자에게 인도되는 시점에 이루어진다.',
    '재화의 판매에 따른 수익은 재화가 구매자에게 인도되는 시점에 인식한다.',
    '일반적으로 재화나 용역을 이전하는 시점과 대가 지급 시점 사이가 1년 이내이며 유의적인 금융요소를 조정하지 않는 실무적 간편법을 사용한다.',
])
@pytest.mark.parametrize('slot', ['business_model:revenue_model','business_model:value_exchange'])
def test_참인_회계정책도_수익방식이나_대가칸을_대신_채우지_않는다(claim,slot):
    source='재화의 판매에 따른 수익은 구매자 인도 시 인식한다. 회사는 고객에게 운송용역을 제공하고 운송대가를 받는다.'
    assert business_relation_scope_problem(claim, {'a':source}, section_id='business_model', claim_slot=slot) == 'scope_condition_unbound'


@pytest.mark.parametrize('claim,source', [
    ('국내 판매 대금은 현금 또는 30~120일 어음으로 회수한다.', '국내 판매 대금은 현금 또는 30~120일 어음으로 회수한다.'),
    ('회사는 일부 고객에게 운송용역을 제공하고 대가를 받는다.', '재화의 수익 인식은 인도 시 이루어진다. 회사는 일부 고객에게 운송용역을 제공하고 대가를 받는다.'),
    ('회사는 제품을 제공하고 대금은 고객에게 인도할 때 현금으로 받으며 수익을 인식한다.', '회사는 제품을 제공하고 대금은 고객에게 인도할 때 현금으로 받으며 수익을 인식한다.'),
    ('기업대출 고객의 이자와 대출취급 수수료로 금융수익을 얻는다.', '기업대출 고객의 이자와 대출취급 수수료로 금융수익을 얻는다.'),
    ('은행은 기업대출 이자수익을 유효이자율법으로 인식한다.', '은행은 기업대출 이자수익을 유효이자율법으로 인식한다.'),
    ('보험계약자가 지급한 보험료를 수익으로 인식한다.', '보험계약자가 지급한 보험료를 수익으로 인식한다.'),
    ('회사는 신탁 운용보수를 수익으로 인식한다.', '회사는 신탁 운용보수를 수익으로 인식한다.'),
    ('소형제품 매출은 30%, 대형제품 매출은 70%이다.', '소형제품 매출은 30%, 대형제품 매출은 70%이다.'),
])
@pytest.mark.parametrize('slot', ['business_model:revenue_model','business_model:value_exchange'])
def test_실제판매_수금_운송대가_금융상품_제품매출은_보존한다(claim,source,slot):
    assert business_relation_scope_problem(claim, {'a':source}, section_id='business_model', claim_slot=slot) == ''


def test_실제거래없는_정책원문에_수취행동을_만든_후보도_제한한다():
    assert business_relation_scope_problem('회사는 재화를 팔아 판매대금을 받는다.',
        {'a':'재화의 판매에 따른 수익은 구매자 인도 시 인식한다.'},
        section_id='business_model', claim_slot='business_model:value_exchange') == 'scope_condition_unbound'


def test_모델의_참응답과_고쳐쓰기에도_같은guard가_적용되고_원문hash는_불변이다():
    source='재화의 판매에 따른 수익은 구매자 인도 시 인식한다.'
    before=hashlib.sha256(source.encode()).hexdigest()
    claim='회사의 수익 인식은 구매자 인도 시 이루어진다.'
    verdicts={1:'참'}
    raw=json.dumps({'판정':[{'번호':1,'결과':'참','근거':['a']}]},ensure_ascii=False)
    context={1:('business_model','본문',claim)}
    for _ in range(2):
        problems={}
        result=_apply_grounding(raw,verdicts,{1:(claim,{'a':source})}, diagnostic_contexts=context,
            claim_slots_by_number={1:'business_model:value_exchange'}, grounding_problems=problems)
        assert result[1] != '참'
        assert problems[1] == 'scope_condition_unbound'
    assert hashlib.sha256(source.encode()).hexdigest()==before


def test_산업관계의_참승인도_최종검수입구에서_같은사유로_제한한다():
    claim='회사는 OEM으로 부품을 공급한다.'
    sources={'a':'회사는 부품을 생산한다.', 'b':'산업의 특성. 부품의 대부분은 OEM으로 공급된다.'}
    raw=json.dumps({'판정':[{'번호':1,'결과':'참','근거':['a','b']}]},ensure_ascii=False)
    problems={}
    result=_apply_grounding(raw,{1:'참'},{1:(claim,sources)},
        diagnostic_contexts={1:('operations_partners','본문',claim)}, grounding_problems=problems)
    assert result[1] != '참'
    assert problems[1] == 'scope_condition_unbound'
