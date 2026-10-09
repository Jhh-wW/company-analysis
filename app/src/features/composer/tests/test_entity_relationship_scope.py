"""행위 대상과 소유 관계를 구별하고 명시된 정상 관계를 보존한다."""

import pytest

from src.features.composer.grounding import grounding_problem
from src.features.composer.entity_relationship_scope import entity_relationship_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("prefix", ["종속회사", "당사의 종속회사 중", "자회사인", "계열사", "관계기업인"])
def test_event_subject_does_not_prove_added_ownership(prefix):
    source = "일자 | 대상법인 | 조치내용 ; 2025.03.02 | 다온설비㈜ | 개선 완료"
    candidate = f"{prefix} 다온설비㈜는 개선을 완료했다."
    assert grounding_problem(candidate, {"1": source}, {"결과": "참"}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("source", [
    "당사의 종속회사 다온설비㈜는 산업장비를 제조한다.",
    "다온설비 주식회사는 당사의 자회사이다.",
    "회사명 | 관계 ; 다른회사㈜ | 계열사 ; 다온설비㈜ | 종속기업",
])
def test_same_entity_membership_in_own_source_is_preserved(source):
    assert entity_relationship_problem("당사의 종속회사 중 다온설비㈜는 산업장비를 제조한다.", {"1": source}) == ""


@pytest.mark.parametrize("source", [
    "종속회사 라온설비㈜는 장비를 제조한다. 다온설비㈜는 개선을 완료했다.",
    "다온설비㈜는 계열사이다.",
    "회사명 | 관계 ; 다온설비㈜ | 계열사 ; 라온설비㈜ | 종속기업",
    "타사의 종속회사 다온설비㈜는 장비를 제조한다.",
    "다온설비㈜는 당사의 종속회사가 아니다.",
])
def test_other_entity_relation_type_owner_or_negative_cannot_support_membership(source):
    assert entity_relationship_problem("당사의 종속회사 다온설비㈜는 장비를 제조한다.", {"1": source}) == SCOPE_CONDITION_UNBOUND


def test_valid_relation_may_be_separately_cited_without_borrowing_uncited_sources():
    claim = "종속회사 다온설비㈜는 개선을 완료했다."
    action = "다온설비㈜는 개선을 완료했다."
    relation = "회사명 | 관계 ; 다온설비㈜ | 종속회사"
    assert entity_relationship_problem(claim, {"1": action})
    assert entity_relationship_problem(claim, {"1": action, "2": relation}) == ""


def test_no_added_relationship_and_generic_company_policy_are_unchanged():
    assert entity_relationship_problem("다온설비㈜는 개선을 완료했다.", {"1": "다온설비㈜ | 개선 완료"}) == ""
    assert entity_relationship_problem("자회사 관리는 정기적으로 이루어진다.", {"1": "계열사 관리 정책"}) == ""


def test_abbreviated_legal_form_is_still_checked():
    assert entity_relationship_problem("종속회사 다온설비는 개선을 완료했다.", {"1": "다온설비㈜ | 개선 완료"})


def test_source_mapping_is_not_modified():
    source = {"1": "다온설비㈜ | 개선 완료"}
    before = dict(source)
    entity_relationship_problem("종속회사 다온설비㈜는 개선을 완료했다.", source)
    assert source == before


@pytest.mark.parametrize("source", [
    "종속회사 다온설비㈜는 금융회사가 아니다.",
    "다온설비㈜는 당사의 자회사이며 금융회사가 아니다.",
    "종속회사 다온설비㈜는 장비를 공급하며, 라온설비㈜는 자회사가 아니다.",
])
def test_unrelated_negative_statement_does_not_erase_named_relationship(source):
    assert entity_relationship_problem("종속회사 다온설비㈜는 장비를 공급한다.", {"1": source}) == ""


def test_entity_name_suffix_cannot_borrow_another_company_relationship():
    assert entity_relationship_problem(
        "자회사 다온설비㈜는 장비를 공급한다.",
        {"1": "라다온설비㈜는 당사의 자회사이다. 다온설비㈜는 장비를 공급한다."},
    ) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("prefix", ["현재 ", "2026년 "])
def test_whitespace_before_a_named_entity_preserves_stated_relationship(prefix):
    assert entity_relationship_problem(
        "자회사 다온설비㈜는 장비를 공급한다.",
        {"1": prefix + "다온설비㈜는 당사의 자회사이다."},
    ) == ""


@pytest.mark.parametrize("source", [
    "타사의 종속회사 현황\n회사명 | 관계 ; 다온설비㈜ | 종속회사",
    "라온기업㈜의 종속회사 다온설비㈜는 장비를 공급한다.",
    "라온기업㈜의 종속회사 현황\n회사명 | 관계 ; 다온설비㈜ | 종속회사",
])
def test_named_or_generic_other_owner_does_not_prove_company_membership(source):
    assert entity_relationship_problem("당사의 종속회사 다온설비㈜는 장비를 공급한다.", {"1": source}) == SCOPE_CONDITION_UNBOUND


def test_explicit_self_owned_table_after_other_owner_heading_is_preserved():
    source = (
        "타사의 종속회사 현황\n회사명 | 관계 ; 라온설비㈜ | 종속회사 ; "
        "당사의 종속회사 현황\n회사명 | 관계 ; 다온설비㈜ | 종속회사"
    )
    assert entity_relationship_problem("종속회사 다온설비㈜는 장비를 공급한다.", {"1": source}) == ""


def test_funding_recipient_table_binds_entity_and_parent_relationship_on_same_row():
    source = "제공받은 기업 | 지배기업과의 관계 | 채권자 ; 다온설비㈜ | 종속기업 | 지역은행"
    assert entity_relationship_problem("종속기업 다온설비㈜는 자금을 제공받았다.", {"1": source}) == ""
    assert entity_relationship_problem("종속기업 라온설비㈜는 자금을 제공받았다.", {"1": source})


def test_explicit_other_owner_in_candidate_is_left_to_existing_semantic_review():
    source = "라온기업㈜는 종속기업인 Daon East와 Daon West의 영업을 중단했다."
    candidate = "라온기업㈜의 종속기업인 Daon West는 영업을 중단했다."
    assert entity_relationship_problem(candidate, {"1": source}) == ""
    # 같은 이름을 보고 회사의 소속으로 바꾸면 이 검사가 발동한다.
    assert entity_relationship_problem(candidate.replace("라온기업㈜의", "당사의"), {"1": source})
