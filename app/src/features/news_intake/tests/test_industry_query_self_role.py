"""자기 앵커의 유일 현재 역할만 검색 표현으로 운반한다."""

from dataclasses import replace

import pytest

from src.features.news_intake.search_snapshot import industry_anchor_search_expression, industry_query_derivation, search_plan
from src.features.news_intake.tests.test_industry_context import AS_OF
from src.features.news_intake.tests.test_industry_query_opportunity_balance import company
from src.shared.report_generation.models import exact_text_sha256


def anchor(text, business="검사 사업"):
    original = company().business_anchors[0]
    text += f" 당사의 현재 사업은 {business}이다."
    return replace(original, exact_text=text, text_sha256=exact_text_sha256(text),
                   location=f"chars:0-{len(text)}", business_item=business)


def test_single_self_role_qualifies_only_its_own_anchor_and_derivation():
    owned = anchor("당사는 계측 전문 시험소로 다양한 검증을 제공하고 있다.")
    other = company().business_anchors[1]
    context = replace(company(), business_anchors=(owned, other))
    rows = [row for row in search_plan(context, AS_OF) if row[2].startswith("industry_")]
    assert len(rows) == 4
    assert all("시험소" in row[0] for row in rows[:2])
    assert all("시험소" not in row[0] for row in rows[2:])
    record = industry_query_derivation(context, rows[0][0], rows[0][2])
    assert record["original_business_item"] == owned.business_item
    assert record["role_qualifier"] == "시험소"
    assert record["anchor_location"] == owned.location
    assert record["anchor_text_sha256"] == owned.text_sha256
    assert record["qualified_search_expression"] == "검사 사업 시험소"
    assert search_plan(context, AS_OF)[:2] == search_plan(company(), AS_OF)[:2]


@pytest.mark.parametrize("text", [
    "검사 사업을 제공한다.",
    "당사는 시험소입니다. 당사는 연구소입니다.",
    "자회사는 계측 전문 시험소로 운영한다.",
    "당사는 자회사의 계측 전문 시험소로 운영한다.",
    "당사는 계측 전문 시험소로 전환할 계획이다.",
    "당사는 과거 계측 전문 시험소로 운영했다.",
    "당사는 계측 전문 시험소로 활동했으나 현재 중단했다.",
    "당사는 전문 기관으로부터 자금을 받았다.",
    "당사는 전문 서비스로 고객에게 검사 결과를 제공했다.",
])
def test_absent_multiple_other_owner_and_noncurrent_roles_keep_original_query(text):
    assert industry_anchor_search_expression(anchor(text)) == ("검사 사업", "")


def test_role_already_in_business_item_is_not_repeated():
    owned = anchor("당사는 시험소입니다.", "시험소 검사 사업")
    assert industry_anchor_search_expression(owned) == ("시험소 검사 사업", "")


def test_identity_context_cannot_supply_a_missing_role():
    owned = anchor("당사는 검사 사업을 제공한다.")
    context = replace(company(), business_anchors=(owned,), identity_context="당사는 시험소입니다.")
    rows = [row for row in search_plan(context, AS_OF) if row[2].startswith("industry_")]
    assert len(rows) == 4 and all("시험소" not in row[0] for row in rows)
