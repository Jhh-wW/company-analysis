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
