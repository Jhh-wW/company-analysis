# -*- coding: utf-8 -*-
"""회귀: "사업보고서"의 "보고", "감독당국"의 "감독"만으로 거짓 면제되지 않는다.

기존 CULTURE_FINANCIAL_RISK_GOVERNANCE_VERB_RE가 "보고"·"감독"을 활용형
확인 없이 통째로 매치해, 순수 환율위험·파생상품 노출 문장에 "사업보고서"나
"감독당국" 같은 낱말만 더해도 검토/승인/감독 절차가 있는 것처럼 잘못
면제됐다. 이 파일은 그 회귀를 재현하고, 최소 동사 활용/조사 경계 조건으로
고친 뒤에도 실제 절차 문장은 여전히 보존됨을 같은 절 단위로 확인한다.
plain import만 쓴다(sys.path 조작 없음) — 이식형 시험.
"""
from __future__ import annotations

from src.features.composer.culture_constants import CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED
from src.features.composer.culture_guard import culture_financial_risk_goal_problem

# 순수 노출 문장에 "사업보고서"라는 낱말만 추가 — 검토/승인/감독 절차는 없다.
FX_EXPOSURE_WITH_REPORT_DOCUMENT_WORD = (
    "회사는 국제적 영업활동으로 인해 주요 통화의 환율 변동 위험에 노출되어 있으며, "
    "특정 위험을 회피하기 위하여 파생상품을 이용하고 있고 그 내용을 사업보고서에 "
    "기재한다."
)
# 순수 목표 문장에 "감독당국"이라는 낱말만 추가 — 이사회 절차가 아니라 외부 기관 명사다.
GENERAL_GOAL_WITH_REGULATOR_NOUN = (
    "회사는 금융시장의 변동성에 초점을 맞춘 전반적인 위험관리정책을 수립하고 있으며, "
    "감독당국의 규정에 따라 재무성과에 미치는 부정적 영향을 최소화하는 데 중점을 "
    "두고 있다."
)
# 실제 절차: 이사회가 «직접» 보고·검토·승인한다 — 여전히 보존돼야 한다.
GOAL_WITH_REAL_BOARD_REPORTING = (
    "이사회는 금융시장의 변동성에 초점을 맞춘 위험관리정책을 매 분기 보고받고 "
    "검토하며 승인한다."
)
# 실제 절차: 이사회 감독 «하에» 재무부서가 주관 — 여전히 보존돼야 한다.
GOVERNANCE_STRUCTURE = (
    "회사는 이사회를 중심으로 위험관리 체계를 구축하고 감독하는 구조를 갖추고 "
    "있으며, 재무위험관리 활동은 지배기업의 재무 담당부서에서 주관하고 전사통합적 "
    "관점에서 정책을 수립한다."
)


def test_report_document_noun_does_not_exempt_pure_fx_exposure():
    assert (culture_financial_risk_goal_problem(FX_EXPOSURE_WITH_REPORT_DOCUMENT_WORD)
            == CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED)


def test_regulator_authority_noun_does_not_exempt_pure_general_goal():
    assert (culture_financial_risk_goal_problem(GENERAL_GOAL_WITH_REGULATOR_NOUN)
            == CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED)


def test_real_board_reporting_review_approval_is_still_preserved():
    assert culture_financial_risk_goal_problem(GOAL_WITH_REAL_BOARD_REPORTING) == ""


def test_board_oversight_and_finance_department_stewardship_is_still_preserved():
    assert culture_financial_risk_goal_problem(GOVERNANCE_STRUCTURE) == ""
