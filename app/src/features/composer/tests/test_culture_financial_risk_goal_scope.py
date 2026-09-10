# -*- coding: utf-8 -*-
"""문화 장의 실측 재무위험 설명 제외와 실제 업무 절차 보존을 검증한다."""
from __future__ import annotations

import json
import re
from hashlib import sha256

import pytest

from src.features.composer.culture_constants import CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED
from src.features.composer.culture_guard import (
    culture_financial_risk_goal_problem,
)
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report

# SM run 2bc1996f... report.json culture 장 실제 산문(정확 원문 그대로).
GOVERNANCE_TEXT = (
    "회사는 이사회를 중심으로 위험관리 체계를 구축하고 감독하는 구조를 갖추고 "
    "있으며, 재무위험관리 활동은 지배기업의 재무 담당부서에서 주관하고 전사통합적 "
    "관점에서 정책을 수립한다."
)
GENERAL_GOAL_TEXT = (
    "회사는 금융시장의 변동성에 초점을 맞춘 전반적인 위험관리정책을 수립하고 "
    "있으며, 재무성과에 미치는 부정적 영향을 최소화하는 데 중점을 두고 있다."
)
FX_EXPOSURE_TEXT = (
    "회사는 국제적 영업활동으로 인해 미국달러화, 일본 엔화, 중국 위안화 등 주요 "
    "통화의 환율 변동 위험에 노출되어 있으며, 특정 위험을 회피하기 위하여 파생상품을 "
    "이용하고 있다."
)


def _approval(calls):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 검수 입력에 후보가 없습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations), "결과": "참"}
            for item in items
        ]}, ensure_ascii=False)
    return ask


# ── ② 산문(평문 단건) — culture_financial_risk_goal_problem ──
def test_prose_general_goal_and_exposure_blocked_governance_kept():
    assert culture_financial_risk_goal_problem(GENERAL_GOAL_TEXT) == CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED
    assert culture_financial_risk_goal_problem(FX_EXPOSURE_TEXT) == CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED
    assert culture_financial_risk_goal_problem(GOVERNANCE_TEXT) == ""


# ── ③ 실제 verify_report 경계 — 평문/묶음 둘 다, 다른 장 보존까지 ──
@pytest.mark.parametrize("grouped", (False, True))
def test_verify_report_culture_keeps_governance_and_blocks_goal_exposure(grouped):
    sentences = (
        ComposedSentence(GOVERNANCE_TEXT, ("gov",), "확인"),
        ComposedSentence(GENERAL_GOAL_TEXT, ("goal",), "확인"),
        ComposedSentence(FX_EXPOSURE_TEXT, ("fx",), "확인"),
    )
    report = ComposedReport((ComposedSection("culture", sentences),))
    fragments = (
        CollectedFragment("gov", "공시", GOVERNANCE_TEXT),
        CollectedFragment("goal", "공시", GENERAL_GOAL_TEXT),
        CollectedFragment("fx", "공시", FX_EXPOSURE_TEXT),
    )
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
        allowed_fragment_ids_by_section={"culture": frozenset(("gov", "goal", "fx"))} if grouped else None,
    )
    assert [s.text for s in checked.sections[0].sentences] == [GOVERNANCE_TEXT]
    reason_codes = sorted(d["reason_code"] for d in diagnostics)
    assert reason_codes == [CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED] * 2
    hashes = {d["candidate_sha256"] for d in diagnostics}
    assert hashes == {
        sha256(GENERAL_GOAL_TEXT.encode()).hexdigest(),
        sha256(FX_EXPOSURE_TEXT.encode()).hexdigest(),
    }


def test_verify_report_other_section_preserves_the_same_text():
    """current_challenges 같은 다른 장은 같은 문장이라도 그대로 보존한다."""
    sentences = (ComposedSentence(GENERAL_GOAL_TEXT, ("goal",), "확인"),)
    report = ComposedReport((ComposedSection("current_challenges", sentences),))
    fragments = (CollectedFragment("goal", "공시", GENERAL_GOAL_TEXT),)
    calls = []
    checked = verify_report(report, fragments, None, _approval(calls))
    assert [s.text for s in checked.sections[0].sentences] == [GENERAL_GOAL_TEXT]
