"""자기 앵커 안의 업종 정의를 탐색 순위 보조로만 사용한다."""

from dataclasses import replace

import pytest

from src.features.news_intake.search_snapshot import (
    _industry_candidates, _industry_role_problem, _industry_self_roles,
)
from src.features.news_intake.tests.test_industry_query_opportunity_balance import candidate, company
from src.shared.report_generation.models import exact_text_sha256


@pytest.mark.parametrize("text,roles", [
    ("당사는 시험소입니다.", ("시험소",)),
    ("당사는 계측 전문 시험소로 다양한 검증을 제공하고 있다.", ("시험소",)),
    ("당사는 은행입니다.", ("은행",)),
    ("당사는 종합 연구소이다.", ("연구소",)),
])
def test_explicit_current_self_role_keeps_original_word(text, roles):
    assert _industry_self_roles(text) == roles
    assert all(role in text for role in roles)


@pytest.mark.parametrize("text", [
    "당사는 전문 기관으로부터 자금을 받았다.",
    "당사는 전문 서비스로 고객에게 검사 결과를 제공했다.",
    "당사는 전문 검사서비스로 고객에게 결과를 제공했다.",
    "당사는 서비스로 고객 업무를 지원한다.",
    "당사는 자회사의 전문 시험소로 운영한다.",
    "당사는 다른 회사의 전문 시험소로 이전한다.",
    "당사는 새봄기관의 계측 전문 시험소로 이전한다.",
    "당사는 자회사가 계측 전문 시험소로 운영된다고 설명한다.",
    "거래처는 계측 전문 시험소로 활동한다.",
    "당사는 과거 계측 전문 시험소로 운영했다.",
    "당사는 계측 전문 회사로 활동한다.",
    "당사는 계측 전문 시험소로서 검증 업무를 한다.",
    "당사는 계측 전문 시험소로써 검증 업무를 한다.",
    "당사는 전문 검사서비스입니다.",
    "당사는 계측 전문 시험소로 전환할 계획이다.",
    "당사는 계측 전문 시험소로 활동했으나 현재 그 사업을 중단했다.",
    "당사는 계측 전문 시험소로 운영하지 않는다.",
    "당사는 계측 전문 시험소로 활동하지 않으며 검사 비용을 계산한다.",
])
def test_instrument_recipient_other_owner_and_generic_role_use_fallback(text):
    assert _industry_self_roles(text) == ()


def role_company():
    original = company()
    text = "당사는 계측 전문 시험소로 다양한 검증을 제공하고 있다. 당사의 사업은 검사 사업이다."
    anchor = replace(original.business_anchors[1], business_item="검사 사업", exact_text=text,
                     text_sha256=exact_text_sha256(text), location=f"chars:0-{len(text)}")
    return replace(original, business_anchors=(original.business_anchors[0], anchor))


def test_official_self_role_and_same_clause_problem_precede_generic_spending():
    linked = candidate("provider", "industry_global:advertising", "신기술과 시장",
                       "시험소의 검사비 수익 감소와 경영 위기가 나타났다", date="2026-08-01")
    consumer = candidate("consumer", "industry_global:advertising", "식품업 위기",
                         "검사비 비용 부담이 커졌다", date="2026-09-01")
    assert _industry_role_problem(linked, role_company())
    assert not _industry_role_problem(consumer, role_company())
    assert _industry_candidates([consumer, linked], company=role_company())[0] == linked


def test_other_anchor_identity_context_and_separate_clause_do_not_lend_roles():
    metadata = "시험소 검사비 수익 감소 위기"
    other = candidate("other", "industry_global:content", "시험소 뉴스콘텐츠 공급 차질 위기")
    assert not _industry_role_problem(other, role_company())
    separate = candidate("separate", "industry_global:advertising", "시험소 소식",
                         "검사비 수익 감소 위기")
    assert not _industry_role_problem(separate, role_company())
    no_role = company()
    changed_identity = replace(no_role, identity_context="당사는 계측 전문 시험소로 운영한다.")
    assert not _industry_role_problem(candidate("outside", "industry_global:advertising", "시험소 광고료 수익 감소 위기"), changed_identity)


def test_source_publisher_is_not_a_business_role():
    item = replace(candidate("publisher", "industry_global:advertising", "검사비 수익 위기"),
                   publisher="시험소", source_url="https://media.example/시험소")
    assert not _industry_role_problem(item, role_company())
