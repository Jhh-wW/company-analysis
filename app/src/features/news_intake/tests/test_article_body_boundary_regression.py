"""AI 보조 구획을 배제하면서 기자 본문·기간·금액을 보존하는 독립 회귀.

실제 MK329의 article_body/stock_story 형제를 축소했다. 기본 주추출기는
pipeline.real._news_primary_body와 동일한 extract_usable_ranges를 사용한다.
외부 원문 파일·DB·네트워크 없이 이 파일의 합성 입력만 실행한다.
"""

from __future__ import annotations

import json

import pytest

from src.features.homepage.wide_extract import extract_usable_ranges
from src.features.news_intake.fetch import (
    extract_article_published_on,
    extract_article_text,
)


BANK_FACT = "2026년 6월 말 우리은행의 주택담보대출 연체 잔액은 3419억6000만원이었다."
FOLLOWING_FACT = "우리은행은 취약 차주의 상환 능력과 담보 자산의 건전성을 점검한다고 밝혔다."
AI_COMMENTARY = "우리금융지주와 우리은행의 사업 관계는 은행 부문의 건전성 관리와 연결됩니다."
OTHER_ARTICLE = "다른 기업은 물류 자동화 설비를 도입하고 해외 생산거점을 확대한다고 밝혔다."
AI_COMPANY_NEWS = "Perplexity는 기업용 AI 검색 서비스를 출시했고 우리은행은 AI 활용 사례를 발표했다."
PUBLISHED_META = '<meta property="article:published_time" content="2026-09-08T11:49:26+09:00">'


def _primary(raw_html: str) -> str:
    ranges, _title = extract_usable_ranges(raw_html)
    return "\n".join(ranges).strip()


def _page(body: str, *, head: str = "") -> str:
    return f"<html><head>{PUBLISHED_META}{head}</head><body>{body}</body></html>"


def _widget(body: str = AI_COMMENTARY, *, tag: str = "section") -> str:
    return (
        f'<{tag} id="stock_story" class="stock_story">'
        '<h2>기사 속 종목 이야기</h2><span>Powered by perplexity</span>'
        f'<div class="card-desc-wrap"><p class="stock-desc">{body}</p></div>'
        f"</{tag}>"
    )


def _json_ld(body: str, *, published: str = "2026-09-08") -> str:
    return '<script type="application/ld+json">' + json.dumps(
        {"@context": "https://schema.org", "@type": "NewsArticle",
         "articleBody": body, "datePublished": published}, ensure_ascii=False,
    ) + "</script>"


def _assert_bank_fact(text: str) -> None:
    assert "2026년 6월 말" in text
    assert "우리은행" in text and "주택담보대출 연체 잔액" in text
    assert "3419억6000만원" in text
    assert AI_COMMENTARY not in text


def test_mk_sibling_sections_preserve_bank_fact_and_publication_date() -> None:
    raw = _page(
        '<div id="print_contents" class="article_view">'
        f'<div id="article_body" class="article_body" itemprop="articleBody"><p>{BANK_FACT}</p></div>'
        f'<div id="box-container">{_widget()}</div></div>'
    )
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    _assert_bank_fact(text)
    assert extract_article_published_on(raw) == "2026-09-08"


@pytest.mark.parametrize("container", ["article", "div"])
@pytest.mark.parametrize("voids", ["<img src='photo.png'><br><input hidden>", "<img src='photo.png'/><br/><input hidden/>"])
def test_nested_widget_void_elements_preserve_following_body(container: str, voids: str) -> None:
    widget = _widget(f"{voids}<div><section>{AI_COMMENTARY}</section></div>")
    raw = _page(f"<{container}><p>{BANK_FACT}</p>{widget}<p>{FOLLOWING_FACT}</p></{container}>")
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    _assert_bank_fact(text)
    assert FOLLOWING_FACT in text


@pytest.mark.parametrize("empty_widget", ['<section id="stock_story"/>', '<img id="stock_story" src="widget.png">'])
def test_self_closing_and_void_widgets_preserve_following_body(empty_widget: str) -> None:
    raw = _page(f"<article>{empty_widget}<p>{BANK_FACT}</p><p>{FOLLOWING_FACT}</p></article>")
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    _assert_bank_fact(text)
    assert FOLLOWING_FACT in text


def test_nested_article_preserves_surrounding_journalist_body() -> None:
    raw = _page(f"<article><p>{BANK_FACT}</p><article>{_widget()}</article><p>{FOLLOWING_FACT}</p></article>")
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    _assert_bank_fact(text)
    assert FOLLOWING_FACT in text


@pytest.mark.parametrize("wrapper", ["article", "div"])
def test_real_ai_and_perplexity_news_is_not_a_keyword_false_positive(wrapper: str) -> None:
    raw = _page(f'<{wrapper} class="ai-news"><p>{AI_COMPANY_NEWS}</p><p>{BANK_FACT}</p></{wrapper}>')
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    assert AI_COMPANY_NEWS in text
    _assert_bank_fact(text)


def test_journalist_quotation_of_widget_branding_remains_article_content() -> None:
    sentence = "기자는 서비스 화면의 Powered by Perplexity 표기를 확인했고 도입 배경을 취재했다."
    raw = _page(f"<article><p>{AI_COMPANY_NEWS}</p><p>{sentence}</p><p>{BANK_FACT}</p></article>")
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    assert AI_COMPANY_NEWS in text and sentence in text
    _assert_bank_fact(text)


def test_official_json_ld_preserves_ai_news_and_publication_date() -> None:
    body = f"{AI_COMPANY_NEWS} {BANK_FACT}"
    raw = _page(_widget(), head=_json_ld(body))
    text, stage = extract_article_text(raw, primary_extract=_primary)
    assert text == body and stage == "json_ld_article_body"
    assert extract_article_published_on(raw) == "2026-09-08"


def test_json_ld_and_dom_do_not_merge_article_with_ai_appendix() -> None:
    raw = _page(f"<article><p>{BANK_FACT}</p>{_widget()}</article>", head=_json_ld(BANK_FACT))
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    _assert_bank_fact(text)
    assert text.count(BANK_FACT) == 1


def test_json_ld_inside_widget_does_not_replace_journalist_body() -> None:
    raw = _page(f"<article><p>{BANK_FACT}</p></article>" + _widget(_json_ld(AI_COMMENTARY) + AI_COMMENTARY))
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    _assert_bank_fact(text)


def test_two_independent_articles_remain_ambiguous() -> None:
    raw = _page(f"<article><p>{BANK_FACT}</p></article><article><p>{OTHER_ARTICLE}</p></article>{_widget()}")
    text, stage = extract_article_text(raw, primary_extract=_primary)
    assert (text, stage) == ("", "")


def test_multiple_articles_preserve_unambiguous_json_ld_body() -> None:
    raw = _page(f"<article>{BANK_FACT}</article><article>{OTHER_ARTICLE}</article>{_widget()}", head=_json_ld(BANK_FACT))
    text, stage = extract_article_text(raw, primary_extract=_primary)
    assert text == BANK_FACT and stage == "json_ld_article_body"


def test_widget_article_does_not_make_journalist_article_ambiguous() -> None:
    raw = _page(f"<article>{BANK_FACT}</article>" + _widget(f"<article>{AI_COMMENTARY}</article>"))
    text, _stage = extract_article_text(raw, primary_extract=_primary)
    _assert_bank_fact(text)


def test_primary_exception_keeps_valid_metadata_fallback_and_date() -> None:
    def broken_primary(_raw: str) -> str:
        raise ValueError("주추출기 실패 재현")

    raw = _page(f"<div><p>{BANK_FACT}</p>{_widget()}</div>", head=f'<meta name="description" content="{BANK_FACT}">')
    text, _stage = extract_article_text(raw, primary_extract=broken_primary)
    _assert_bank_fact(text)
    assert extract_article_published_on(raw) == "2026-09-08"


def test_metadata_does_not_reintroduce_removed_widget_commentary() -> None:
    raw = _page(_widget(), head=f'<meta name="description" content="{AI_COMMENTARY}">')
    text, _stage = extract_article_text(raw, primary_extract=lambda _raw: "")
    assert not text


def test_stale_primary_cannot_reintroduce_removed_widget_commentary() -> None:
    raw = _page(f"<div><p>{BANK_FACT}</p>{_widget()}</div>", head=f'<meta name="description" content="{BANK_FACT}">')
    seen = []

    def stale_primary(value: str) -> str:
        seen.append(value)
        return AI_COMMENTARY

    text, _stage = extract_article_text(raw, primary_extract=stale_primary)
    assert seen and AI_COMMENTARY not in seen[0]
    assert AI_COMMENTARY not in text
    _assert_bank_fact(text)


def test_pipeline_transport_preserves_bank_fact_and_date_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.robotparser import RobotFileParser

    from src.features.homepage.wide_fetch import WideRawResponse, WideRobotsPolicy
    from src.features.pipeline import real

    url = "https://www.media.example/news/boundary"
    raw = _page(f'<div id="article_body" itemprop="articleBody"><p>{BANK_FACT}</p></div>{_widget()}')
    parser = RobotFileParser()
    parser.parse([])
    policy = WideRobotsPolicy(host="www.media.example", parser=parser,
                              outcome="proceed_parsed", reason_code="robots_ok")
    requested = []

    def transport(candidate: str, url_allowed=None):
        requested.append(candidate)
        assert candidate == url
        assert url_allowed is None or url_allowed(candidate)
        return WideRawResponse(status=200, text=raw, effective_url=url, content_type="text/html")

    monkeypatch.setattr(real, "load_robots_policy", lambda **kwargs: policy)
    monkeypatch.setattr(real, "default_wide_transport", transport)
    result = real._fetch_news_article_text(url)

    assert requested == [url]
    assert result.succeeded and result.effective_url == url
    assert result.published_on == "2026-09-08"
    _assert_bank_fact(result.text)
