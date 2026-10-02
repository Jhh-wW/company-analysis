"""최종 검수에도 금융 관리·감사인의 문맥을 같은 기준으로 적용한다."""
import pytest

from src.features.composer.challenge_accounting_policy import is_challenge_accounting_policy


@pytest.mark.parametrize('text', [
    '핵심감사사항이 감사에서 다루어진 방법\n핵심감사사항에 대응하기 위하여 우리는 감사절차를 수행하였습니다.ㆍ손상평가의 손상징후 검토ㆍ미래현금흐름 추정 및 가정의 내부통제 테스트ㆍ사용가치 추정에 참여한 전문가의 적격성 평가',
    '비상장 지분증권과 ESG관련 위험으로 인해 관측불가능한 조정이 반영되는 금융상품과 같이, 자산이나 부채에 대한 관측할 수 없는 투입변수(수준 3)',
    '회사는 시장위험에 노출되어 위험관리정책을 검토합니다. 재무회계부문은 금융관리위원회의 위험관리정책을 검토하며 헷지, 보험 가입 등의 관리방안을 실행합니다.',
])
def test_only_financial_administration_and_audit_work_are_policy(text):
    assert is_challenge_accounting_policy(text)


@pytest.mark.parametrize('business', [
    '회사는 고객에게 위험관리정책에 따른 보험 상품을 판매합니다.',
    '회사는 고객에게 공정가치 평가 서비스를 제공하고 이용료를 받습니다.',
    '회사는 고객의 미래현금흐름을 검토하여 공정가치 평가 보고서를 발행합니다.',
    '공장에서 제품 결함이 발생하여 생산을 중단했습니다.',
])
def test_financial_management_context_does_not_erase_real_business(business):
    assert not is_challenge_accounting_policy('회사는 시장위험에 노출되어 위험관리정책을 검토합니다. ' + business)


def test_intended_service_is_not_current_service_exemption():
    assert is_challenge_accounting_policy('회사는 고객에게 공정가치 평가 서비스를 제공하고자 합니다.')


def test_explicit_auditor_and_financial_context_dependent_clauses():
    assert is_challenge_accounting_policy('금융위험 관리\n연결회사는 자본을 관리하고 있습니다. 위험을 모니터링하고 평가합니다.')
    assert is_challenge_accounting_policy('감사방법\n우리는 감사절차를 수행하였습니다. 내부통제를 검토하고 평가합니다.')
    assert not is_challenge_accounting_policy('공장은 안전 위험을 모니터링하고 평가합니다.')
    assert not is_challenge_accounting_policy('회사는 내부통제를 검토하고 평가합니다.')
