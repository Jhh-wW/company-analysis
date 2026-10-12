"""명시 상품·현재 실행·역할 결속과 원래 후보·예산 보존을 검증한다."""

import hashlib
import json
from dataclasses import replace

import pytest

from features.evidence_collection import constants as c
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import RawFilingRow
from features.evidence_collection.product_role_relation import current_product_role_relation, product_role_actor_matches
from features.evidence_collection.product_role_relation_constants import REASON_CODE, SLOT_ID
from features.evidence_collection.relevance import product_role_relation_scores, score_fragment_slots
from features.evidence_collection.serialize import harvest_to_mapping
from features.evidence_collection.tests.test_collect import _fetcher, _NOW

MANUFACTURER = '당사는 감지모듈을 생산하는 산업용 센서제품 전문 제조회사로서, 지역별 매출액은 아래와 같습니다.'
SERVICE = ('시제품 부문은 현재 고품질 SDM(신속금형) 제작기술을 기반으로 '
           '다품종 소량생산 서비스를 제공하는 SDM 사업부를 주력으로 하고 있습니다.')
HEADING = '가. 주요 제품 및 서비스'


@pytest.mark.parametrize('text,heading', [(MANUFACTURER, HEADING), (SERVICE, '가. 사업의 현황'),
    (MANUFACTURER.replace('당사', '새봄기술㈜'), HEADING)])
def test_current_named_offering_and_role_support_only_product_slot(text, heading):
    assert current_product_role_relation(text, heading)
    score, = product_role_relation_scores(text, heading)
    assert score.slot_id == SLOT_ID and score.reason_codes == (REASON_CODE,)


def test_paper_packaging_object_is_not_an_embedded_subject():
    text = '당사는 종이 포장지와 포장상자를 생산하는 포장제품 전문 제조회사입니다.'
    assert current_product_role_relation(text, HEADING)
    assert product_role_actor_matches(text, HEADING, document_actor='가온기업㈜')


@pytest.mark.parametrize('text,heading', [
    ('주요 제품 및 서비스', HEADING),
    ('당사는 감지모듈, 구동모듈 등을 보유하고 있습니다.', HEADING),
    (MANUFACTURER, '가. 임원 경력'),
    (MANUFACTURER, '1. 미래 비전 및 주요 제품 계획'),
    (MANUFACTURER.replace('당사는', '경쟁사는'), HEADING),
    (MANUFACTURER.replace('당사는', '다른 회사는'), HEADING),
    (MANUFACTURER.replace('당사는', '고객사는'), HEADING),
    (MANUFACTURER.replace('당사는', '업체들은'), HEADING),
    (MANUFACTURER.replace('감지모듈', '다른 회사의 감지모듈'), HEADING),
    (MANUFACTURER.replace('생산하는', '생산하지 않는'), HEADING),
    (MANUFACTURER.replace('생산하는', '생산할 예정인'), HEADING),
    (MANUFACTURER.replace('제조회사로서', '제조회사가 아닙니다'), HEADING),
    ('당사는 향후 감지모듈을 생산하는 산업용 센서제품 전문 제조회사가 될 계획입니다.', HEADING),
    ('당사는 감지모듈을 생산하는 경우 산업용 센서제품 전문 제조회사가 됩니다.', HEADING),
    ('당사는 과거에는 감지모듈을 생산하는 산업용 센서제품 전문 제조회사였습니다.', HEADING),
    (MANUFACTURER + ' 해당 제품은 단종되었습니다.', HEADING),
    ('당사는 제품을 생산하는 산업용 센서제품 전문 제조회사입니다.', HEADING),
    ('당사는 산업을 선도하며 다양한 솔루션을 제시합니다.', HEADING),
    ('당사는 재고자산 계정에 감지모듈을 생산하는 산업용 센서제품 전문 제조회사의 제품을 기록합니다.', HEADING),
    ('당사는 감지모듈을 생산합니다. 산업용 센서제품 전문 제조회사 역할입니다.', HEADING),
    ('당사는 새로운 서비스를 제공하는 SDM 사업부를 주력으로 하고 있습니다.', HEADING),
    ('당사는 새봄주식회사가 압력조절기를 생산하는 제어부품 전문 제조회사로서 공급망에 참여한다고 밝힙니다.', HEADING),
])
def test_non_current_unbound_or_third_party_does_not_add_product_role(text, heading):
    assert not current_product_role_relation(text, heading)
    assert product_role_relation_scores(text, heading) == ()


def _harvest(text, *, actor='(주)가온기업'):
    row = RawFilingRow('20260315000001', '사업보고서 (2025.12)', '20260315', '00126380')
    fetcher = _fetcher('A', row, text)
    fetcher.document_responses_by_rcept_no[row.rcept_no] = replace(
        fetcher.document_responses_by_rcept_no[row.rcept_no], corp_code='00126380', document_actor=actor)
    return collect_dart_evidence(fetcher, '00126380', now=_NOW)


def test_collector_keeps_operating_parent_and_exact_document_binding():
    body = '(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + MANUFACTURER
    original_slots = {score.slot_id for score in score_fragment_slots(MANUFACTURER, HEADING)}
    result = _harvest(body)
    matching = [f for f in result.fragments if f.text.endswith(MANUFACTURER)]
    assert len(matching) == 2
    role, = [f for f in matching if REASON_CODE in f.reason_codes]
    parent, = [f for f in matching if REASON_CODE not in f.reason_codes]
    assert set(parent.covered_slot_ids) == original_slots
    assert role.covered_slot_ids == (SLOT_ID,)
    assert role.fragment_id != parent.fragment_id
    assert role.location == parent.location and role.text_sha256 == parent.text_sha256
    assert role.source_context_json == parent.source_context_json
    assert role.section_context_json == parent.section_context_json
    assert role.text_sha256 == hashlib.sha256(role.text.encode()).hexdigest()
    document, = result.documents
    assert len(document.usable_ranges) == 1
    mapping = harvest_to_mapping(result)
    assert len(mapping['fragments']) == 2


def test_supplementary_product_lane_stays_bounded_and_preserves_selected_parent(monkeypatch):
    monkeypatch.setattr(c, 'MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT', len(c.COLLECTOR_SLOT_IDS) * 3)
    existing = '당사의 주력 제품은 안정적인 기존 제품이며 지역별 매출액은 아래와 같습니다.'
    body = '(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + existing + '\n\n'
    baseline = _harvest(body)
    body += '\n\n'.join(MANUFACTURER.replace('감지모듈', f'감지모듈{i}') for i in range(30))
    result = _harvest(body)
    assert {f.text for f in baseline.fragments} <= {f.text for f in result.fragments}
    role_fragments = [f for f in result.fragments if SLOT_ID in f.covered_slot_ids]
    assert len(role_fragments) <= 3
    assert all(f.text in body for f in role_fragments)


def test_unrelated_discontinued_product_does_not_hide_current_offering():
    assert current_product_role_relation(MANUFACTURER + ' 다른 상품은 단종되었습니다.', HEADING)


def test_official_source_kind_slot_allowlist_keeps_auxiliary_slot_closed(monkeypatch):
    monkeypatch.setattr(c, 'SOURCE_KIND_CANDIDATE_SLOT_SCOPE', {
        kind: tuple(slot for slot in slots if slot != SLOT_ID)
        for kind, slots in c.SOURCE_KIND_CANDIDATE_SLOT_SCOPE.items()
    })
    result = _harvest('(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + MANUFACTURER)
    assert all(SLOT_ID not in f.covered_slot_ids for f in result.fragments)


def test_unverified_affiliate_keeps_primary_without_auxiliary_parent_substitution():
    offering = MANUFACTURER.replace('당사', '새봄기술㈜')
    body = ('(주)가온기업\nII. 사업의 내용\n[기타부문-센서]\n'
            '(1) 영업개황새봄기술㈜는 센서 제조업을 영위하고 있습니다.\n\n'
            + HEADING + '\n\n' + offering)
    result = _harvest(body)
    assert all(REASON_CODE not in f.reason_codes for f in result.fragments)
    parent, = [f for f in result.fragments if f.text.endswith(offering)]
    assert '새봄기술㈜는' in parent.text and '(주)가온기업' not in parent.text
    assert json.loads(parent.section_context_json)['text'] == '[기타부문-센서]'
    assert json.loads(parent.source_context_json)['actor'] == '새봄기술㈜'


@pytest.mark.parametrize('actor,expected', [('당사', True), ('가온기업㈜', True),
                                          ('새봄기술㈜', False)])
def test_named_actor_must_match_document_target(actor, expected):
    offering = MANUFACTURER.replace('당사', actor)
    assert current_product_role_relation(offering, HEADING)
    assert product_role_actor_matches(offering, HEADING,
        document_actor='(주)가온기업') is expected


def test_self_department_current_service_has_target_owned_auxiliary_relation():
    text = SERVICE.replace('시제품 부문', '당사의 시제품 부문')
    assert current_product_role_relation(text, '가. 사업의 현황')
    assert product_role_actor_matches(text, '가. 사업의 현황', document_actor='(주)가온기업')
    result = _harvest('(주)가온기업\nII. 사업의 내용\n가. 사업의 현황\n\n' + text)
    assert any(REASON_CODE in f.reason_codes for f in result.fragments)


def test_later_native_product_evidence_has_priority_over_earlier_auxiliary(monkeypatch):
    monkeypatch.setattr(c, 'MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT', len(c.COLLECTOR_SLOT_IDS) * 3)
    heading = '(주)가온기업\nII. 사업의 내용\n가. 사업의 현황\n\n'
    native = '\n\n'.join(f'당사의 주력 제품은 기존 감지장치{i}이며 현재 사업에서 반복적으로 생산하고 있습니다.' for i in range(4))
    baseline = _harvest(heading + native)
    auxiliary = '\n\n'.join(MANUFACTURER.replace('감지모듈', f'감지모듈{i}') for i in range(30))
    result = _harvest(heading + auxiliary + '\n\n' + native)
    # 첫 문단에만 붙는 실제 절 제목은 삽입 위치에 따라 달라진다.
    baseline_product = {f.text.rsplit('\n', 1)[-1] for f in baseline.fragments if SLOT_ID in f.covered_slot_ids}
    assert baseline_product and baseline_product <= {f.text.rsplit('\n', 1)[-1] for f in result.fragments}


def test_self_reference_cannot_borrow_context_from_another_document_actor():
    from features.evidence_collection.source_context import heading_source_scopes, context_for_candidate
    text = ('새봄기술㈜\n[제품부문]\n(1) 영업개황새봄기술㈜는 제조업을 영위합니다.\n\n' + MANUFACTURER)
    start = text.index(MANUFACTURER)
    context = context_for_candidate(text=MANUFACTURER, start=start, end=len(text),
        table_contexts=(), scopes=heading_source_scopes(text, document_actor='새봄기술㈜'))
    assert context and not product_role_actor_matches(MANUFACTURER, HEADING,
        document_actor='(주)가온기업', source_context_json=context)


@pytest.mark.parametrize('actor', ['당사', '가온기업㈜'])
def test_preceding_strong_hypothetical_keeps_primary_but_blocks_new_role(actor):
    offering = MANUFACTURER.replace('당사', actor)
    text = ('(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n'
            '예를 들어 가상의 회사 업무를 수행한다고 가정합니다.\n\n' + offering)
    result = _harvest(text)
    assert any(f.text.endswith(offering) for f in result.fragments)
    assert all(REASON_CODE not in f.reason_codes for f in result.fragments)
    assert all(f.text in text and hashlib.sha256(f.text.encode()).hexdigest() == f.text_sha256
               for f in result.fragments)


@pytest.mark.parametrize('boundary', [
    '실제 고객 적용 사례',
    '실제로 당사는 센서 제조를 시작했습니다.',
    '2. 주요 제품 및 서비스',
])
def test_explicit_actual_or_new_official_section_closes_hypothetical(boundary):
    text = ('(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n'
            '예를 들어 가상의 회사 업무를 수행한다고 가정합니다.\n\n'
            + boundary + '\n\n' + MANUFACTURER)
    assert any(REASON_CODE in f.reason_codes for f in _harvest(text).fragments)


def test_isolated_assumption_word_does_not_blacklist_document():
    text = ('(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n'
            '회계 가정에 관한 설명은 별도 주석에서 확인합니다.\n\n' + MANUFACTURER)
    assert any(REASON_CODE in f.reason_codes for f in _harvest(text).fragments)


def test_auxiliary_context_requires_exact_original_coordinates():
    from features.evidence_collection.product_role_relation import product_role_context_allows
    assert product_role_context_allows(MANUFACTURER, start=0, end=len(MANUFACTURER),
        document_text=MANUFACTURER, hypothetical_ranges=())
    assert not product_role_context_allows(MANUFACTURER, start=1, end=len(MANUFACTURER),
        document_text=MANUFACTURER, hypothetical_ranges=())
    assert not product_role_context_allows(MANUFACTURER, start=0, end=len(MANUFACTURER),
        document_text=MANUFACTURER, hypothetical_ranges=None)


def test_hypothetical_scope_budget_only_closes_auxiliary(monkeypatch):
    from features.evidence_collection import product_role_relation_constants as role_c
    monkeypatch.setattr(role_c, 'MAX_CONTEXT_EVENTS', 1)
    text = ('(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n'
            '예를 들어 가상의 업무를 수행한다고 가정합니다.\n\n'
            '2. 주요 제품 및 서비스\n\n' + MANUFACTURER)
    result = _harvest(text)
    assert any(f.text.endswith(MANUFACTURER) for f in result.fragments)
    assert all(REASON_CODE not in f.reason_codes for f in result.fragments)


@pytest.mark.parametrize('declaration', [
    '가상의 회사가 다음 제품을 만든다고 가정합니다.',
    '다음은 가상의 회사입니다.',
    '가상의 기업을 대상으로 제품 설명을 작성하는 실습입니다.',
])
def test_explicit_fictional_company_declaration_only_blocks_new_role(declaration):
    text = '(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + declaration + '\n\n' + MANUFACTURER
    result = _harvest(text)
    assert any(f.text.endswith(MANUFACTURER) for f in result.fragments)
    assert all(REASON_CODE not in f.reason_codes for f in result.fragments)


@pytest.mark.parametrize('real_context', [
    '퇴직급여 산정에서 할인 이자율을 가정합니다.',
    '당사는 고객의 가상 데이터를 사용하여 감지모듈을 검증합니다.',
    '당사는 가상의 회사가 요청한 샘플 데이터를 사용하여 제품을 검증합니다.',
    '당사는 실제 고객을 대상으로 제품 설명을 작성하는 실습을 제공합니다.',
])
def test_real_company_data_or_accounting_assumptions_do_not_open_fiction(real_context):
    text = '(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + real_context + '\n\n' + MANUFACTURER
    assert any(REASON_CODE in f.reason_codes for f in _harvest(text).fragments)
