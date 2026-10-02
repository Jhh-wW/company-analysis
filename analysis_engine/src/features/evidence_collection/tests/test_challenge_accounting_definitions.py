"""회계기준 설명과 실제 고객 사업을 같은 원문에서 구별한다."""
import hashlib

import pytest

from features.evidence_collection.challenge_accounting_policy import (
    is_challenge_accounting_policy, split_challenge_accounting_policy,
)
from features.evidence_collection.relevance import score_fragment_slots_with_signal


@pytest.mark.parametrize("text", [
    "- | 기업회계기준서 제1111호: 위험회피회계 적용 ; - | 기업회계기준서 제1007호: 공시 변경",
    "경영진은 자산 및 부채 장부금액의 조정과 관련된 유의적 위험을 추정한다. 추가적인 판단 및 추정 정보는 다음과 같다.",
    "[파생상품 및 위험관리정책에 관한 사항]",
    "회사는 제품 결함이 발견될 경우 보증충당부채를 추정하여 인식한다.",
    "자가 사용 예외의 평가 대상을 정의했다. 위험회피회계 요건을 변경하고 관련 공시를 추가했다.",
    "측정일에 동일한 자산에 접근 가능한 활성시장의 조정하지 않은 공시가격이다. "
    "해당 공시가격에는 ESG 관련 위험의 가정이 반영되어 있다(수준 1).",
    "측정일에 동일한 부채의 활성시장 공시가격으로, 해당 공시가격에는 금리 상승, "
    "ESG 관련 위험 등의 경제환경 변화에 대한 시장의 가정이 반영되어 있다(수준 1).",
])
def test_accounting_definitions_do_not_become_business_challenges(text):
    before = hashlib.sha256(text.encode()).hexdigest()
    split = split_challenge_accounting_policy(text)
    assert "위험" not in split.score_text
    assert split.excluded_clauses
    assert hashlib.sha256(text.encode()).hexdigest() == before
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert not any(score.section_id == 'current_challenges' for score in scores)


@pytest.mark.parametrize("business", [
    "회사는 고객에게 회계기준 적용 자문 서비스를 제공합니다.",
    "회사는 고객에게 공정가치와 ESG 위험 평가 보고서를 발행합니다.",
    "회사는 고객에게 위험회피회계 시스템을 판매합니다.",
    "회사는 고객에게 파생상품 위험관리 플랫폼을 제공합니다.",
    "회사는 고객에게 ESG 위험 평가 보고서를 발행합니다.",
    "고객 대출 연체율이 상승하여 심사모형을 개선했다.",
    "보안 위험으로 고객 서비스가 중단됐으며 서버를 복구했다.",
    "공장에서 제품 결함이 발생하여 생산을 중단했다.",
])
def test_customer_business_and_real_incidents_survive_accounting_context(business):
    text = "기업회계기준서 제1111호 위험회피회계 적용. " + business
    split = split_challenge_accounting_policy(text)
    assert business.rstrip(".") in split.score_text
    assert split.excluded_clauses
    assert not is_challenge_accounting_policy(text)
