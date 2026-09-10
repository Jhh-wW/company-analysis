"""보상 회계 설명에서 부정된 절차·명사와 실제 검토·승인·감독을 구분한다."""
from __future__ import annotations

import pytest

from src.features.composer.culture_constants import CULTURE_ACCOUNTING_POLICY_MISPLACED
from src.features.composer.culture_guard import culture_accounting_policy_problem

_ACCOUNTING_CLAUSE = (
    "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
    "재측정하여 보상원가에 반영하고 있으나"
)


# ══════════════════════════════════════════════════════════
# ① 부정된 승인/검토 — "~한 바 없다"·"~한 적이 없다"도 차단해야 한다
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "negated_clause",
    [
        "보상위원회가 이를 승인한 바 없다",
        "보상위원회가 이를 검토한 적이 없다",
        "이사회가 이를 감독한 적이 없다",
        "보상위원회가 이를 검토한 적 없다",  # "이" 없는 축약형도 함께
    ],
)
def test_negated_review_with_han_ba_eobda_form_does_not_rescue_the_sentence(negated_clause):
    text = f"{_ACCOUNTING_CLAUSE}, {negated_clause}."
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_existing_negation_forms_still_block_the_sentence():
    """기존에 이미 옳게 차단하던 부정 표현(받지 못했다)이 이번 수정으로
    깨지지 않는지 재확인한다."""
    text = f"{_ACCOUNTING_CLAUSE}, 보상위원회의 승인을 받지 못했다."
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ══════════════════════════════════════════════════════════
# ② 활용되지 않은 명사 부분일치 — 조사·명사 결합만으로는 면제하지 않는다
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "noun_only_sentence",
    [
        # ★ 쉼표로 이어 «같은 절»에 넣는다 — 마침표로 문장을 나누면 애초에
        #   회계 절과 명사가 다른 절이라 이 버그를 시험하지 못한다(C의 원본
        #   재현 문장과 동일한 절 결합 방식).
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하고 있고, 이는 이사회의 감독의무 범위에 속한다.",
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하고 있으며, 관련 내용은 외부 감독을 위한 "
        "참고자료로 보관한다.",
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하고, 관련 지출은 연간 승인한도 안에서 집행한다.",
    ],
)
def test_particle_or_compound_noun_alone_does_not_rescue_the_sentence(noun_only_sentence):
    assert culture_accounting_policy_problem(noun_only_sentence) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_existing_noun_only_negatives_still_block_the_sentence():
    """기존에 이미 옳게 차단하던 명사(감독당국/승인권한/검토자료)가 이번
    수정으로 깨지지 않는지 재확인한다."""
    texts = [
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영한다. 이는 감독당국의 검사 대상이다.",
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영하는 절차는 보상위원회의 승인권한의 유무에 따라 "
        "달라진다.",
        "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
        "재측정하여 보상원가에 반영한 검토자료를 사업보고서에 첨부한다.",
    ]
    for text in texts:
        assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ══════════════════════════════════════════════════════════
# ③ 정상 결속 절차는 그대로 보존한다 — "을/를 + 받다" 분리형 포함
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "bound_clause",
    [
        "보상위원회가 매 결산기마다 검토하고 승인한다",
        "보상위원회가 이를 승인함",
        "보상위원회의 승인을 받는다",
        "보상위원회의 검토를 받는다",
        "이사회의 감독을 받는다",
    ],
)
def test_actual_review_approval_oversight_procedure_is_preserved(bound_clause):
    text = f"{_ACCOUNTING_CLAUSE}, {bound_clause}."
    assert culture_accounting_policy_problem(text) == ""


def test_oversight_verb_alone_still_preserves_the_sentence():
    text = (
        "리스크관리위원회가 성과연동형 주식보상의 현금결제방식 회계처리와 부채의 공정가치 "
        "재측정에 따른 보상원가 반영을 정기적으로 감독한다."
    )
    assert culture_accounting_policy_problem(text) == ""


# ══════════════════════════════════════════════════════════
# ④ 기존 손실충당금/재무위험/도식 범위는 이 수정으로 바뀌지 않는다
# ══════════════════════════════════════════════════════════

def test_loss_allowance_negation_and_governance_are_unaffected():
    """손실충당금 블록은 별도 상수(공유되지 않는 CULTURE_ACCOUNTING_
    GOVERNANCE_NEGATION_RE/VERB_RE)를 그대로 쓰므로, 이번 compensation
    전용 수정이 그 판정을 바꾸면 안 된다."""
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


def test_flow_problem_still_does_not_know_the_compensation_bundle():
    from src.features.composer.culture_guard import culture_accounting_flow_problem

    cells = ("보상 정책", "현금결제방식 회계처리, 부채 공정가치 재측정, 보상원가 반영", "")
    sources = {"s1": f"{_ACCOUNTING_CLAUSE}, 경영진 보상을 관리하고 있다."}
    assert culture_accounting_flow_problem(cells, sources) == ""
