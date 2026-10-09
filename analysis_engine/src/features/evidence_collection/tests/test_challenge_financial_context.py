"""금융 관리·감사인의 절을 실제 고객 사업과 구별한다."""
import pytest

from features.evidence_collection.challenge_accounting_policy import (
    is_challenge_accounting_policy, split_challenge_accounting_policy,
)
from features.evidence_collection.relevance import score_fragment_slots_with_signal


@pytest.mark.parametrize('text', [
    '핵심감사사항이 감사에서 다루어진 방법\n핵심감사사항에 대응하기 위하여 우리는 다음을 포함한 감사절차를 수행하였습니다.ㆍ손상평가의 손상징후에 대한 검토ㆍ미래현금흐름 추정 및 가정에 대한 경영진의 검토 및 승인 등 내부통제 테스트ㆍ사용가치 추정에 참여한 경영진측 전문가의 적격성과 독립성 평가',
    '비상장 지분증권과 ESG관련 위험으로 인해 유의적인 관측불가능한 조정이 반영되는 금융상품과 같이, 자산이나 부채에 대한 관측할 수 없는 투입변수(수준 3)',
    '회사는 시장위험에 노출되어 있으며 위험관리정책을 검토하고 있습니다. 재무회계부문은 금융관리위원회가 수립한 정책에 따라 위험을 모니터링 및 평가하고 헷지, 보험 가입 등의 관리방안을 실행하고 있습니다. 관련 위험을 최소화하기 위해 위험별 관리절차를 검토하며 위험관리부서의 정책에는 변동이 없습니다.',
])
def test_financial_management_and_auditor_work_do_not_support_challenges(text):
    original = text
    scores, observed = score_fragment_slots_with_signal(text)
    assert not [score for score in scores if score.section_id == 'current_challenges']
    assert observed
    assert is_challenge_accounting_policy(text)
    assert text == original


@pytest.mark.parametrize('business', [
    '회사는 고객에게 위험관리정책에 따른 보험 상품을 판매합니다.',
    '회사는 고객에게 공정가치 평가 서비스를 제공하고 이용료를 받습니다.',
    '회사는 고객에게 감사절차 자동화 솔루션을 제공합니다.',
    '공장에서 제품 결함이 발생하여 생산을 중단했습니다.',
    '고객 대출 연체율이 상승하여 신규 심사모형을 도입했습니다.',
    '회사는 장비 납기 지연을 해결하기 위해 차입금으로 생산설비를 도입했다.',
])
def test_financial_context_keeps_actual_business_and_incidents(business):
    text = '회사는 시장위험에 노출되어 위험관리정책을 검토합니다. ' + business
    result = split_challenge_accounting_policy(text)
    assert business.rstrip('.') in result.score_text
    assert result.excluded_clauses
    assert not is_challenge_accounting_policy(text)


def test_auditor_work_and_independent_company_incident_are_separated():
    business = '회사는 제품 결함이 발생하여 생산을 중단했습니다.'
    text = '핵심감사사항이 감사에서 다루어진 방법\n우리는 감사절차를 수행하였습니다. 손상평가와 관련한 미래현금흐름 추정을 검토하였습니다. ' + business
    result = split_challenge_accounting_policy(text)
    assert business.rstrip('.') in result.score_text
    assert '감사절차' not in result.score_text
    assert '미래현금흐름' not in result.score_text


def test_without_auditor_context_financial_evaluation_business_is_retained():
    text = '회사는 고객의 미래현금흐름을 검토하여 공정가치 평가 보고서를 발행합니다.'
    assert split_challenge_accounting_policy(text).score_text == text
    assert not is_challenge_accounting_policy(text)


def test_financial_management_cross_reference_is_not_a_remaining_business_clause():
    text = '회사는 유동성위험을 관리합니다. 또한, 재무위험관리의 자세한 사항은 "Ⅲ. 재무에 관한 사항 - 3. 연결재무제표 주석 - 4. 재무위험관리"를 참고하십시오.'
    assert is_challenge_accounting_policy(text)
    assert split_challenge_accounting_policy(text).score_text == ''


@pytest.mark.parametrize('text', [
    '금융위험 관리\n연결회사는 자본을 관리하고 있습니다. 위험을 모니터링하고 평가합니다.',
    '감사방법\n우리는 감사절차를 수행하였습니다. 내부통제를 검토하고 평가합니다.',
])
def test_explicit_financial_or_auditor_actor_context_covers_dependent_work(text):
    assert is_challenge_accounting_policy(text)
    assert split_challenge_accounting_policy(text).score_text == ''
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert not [score for score in scores if score.section_id == 'current_challenges']


def test_general_risk_or_internal_control_without_financial_or_auditor_context_is_kept():
    for text in ('공장은 안전 위험을 모니터링하고 평가합니다.',
                 '회사는 내부통제를 검토하고 평가합니다.'):
        assert split_challenge_accounting_policy(text).score_text == text
        assert not is_challenge_accounting_policy(text)
