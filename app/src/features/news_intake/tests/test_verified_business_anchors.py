"""검증된 연속 인용의 뉴스 앵커 생산·메타·선택 경계."""

from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest

from src.features.news_intake.business_anchors import extend_verified_news_business_anchors
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext, GroundedNewsExcerpt
from src.shared.report_evidence.news_business_activity import news_business_anchor_problem
from src.shared.report_evidence.news_business_activity_constants import NEWS_BUSINESS_MAX_ANCHORS
from src.features.news_intake.industry_context import extend_prompt


def verified_excerpt(text="가람회사는 계측 사업 부문을 인수해 시장 공략을 강화했다."):
    candidate = NewsCandidate(
        id="article-one", title="사업 인수 기사", description="", originallink="", link="",
        published_on="2026-10-08", publisher="media.example", priority=3,
        source_url="https://media.example/one", source_category="news_report",
    )
    return GroundedNewsExcerpt(candidate, text, "past_changes", "past_changes:completed_execution",
                               "reported_fact", "completed", "business", "사업 인수", "", 50, 50 + len(text))


def anchored_company(excerpt=None):
    excerpt = excerpt or verified_excerpt()
    company = NewsCompanyContext("가람회사", company_id="12345678")
    return extend_verified_news_business_anchors(
        company, [excerpt], document_hashes={excerpt.candidate.source_url: sha256(b"article").hexdigest()},
        as_of=date(2026, 10, 9),
    )


def test_news_anchor_works_without_formal_anchor():
    company = anchored_company()
    assert len(company.business_anchors) == 1
    anchor = company.business_anchors[0]
    assert anchor.source_kind == "news" and anchor.business_item == "계측 사업 부문"
    assert not news_business_anchor_problem(anchor, company_id="12345678", reference_date="2026-10-09")


def test_missing_verified_company_id_does_not_mint_anchor():
    excerpt = verified_excerpt()
    original = NewsCompanyContext("가람회사")
    assert extend_verified_news_business_anchors(
        original, [excerpt], document_hashes={excerpt.candidate.source_url: sha256(b"article").hexdigest()},
        as_of=date(2026, 10, 9),
    ) is original


@pytest.mark.parametrize("field,value", [
    ("source_url", "https://media.example/other"), ("publisher", "other.example"),
    ("title", "다른 제목"), ("location", "다른 위치"), ("published_on", "2026-10-07"),
    ("document_content_sha256", "a" * 64), ("company_id", "87654321"),
])
def test_anchor_metadata_change_is_closed(field, value):
    with pytest.raises(ValueError):
        replace(anchored_company().business_anchors[0], **{field: value})


def test_original_anchor_order_and_limit_are_preserved():
    original = anchored_company()
    first = original.business_anchors[0]
    filled = replace(original, business_anchors=tuple(
        replace(first, anchor_id=f"existing-{index}") for index in range(NEWS_BUSINESS_MAX_ANCHORS)
    ))
    assert extend_verified_news_business_anchors(
        filled, [verified_excerpt("가람회사는 조명 사업 부문을 인수했다.")],
        document_hashes={first.source_url: first.document_content_sha256}, as_of=date(2026, 10, 9),
    ).business_anchors == filled.business_anchors


def test_news_priority_prompt_preserves_news_kind_without_official_label():
    prompt = extend_prompt("기존 기사 입력", anchored_company(), priority=True)
    assert '"source_kind": "news"' in prompt
    assert "검증된 사업 앵커" in prompt
    assert "공식 앵커" not in prompt and "공식 anchor_id" not in prompt
    assert "주력·최초 개시·직접 생산" in prompt
