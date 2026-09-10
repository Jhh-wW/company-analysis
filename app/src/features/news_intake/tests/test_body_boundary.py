"""실제 보조 위젯 구조와 모든 본문 폴백의 독립 경계를 검증한다."""
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import json

import pytest

from src.features.news_intake.fetch import extract_article_text


BODY = "가나다은행은 2026년 9월 기업대출 지원 규모를 120억원으로 확대했다고 밝혔다."
FOLLOWING = "가나다은행은 지원 대상 기업에 상담 서비스도 제공한다고 밝혔다."
AI_NEWS = "가나다전자는 Perplexity와 AI 검색 사업을 추진하며 2026년 연구비 120억원을 투자했다."
COMMENTARY = "우리금융지주는 우리은행을 핵심 계열사로 두고 있어 주택담보대출의 건전성 변화가 은행 부문 관리와 연결됩니다."
FIXTURE = Path(__file__).with_name("fixtures") / "auxiliary_stock_story.html"


class VisibleText(HTMLParser):
    """입력 HTML의 가시 글자를 실제로 읽는 최소 주추출기다."""
    def __init__(self):
        super().__init__()
        self.chunks = []

    def handle_data(self, data):
        if data.strip():
            self.chunks.append(data.strip())


def primary(raw_html):
    parser = VisibleText()
    parser.feed(raw_html)
    return " ".join(parser.chunks)


def widget(content=COMMENTARY, attributes='id="stock_story" class="stock_story"'):
    return f'<section {attributes}><h2>기사 속 종목 이야기</h2><p>{content}</p></section>'


@pytest.mark.parametrize("article", [False, True])
def test_real_widget_structure_never_becomes_article_body(article):
    raw = FIXTURE.read_text(encoding="utf-8")
    if article:
        raw = raw.replace('<div class="article_view" id="print_contents">', '<article>').replace('</div></body>', '</article></body>')
    text, stage = extract_article_text(raw, primary_extract=primary)
    assert BODY in text and FOLLOWING in text
    assert COMMENTARY not in text
    assert "Powered by perplexity" not in text
    assert stage == ("article_tag" if article else "usable_ranges")


@pytest.mark.parametrize("inner", [
    '<div><span>해설</span><div><p>{}</p></div></div>',
    '<img src="widget.png"><br><input type="hidden"><p>{}</p>',
    '<img src="widget.png"/><br/><div/><p>{}</p>',
    '<article><p>{}</p></article>',
])
@pytest.mark.parametrize("article", [False, True])
def test_nested_void_and_self_closing_tags_keep_following_body(inner, article):
    tag = "article" if article else "main"
    raw = f'<{tag}><p>{BODY}</p>{widget(inner.format(COMMENTARY))}<p>{FOLLOWING}</p></{tag}>'
    text, _ = extract_article_text(raw, primary_extract=primary)
    assert BODY in text and FOLLOWING in text
    assert COMMENTARY not in text


@pytest.mark.parametrize("element", ['<div class="ai-summary"/>', '<input class="stock_story">', '<br class="ai-commentary"/>'])
def test_empty_auxiliary_elements_do_not_swallow_body(element):
    text, _ = extract_article_text(element + f'<article><p>{AI_NEWS}</p></article>', primary_extract=primary)
    assert text == AI_NEWS


@pytest.mark.parametrize("attributes", [
    'class="layout stock_story compact"', 'id="stock-story"',
    'class="ai-summary"', 'data-component="ai-commentary"',
    'data-widget="recommended-articles"', 'aria-label="기사 속 종목 이야기"',
])
def test_explicit_auxiliary_semantics_apply_without_domain_rules(attributes):
    text, _ = extract_article_text(f'<article><p>{AI_NEWS}</p>{widget(attributes=attributes)}</article>', primary_extract=primary)
    assert text == AI_NEWS


@pytest.mark.parametrize("markup", [
    '<article class="ai"><p>{}</p></article>',
    '<article id="perplexity"><p>{}</p></article>',
    '<article><h2>AI와 Perplexity 사업 보도</h2><p>{}</p></article>',
])
def test_ai_company_reporting_is_not_a_widget(markup):
    text, _ = extract_article_text(markup.format(AI_NEWS), primary_extract=primary)
    assert AI_NEWS in text


def test_recommended_article_does_not_make_main_article_ambiguous():
    raw = f'<article><p>{BODY}</p></article>{widget("<article><p>" + COMMENTARY + "</p></article>")}'
    assert extract_article_text(raw, primary_extract=primary) == (BODY, "article_tag")


def test_multiple_real_articles_remain_ambiguous():
    raw = f'<article><p>{BODY}</p></article><article><p>{AI_NEWS}</p></article>{widget()}'
    assert extract_article_text(raw, primary_extract=primary) == ("", "")


def test_widget_json_ld_is_not_an_independent_article():
    ld = json.dumps({"@type": "NewsArticle", "articleBody": COMMENTARY}, ensure_ascii=False)
    embedded = '<script type="application/ld+json">' + ld + '</script>'
    raw = f'<article><p>{BODY}</p></article>{widget(embedded)}'
    assert extract_article_text(raw, primary_extract=primary) == (BODY, "article_tag")


def test_json_ld_embedded_widget_markup_is_removed():
    ld = json.dumps({"@type": "NewsArticle", "articleBody": f"<p>{AI_NEWS}</p>{widget()}"}, ensure_ascii=False)
    text, stage = extract_article_text(f'<script type="application/ld+json">{ld}</script>', primary_extract=primary)
    assert text == AI_NEWS
    assert stage == "json_ld_article_body"


@pytest.mark.parametrize("kind", ["json_ld", "meta"])
def test_metadata_copy_cannot_reintroduce_excluded_widget(kind):
    if kind == "json_ld":
        ld = json.dumps({"@type": "NewsArticle", "articleBody": COMMENTARY}, ensure_ascii=False)
        metadata = f'<script type="application/ld+json">{ld}</script>'
    else:
        metadata = f'<meta name="description" content="{escape(COMMENTARY, quote=True)}">'
    raw = metadata + f'<article><p>{BODY}</p></article>' + widget()
    assert extract_article_text(raw, primary_extract=primary) == (BODY, "article_tag")
    assert extract_article_text(metadata + widget(), primary_extract=primary) == ("", "")


def test_meta_inside_widget_is_not_page_metadata():
    raw = widget(f'<meta name="description" content="{COMMENTARY}">')
    assert extract_article_text(raw, primary_extract=primary) == ("", "")


def test_normal_ai_news_in_json_ld_and_meta_is_preserved():
    ld = json.dumps({"@type": "NewsArticle", "articleBody": AI_NEWS}, ensure_ascii=False)
    assert extract_article_text(f'<script type="application/ld+json">{ld}</script>{widget()}', primary_extract=primary) == (AI_NEWS, "json_ld_article_body")
    assert extract_article_text(f'<meta name="description" content="{AI_NEWS}">{widget()}', primary_extract=primary) == (AI_NEWS, "meta_description")


def test_text_units_entities_and_company_numbers_are_preserved():
    raw = f'<article><p>{BODY}</p><p>한글 법인명 &amp; AI 기업의 120억원 계약을 확인했다.</p>{widget()}</article>'
    text, _ = extract_article_text(raw, primary_extract=primary)
    assert text == BODY + " 한글 법인명 & AI 기업의 120억원 계약을 확인했다."


def test_json_ld_inline_markup_does_not_split_company_or_amount():
    value = '<p>한글A&amp;B사는 120<span>억</span>원 규모의 AI 검색 계약을 체결했다.</p>'
    ld = json.dumps({"@type": "NewsArticle", "articleBody": value}, ensure_ascii=False)
    text, _ = extract_article_text(f'<script type="application/ld+json">{ld}</script>', primary_extract=primary)
    assert text == '한글A&B사는 120억원 규모의 AI 검색 계약을 체결했다.'


def test_legitimate_article_sentence_repeated_in_widget_is_preserved():
    raw = f'<article><p>{AI_NEWS}</p></article>{widget(AI_NEWS)}'
    assert extract_article_text(raw, primary_extract=primary) == (AI_NEWS, "article_tag")


def test_short_metadata_copy_uses_same_length_unit_as_body_admission():
    auxiliary = "이 종목은 AI의 판단에 따라 상승할 수 있다."
    assert len(auxiliary) >= 20 and len("".join(auxiliary.split())) < 20
    raw = f'<meta name="description" content="{auxiliary}">{widget(auxiliary)}'
    assert extract_article_text(raw, primary_extract=primary) == ("", "")
