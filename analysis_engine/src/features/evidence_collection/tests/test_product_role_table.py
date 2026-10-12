"""현재 자기부문 제품 현황표만 원문 관계와 소속으로 보조 지원한다."""

import hashlib
import json

import pytest
from unittest.mock import patch

from features.evidence_collection.product_role_table import (
    product_table_role_supported, self_section_declarations, self_section_product_context,
)
from features.evidence_collection.section_scope import section_scopes, context_for_section_candidate
from features.evidence_collection.source_context import validate_source_context

ACTOR = '푸른주식회사'
HEADING = '가. 주요 제품, 서비스 등의 현황'
TABLE = '사업부문 | 매출유형 | 품목 | 구체적용도 | 매출액 ; 계측 | 제품 | 감지모듈 | 산업설비 계측용 | 10 ; 기타 | 기타 | 기타 | - | 1 ; 합계 | 합계 | 합계 | 합계 | 11'
DECLARATION = '당사는 계측기를 제조/판매 등을 하는 계측 부문으로 구성되어 있는 기업입니다.'


def build(prefix='', *, declaration=DECLARATION, heading=HEADING, table=TABLE, declaration_after='', introduction=''):
    document = f'{ACTOR}\n\n{introduction}\n\nI. 회사의 개요\n\n아. 주요사업의 내용\n\n{declaration}\n\n{declaration_after}\n\nII. 사업의 내용\n\n2. 주요 제품 및 서비스\n\n[계측 부문]\n\n{prefix}\n\n{heading}\n\n{table}'
    start = document.index(table)
    end = start + len(table)
    section = context_for_section_candidate(
        section_scopes(document), text=table, start=start, end=end, document_id='d1',
        document_sha256=hashlib.sha256(document.encode()).hexdigest(),
    )
    context = self_section_product_context(
        table, heading, start=start, end=end, document_text=document, document_actor=ACTOR,
        source_context_json='', section_context_json=section,
        declarations=self_section_declarations(document, document_actor=ACTOR),
    )
    return document, section, context


def test_자기부문_선언과_같은행_품목용도는_정확히_결속된다():
    document, section, context = build()
    value = validate_source_context(context, document_text=document, document_id='d1',
                                    section_context_json=section)
    assert value['origin'] == 'self_section_declaration'
    assert value['actor'] == ACTOR
    assert value['declaration_part'] == '계측부문'


@pytest.mark.parametrize('prefix', [
    '다음은 협력회사의 제품 현황을 인용한 자료입니다.',
    '계측 산업의 일반적인 제품 현황은 다음과 같습니다.',
    '향후 비전: 다음 제품을 제공하는 것을 목표로 합니다.',
])
def test_현부문표의_외부일반과_미래제품소개는_자기제품으로_보조분류하지않는다(prefix):
    assert build(prefix)[2] == ''


def test_선언후_가상전환은_형식사업제목만으로_자기제품proof가되지않는다():
    marker = '다음은 가상의 회사가 계측기를 판매한다고 가정한 실습 예시입니다.'
    assert build(declaration_after=marker)[2] == ''
    assert build('실제 고객 적용 사례', declaration_after=marker)[2]


def test_문서가상소개는_형식개요제목만으로_실제자기선언이되지않는다():
    marker = '다음은 가상의 회사가 계측기를 판매한다고 가정한 실습 예시입니다.'
    assert build(introduction=marker)[2] == ''
    assert build(introduction=marker + '\n\n실제 회사 사례')[2]


@pytest.mark.parametrize('prefix', [
    '새봄주식회사는 이 부문을 운영합니다.',
    '새봄주식회사는 이 부문을 운영합니다.\n\n당사는 제품을 판매합니다.',
    '가상의 회사가 다음 제품을 만든다고 가정합니다.',
    '예를 들어 가상의 회사 업무를 수행한다고 가정합니다.',
])
def test_현절_타법인과_가상전환은_소속문맥을_추가하지_않는다(prefix):
    assert build(prefix)[2] == ''


@pytest.mark.parametrize('table', [
    TABLE.replace('구체적용도', '장부금액'),
    TABLE.replace('산업설비 계측용', '-'),
    TABLE.replace('감지모듈', '기타'),
    TABLE.replace('제품', '재고자산'),
    '매출유형 | 품목 | 구체적용도 ; 제품 | 감지모듈 | - ; 제품 | - | 설비용',
])
def test_헤더_빈용도_회계_다른행_용도는_역할로_추정하지_않는다(table):
    assert not product_table_role_supported(table, HEADING)


def test_부문명_선언위조와_다른문서_조각은_거절된다():
    document, section, context = build()
    changed = json.loads(context)
    changed['declaration_part'] = '다른부문'
    with pytest.raises(ValueError):
        validate_source_context(json.dumps(changed, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')), document_text=document)
    for kwargs in ({'document_id': 'other'}, {'fragment_sha256': '0' * 64},
                   {'fragment_location': '1-2'}, {'section_context_json': ''}):
        with pytest.raises(ValueError):
            validate_source_context(context, **kwargs)


def test_미래선언과_중복선언은_새보조범위를_열지_않는다():
    assert build(declaration=DECLARATION.replace('당사는', '당사는 향후'))[2] == ''
    assert build(declaration=DECLARATION + '\n\n아. 주요사업의 내용\n\n' + DECLARATION)[2] == ''


def test_보조표_몫은_기존선택과_원문위치해시를_밀지않는다():
    from features.evidence_collection import collect, constants, relevance
    from features.evidence_collection.filing_select import DocumentFetchResult, FilingSelectionResult, SelectedFiling
    from features.evidence_collection.product_role_relation import current_product_role_relation
    document, _, _ = build(table='\n\n'.join(TABLE.replace('감지모듈', f'감지모듈{i}') for i in range(30)))
    filing = SelectedFiling('dart_business_report', 'REQUIRED', '20260318000001',
                             '사업보고서', '20260318', filing_list_corp_code='00000001')
    class LocalFetcher:
        def fetch_document_text(self, receipt):
            return DocumentFetchResult(state='OK', text=document, corp_code='00000001', document_actor=ACTOR)
    original_product_score = relevance.product_role_relation_scores
    def old_product_score(text, heading=''):
        return original_product_score(text, heading) if current_product_role_relation(text, heading) else ()
    with patch.object(collect.filing_select, 'select_related_filings',
                      return_value=FilingSelectionResult((filing,), (), ())):
        with patch.object(collect, 'self_section_product_context', return_value=''), \
                patch.object(relevance, 'product_role_relation_scores', old_product_score):
            before = collect.collect_dart_evidence(LocalFetcher(), '00000001', now='2026-10-10T00:00:00Z')
        after = collect.collect_dart_evidence(LocalFetcher(), '00000001', now='2026-10-10T00:00:00Z')
    key = lambda f: (f.fragment_id, f.section_id, f.slot_id, f.covered_slot_ids,
                     f.location, f.text, f.text_sha256, f.score_millis)
    assert set(map(key, before.fragments)) <= set(map(key, after.fragments))
    products = [f for f in after.fragments if f.slot_id == 'portfolio:product_role']
    assert 0 < len(products) <= (constants.RETAINED_TOP_PER_SLOT + constants.RETAINED_RECENT_PER_SLOT
                                + constants.RETAINED_CHANGE_PER_SLOT)
    for fragment in after.fragments:
        start, end = map(int, fragment.location.split('-'))
        assert fragment.text == document[start:end]
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
    contexts = {}
    for fragment in after.fragments:
        key = (fragment.location, fragment.text_sha256)
        assert contexts.setdefault(key, fragment.source_context_json) == fragment.source_context_json
