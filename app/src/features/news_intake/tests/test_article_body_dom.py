"""동일 요약과 명시 구획의 결속만으로 복구하며 모호한 구획은 기존 경로를 유지한다."""
import hashlib
import json

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake import fetch

SUMMARY = "산업 생산과 소비가 전월보다 감소했다고 통계기관은 발표했다."
DETAIL = "타이어 수요 감소로 고무 제품 생산이 줄었으며 업계는 납품 일정을 점검하고 있다."
AFTER = "정부는 다음 달 생산이 반등할 것으로 예상하며 공급 현황을 점검한다고 밝혔다."
OTHER = "다른 회사는 해외 부동산을 매입했으며 이 사업은 별도 법인이 운영한다."


def page(dom, **values):
    article = {"@type": "NewsArticle", "articleBody": SUMMARY, **values}
    return '<script type="application/ld+json">' + json.dumps(article, ensure_ascii=False) + '</script>' + dom


def root(dom):
    return '<div itemprop="articleBody">' + dom + '</div>'


def extract(raw):
    digest = hashlib.sha256(raw.encode()).hexdigest()
    result = fetch.extract_article_text(raw, primary_extract=lambda _: "")
    assert hashlib.sha256(raw.encode()).hexdigest() == digest
    return result


def widget(*, heading="주요 뉴스", content="", second=True, href="https://else.example/a"):
    links = '<a href="' + href + '">' + OTHER + '</a>'
    if second:
        links += '<ul><li><a href="https://else.example/b">별도 기사 제목</a></li></ul>'
    return '<section><h3>' + heading + '</h3>' + content + links + '</section>'


def test_unique_bound_paragraphs_preserve_literal_text_and_order():
    text, stage = extract(page(root(f'<h2>산업 동향</h2><p>{SUMMARY}</p><p>{DETAIL}</p>')))
    assert stage == c.BODY_STAGE_BOUND_DOM
    assert text == '산업 동향\n' + SUMMARY + '\n' + DETAIL


def test_generic_link_widget_only_is_removed_and_later_paragraph_survives():
    text, _ = extract(page(root(f'<p>{SUMMARY}</p>' + widget() + f'<p>{DETAIL}</p><p>{AFTER}</p>')))
    assert text == '\n'.join((SUMMARY, DETAIL, AFTER))
    assert OTHER not in text


@pytest.mark.parametrize('dom', [
    root(f'<p>{SUMMARY}</p>'),
    root(f'<p>{OTHER}</p><p>{DETAIL}</p>'),
    root(f'<p>{SUMMARY}</p><p>{DETAIL}</p>') * 2,
    root(f'<p>{SUMMARY}</p>' + root(f'<p>{DETAIL}</p>')),
    root(f'<p>{SUMMARY}</p><p>{DETAIL}</p><article><p>{OTHER}</p></article>'),
    root(f'<p>{SUMMARY}</p><p>{DETAIL}</p><div itemtype="https://schema.org/NewsArticle"><p>{OTHER}</p></div>'),
    '<div itemprop="articleBody"><p>' + SUMMARY + '</p><p>' + DETAIL + '</p>',
])
def test_ambiguous_or_incomplete_dom_keeps_original_jsonld(dom):
    assert extract(page(dom)) == (SUMMARY, c.BODY_STAGE_JSON_LD)


@pytest.mark.parametrize('component', [
    widget(second=False),
    widget(href='#same'),
    widget(content='<p>' + OTHER + '</p>'),
    widget(content='독립적인 기자 설명'),
])
def test_inconclusive_related_structure_does_not_expand(component):
    assert extract(page(root(f'<p>{SUMMARY}</p>' + component + f'<p>{DETAIL}</p>'))) == (SUMMARY, c.BODY_STAGE_JSON_LD)


@pytest.mark.parametrize('extra,values', [
    ('<meta property="og:title" content="다른 기사">', {'headline': '산업 통계'}),
    ('<link rel="canonical" href="https://media.example/other">', {'url': 'https://media.example/original'}),
    ('<meta property="og:url" content="https://media.example/other">', {'mainEntityOfPage': {'@id': 'https://media.example/original'}}),
])
def test_explicit_page_identity_conflict_prevents_upgrade(extra, values):
    assert extract(page(extra + root(f'<p>{SUMMARY}</p><p>{DETAIL}</p>'), **values)) == (SUMMARY, c.BODY_STAGE_JSON_LD)


def test_recovery_failure_keeps_valid_jsonld(monkeypatch):
    def broken(*args):
        raise RecursionError('복구 구획이 너무 깊다')
    monkeypatch.setattr(fetch, 'recover_article_body', broken)
    assert extract(page(root(f'<p>{SUMMARY}</p><p>{DETAIL}</p>'))) == (SUMMARY, c.BODY_STAGE_JSON_LD)


def test_disabled_jsonld_stage_also_disables_optional_recovery(monkeypatch):
    monkeypatch.setattr(c, 'BODY_EXTRACTION_STAGE_ORDER', (c.BODY_STAGE_META_DESCRIPTION,))
    assert extract(page(root(f'<p>{SUMMARY}</p><p>{DETAIL}</p>'))) == ('', '')


def test_full_jsonld_is_not_truncated_to_short_dom():
    full = SUMMARY + ' ' + DETAIL
    assert extract(page(root(f'<p>{SUMMARY}</p>'), articleBody=full)) == (full, c.BODY_STAGE_JSON_LD)


def test_normal_ai_news_and_foreign_company_are_kept_without_attribution_changes():
    news = '기자는 AI 추천 기술의 보안 장애와 고객 대응 현황을 설명했다.'
    text, _ = extract(page(root(f'<p>{SUMMARY}</p><p>{news}</p><p>{OTHER}</p>')))
    assert text == '\n'.join((SUMMARY, news, OTHER))


def test_nested_widget_cannot_reenter_through_parent_paragraph():
    text, _ = extract(page(root('<p>' + SUMMARY + widget() + '</p><p>' + DETAIL + '</p>')))
    assert text == SUMMARY + '\n' + DETAIL


def test_tables_and_figures_are_not_claimed_as_full_body_recovery():
    text, _ = extract(page(root(f'<p>{SUMMARY}</p><figure><p>{OTHER}</p></figure><table><tr><td>별도 표</td></tr></table><p>{DETAIL}</p>')))
    assert text == SUMMARY + '\n' + DETAIL
