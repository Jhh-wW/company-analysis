"""명시적 인라인 시세 가격만 제거하고 본문 숫자·이름·폴백 경계를 보존한다."""
from html import escape
from pathlib import Path
import json

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.body_boundary import body_boundary, metadata_body_text
from src.features.news_intake.fetch import extract_article_text
from src.features.news_intake.tests.test_article_body_boundary_regression import _primary

FIXTURE = Path(__file__).with_name("fixtures") / "inline_stock_quote.html"
COMPANY = "가나다기업"
QUOTE = "(91,700원 ▲1,200 +1.33%)"
INTRO = "기자는 2026년 7월 15일 서울에서 열린 사업 설명회와 계약 체결 현장을 직접 취재했다."
FOLLOWING = "회사는 연구비 120억원(전년 대비 +3.02%)을 투입하고 7개 지역으로 확대한다고 밝혔다."
AI_NEWS = "Perplexity는 AI 검색 사업에 120억원을 투자했다고 밝혔다."
AUXILIARY = "별도 자동 해설은 관련 종목의 투자 매력과 예상 주가 흐름을 소개한다."


def _component(price: str = QUOTE, *, container_attrs: str = 'class="stock down" data-testid="stock"') -> str:
    return f'<a {container_attrs}><span class="name">{escape(COMPANY)}</span><span class="price">{price}</span></a>'


def _json_ld(body: str) -> str:
    return '<script type="application/ld+json">' + json.dumps(
        {"@type": "NewsArticle", "articleBody": body}, ensure_ascii=False,
    ) + '</script>'


@pytest.mark.parametrize("stage", c.BODY_EXTRACTION_STAGE_ORDER)
def test_inline_quote_markup_uses_same_boundary_in_every_stage(stage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(c, "BODY_EXTRACTION_STAGE_ORDER", (stage,))
    story = f'<p>{INTRO}</p><p>{_component()}는 신규 공급 계약을 체결하고 국내 여러 지역에서 상담 서비스를 함께 운영한다고 밝혔다.</p><p>{FOLLOWING}</p>'
    if stage == c.BODY_STAGE_JSON_LD:
        raw = _json_ld(story)
    elif stage == c.BODY_STAGE_META_DESCRIPTION:
        raw = f'<meta name="description" content="{escape(story, quote=True)}">'
    else:
        raw = f'<article>{story}</article>' if stage == c.BODY_STAGE_ARTICLE_TAG else f'<main>{story}</main>'
    text, actual_stage = extract_article_text(raw, primary_extract=_primary)
    assert actual_stage == stage
    assert INTRO in text and FOLLOWING in text and COMPANY in text
    assert QUOTE not in text


def test_actual_selected_sm_paragraph_preserves_company_and_contract() -> None:
    text, stage = extract_article_text('<article>' + FIXTURE.read_text(encoding="utf-8") + '</article>', primary_extract=_primary)
    assert stage == c.BODY_STAGE_ARTICLE_TAG
    assert "에스엠" in text and "엔터테인먼트는 보이그룹 NCT127" in text
    assert "전속 재계약을 완료했다고 15일 밝혔다." in text
    assert "80,200" not in text and "2,500" not in text and "3.02%" not in text


@pytest.mark.parametrize("metadata", [False, True])
def test_company_entities_and_reporter_entities_keep_their_text(metadata: bool) -> None:
    story = ('<p><a class="stock" data-testid="stock"><span class="name">가나다A&amp;B</span>'
             f'<span class="price">{QUOTE}</span></a>는 120<span>억</span>원 규모 계약을 발표했다.</p>'
             '<p>기자는 전년 대비 &#43;3.02% 증가와 (계약금 30억원)을 확인했다.</p>')
    raw = _json_ld(story) if metadata else f'<article>{story}</article>'
    text, _ = extract_article_text(raw, primary_extract=_primary)
    assert "가나다A&B" in text and "+3.02%" in text and "(계약금 30억원)" in text
    assert QUOTE not in text


@pytest.mark.parametrize("attrs", [
    'class="stock"', 'data-testid="stock"', 'class="stocking" data-testid="stock"',
    'class="stock" data-testid="stock-story"', 'class="price"',
])
def test_partial_or_unrelated_component_markers_preserve_reporter_numbers(attrs: str) -> None:
    body = f'<article><p>{_component(container_attrs=attrs)}의 주가를 기자가 직접 확인했다.</p><p>{FOLLOWING}</p></article>'
    text, _ = extract_article_text(body, primary_extract=_primary)
    assert QUOTE in text and COMPANY in text and FOLLOWING in text


def test_generic_price_class_and_adjacent_bracketed_numbers_are_preserved() -> None:
    direct = f'기자는 <span class="price">{QUOTE}</span>라는 실제 거래가격과 (계약금 30억원)을 확인했다.'
    raw = f'<article><p>{_component()}는 제휴를 발표했다.</p><p>{direct}</p><p>{FOLLOWING}</p></article>'
    text, _ = extract_article_text(raw, primary_extract=_primary)
    assert text.count(QUOTE) == 1
    assert "(계약금 30억원)" in text and FOLLOWING in text


@pytest.mark.parametrize("price", [
    '<strong>(91,700원</strong><span> ▲1,200</span><em> +1.33%)</em>',
    '<img src="icon"><br><input hidden>(91,700원 ▲1,200 +1.33%)',
    '<img src="icon"/><br/><span/>(91,700원 ▲1,200 +1.33%)',
    '(91,700원&nbsp;&#9650;1,200 +1.33%)',
])
def test_nested_void_self_closing_and_entities_keep_following_text(price: str) -> None:
    raw = f'<article><p>{INTRO}</p><p>{_component(price)}</p><p>{FOLLOWING}</p><p><span class="price">기자가 쓴 가격 3,500원</span></p></article>'
    text, _ = extract_article_text(raw, primary_extract=_primary)
    assert COMPANY in text and INTRO in text and FOLLOWING in text
    assert "91,700" not in text and "1,200" not in text
    assert "기자가 쓴 가격 3,500원" in text


@pytest.mark.parametrize("price_markup", ['<span class="price"/>', '<span class="price"></span>'])
def test_empty_price_does_not_exclude_company_or_next_sibling(price_markup: str) -> None:
    body = f'<a class="stock" data-testid="stock"><span class="name">{COMPANY}</span>{price_markup}<span>기자가 쓴 3억원</span></a>'
    text = metadata_body_text(body + FOLLOWING)
    assert COMPANY in text and "기자가 쓴 3억원" in text and FOLLOWING in text


def test_closed_quote_scope_does_not_affect_next_price_element() -> None:
    raw = _component() + '<p><span class="price">정상 가격 (3,500원 -2%)</span></p>' + FOLLOWING
    text = metadata_body_text(raw)
    assert QUOTE not in text and COMPANY in text
    assert "정상 가격 (3,500원 -2%)" in text and FOLLOWING in text


@pytest.mark.parametrize("component", [
    '<a class="stock" data-testid="stock"/>',
    '<a class="stock" data-testid="stock"><span class="name">가나다기업</span><span class="price">(2원)</a>',
])
def test_self_closing_or_parent_closed_component_releases_scope(component: str) -> None:
    text = metadata_body_text(component + '<p><span class="price">정상 가격 3,500원</span></p>' + FOLLOWING)
    assert "정상 가격 3,500원" in text and FOLLOWING in text
    assert "(2원)" not in text


@pytest.mark.parametrize("metadata", ["json_ld", "meta"])
@pytest.mark.parametrize("price", [QUOTE, "(2원)"])
def test_plain_metadata_copy_of_observed_component_cannot_reenter(metadata: str, price: str) -> None:
    copied = f'{COMPANY}{price}는 새 계약을 체결했다고 밝혔다.'
    head = _json_ld(copied) if metadata == "json_ld" else f'<meta name="description" content="{escape(copied, quote=True)}">'
    raw = head + f'<article><p>{_component(price)}는 새 계약을 체결했다고 밝혔다.</p><p>{FOLLOWING}</p></article>'
    text, stage = extract_article_text(raw, primary_extract=_primary)
    assert stage == c.BODY_STAGE_ARTICLE_TAG
    assert price not in text and COMPANY in text and FOLLOWING in text


@pytest.mark.parametrize("metadata", ["json_ld", "meta"])
def test_clean_metadata_keeps_identical_reporter_price_after_dom_removal(metadata: str) -> None:
    reporter = f'{AI_NEWS} 기자가 확인한 주가는 {QUOTE}였으며 이는 기사 속 직접 인용이다.'
    head = _json_ld(reporter) if metadata == "json_ld" else f'<meta name="description" content="{escape(reporter, quote=True)}">'
    raw = head + f'<aside>{_component()}</aside>'
    text, stage = extract_article_text(raw, primary_extract=lambda _raw: "")
    assert text == reporter
    assert stage == (c.BODY_STAGE_JSON_LD if metadata == "json_ld" else c.BODY_STAGE_META_DESCRIPTION)


def test_plain_metadata_without_component_evidence_is_not_guessed() -> None:
    reporter = f'{COMPANY}{QUOTE}라는 괄호는 기자가 직접 쓴 주가 인용이며 별도 위젯 증거가 없다.'
    assert extract_article_text(_json_ld(reporter), primary_extract=_primary) == (reporter, c.BODY_STAGE_JSON_LD)


def test_visible_journalist_copy_is_preserved_when_widget_has_same_text() -> None:
    reporter = f'{COMPANY}{QUOTE}라는 수치를 기자가 직접 확인했다고 밝혔다.'
    raw = f'<article><p>{reporter}</p><p>{FOLLOWING}</p></article><aside>{_component()}</aside>'
    text, _ = extract_article_text(raw, primary_extract=_primary)
    assert reporter in text and FOLLOWING in text


def test_stale_primary_copy_is_rejected_and_clean_meta_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(c, "BODY_EXTRACTION_STAGE_ORDER", (c.BODY_STAGE_USABLE_RANGES, c.BODY_STAGE_META_DESCRIPTION))
    stale = f'{COMPANY}{QUOTE}는 계약을 발표했다고 밝혔다.'
    seen = []
    def primary(raw: str) -> str:
        seen.append(raw)
        return stale
    raw = f'<meta name="description" content="{FOLLOWING}"><main>{_component()}</main>'
    assert extract_article_text(raw, primary_extract=primary) == (FOLLOWING, c.BODY_STAGE_META_DESCRIPTION)
    assert seen and QUOTE not in seen[0]


def test_auxiliary_ai_boundary_remains_independent_of_inline_price() -> None:
    widget = f'<section class="ai-summary"><p>{AUXILIARY}</p>{_component()}</section>'
    raw = _json_ld(AUXILIARY) + f'<article><p>{AI_NEWS}</p><p>{_component()}는 계약을 발표했다.</p>{widget}<p>{FOLLOWING}</p></article>'
    text, stage = extract_article_text(raw, primary_extract=_primary)
    assert stage == c.BODY_STAGE_ARTICLE_TAG
    assert AI_NEWS in text and FOLLOWING in text and COMPANY in text
    assert AUXILIARY not in text and QUOTE not in text


def test_price_only_component_does_not_blacklist_reporter_amount() -> None:
    raw = f'<a class="stock" data-testid="stock"><span class="price">{QUOTE}</span></a>'
    boundary = body_boundary(raw)
    assert QUOTE not in boundary.html
    assert not boundary.excluded_text
