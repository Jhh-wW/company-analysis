"""별도 배포되는 엔진의 좁은 유동성 판정과 composer 규칙을 대조한다."""

from __future__ import annotations

import ast
from pathlib import Path

from src.features.composer.accounting_policy_constants import (
    COMPANY_EVENT_RE,
    ACCOUNTING_POLICY_CLAUSE_SPLIT_RE,
    LIQUIDITY_ACTUAL_PRESSURE_RE,
    LIQUIDITY_BUSINESS_OFFERING_RE,
    LIQUIDITY_BUSINESS_EVENT_RE,
    LIQUIDITY_BOILERPLATE_RE,
    LIQUIDITY_SUBJECT_RE,
    MONETARY_BARE_WON_RE,
    MONETARY_FOREIGN_AMOUNT_RE,
    MONETARY_UNIT_AMOUNT_RE,
)
from src.features.composer.accounting_policy_guard import accounting_policy_matched_rules
from src.features.composer.culture_constants import SOURCE_CLAUSE_SPLIT_RE


ENGINE_SRC = Path(__file__).resolve().parents[5] / "analysis_engine" / "src"
ENGINE_CONSTANTS = (
    ENGINE_SRC / "features" / "evidence_collection" / "liquidity_constants.py"
)


def _engine_patterns() -> dict[str, str]:
    """배포 단위간 직접 import 없이 좁은 정책 문자열을 AST로 대조한다."""

    tree = ast.parse(ENGINE_CONSTANTS.read_text(encoding="utf-8"))
    names = {
        "CLAUSE_SPLIT_PATTERN",
        "DECIMAL_SAFE_CLAUSE_SPLIT_PATTERN",
        "ACTUAL_PRESSURE_PATTERN",
        "BUSINESS_OFFERING_PATTERN",
        "BUSINESS_EVENT_PATTERN",
        "LIQUIDITY_SUBJECT_PATTERN",
        "LIQUIDITY_BOILERPLATE_PATTERN",
        "MONETARY_UNIT_AMOUNT_PATTERN",
        "MONETARY_BARE_WON_PATTERN",
        "MONETARY_FOREIGN_AMOUNT_PATTERN",
        "COMPANY_EVENT_PATTERN",
    }
    values: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in names and node.value is not None:
                values[node.target.id] = ast.literal_eval(node.value)
    assert set(values) == names
    return values


def test_liquidity_rule_patterns_match_collector_copy() -> None:
    engine = _engine_patterns()
    assert engine["CLAUSE_SPLIT_PATTERN"] == SOURCE_CLAUSE_SPLIT_RE.pattern
    assert engine["DECIMAL_SAFE_CLAUSE_SPLIT_PATTERN"] == ACCOUNTING_POLICY_CLAUSE_SPLIT_RE.pattern
    assert engine["ACTUAL_PRESSURE_PATTERN"] == LIQUIDITY_ACTUAL_PRESSURE_RE.pattern
    assert engine["BUSINESS_OFFERING_PATTERN"] == LIQUIDITY_BUSINESS_OFFERING_RE.pattern
    assert engine["BUSINESS_EVENT_PATTERN"] == LIQUIDITY_BUSINESS_EVENT_RE.pattern
    assert engine["LIQUIDITY_SUBJECT_PATTERN"] == LIQUIDITY_SUBJECT_RE.pattern
    assert engine["LIQUIDITY_BOILERPLATE_PATTERN"] == LIQUIDITY_BOILERPLATE_RE.pattern
    assert engine["MONETARY_UNIT_AMOUNT_PATTERN"] == MONETARY_UNIT_AMOUNT_RE.pattern
    assert engine["MONETARY_BARE_WON_PATTERN"] == MONETARY_BARE_WON_RE.pattern
    assert engine["MONETARY_FOREIGN_AMOUNT_PATTERN"] == MONETARY_FOREIGN_AMOUNT_RE.pattern
    assert engine["COMPANY_EVENT_PATTERN"] == COMPANY_EVENT_RE.pattern


def test_liquidity_ordinary_policy_money_and_event_match_guard() -> None:
    cases = (
        ("회사는 유동성을 예측하고 관리합니다", "사업문제미결속재무회계조건"),
        ("유동성 위험을 관리하고 금융부채 잔액 1,234백만원을 확인했습니다", "사업문제미결속재무회계조건"),
        ("회사는 유동성 위험관리 방식을 변경했습니다", "사업문제미결속재무회계조건"),
        ("부채상환을 위한 유동성은 3개월 단위로 예측하고 관리합니다", "사업문제미결속재무회계조건"),
        ("회사의 신용등급 하락으로 유동성위험이 증가했습니다", "사업문제미결속재무회계조건"),
        ("채무가 연체되어 유동성위험이 커졌습니다", "사업문제미결속재무회계조건"),
        ("유동성위험이 확대되어 금융기관과 협의 중입니다", "사업문제미결속재무회계조건"),
        ("금융회사는 고객에게 유동성위험 관리 서비스를 제공합니다", ""),
        ("유동성 위험 | 영업자금 수요를 예측하고 관리합니다", "사업문제미결속재무회계조건"),
        ("당사는 유동성위험으로 인해 신규 주문의 수주를 중단하였다", ""),
        ("당사는 유동성위험에 대응하기 위해 공장 생산을 중단하였다", ""),
        ("당사는 기업고객에게 유동성위험 모니터링 솔루션을 제공한다", ""),
        ("당사는 기업고객에게 유동성위험 관리 솔루션을 제공한다", ""),
        ("당사는 기업고객에게 유동성위험 평가 보고서를 발행한다", ""),
        ("부채상환을 위한 유동성 관리와 관련하여 공장 생산을 중단하였다", ""),
        ("유동성위험을 관리하며 필요시 공장 생산을 중단할 수 있다", "사업문제미결속재무회계조건"),
        ("유동성위험을 관리하며 고객에게 지급할 대금을 점검한다", "사업문제미결속재무회계조건"),
    )
    # 새 5장 계약에서는 금액·신용사건도 사업 운영과 연결해야 한다.
    for clause, expected in cases:
        composer_rule = accounting_policy_matched_rules(clause, None, "current_challenges")
        assert composer_rule == (expected,)


def test_decimal_financial_amount_is_preserved_but_not_a_business_challenge() -> None:
    from src.features.composer.accounting_policy_guard import accounting_policy_mixed

    amount = "유동성 위험에 노출된 금융부채 잔액은 1.5억원입니다."
    assert accounting_policy_matched_rules(amount, None, "current_challenges") == ("사업문제미결속재무회계조건",)
    assert not accounting_policy_mixed(amount, None, "current_challenges")
    mixed = "회사는 유동성 위험을 예측하고 관리합니다. " + amount
    assert accounting_policy_matched_rules(mixed, None, "current_challenges") == (
        "사업문제미결속재무회계조건", "사업문제미결속재무회계조건",
    )
    assert not accounting_policy_mixed(mixed, None, "current_challenges")
