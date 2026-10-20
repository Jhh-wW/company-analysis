"""현재 제공하는 구체 서비스의 기능과 법인 소속을 제품 근거로 보존한다."""

import hashlib

import pytest

from features.evidence_collection.product_role_relation import (
    current_product_role_relation, product_role_actor_matches,
)
from features.evidence_collection.product_role_relation_constants import REASON_CODE, SLOT_ID
from features.evidence_collection.relevance import score_fragment_slots
from features.evidence_collection.tests.test_product_role_relation import _harvest


HEADING = '가. 주요 제품 및 서비스'
SERVICE = (
    '당사는 온라인 가맹점의 결제 승인과 정산을 연결하는 전자결제 서비스를 '
    '제공하고 있습니다.'
)


@pytest.mark.parametrize('text', [
    SERVICE,
    '회사는 기업 고객의 계약 작성과 전자서명을 지원하는 계약관리 플랫폼을 운영합니다.',
    '당사는 고객의 설비 진동을 분석하는 설비진단 솔루션을 제공한다.',
])
def test_현재_서비스의_대상과_기능을_같은_문장에서_제품역할로_회복한다(text):
    assert current_product_role_relation(text, HEADING)
    assert product_role_actor_matches(text, HEADING, document_actor='(주)가온기업')


@pytest.mark.parametrize('text', [
    SERVICE.replace('제공하고 있습니다', '제공할 예정입니다'),
    SERVICE.replace('제공하고 있습니다', '제공하지 않습니다'),
    SERVICE.replace('제공하고 있습니다', '제공했습니다'),
    SERVICE.replace('당사', '경쟁사'),
    SERVICE.replace('전자결제 서비스', '다른 회사의 전자결제 서비스'),
    SERVICE.replace('당사는', '당사는 자회사가'),
    SERVICE.replace('온라인 가맹점의 결제 승인과 정산을 연결하는', '다양한'),
    '당사는 시장을 선도하는 혁신적인 서비스를 제공합니다.',
    '당사는 고객에게 다양한 솔루션을 제공합니다.',
    '당사는 고객에게 서비스를 제공하고 수익을 인식합니다.',
    SERVICE + ' 해당 서비스는 종료되었습니다.',
])
def test_미래_과거_부정_타사_홍보_회계문구는_새_제품근거로_추가하지_않는다(text):
    assert not current_product_role_relation(text, HEADING)


def test_구체서비스_보조근거는_원래_수익근거와_좌표를_보존한다():
    text = SERVICE + ' 서비스 수수료는 거래 금액에 따라 청구합니다.'
    body = '(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + text
    original_slots = {score.slot_id for score in score_fragment_slots(text, HEADING)}
    harvest = _harvest(body)
    matching = [fragment for fragment in harvest.fragments if fragment.text.endswith(text)]
    role, = [fragment for fragment in matching if REASON_CODE in fragment.reason_codes]
    original, = [fragment for fragment in matching if REASON_CODE not in fragment.reason_codes]
    assert set(original.covered_slot_ids) == original_slots
    assert role.covered_slot_ids == (SLOT_ID,)
    assert role.location == original.location
    assert role.text_sha256 == original.text_sha256 == hashlib.sha256(role.text.encode()).hexdigest()
    begin, end = map(int, role.location.split('-'))
    assert body[begin:end] == role.text


def test_별도법인명의_현재서비스는_대상회사_제품으로_승격하지_않는다():
    text = SERVICE.replace('당사', '다른기업㈜')
    assert current_product_role_relation(text, HEADING)
    assert not product_role_actor_matches(text, HEADING, document_actor='(주)가온기업')


@pytest.mark.parametrize('prefix', [
    '실습 프로젝트의 예시: 가상의 업무를 수행한다고 가정합니다.',
    '요청 예시: 이 회사가 다음 서비스를 운영한다고 가정합니다.',
    '실습 프로젝트의 예시:\n가상의 업무를 수행한다고 가정합니다.',
    '실습 프로젝트의 예시:\n\n가상의 업무를 수행한다고 가정합니다.',
    '실습 프로젝트의 예시: 가상의 회사입니다.',
    '실습 프로젝트의 예시: 가상의 기업을 대상으로 제품 설명을 작성하는 실습입니다.',
])
def test_실습_가정의_뒤_문단을_실제_제품_근거로_추가하지_않는다(prefix):
    body = '(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + prefix + '\n\n' + SERVICE
    assert all(REASON_CODE not in fragment.reason_codes for fragment in _harvest(body).fragments)


@pytest.mark.parametrize('separator', [' ', '\n', '\n\n'])
def test_실제_가상데이터_설명은_실습_가정으로_확대하지_않는다(separator):
    prefix = '실습 예시:' + separator + '당사는 고객의 가상 데이터를 사용하여 서비스를 검증합니다.'
    body = '(주)가온기업\nII. 사업의 내용\n' + HEADING + '\n\n' + prefix + '\n\n' + SERVICE
    assert any(REASON_CODE in fragment.reason_codes for fragment in _harvest(body).fragments)
