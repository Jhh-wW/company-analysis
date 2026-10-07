"""조건부 일반 업무와 사고 이력의 현재 필수칸을 구분한다."""
import hashlib

import pytest

from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_problem, challenge_eligibility_quote_problem,
    challenge_eligibility_scope,
)

MANAGEMENT = (
    "법률 리스크 관리는 국내법무팀 및 국제법무팀에서 전문가를 자문역으로 하여 "
    "사업 수행 과정에서 발생할 수 있는 법률적 분쟁을 미연에 방지하고 있으며, "
    "분쟁 발생 시 소송 수행을 지원하는 사후 교정적 업무를 다루고 있습니다."
)
LEASE = (
    "리스제공자로서 회사는 리스약정일에 금융리스인지 운용리스인지 판단합니다. "
    "운용리스 수익은 리스기간에 걸쳐 정액기준으로 인식합니다. "
    "리스 체결 과정에서 부담하는 직접원가를 장부금액에 더하고 리스기간에 걸쳐 비용으로 인식합니다."
)
DEFINITION = "동 위험은 회사의 투자 및 재무활동에서 발생하는 이자수익과 이자비용이 변동될 위험을 의미합니다."
RULE = (
    "당사는 시장지배적사업자로 추정됩니다. 동 규정을 위반할 경우 "
    "시정조치와 과징금을 부과받을 수 있습니다."
)
HEADER = "재해발생회사 | 중대재해발생일자 | 발생장소 | 재해내용 | 조치 및 전망"
HISTORY = "가온제조 | 2026.10.04 | 가온제조 공장 | 성형공정 끼임사고 | 안전센서 설치, 연속 동작 금지"


@pytest.mark.parametrize("source", [MANAGEMENT, LEASE, DEFINITION, RULE,
    "회사는 증분차입이자율을 사용한다. 리스부채를 재평가하고 사용권자산을 원가로 측정한다.",
])
def test_general_management_and_definition_do_not_fill_current_slots(source):
    assert challenge_eligibility_problem(source) == "challenge_business_relation_unbound"
    for slot in ("current_challenges:issue", "current_challenges:response"):
        assert challenge_eligibility_quote_problem(source, source, slot)
        assert not challenge_eligibility_quote_problem(source, source, "past_changes:completed_execution")


@pytest.mark.parametrize("source", [MANAGEMENT, LEASE, DEFINITION, RULE])
@pytest.mark.parametrize("problem", [
    "보험 고객의 보상 지급 지연이 지속되고 있다.",
    "은행의 대출 고객이 연체 피해를 겪고 있어 대응하고 있다.",
    "회사의 제품 결함으로 고객 납품이 중단됐다.",
    "고객의 실제 발생한 소송이 진행 중이며 법률 서비스를 제공하고 있다.",
])
def test_actual_problem_in_mixed_raw_survives_and_original_stays_exact(source, problem):
    raw = source + " " + problem
    scope = challenge_eligibility_scope(raw)
    assert problem in scope.score_text
    assert source not in scope.score_text
    assert not challenge_eligibility_quote_problem(problem, raw, "current_challenges:issue")
    assert challenge_eligibility_quote_problem(source, raw, "current_challenges:issue")
    assert raw == source + " " + problem


def test_general_legal_risk_and_conditional_management_do_not_fill_current_slots():
    source = "법률 리스크가 증가하고 있습니다. OE 사업에서 법률 리스크가 발생할 수 있어 전담 조직을 갖추고 있습니다."
    digest = hashlib.sha256(source.encode()).hexdigest()
    # 일반 위험 증가와 조건부 관리만으로 현재 사업 피해나 진행 분쟁을 증명하지 않는다.
    assert challenge_eligibility_problem(source) == "challenge_business_relation_unbound"
    assert not challenge_eligibility_scope(source).score_text.strip()
    for slot in ("current_challenges:issue", "current_challenges:response"):
        assert challenge_eligibility_quote_problem(source, source, slot) == "challenge_business_relation_unbound"
    assert not challenge_eligibility_quote_problem(source, source, "past_changes:completed_execution")
    assert hashlib.sha256(source.encode()).hexdigest() == digest


def test_actual_product_dispute_survives_the_same_conditional_management_context():
    management = "법률 리스크가 증가하고 있습니다. OE 사업에서 법률 리스크가 발생할 수 있어 전담 조직을 갖추고 있습니다."
    problem = "현재 제품 특허 소송이 진행 중이며 고객 납품 차질이 지속되고 있다."
    source = management + " " + problem
    digest = hashlib.sha256(source.encode()).hexdigest()
    scope = challenge_eligibility_scope(source)
    assert problem in scope.score_text
    assert management not in scope.score_text
    assert not challenge_eligibility_quote_problem(problem, source, "current_challenges:issue")
    for slot in ("current_challenges:issue", "current_challenges:response"):
        assert challenge_eligibility_quote_problem(management, source, slot) == "challenge_business_relation_unbound"
    assert hashlib.sha256(source.encode()).hexdigest() == digest


def test_dated_accident_measures_do_not_prove_current_status_or_completion():
    raw = HEADER + "; " + HISTORY
    assert challenge_eligibility_problem(raw) == "challenge_current_problem_unbound"
    assert challenge_eligibility_quote_problem("안전센서 설치", raw, "current_challenges:response")
    assert not challenge_eligibility_quote_problem(HISTORY, raw, "past_changes:completed_execution")
    assert raw == HEADER + "; " + HISTORY


@pytest.mark.parametrize("status", [
    "안전센서 설치 진행 중", "현재 생산이 중단되어 고객 납품 지연이 지속되고 있다",
    "사고 위험이 여전히 미해결 상태다", "복구 진행 중", "와이어 시스템 설치 진행 중",
])
def test_same_row_current_status_survives_and_does_not_restore_another_history(status):
    current = HISTORY.replace("2026.10.04", "2026.10.03").replace("안전센서 설치, 연속 동작 금지", status)
    raw = HEADER + "; " + HISTORY + "; " + current
    scope = challenge_eligibility_scope(raw)
    assert HEADER in scope.score_text and current in scope.score_text
    assert HISTORY not in scope.score_text
    assert not challenge_eligibility_quote_problem(current, raw, "current_challenges:issue")
    assert challenge_eligibility_quote_problem(HISTORY, raw, "current_challenges:issue")


def test_new_accident_news_without_history_table_is_preserved():
    source = "2026.10.04 공장 화재로 생산이 중단됐다. 안전센서 설치를 검토한다."
    assert not challenge_eligibility_problem(source)
    assert challenge_eligibility_scope(source).score_text == source


def test_period_inside_incident_column_does_not_separate_measures_from_same_row():
    row = HISTORY.replace("성형공정 끼임사고", "설비에서 추락함.")
    raw = HEADER + "; " + row
    assert not challenge_eligibility_scope(raw).score_text
    assert challenge_eligibility_quote_problem("안전센서 설치", raw, "current_challenges:response")


def test_leasing_service_business_is_not_rejected_by_lease_term_alone():
    source = "리스는 리스제공자가 운송차량을 임대해 월 이용료를 받는 핵심 서비스다. 리스 차량의 수리비가 지난해보다 20% 증가했다."
    assert challenge_eligibility_scope(source).score_text == source
    assert not challenge_eligibility_problem(source)


def test_actual_leasing_cost_change_survives_accounting_definition_context():
    problem = "리스 차량의 수리비가 지난해보다 20% 증가했다."
    source = LEASE + " " + problem
    assert problem in challenge_eligibility_scope(source).score_text
    assert not challenge_eligibility_quote_problem(problem, source, "current_challenges:issue")


def test_actual_production_leasing_cost_survives_accounting_change_context():
    policy = "회계정책과 공시의 변경으로 기업회계기준서를 적용한다."
    problem = "신규 생산설비의 리스료가 전년보다15% 상승했다."
    source = policy + " " + problem
    assert problem in challenge_eligibility_scope(source).score_text
    assert policy not in challenge_eligibility_scope(source).score_text
    assert not challenge_eligibility_quote_problem(problem, source, "current_challenges:issue")
    assert challenge_eligibility_quote_problem(policy, source, "current_challenges:issue")
