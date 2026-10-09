"""혼합 원문과 실제 진행 사업 문제를 과거 완료·공시준수와 구별한다."""
from pathlib import Path

import pytest

from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_problem, challenge_eligibility_quote_problem,
    challenge_eligibility_scope, challenge_incident_row_problem,
)

HEADER = "제재조치일 | 처벌 또는 조치대상자 | 처벌 또는 조치내용 | 사유 및 근거법령 | 이행 및 재발방지대책"
COMPLETED = "2024.04.10 | 가온기업 | 과태료 100만원 | 안전관리 위반 | 납부 완료, 설비 점검 완료"


def test_completed_sanction_history_only_loses_current_slots_and_raw_stays_exact():
    source = HEADER + "; " + COMPLETED
    scope = challenge_eligibility_scope(source)
    assert not scope.score_text
    assert scope.excluded_spans == ((len(HEADER) + 1, len(source), "challenge_current_problem_unbound"),)
    quote = "설비 점검 완료"
    assert challenge_eligibility_quote_problem(quote, source, "current_challenges:response")
    assert not challenge_eligibility_quote_problem(quote, source, "past_changes:completed_execution")
    assert source == HEADER + "; " + COMPLETED


@pytest.mark.parametrize("text", [
    "2026.10.04 공장 화재로 생산이 중단됐다.",
    "2026년 서비스 장애로 고객 결제가 지연됐다.",
    "은행의 차주 연체율이 급증하여 신용위험에 대응했다.",
    "고객에게 대출 관리 서비스를 제공한다. 대출 고객의 연체율이 상승했다.",
    "공시 플랫폼 서비스 장애로 이용자의 공시 제출이 지연되고 있다.",
    "제품 결함 때문에 리콜을 진행 중이다.",
])
def test_actual_business_problem_without_fixed_current_word_is_kept(text):
    assert not challenge_eligibility_problem(text)
    assert challenge_eligibility_scope(text).score_text == text


@pytest.mark.parametrize("response", [
    "납부 완료, 안전설비 설치 진행 중",
    "납부 완료, 복구 중",
    "납부 완료, 리콜 진행 중",
    "납부 완료, 고객 납품 지연이 지속되고 있다",
])
def test_same_row_current_impact_or_operating_response_is_preserved(response):
    row = COMPLETED.rsplit("|", 1)[0] + "| " + response
    assert not challenge_incident_row_problem(row)


@pytest.mark.parametrize("response", ["납부 완료, 교육 진행 중", "납부 완료, 공시교육 실시"])
def test_payment_or_training_progress_does_not_prove_business_problem(response):
    row = COMPLETED.rsplit("|", 1)[0] + "| " + response
    assert challenge_incident_row_problem(row)


def test_another_row_current_impact_does_not_restore_completed_row_quote():
    current = COMPLETED.replace("2024.04.10", "2024.09.12").replace("설비 점검 완료", "고객 납품 지연이 지속되고 있다")
    source = HEADER + "; " + COMPLETED + "; " + current
    scoped = challenge_eligibility_scope(source)
    assert COMPLETED not in scoped.score_text and current in scoped.score_text
    assert challenge_eligibility_quote_problem("설비 점검 완료", source, "current_challenges:response")
    assert not challenge_eligibility_quote_problem("고객 납품 지연이 지속되고 있다", source, "current_challenges:issue")


@pytest.mark.parametrize("text", [
    "기업은 지연공시로 공시위반 제재금을 납부하고 공시교육을 실시했다.",
    "상계신고 누락으로 과태료를 납부 완료했다.",
    "우수기업으로 선정됐다.",
])
def test_pure_administration_and_positive_award_do_not_fill_current_slots(text):
    assert challenge_eligibility_problem(text)


def test_positive_sentence_does_not_remove_unlisted_real_problem_in_mixed_raw():
    positive = "우수기업으로 선정됐다"
    problem = "원자재 가격 급등으로 생산원가 부담이 커졌다"
    source = positive + "; " + problem
    scope = challenge_eligibility_scope(source)
    assert positive not in scope.score_text and problem in scope.score_text
    assert not challenge_eligibility_quote_problem(problem, source, "current_challenges:issue")
    assert challenge_eligibility_quote_problem(positive, source, "current_challenges:response")
    assert source == positive + "; " + problem


@pytest.mark.parametrize("connector", ["으며", "지만", "고"])
def test_positive_predicate_keeps_unknown_problem_in_the_following_clause(connector):
    positive = "우수기업으로 선정됐"
    problem = "원자재 가격 급등으로 생산원가 부담이 커졌다"
    source = positive + connector + " " + problem
    scope = challenge_eligibility_scope(source)
    assert positive not in scope.score_text and problem in scope.score_text
    assert not challenge_eligibility_problem(source)
    assert challenge_eligibility_quote_problem(positive, source, "current_challenges:response")
    assert not challenge_eligibility_quote_problem(problem, source, "current_challenges:issue")


@pytest.mark.parametrize("problem", [
    "원자재 가격 급등에 따른 생산원가 부담",
    "핵심 시장의 수요 둔화",
    "고객 이탈에 따른 수익성 악화",
])
def test_one_clause_positive_and_problem_is_left_for_existing_semantic_review(problem):
    source = "회사는 우수기업 선정과 " + problem + "을 함께 보고했다."
    assert challenge_eligibility_scope(source).score_text == source
    assert not challenge_eligibility_problem(source)
    assert not challenge_eligibility_quote_problem(source, source, "current_challenges:issue")
    assert not challenge_eligibility_quote_problem(source, source, "current_challenges:response")


def test_engine_and_shared_helpers_have_same_contract():
    root = Path(__file__).resolve().parents[5]
    engine = root / "analysis_engine/src/features/evidence_collection"
    shared = root / "app/src/shared/report_evidence"
    assert (engine / "challenge_eligibility_constants.py").read_text(encoding="utf-8") == (shared / "challenge_eligibility_constants.py").read_text(encoding="utf-8")
    engine_code = (engine / "challenge_eligibility.py").read_text(encoding="utf-8")
    shared_code = (shared / "challenge_eligibility.py").read_text(encoding="utf-8")
    assert engine_code.replace("from features.evidence_collection import", "from src.shared.report_evidence import") == shared_code


@pytest.mark.parametrize("source", [
    "시장이자율 변동으로 회사의 투자 및 재무활동에서 발생하는 이자수익과 이자비용이 변동될 위험에 노출되어 있습니다.",
    "예측정보는 미래 사업환경의 가정에 기초합니다. 회사는 예측정보를 수정하는 정정보고서를 공시할 의무는 없음을 알려드립니다.",
    "매년 영업권 손상검사를 수행한다. 현금창출단위의 회수가능액은 사용가치에 근거한다.",
    "회계정책과 공시의 변경에 따라 기업회계기준서를 적용했다. 해당 개정이 재무제표에 미치는 중요한 영향은 없다.",
    "재무제표 작성에는 미래 가정 및 추정이 요구된다. 회계정책을 적용할 때 회계추정과 가정을 평가한다.",
    "회사의 재무부문은 금융위험을 감시하고 관리한다. 이러한 위험관리 정책은 전기말 이후 변동이 없다.",
])
def test_standard_policy_context_is_not_a_current_issue_and_mixed_financial_business_stays(source):
    assert challenge_eligibility_problem(source) == "challenge_business_relation_unbound"
    current = "보험 고객의 실제 보상 지급 지연이 지속되고 있어 고객 서비스를 개선하고 있다."
    mixed = source + " " + current
    assert current in challenge_eligibility_scope(mixed).score_text
    assert not challenge_eligibility_quote_problem(current, mixed, "current_challenges:issue")
    assert challenge_eligibility_quote_problem(source, mixed, "current_challenges:issue")
    assert not challenge_eligibility_quote_problem(source, mixed, "past_changes:performance")


def test_mixed_table_keeps_header_and_exact_current_row_separators():
    current = COMPLETED.replace("2024.04.10", "2026.10.04").replace("안전관리 위반", "화재로 생산이 중단됐다.").replace("설비 점검 완료", "복구 진행 중")
    source = HEADER + "; " + COMPLETED + "; " + current
    scoped = challenge_eligibility_scope(source)
    assert HEADER in scoped.score_text and current in scoped.score_text
    assert COMPLETED not in scoped.score_text
    assert not challenge_eligibility_problem(source)


def test_event_header_without_data_is_not_current_support():
    assert challenge_eligibility_problem(HEADER)
