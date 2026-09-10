# -*- coding: utf-8 -*-
"""문화 장의 «재무위험 관리 규정» 서술 제외와 조직·제도 문장 보존을 검증한다.

★ 왜 이 파일이 따로 필요한가 (실측) — 교육서비스 회사의 실제 유료 실행에서
  「인재상과 일하는 방식」 장 6문장 중 4문장이 재무 서술이었다. 그중 셋은
  기존 culture 게이트(회계 인식·측정, 재무위험 목표/노출)의 어느 표지에도
  걸리지 않고 그대로 실렸다:
    · 신용검증절차·연체관리·손상여부 (신용위험 관리 원칙)
    · 자금수지 예측·현금수준 추정 (유동성위험 관리 원칙)
    · 환위험 관리 규정의 정의·측정주기·관리주체·관리절차
  기존 규칙은 「위험관리정책 + 금융시장 변동성」과 「환율변동위험·파생상품」
  이라는 두 모양만 알고 있었다. 아래 시험은 그 빈자리를 못 박는다.

★ 보존해야 하는 것도 같이 못 박는다 — 재무위험을 «누가» 맡는지 조직으로
  설명한 문장, 복리후생·직원 현황·조직개편 같은 사람·조직 자료.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256

import pytest

from src.features.composer.culture_constants import (
    CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED,
)
from src.features.composer.culture_guard import culture_financial_risk_goal_problem
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report

# ── 실측 원문 그대로 (run 979d5e3f… report.json 의 culture 장 산문) ──
CREDIT_RULE_TEXT = (
    "회사는 신용거래를 희망하는 모든 거래상대방에 대하여 신용검증절차의 수행을 "
    "원칙으로 하여 신용상태가 건전한 거래상대방과의 거래만을 수행하고, 주기적으로 "
    "연체관리를 하며 매 보고기간말에 개별적으로 손상여부를 검토하는 체계적 관리 "
    "원칙을 유지하고 있다."
)
LIQUIDITY_RULE_TEXT = (
    "회사는 적정 유동성의 유지를 위하여 주기적인 자금수지 예측, 필요 현금수준 추정, "
    "자금수지 관리 및 계획대비 실적 관리를 통하여 유동성 위험을 최소화하는 선제적 "
    "재무관리 원칙을 실행하고 있다."
)
FX_RULE_TEXT = (
    "멀티캠퍼스는 환위험이 최소화되도록 사전적으로 관리하며, 환위험 관리 규정에 "
    "환위험의 정의, 측정주기, 관리주체, 관리절차 등을 포함하고 외환거래는 경상거래와 "
    "관련된 건으로 엄격하게 제한하며 투기적 거래를 금지하는 규범적 관리 체계를 "
    "갖추고 있다."
)
# 같은 장의 나머지 재무 문장 — 이건 «누가 맡는가»를 조직으로 말하므로 보존한다.
RISK_OWNER_TEXT = (
    "멀티캠퍼스는 재무위험관리를 주로 경영지원팀에서 주관하되, 회사 내 사업부와의 "
    "긴밀한 협조 하에 재무위험 관리정책 수립 및 재무위험의 측정, 평가, 헷지 등을 "
    "실행하는 협력적 의사결정 구조를 운영하고 있다."
)

BLOCKED_TEXTS = (CREDIT_RULE_TEXT, LIQUIDITY_RULE_TEXT, FX_RULE_TEXT)

# ── 보존돼야 하는 사람·조직 자료 (원문에 실제로 있던 종류) ──
BENEFIT_TEXT = (
    "회사는 복리후생 규정에 따라 임직원에게 학자금과 의료비를 지원하고 있으며, "
    "선택적 복지 제도를 운영한다."
)
HEADCOUNT_TEXT = "2025년 말 기준 직원 수는 1,234명이고 평균 근속연수는 7.2년이다."
REORGANIZATION_TEXT = (
    "회사는 인재개발사업부, 스마트교육사업부, 전략사업부, HRD R&D센터, 경영지원실로 "
    "조직을 개편했다."
)
COMMITTEE_TEXT = "보상위원회는 성과평가 결과를 검토하고 임원 보상안을 승인한다."
FINANCE_EDUCATION_TEXT = (
    "회사는 임직원을 대상으로 금융상품 이해도를 높이는 교육을 분기마다 실시한다."
)

KEPT_TEXTS = (
    RISK_OWNER_TEXT,
    BENEFIT_TEXT,
    HEADCOUNT_TEXT,
    REORGANIZATION_TEXT,
    COMMITTEE_TEXT,
    FINANCE_EDUCATION_TEXT,
)


def _approval(calls):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 검수 입력에 후보가 없습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations),
             "결과": "참"}
            for item in items
        ]}, ensure_ascii=False)
    return ask


# ══════════════════════════════════════════════════════════
# ① 실측 3문장이 차단된다 (양성)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("text", BLOCKED_TEXTS)
def test_실측_재무위험_관리규정_문장은_문화장에서_제외된다(text):
    assert culture_financial_risk_goal_problem(text) == CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED


# ══════════════════════════════════════════════════════════
# ② 조직·제도 문장은 보존된다 (음성)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("text", KEPT_TEXTS)
def test_사람_조직_자료와_담당조직_설명은_보존된다(text):
    assert culture_financial_risk_goal_problem(text) == ""


def test_조직_주체가_없으면_절차_동사만으로는_면제되지_않는다():
    """«손상여부를 검토하는»의 «검토»는 사람·조직이 하는 승인 절차가 아니다.

    이 문장에는 검토·관리 같은 동사가 있는데도 차단돼야 한다 — 동사만 보는
    기존 면제 규칙을 이 블록이 그대로 쓰면 실측 문장이 다시 새어 나간다.
    """
    assert "검토" in CREDIT_RULE_TEXT
    assert culture_financial_risk_goal_problem(CREDIT_RULE_TEXT) == (
        CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED
    )


def test_담당조직이_있어도_절차가_부정되면_보존하지_않는다():
    text = "회사의 신용위험 관리규정은 담당부서가 검토하지 않고 승인하지 않는다."
    assert culture_financial_risk_goal_problem(text) == (
        CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED
    )


def test_위험_범주만_있고_규정_표현이_없으면_대상이_아니다():
    """«금융상품»·«유동성» 같은 낱말 하나로 일괄 차단하지 않는다."""
    assert culture_financial_risk_goal_problem(
        "회사는 임직원에게 금융상품 관련 사내 세미나를 열었다."
    ) == ""


# ══════════════════════════════════════════════════════════
# ③ 실제 verify_report 경계 — 배선까지 확인한다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("grouped", (False, True))
def test_verify_report가_문화장의_재무규정만_빼고_조직_설명은_남긴다(grouped):
    texts = (RISK_OWNER_TEXT, CREDIT_RULE_TEXT, LIQUIDITY_RULE_TEXT, FX_RULE_TEXT)
    ids = ("owner", "credit", "liquidity", "fx")
    report = ComposedReport((ComposedSection(
        "culture",
        tuple(ComposedSentence(text, (fragment_id,), "확인")
              for text, fragment_id in zip(texts, ids, strict=True)),
    ),))
    fragments = tuple(
        CollectedFragment(fragment_id, "공시", text)
        for text, fragment_id in zip(texts, ids, strict=True)
    )
    diagnostics: list[dict] = []
    checked = verify_report(
        report, fragments, None, _approval([]), diagnostics=diagnostics,
        allowed_fragment_ids_by_section={"culture": frozenset(ids)} if grouped else None,
    )

    assert [s.text for s in checked.sections[0].sentences] == [RISK_OWNER_TEXT]
    assert sorted(d["reason_code"] for d in diagnostics) == (
        [CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED] * 3
    )
    assert {d["candidate_sha256"] for d in diagnostics} == {
        sha256(text.encode()).hexdigest() for text in BLOCKED_TEXTS
    }


# ══════════════════════════════════════════════════════════
# ④ 작성 안내문이 «무엇을 써야 하는지»까지 말한다
# ══════════════════════════════════════════════════════════


def test_문화장_안내문이_사람_조직_자료를_이_장_소유로_지목한다():
    """★ 왜 필요한가 (실측) — 실행 원문에는 복리후생 11회·「직원 등의 현황」
    3회·근속연수 6회·조직개편 3회가 있었는데 이 장은 한 문장도 쓰지 않았다.
    이 실행은 장별 조각 묶음(packet) 경로가 아니라 아홉 장이 «같은 조각
    전부»를 보는 경로였다(`report.json`에 `release_mode`·
    `public_structure_manifest`가 없다 = FULL 아님 → `build_section_prompt`가
    `prepared is None` 가지로 들어가 조각을 그대로 넘긴다). 즉 자료는 눈앞에
    있었고 작가를 그쪽으로 보낸 안내가 없었던 것이다. 4장·7장에는 «★ …이
    장이 소유한다» 문장이 있는데 8장에만 없었다.
    """
    from src.features.composer.constants import SECTION_GUIDES

    guide = SECTION_GUIDES["culture"]
    assert "이 장이 소유한다" in guide
    for material in ("인재상", "복리후생", "직원 현황", "근속", "조직개편"):
        assert material in guide, f"안내문에 «{material}» 자료 지목이 없습니다"


def test_문화장_안내문이_재무_위험관리_규정을_이_장_밖으로_돌린다():
    from src.features.composer.constants import SECTION_GUIDES

    guide = SECTION_GUIDES["culture"]
    owned, _, not_owned = guide.partition("이 장이 소유하지 않는 것:")
    assert not_owned, "안내문에 «소유하지 않는 것» 절이 없습니다"
    for term in ("신용위험", "유동성", "환위험"):
        assert term in not_owned, f"«{term}»가 제외 목록에 없습니다"
    # 담당 조직 설명은 예외로 남긴다 — 이 장의 실제 보존 대상이다.
    assert "누가" in not_owned


def test_다른_장은_같은_문장을_그대로_보존한다():
    """장 배치 검사다 — 5장에 실린 같은 문장은 손대지 않는다."""
    report = ComposedReport((ComposedSection(
        "current_challenges",
        (ComposedSentence(FX_RULE_TEXT, ("fx",), "확인"),),
    ),))
    fragments = (CollectedFragment("fx", "공시", FX_RULE_TEXT),)
    checked = verify_report(report, fragments, None, _approval([]))
    assert [s.text for s in checked.sections[0].sentences] == [FX_RULE_TEXT]
