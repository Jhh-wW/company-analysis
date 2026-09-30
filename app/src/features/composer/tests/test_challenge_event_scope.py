"""시점 없는 과거 사건과 현재 사업 위험·정상 발생기간을 구별한다."""
import pytest
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_event_scope import challenge_event_scope_problem

RECORDS = (
    "제재조치일 | 조치대상자 | 처벌 또는 조치내용 | 이행 및 재발방지대책 ; "
    "2023.04.10 | 합성회사 | 안전관리 위반 과태료 | 납부 완료, 설비 개선 완료 ; "
    "2025.09.25 | 합성회사 | 환경 변경신고 위반 과태료 | 납부 완료, 변경신고 진행中"
)


@pytest.mark.parametrize("text", [
    "회사는 안전관리 위반으로 과태료를 받았다.",
    "회사는 2022년 안전관리 위반으로 과태료를 받았다.",
    "회사는 2023년 안전관리 위반 문제가 현재도 미해결이다.",
])
def test_dated_record_cannot_lose_its_period_or_borrow_other_period_progress(text):
    assert challenge_business_problem(text, {"1": RECORDS}) == "time_invalid"


@pytest.mark.parametrize("text", [
    "회사는 2023년 안전관리 위반으로 과태료를 받았고 설비 개선을 완료했다.",
    "회사는 2025년 환경 변경신고 위반으로 과태료를 받았고 변경신고를 진행 중이다.",
    "회사는 고객 수요 감소로 납품에 어려움을 겪고 있다.",
])
def test_actual_period_and_independent_current_business_are_preserved(text):
    assert not challenge_business_problem(text, {"1": RECORDS})


def test_flow_reads_period_from_whole_row_and_financial_guard_remains_issue_only():
    assert challenge_business_problem("안전관리 위반 과태료", {"1": RECORDS},
        cells=("2023년 안전관리 위반 과태료", "납부와 개선 완료")) == ""
    assert challenge_business_problem("안전관리 위반 과태료", {"1": RECORDS},
        cells=("안전관리 위반 과태료", "납부와 개선 완료")) == "time_invalid"
    assert challenge_business_problem("안전관리 위반 과태료", {"1": RECORDS},
        cells=("안전관리 위반 과태료", "사고 예방 교육")) == "time_invalid"
    assert not challenge_business_problem("납품 지연", {"1": "납품이 지연되어 대출을 받아 설비를 개선했다."},
        cells=("납품 지연", "대출을 받아 설비 개선"))


def test_undated_conditional_law_and_date_only_law_are_not_actual_event_records():
    for source in (
        "회사는 안전관리 위반으로 과태료를 받았다.",
        "2025년 법규를 위반할 경우 과징금을 부과받을 수 있습니다.",
        "법 시행일은 2025년이며 사고가 발생할 경우 보고할 예정입니다.",
    ):
        assert not challenge_event_scope_problem("회사는 규제 위반 제재 위험에 노출되어 있다.", {"1": source})


def test_dated_actual_accident_sentence_requires_period_without_removing_source():
    source = "2024년 공장에서 끼임사고가 발생하였고 회사는 안전설비 개선을 완료했다."
    assert challenge_event_scope_problem("공장에서 끼임사고가 발생하였다.", {"1": source}) == "time_invalid"
    assert not challenge_event_scope_problem("2024년 끼임사고가 발생하여 안전설비 개선을 완료했다.", {"1": source})
    assert source == "2024년 공장에서 끼임사고가 발생하였고 회사는 안전설비 개선을 완료했다."


def test_same_year_other_event_progress_cannot_be_borrowed_and_exact_day_remains():
    source = (
        "제재조치일 | 조치내용 | 이행 및 대책 ; "
        "2025.04.10 | 안전관리 위반 과태료 | 납부 완료, 개선 완료 ; "
        "2025.09.25 | 환경 변경신고 위반 과태료 | 납부 완료, 변경신고 진행中"
    )
    assert challenge_event_scope_problem("2025년 안전관리 위반 과태료 문제가 현재도 미해결이다.", {"1": source}) == "time_invalid"
    assert challenge_event_scope_problem("2025.04.10 안전관리 위반 과태료 문제가 현재도 미해결이다.", {"1": source}) == "time_invalid"
    assert not challenge_event_scope_problem("2025.09.25 환경 변경신고 위반 제재와 관련하여 변경신고를 진행 중이다.", {"1": source})
    assert not challenge_event_scope_problem("2025년 안전관리 위반 과태료를 받았고 개선을 완료했다.", {"1": source})


def test_general_regulatory_risk_does_not_assert_a_past_sanction_occurred_now():
    for text in (
        "회사는 안전관리 제재 위험에 노출되어 전담 조직을 운영한다.",
        "회사는 사고 예방을 위해 설비 점검을 실시한다.",
        "회사는 법규 위반에 따른 제재 가능성에 대응하고 있다.",
    ):
        assert not challenge_event_scope_problem(text, {"1": RECORDS})
    # 같은 문장에 실제 제재 발생도 쓰면 일반 위험어로 날짜 검사를 우회하지 못한다.
    assert challenge_event_scope_problem("회사는 안전관리 제재를 받았고 사고 위험을 줄이고 있다.", {"1": RECORDS}) == "time_invalid"


def test_explicit_current_completion_preserves_dated_history_without_hiding_unresolved_claim():
    assert not challenge_event_scope_problem(
        "회사는 2023년 과태료를 받았고 현재 납부와 설비 개선을 완료했다.", {"1": RECORDS})
    for text in (
        "회사는 2023년 과태료 문제가 현재 미해결이지만 납부와 설비 개선을 완료했다.",
        "회사는 2023년 과태료를 받았고 현재 납부와 설비 개선을 완료했으나 문제가 여전히 미해결이다.",
        "회사는 2023년 과태료 문제가 현재 진행 중이며 다른 설비 개선을 완료했다.",
    ):
        assert challenge_event_scope_problem(text, {"1": RECORDS}) == "time_invalid"


MIXED_ACTOR_RECORDS = (
    "제재조치일 | 조치대상자 | 처벌 또는 조치내용 | 이행 및 재발방지대책 ; "
    "2025.04.10 | 가온제조 | 안전관리 위반 과태료 | 납부 완료, 교육 완료 ; "
    "2025.09.25 | 해외설비법인 | 경쟁법 위반 과징금 | 납부 예정, 교육 완료"
)


def test_mixed_actor_event_cannot_omit_actor_or_borrow_another_row():
    assert challenge_event_scope_problem(
        "2025년 9월 경쟁법 위반 과징금을 받았고 교육을 완료했다.",
        {"1": MIXED_ACTOR_RECORDS}) == "scope_condition_unbound"
    assert not challenge_event_scope_problem(
        "해외설비법인은 2025년 9월 경쟁법 위반 과징금을 받았고 교육을 완료했다.",
        {"1": MIXED_ACTOR_RECORDS})
    blank_actor = MIXED_ACTOR_RECORDS.replace("가온제조", "-")
    assert challenge_event_scope_problem(
        "해외설비법인은 2025.04.10 과태료를 받았다.",
        {"1": blank_actor}) == "scope_condition_unbound"


def test_single_actor_existing_self_subject_remains_valid():
    assert not challenge_event_scope_problem(
        "회사는 2023년 과태료를 받았고 설비 개선을 완료했다.", {"1": RECORDS})


def test_sanction_row_cannot_support_actual_accident():
    assert challenge_event_scope_problem(
        "가온제조는 2025.04.10 추락사고가 발생하여 근로자가 사망했다.",
        {"1": MIXED_ACTOR_RECORDS}) == "scope_condition_unbound"


def test_pending_action_cannot_become_completed_but_other_completed_action_remains():
    assert challenge_event_scope_problem(
        "해외설비법인은 2025년 9월 과징금을 받았고 납부를 완료했다.",
        {"1": MIXED_ACTOR_RECORDS}) == "time_invalid"
    assert not challenge_event_scope_problem(
        "해외설비법인은 2025년 9월 과징금을 받았고 납부 예정이며 교육을 완료했다.",
        {"1": MIXED_ACTOR_RECORDS})


def test_multiple_event_periods_keep_each_action_state():
    assert not challenge_event_scope_problem(
        "가온제조는 2025.04.10 과태료를 받았고 납부를 완료했다. "
        "해외설비법인은 2025.09.25 과징금을 받았고 납부 예정이며 교육을 완료했다.",
        {"1": MIXED_ACTOR_RECORDS})


def test_possible_legal_penalty_is_not_company_response_and_prevention_is_preserved():
    law = "회사는 경쟁법 위반 시 시정명령과 과징금을 부과받을 수 있습니다."
    assert not challenge_event_scope_problem("회사는 경쟁법 제재 가능성에 노출되어 있다.", {"1": law})
    assert challenge_event_scope_problem("경쟁법 준수 의무", {"1": law},
        cells=("경쟁법 준수 의무", "시정명령, 과징금 등 제재 가능")) == "challenge_response_not_in_source"
    preventive = "회사는 과태료 부과 가능성을 줄이기 위해 안전교육을 실시했다."
    assert not challenge_event_scope_problem("안전관리 위험", {"1": preventive},
        cells=("안전관리 위험", preventive))


def test_contractor_relationship_is_preserved_and_does_not_prove_target_employee():
    source = (
        "재해발생회사 | 중대재해발생일자 | 발생장소 | 재해내용 | 조치 및 전망 ; "
        "협력설비(제조사 도급사) | 2025.03.03 | 제조사 공장 | 추락사고로 근로자 사망 | 안전설비 설치"
    )
    assert not challenge_event_scope_problem(
        "2025.03.03 제조사 공장에서 도급사 근로자의 추락사고가 발생했고 안전설비를 설치했다.",
        {"1": source})
    assert source.endswith("안전설비 설치")


def test_completed_action_cannot_be_borrowed_from_another_event_row():
    assert challenge_event_scope_problem(
        "가온제조는 2025.04.10 안전관리 위반 과태료 관련 변경신고를 완료했다.",
        {"1": MIXED_ACTOR_RECORDS.replace("납부 예정, 교육 완료", "납부 완료, 변경신고 완료")}
    ) == "scope_condition_unbound"


def test_conditional_penalty_cannot_prove_actual_payment_in_prose():
    assert challenge_event_scope_problem(
        "회사는 2025년 안전관리 위반 벌금을 납부했다.",
        {"1": "2025년 산업안전 규정을 위반할 경우 벌금을 부과할 수 있다."}
    ) == "scope_condition_unbound"


def test_prevention_exception_cannot_borrow_other_actor_action_even_on_same_day():
    source = (
        "제재조치일 | 조치대상자 | 처벌 또는 조치내용 | 대책 ; "
        "2025.04.10 | 가온제조 | 안전관리 위반 과태료 | 납부 완료 ; "
        "2025.04.10 | 해외설비법인 | 환경 위반 과태료 | 안전교육 실시 완료"
    )
    text = "가온제조는 2025.04.10 과태료 부과 가능성을 줄이기 위해 안전교육을 실시했다."
    assert challenge_event_scope_problem(text, {"1": source}) == "scope_condition_unbound"
    assert challenge_event_scope_problem(text, {"1": source},
        cells=("가온제조의 2025.04.10 안전관리 위반 과태료", text)) == "scope_condition_unbound"
