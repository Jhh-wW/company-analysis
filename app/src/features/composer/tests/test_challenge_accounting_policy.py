"""회사 사건·서비스 보존 및 엔진과 최종 공개 가드의 동일 계약."""
import ast
from pathlib import Path

from src.features.composer import challenge_accounting_constants as c
from src.features.composer.accounting_policy_guard import accounting_policy_matched_rules, accounting_policy_mixed
from src.features.composer.challenge_accounting_policy import is_challenge_accounting_policy


def test_engine_app_policy_patterns_match():
    path = Path(__file__).resolve().parents[5] / "analysis_engine/src/features/evidence_collection/challenge_accounting_constants.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    patterns = {node.target.id: ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.AnnAssign) and node.target.id.endswith("_PATTERN")}
    assert patterns
    for name, value in patterns.items():
        assert getattr(c, name) == value


def test_audit_procedures_and_financial_assumptions_are_not_business_challenges():
    for text in (
        "연결회사는 지분상품에서 발생하는 가격변동위험에 노출되어 있습니다.",
        "부정 및 부정위험과 관련된 감사 절차 수행결과 보고",
        "감사 진행상황 보고 (독립성, 핵심감사사항, 내부회계관리제도감사 및 자금 관련 부정위험 통제 등)",
        "금융상품과 같이 자산이나 부채에 대한 관측할 수 없는 투입변수의 위험을 평가한다.",
        "금융 위험을 식별, 모니터링 및 평가하여 관리한다.",
    ):
        assert is_challenge_accounting_policy(text), text
        assert accounting_policy_matched_rules(text, None, "current_challenges")


def test_audit_customer_service_and_real_operation_mixed_with_policy_survive():
    for text in (
        "회사는 고객에게 공정가치 평가 서비스를 제공한다.",
        "은행의 차주 연체율이 급증하여 신용위험에 대응했다.",
        "감사 진행상황을 보고하며, 공장 생산이 중단되어 고객 납품 지연이 발생하여 대응했다.",
    ):
        assert not is_challenge_accounting_policy(text), text


def test_policy_amount_and_sales_rights_do_not_become_challenges():
    for text in (
        "회사는 판매로 인하여 부담하는 보증책임에 대한 충당부채를 합리적으로 추정하여 인식하고 있다.",
        "회사는 제품 판매에서 수량할인을 제공하며 고객은 불량재화를 반품할 권리를 가지고 있다.",
        "충당부채 1.5억원은 보증책임을 추정하여 인식한 금액이다.",
        "공장 투자용 대출금 잔액은 20억원이다.",
        "유동성 위험에 노출된 금융부채 잔액은 1.5억원이다.",
    ):
        assert accounting_policy_matched_rules(text, None, "current_challenges") == ("사업문제미결속재무회계조건",)


def test_events_and_accounting_business_service_are_not_filtered():
    for text in (
        "제품 결함으로 반품이 급증하여 보증충당부채를 인식하였다.",
        "리콜을 실시한 제품의 환불액을 추정하여 충당부채를 인식하였다.",
        "회사는 고객에게 수익인식 회계 자문 서비스를 제공한다.",
        "유동성 위험 증가로 고객 납품을 중단하였습니다.",
        "금융회사는 고객에게 유동성 관리 서비스를 제공한다.",
        "대출 고객의 연체율이 상승하여 충당부채를 인식하였다.",
    ):
        assert not is_challenge_accounting_policy(text)


def test_mixed_paragraph_is_retained_with_policy_diagnostic():
    text = "반품액은 과거 경험을 바탕으로 추정한다. 제품 결함으로 반품이 급증하였다."
    assert accounting_policy_mixed(text, None, "current_challenges")


def test_same_sentence_actual_events_are_not_hidden_by_conditional_policy():
    for text in (
        "제품 결함으로 리콜을 실시했으며, 추가 손실이 발생할 경우 충당부채를 인식한다.",
        "납품이 중단되었으며, 손실이 발생할 경우 충당부채를 인식한다.",
    ):
        assert not is_challenge_accounting_policy(text)


def test_hypothetical_events_and_planned_service_are_not_current_problems():
    for text in (
        "제품의 결함이 발견되면 보증충당부채를 추정하여 인식한다.",
        "고객의 연체율이 상승할 것으로 예상하여 대출 충당부채를 인식한다.",
        "생산 중단으로 손실이 발생할 경우 충당부채를 추정하여 인식한다.",
        "고객에게 대출 관리 플랫폼을 제공할 계획이며 충당부채를 인식한다.",
    ):
        assert is_challenge_accounting_policy(text)


def test_comma_cannot_remove_accounting_subject_from_final_guard():
    for text in (
        "운송용역의 거래가격을 배분하며, 발생한 원가를 기준으로 진행률을 산정하고 추정치를 매년 검토한다.",
        "회사는 외화 거래를 수행하므로 환율변동으로 인한 위험에 노출되어 있다.",
        "회사는 신용위험을 관리하며, 위험을 식별하여 정기적으로 대응한다.",
    ):
        assert is_challenge_accounting_policy(text)
        assert all(accounting_policy_matched_rules(text, None, "current_challenges"))


def test_actual_business_event_and_fx_customer_service_remain():
    for text in (
        "환율변동 위험이 커져 원재료 조달이 중단되었다.",
        "회사는 고객에게 외환 위험관리 솔루션을 제공한다.",
        "거래가격을 배분하며, 제품 결함으로 리콜을 실시했다.",
        "환율위험을 정기적으로 관리하며, 고객 납품이 중단되었다.",
    ):
        assert not is_challenge_accounting_policy(text)


def test_customer_financial_services_and_real_customer_pressure_are_preserved():
    for text in (
        "회사는 기업 고객에게 공정가치 평가보고서를 발행합니다.",
        "회사는 고객을 대상으로 대출 서비스를 제공한다.",
        "회사는 고객에게 신용위험 평가 서비스를 제공한다.",
        "회사는 차주에게 대출 서비스를 제공합니다.",
        "회사는 대출 고객 확보에 어려움을 겪고 있다.",
        "고객 대출 연체율이 높아 회사가 심사 기준을 강화했다.",
        "고객 대출에서 부실률이 급등했고 회사는 신규 심사모형을 도입했다.",
        "회사는 반품 충당부채를 추정하며, 불량을 줄이기 위해 검사 공정을 자동화했다.",
    ):
        assert not is_challenge_accounting_policy(text), text


def test_conditional_discovered_defect_is_not_an_actual_event():
    text = "회사는 제품 보증기간 중 결함이 발견되었을 때 수익을 인식하지 않고 보증충당부채를 추정합니다."
    assert is_challenge_accounting_policy(text)
    assert all(accounting_policy_matched_rules(text, None, "current_challenges"))


def test_financial_term_does_not_override_independent_business_clause():
    for text in (
        "은행은 대출 신청 고객의 긴 대기시간을 줄이기 위해 비대면 심사를 도입했다.",
        "회사는 금융부채를 측정하며, 반도체 고객 수요가 줄어 생산라인 가동률이 떨어졌다.",
        "회사는 장비 납기 지연을 해결하기 위해 대출을 받아 신규 조립라인을 설치했다.",
        "회사는 공급 지연에 대응하기 위해 차입금으로 신규 생산설비를 도입했다.",
    ):
        assert not is_challenge_accounting_policy(text)
    assert is_challenge_accounting_policy("회사는 고객에게 대출 서비스를 제공할 예정이다.")


def test_interest_administration_is_blocked_in_body_and_own_source_rewording():
    from src.features.composer.challenge_business_scope import challenge_business_problem

    source = (
        "이자율과 외화위험을 관리하기 위해 필요한 경우 파생상품계약을 체결하고 있습니다."
        "연결회사는 이자율 변동으로 인한 불확실성 제거와 금융원가 최소화를 위해, "
        "주기적인 금리동향 모니터링과 적절한 대응방안 수립을 운용하고 있습니다."
    )
    candidate = (
        "회사는 이자율과 외화위험을 관리하기 위해 파생상품계약을 체결하고 있으며, "
        "주기적인 금리동향 모니터링과 적절한 대응방안 수립을 운용하고 있다."
    )
    assert is_challenge_accounting_policy(source)
    assert is_challenge_accounting_policy(candidate)
    assert challenge_business_problem(candidate, {"1": source})
    assert challenge_business_problem("회사는 위험에 적절하게 대응하고 있다.", {"1": source})


def test_interest_service_and_real_operating_issue_are_preserved():
    from src.features.composer.challenge_business_scope import challenge_business_problem

    for text in (
        "회사는 고객에게 금리동향 모니터링 서비스를 제공한다.",
        "회사는 기업 고객에게 이자율 분석 보고서를 발행합니다.",
        "회사는 고객에게 파생상품 위험관리 서비스를 제공한다.",
        "은행은 대출 고객의 연체율이 상승하여 심사 기준을 강화했다.",
        "회사는 공장 안전센서를 모니터링하고 설비 개선을 실시했다.",
        "회사는 금융원가를 최소화하며, 고객 납품이 중단되었다.",
        "회사는 금리동향을 모니터링한다. 제품 결함으로 리콜을 실시했다.",
    ):
        assert not is_challenge_accounting_policy(text), text
        assert not challenge_business_problem(text, {"1": text}), text
