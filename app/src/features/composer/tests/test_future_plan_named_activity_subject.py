"""이미 회사에 결속된 계획의 명시 주어를 같은 활동에서만 대조한다."""
import pytest

from src.features.composer.future_plan_constants import STATED_PLAN_SLOT
from src.features.composer.future_plan_guard import future_plan_prose_problem


def problem(candidate, source, *, evidence_source="1", quote=None):
    evidence = {"미래근거": [{
        "근거": evidence_source, "대상": "보호 정책", "활동": "공개",
        "원문": source if quote is None else quote, "양태": "계획",
    }]}
    return future_plan_prose_problem(candidate, {"1": source}, evidence,
                                     claim_slot=STATED_PLAN_SLOT)


@pytest.mark.parametrize("subject", ["가온기업", "누리", "가온기업이"])
def test_same_explicit_activity_subject_is_preserved(subject):
    # 마지막 사례는 회사 이름 자체가 '이'로 끝나는 경우다.
    source = f"{subject}는 보호 정책을 공개할 계획입니다."
    assert problem(f"{subject}는 보호 정책을 공개할 계획이다.", source) == ""


@pytest.mark.parametrize("candidate", [
    "나래기업은 보호 정책을 공개할 계획이다.",
    "회사는 가온기업을 위해 보호 정책을 공개할 계획이다.",
    "가온기업은 설명을 받았다. 나래기업은 보호 정책을 공개할 계획이다.",
    "가온기업은 설명을 받았으며 나래기업은 보호 정책을 공개할 계획이다.",
    "가온기업은 설명을 받았고, 보호 정책을 공개할 계획이다.",
])
def test_other_subject_or_other_clause_cannot_borrow_named_actor(candidate):
    source = "가온기업은 보호 정책을 공개할 계획입니다."
    assert problem(candidate, source) == "future_plan_subject_mismatch"


def test_same_named_third_party_does_not_become_own_company():
    source = "경쟁사는 보호 정책을 공개할 계획입니다."
    assert problem(source, source) == "future_plan_subject_mismatch"


def test_named_subject_does_not_relax_quote_id_or_current_state():
    source = "가온기업은 보호 정책을 공개할 계획입니다."
    assert problem(source, source, evidence_source="2") == "future_plan_source_not_cited"
    assert problem(source, source, quote="가온기업은 보호 정책을 공개할 방침입니다.") == "future_plan_quote_not_in_source"
    current = "가온기업은 보호 정책을 공개하고 있습니다."
    assert problem(source, current) == "future_plan_source_states_current"


def test_same_explicit_time_and_condition_remain_in_normal_plan():
    source = "가온기업은 보호자 동의를 얻은 경우에만 보호 정책을 내년 초부터 공개할 계획입니다."
    candidate = source.replace("계획입니다", "계획이다")
    assert problem(candidate, source) == ""


@pytest.mark.parametrize("bridge", [
    "내년 말부터", "내년 초부터 별도 인증을", "내년 초부터 승인받으면",
    "내년 초부터 안내서를 작성한 뒤", "내년 초부터 나래기업은",
])
def test_temporal_bridge_cannot_borrow_other_time_object_condition_or_actor(bridge):
    source = "가온기업은 보호 정책을 내년 초부터 공개할 계획입니다."
    candidate = f"가온기업은 보호 정책을 {bridge} 공개할 계획이다."
    assert problem(candidate, source) == "future_plan_target_not_bound_to_activity"
