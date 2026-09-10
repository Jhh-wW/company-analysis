# -*- coding: utf-8 -*-
"""culture 장의 순수 주식기준보상 «회계 인식·측정» 오배치를 검증한다.

`culture_accounting_policy_problem`은 손실충당금/신용특성/연체일 묶음뿐 아니라,
같은 절 안에 현금결제(방식)·회계처리·부채의 공정가치 재측정·보상원가 네 어휘가
모두 있는 순수 주식기준보상 회계 절도 같은 사유(`CULTURE_ACCOUNTING_POLICY_
MISPLACED`)로 막는다. 각 시험은 «막아야 하는 것»과 «살려야 하는 것»을 짝으로
둔다 — 한쪽만 있으면 가드가 전부 막아도(또는 전부 통과시켜도) 녹색이 되기 때문이다.
"""
from __future__ import annotations

import json
import re

import pytest

from src.features.composer.culture_constants import CULTURE_ACCOUNTING_POLICY_MISPLACED
from src.features.composer.culture_guard import (
    culture_accounting_flow_problem,
    culture_accounting_policy_problem,
    culture_problem,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report

# 실제 WOORI report.json culture 문장 그대로.
TARGET_SENTENCE = (
    "우리은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며, 매 결산기마다 "
    "부채의 공정가치를 재측정하여 보상원가에 반영하는 방식으로 경영진 보상을 "
    "관리하고 있다."
)

# ══════════════════════════════════════════════════════════
# ① 대상 문장 — 막아야 하는 것
# ══════════════════════════════════════════════════════════

def test_existing_culture_problem_misses_the_target_sentence():
    """참고 경계: 조직문화 추론 어휘가 없어 culture_problem은 이 문장을 놓친다."""
    assert culture_problem(TARGET_SENTENCE, {}) == ""


def test_new_guard_blocks_the_target_compensation_accounting_sentence():
    assert culture_accounting_policy_problem(TARGET_SENTENCE) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_management_rhetoric_alone_does_not_exempt_the_accounting_clause():
    """회계 인식·측정 절 끝에 '…보상을 관리하고 있다'는 수사만 붙는 경우도
    차단한다 — '관리'는 검토/승인/감독 동사가 아니다."""
    assert culture_accounting_policy_problem(TARGET_SENTENCE) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ══════════════════════════════════════════════════════════
# ② 기존 손실충당금 판정은 그대로 — 이 후보로 깨지지 않는다
# ══════════════════════════════════════════════════════════

def test_existing_loss_allowance_detection_is_unaffected():
    real_failure_text = (
        "회사는 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
        "인식하는 간편법을 적용하며, 신용위험 특성과 연체일을 기준으로 구분하여 "
        "기대신용손실률을 산출한다."
    )
    assert culture_accounting_policy_problem(real_failure_text) == CULTURE_ACCOUNTING_POLICY_MISPLACED
    bound_procedure_text = (
        "재무팀이 손실충당금 산출 시 신용위험 특성과 연체일을 기준으로 검토하고 "
        "이사회가 승인한다."
    )
    assert culture_accounting_policy_problem(bound_procedure_text) == ""


# ══════════════════════════════════════════════════════════
# ③ 실제 직원 대상 성과평가·보상제도/지급기간 설명 — 살려야 하는 것
#    (WOORI citation 59 context.before 원문 그대로, 실측)
# ══════════════════════════════════════════════════════════

EMPLOYEE_INCENTIVE_PROCEDURE_TEXTS = [
    "은행이 임직원들에게 부여한 성과연동형 주식기준보상 약정은 다음과 같다.",
    "최초시점에 최대부여수량이 책정되고 당해연도를 포함하여 총 4개년의 장기성과지표의 "
    "평가결과에 따라 지급수량을 확정한 후 지급시점의 기준주가를 반영하여 최종 현금으로 "
    "보상하는 제도이다.",
    "장기성과지표는 상대적주주수익률, 보통주자본비율, 당기순이익, 자기자본이익률, "
    "판매관리비용률, 고정이하여신비율, 담당업무 성과 등이 있다.",
    "이 제도의 지급기준일은 부여일로부터 4년이 지난 시점이며 보상위원회가 정한 기준에 "
    "따라 결정된다.",
]


def test_employee_incentive_procedure_texts_without_the_accounting_bundle_are_preserved():
    """지급기간·평가지표·보상위원회 기준 서술은 회계 인식·측정 네 어휘가
    같은 절에 없으므로 차단되지 않는다."""
    for text in EMPLOYEE_INCENTIVE_PROCEDURE_TEXTS:
        assert culture_accounting_policy_problem(text) == ""


# ══════════════════════════════════════════════════════════
# ④ 절차 결속 — 같은 절의 실제 검토/승인/감독 동사는 보존한다
# ══════════════════════════════════════════════════════════

def test_governance_bound_compensation_accounting_sentence_is_preserved():
    text = (
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하는 방식을 보상위원회가 매 결산기마다 검토하고 "
        "승인한다."
    )
    assert culture_accounting_policy_problem(text) == ""


def test_oversight_verb_alone_also_preserves_the_sentence():
    text = (
        "리스크관리위원회가 성과연동형 주식보상의 현금결제방식 회계처리와 부채의 공정가치 "
        "재측정에 따른 보상원가 반영을 정기적으로 감독한다."
    )
    assert culture_accounting_policy_problem(text) == ""


# ══════════════════════════════════════════════════════════
# ⑤ 명사만 있는 반례 — 활용되지 않은 "감독당국"·"승인권한"은 면제가 아니다
# ══════════════════════════════════════════════════════════

def test_supervisory_authority_noun_alone_does_not_rescue_the_sentence():
    """'감독당국'처럼 활용되지 않은 명사만으로는 면제하지 않는다."""
    text = (
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하고 있다. 이는 감독당국의 검사 대상이다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_approval_authority_noun_alone_does_not_rescue_the_sentence():
    """'승인권한의 유무'처럼 활용되지 않은 명사만으로는 면제하지 않는다."""
    text = (
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하는 절차는 보상위원회의 승인권한의 유무에 따라 "
        "달라진다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_review_material_noun_alone_does_not_rescue_the_sentence():
    """'검토자료'처럼 활용되지 않은 명사만으로는 면제하지 않는다."""
    text = (
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영한 검토자료를 사업보고서에 첨부한다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ══════════════════════════════════════════════════════════
# ⑥ 부정 사례 — 관계없는 절이나 부정된 절차로 면제를 만들 수 없다
# ══════════════════════════════════════════════════════════

def test_unrelated_later_sentence_mentioning_committee_does_not_rescue_the_earlier_clause():
    text = (
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하고 있다. 보상위원회는 매년 정기적으로 보상제도 "
        "전반을 감독한다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_negated_review_and_approval_does_not_rescue_the_sentence():
    text = (
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하고 있으나 보상위원회의 검토를 받지 않는다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_committee_noun_without_a_governance_verb_does_not_rescue_the_sentence():
    text = (
        "보상위원회 산하 은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 "
        "부채의 공정가치를 재측정하여 보상원가에 반영하고 있다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ══════════════════════════════════════════════════════════
# ⑦ 어휘 부분집합 경계 — 과도 삭제 방지
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "missing_word_text",
    [
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하고 있다.",  # 보상원가 빠짐
        "은행은 성과연동형 주식보상 회계처리 시 부채의 공정가치를 재측정하여 보상원가에 "
        "반영한다.",  # 현금결제 빠짐
        "은행은 성과연동형 주식보상을 현금결제방식으로 지급하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영한다.",  # 회계처리 빠짐
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 공정가치를 참고자료로 "
        "공시하고 보상원가를 산정한다.",  # 부채공정가치재측정 빠짐
    ],
)
def test_four_word_bundle_is_required_single_words_do_not_trigger(missing_word_text):
    assert culture_accounting_policy_problem(missing_word_text) == ""


def test_scope_does_not_depend_on_company_or_citation_numbers():
    text = (
        "새빛은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며, 매 결산기마다 "
        "부채의 공정가치를 재측정하여 보상원가에 반영하는 방식으로 경영진 보상을 "
        "관리하고 있다고 밝혔다. [999][1000]"
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ══════════════════════════════════════════════════════════
# ⑧ 도식(flow/table)은 이 후보로 확장하지 않는다
# ══════════════════════════════════════════════════════════

def test_flow_problem_does_not_know_the_new_compensation_bundle():
    cells = ("보상 정책", "현금결제방식 회계처리, 부채 공정가치 재측정, 보상원가 반영", "")
    sources = {"s1": TARGET_SENTENCE}
    assert culture_accounting_flow_problem(cells, sources) == ""


# ══════════════════════════════════════════════════════════
# ⑨ 실제 verify_report 경계 — 산문/묶음 둘 다, 다른 장 보존까지
# ══════════════════════════════════════════════════════════

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


PRESERVED_PROCEDURE_TEXT = (
    "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
    "재측정하여 보상원가에 반영하는 방식을 보상위원회가 매 결산기마다 검토하고 "
    "승인한다."
)


@pytest.mark.parametrize("grouped", (False, True))
def test_verify_report_culture_keeps_governance_and_blocks_compensation_accounting(grouped):
    sentences = (
        ComposedSentence(TARGET_SENTENCE, ("comp",), "확인"),
        ComposedSentence(PRESERVED_PROCEDURE_TEXT, ("gov",), "확인"),
    )
    report = ComposedReport((ComposedSection("culture", sentences),))
    fragments = (
        CollectedFragment("comp", "공시", TARGET_SENTENCE),
        CollectedFragment("gov", "공시", PRESERVED_PROCEDURE_TEXT),
    )
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
        allowed_fragment_ids_by_section={"culture": frozenset(("comp", "gov"))} if grouped else None,
    )
    remaining = [s.text for s in checked.sections[0].sentences]
    assert TARGET_SENTENCE not in remaining
    assert PRESERVED_PROCEDURE_TEXT in remaining
    reason_codes = [d["reason_code"] for d in diagnostics if d.get("reason_code")]
    assert CULTURE_ACCOUNTING_POLICY_MISPLACED in reason_codes


def test_verify_report_other_section_preserves_the_same_text():
    """current_challenges 같은 다른 장은 같은 문장이라도 그대로 보존한다."""
    sentences = (ComposedSentence(TARGET_SENTENCE, ("comp",), "확인"),)
    report = ComposedReport((ComposedSection("current_challenges", sentences),))
    fragments = (CollectedFragment("comp", "공시", TARGET_SENTENCE),)
    calls = []
    checked = verify_report(report, fragments, None, _approval(calls))
    assert [s.text for s in checked.sections[0].sentences] == [TARGET_SENTENCE]
