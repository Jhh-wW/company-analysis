from __future__ import annotations

import json

from src.features.news_intake import constants as c
from src.features.news_intake.classify import classify_and_read
from src.features.news_intake.mapping import map_articles_to_fragments
from src.features.news_intake.models import NewsCandidate, build_diagnostics
from src.features.news_intake.select import select_candidates
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_generation.models import exact_text_sha256


_ALL_SECTIONS = frozenset(REQUIRED_EVIDENCE_SECTION_IDS)


def _candidate(*, sections: tuple[str, ...], body: str, published_on: str = "2026-08-01"):
    candidate = NewsCandidate(
        id="news-001",
        title="인이지 대표 인터뷰",
        description="인이지 대표가 계획을 설명했다.",
        originallink="https://media.example/interview?utm_source=test#top",
        link="",
        published_on=published_on,
        publisher="media.example",
        priority=3,
        source_url="https://media.example/interview?utm_source=test#top",
    )
    response = json.dumps(
        {"items": [{"id": candidate.id, "sections": list(sections), "kind": "interview"}]}
    )
    return classify_and_read(
        (candidate,),
        classify=lambda _prompt: response,
        fetch_text=lambda _url: body,
        eligible_sections=_ALL_SECTIONS,
    ).articles[0]


def test_오장과_육장은_회사귀속_따옴표_문장만_허용한다() -> None:
    article = _candidate(
        sections=("current_challenges", "future_strategy", "portfolio"),
        body=(
            "기자는 공정의 어려움을 설명했다. "
            "인이지 관계자는 「현장의 문제를 먼저 확인한다」고 말했다. "
            "「곧 좋아질 것이다」라는 전망도 나왔다."
        ),
    )

    result = map_articles_to_fragments((article,), company_name="주식회사 인이지")

    challenge_fragments = [
        item for item in result.fragments if "current_challenges" in item.section_ids
    ]
    strategy_fragments = [
        item for item in result.fragments if "future_strategy" in item.section_ids
    ]
    assert len(challenge_fragments) == 1
    assert len(strategy_fragments) == 1
    assert "「" in challenge_fragments[0].text
    assert "관계자는" in challenge_fragments[0].text


def test_숫자와_전언문장은_어느_장에도_조각을_만들지_않는다() -> None:
    article = _candidate(
        sections=("identity", "current_challenges", "culture"),
        body=(
            "인이지는 매출이 20% 늘었다고 밝혔다. "
            "업계에 따르면 인이지는 새 사업을 준비한다. "
            "대표는 「고객부터 생각한다」고 말했다."
        ),
    )

    result = map_articles_to_fragments((article,), company_name="인이지")

    assert len(result.fragments) == 1
    assert result.fragments[0].text == "대표는 「고객부터 생각한다」고 말했다."
    assert result.exclusion_counts[c.EXCLUDED_NUMERIC_SENTENCE] == 1
    assert result.exclusion_counts[c.EXCLUDED_UNVERIFIED_SENTENCE] == 1


def test_팔장_조각은_기사날짜를_발언시점으로_보존한다() -> None:
    article = _candidate(
        sections=("culture",),
        body="대표는 「서로 질문하는 문화를 지킨다」고 말했다.",
    )

    fragment = map_articles_to_fragments((article,), company_name="인이지").fragments[0]

    assert fragment.published_on == "2026-08-01"
    assert fragment.statement_on == "2026-08-01"


def test_조각은_shared_해시와_URL_정규화와_부록메타를_보존한다() -> None:
    article = _candidate(
        sections=("portfolio",),
        body="인이지는 새 최적화 제품을 공개했다.",
    )

    fragment = map_articles_to_fragments((article,), company_name="인이지").fragments[0]

    assert fragment.text_sha256 == exact_text_sha256(fragment.text)
    assert fragment.document_id == "https://media.example/interview"
    assert fragment.url == fragment.document_id
    assert fragment.source_kind == "news"
    assert fragment.origin == "news_intake"
    assert fragment.publisher == "media.example"
    assert fragment.title == "인이지 대표 인터뷰"
    assert fragment.supported_claim_slots == collector_slots_for("portfolio")


def test_진단숫자는_검색부터_장별조각까지_정합하다() -> None:
    class Item:
        title = "인이지 제품 공개"
        originallink = "https://media.example/product"
        link = ""
        description = "인이지가 제품을 공개했다."
        pubDate = "2026-08-01"

    selection = select_candidates(
        "인이지",
        (),
        (Item(),),
        __import__("datetime").date(2026, 9, 6),
    )
    response = json.dumps(
        {"items": [{"id": "news-001", "sections": ["portfolio"], "kind": "event"}]}
    )
    classification = classify_and_read(
        selection.candidates,
        classify=lambda _prompt: response,
        fetch_text=lambda _url: "인이지는 최적화 제품을 공개했다.",
        eligible_sections=_ALL_SECTIONS,
    )
    mapping = map_articles_to_fragments(
        classification.articles,
        company_name="인이지",
    )

    diagnostics = build_diagnostics(selection, classification, mapping)

    assert (
        diagnostics.searched_count,
        diagnostics.selected_count,
        diagnostics.classified_count,
        diagnostics.fetched_count,
    ) == (1, 1, 1, 1)
    assert diagnostics.fragment_counts_by_section["portfolio"] == 1
    assert sum(diagnostics.fragment_counts_by_section.values()) == 1


def test_따옴표_안의_마침표는_문장을_중간에서_자르지_않는다() -> None:
    article = _candidate(
        sections=("future_strategy",),
        body="인이지 대표는 「현장을 본다. 그리고 적용한다」고 말했다.",
    )

    result = map_articles_to_fragments((article,), company_name="인이지")

    assert len(result.fragments) == 1
    assert result.fragments[0].text == article.text


def test_한_기사에서_나오는_조각은_두개까지다() -> None:
    """한 기사가 전체 조각 상한을 혼자 채우지 못하게 기사마다 따로 건다."""

    article = _candidate(
        sections=("portfolio",),
        body=(
            "인이지는 첫 번째 제품을 공개했다.\n"
            "인이지는 두 번째 제품을 공개했다.\n"
            "인이지는 세 번째 제품을 공개했다.\n"
            "인이지는 네 번째 제품을 공개했다."
        ),
    )

    result = map_articles_to_fragments((article,), company_name="인이지")

    assert len(result.fragments) == c.MAX_FRAGMENTS_PER_ARTICLE
    # 앞선 문장 순서를 그대로 두고 뒤에서 자른다.
    assert [fragment.text for fragment in result.fragments] == [
        "인이지는 첫 번째 제품을 공개했다.",
        "인이지는 두 번째 제품을 공개했다.",
    ]
    assert result.exclusion_counts[c.EXCLUDED_ARTICLE_FRAGMENT_LIMIT] == 2


def test_기사당_상한은_기사마다_따로_센다() -> None:
    """기사 두 건이면 각각 두 개까지라 합계는 네 개가 된다."""

    body = (
        "인이지는 첫 번째 제품을 공개했다.\n"
        "인이지는 두 번째 제품을 공개했다.\n"
        "인이지는 세 번째 제품을 공개했다."
    )
    first = _candidate(sections=("portfolio",), body=body)
    second = _candidate(
        sections=("portfolio",),
        body=body.replace("제품", "설비"),
        published_on="2026-07-01",
    )

    result = map_articles_to_fragments((first, second), company_name="인이지")

    assert len(result.fragments) == 2 * c.MAX_FRAGMENTS_PER_ARTICLE
    assert result.exclusion_counts[c.EXCLUDED_ARTICLE_FRAGMENT_LIMIT] == 2


def test_구장은_보조_칸이_없어_조각을_만들지_않는다() -> None:
    """9장은 회사가 밝힌 차별점만 싣는 장이라 기자 서술을 받지 않는다."""

    article = _candidate(
        sections=("competitive_position",),
        body="인이지는 경쟁사와 다른 접근을 택했다.",
    )

    result = map_articles_to_fragments((article,), company_name="인이지")

    assert result.fragments == ()
    assert result.exclusion_counts[c.EXCLUDED_NO_SUPPLEMENTARY_SLOT] == 1


def test_구장이_섞여도_다른_장은_살고_구장만_빠진다() -> None:
    article = _candidate(
        sections=("competitive_position", "portfolio"),
        body="인이지는 새 최적화 제품을 공개했다.",
    )

    fragment = map_articles_to_fragments((article,), company_name="인이지").fragments[0]

    assert fragment.section_ids == ("portfolio",)
    assert all(
        not slot.startswith("competitive_position:")
        for slot in fragment.supported_claim_slots
    )


def test_한글로_쓴_배수도_숫자문장으로_제외한다() -> None:
    article = _candidate(
        sections=("portfolio",),
        body="인이지는 처리 속도가 두 배라고 밝혔다.",
    )

    result = map_articles_to_fragments((article,), company_name="인이지")

    assert result.fragments == ()
    assert result.exclusion_counts[c.EXCLUDED_NUMERIC_SENTENCE] == 1
