"""등급 설명과 회사가 실제 받은 평가의 원문 범위를 구분한다."""
import hashlib
import pytest

from features.evidence_collection.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_quote_problem,
)

DEFINITIONS = (
    "평가회사 | 평가대상 유가증권 | 신용등급 | 등급의 정의 | 비고 ; "
    "공개평가기관 | 회사채 | AAA | 원리금 지급능력이 최상급임 | 등급 부호 안내 ; "
    "공개평가기관 | 회사채 | CCC | 채무불이행 위험이 큼 | 등급 부호 안내 ; "
    "공개평가기관 | 회사채 | D | 상환 불능 상태임 | 등급 부호 안내"
)


@pytest.mark.parametrize("slot", ["current_challenges:issue", "current_challenges:response"])
def test_definition_rows_have_no_company_problem_but_keep_raw(slot):
    before = hashlib.sha256(DEFINITIONS.encode()).hexdigest()
    scope = challenge_eligibility_scope(DEFINITIONS, slot)
    assert not scope.score_text.strip()
    assert scope.excluded_clauses == 4
    assert hashlib.sha256(DEFINITIONS.encode()).hexdigest() == before


def test_actual_rating_date_and_target_are_preserved():
    raw = ("평가일 | 회사명 | 신용등급 | 등급의 정의 ; "
           "2026.09.15 | 가람제조 | CCC | 고객 결제 지연으로 채무불이행 위험이 커졌다")
    assert challenge_eligibility_scope(raw, "current_challenges:issue").score_text == raw


def test_definition_table_does_not_remove_separate_actual_problem():
    actual = "회사는 등급 하락으로 거래처의 선결제 요구가 늘어 납품 지연이 발생하고 있다."
    raw = DEFINITIONS + " ; " + actual
    scope = challenge_eligibility_scope(raw, "current_challenges:issue")
    assert actual in scope.score_text
    assert "원리금 지급능력이 최상급임" not in scope.score_text


def test_definition_context_resets_for_different_table():
    actual = "회사명 | 평가일 | 위험 현황 ; 가람제조 | 2026.09.15 | 등급 하락으로 납품 지연 발생"
    raw = DEFINITIONS + " ; " + actual
    assert actual in challenge_eligibility_scope(raw, "current_challenges:issue").score_text


def test_cropped_definition_quote_does_not_borrow_actual_problem():
    source = DEFINITIONS + " ; 회사는 공급망 중단에 대응하고 있다."
    assert challenge_eligibility_quote_problem(
        "채무불이행 위험이 큼", source, "current_challenges:issue",
    )


@pytest.mark.parametrize("slot", ["current_challenges:issue", "current_challenges:response"])
def test_markdown_alignment_keeps_definition_context(slot):
    raw = "| 신용등급 | 등급의 정의 |\n| :--- | ---: |\n| C | 상환능력 부족과 부도 위험이 있다 |"
    assert not challenge_eligibility_scope(raw, slot).score_text.strip()
