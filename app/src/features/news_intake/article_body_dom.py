"""유일한 명시 기사 구획과 JSON-LD가 결속된 경우에만 생략 문단을 복구한다."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from src.features.news_intake import constants as c
from src.features.news_intake.body_boundary import contains_excluded_text, metadata_body_text
from src.shared.report_quality.source_identity import canonical_url


def _clean(value: str) -> str:
    return " ".join(unescape(value).split())


@dataclass(eq=False)
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None = None
    children: list[_Node | str] = field(default_factory=list)
    closed: bool = False

    def descendants(self):
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()

    def ancestors(self):
        parent = self.parent
        while parent is not None:
            yield parent
            parent = parent.parent

    def text(self, omitted: set[_Node] | None = None) -> str:
        if self.tag in c.BODY_NON_TEXT_TAGS or (omitted and self in omitted):
            return ""
        return _clean("".join(
            child if isinstance(child, str) else
            ((" " if child.tag in c.BODY_TEXT_BLOCK_TAGS else "") + child.text(omitted) +
             (" " if child.tag in c.BODY_TEXT_BLOCK_TAGS else ""))
            for child in self.children
        ))


class _Dom(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {}, closed=True)
        self.stack = [self.root]
        self.nodes: list[_Node] = []

    def _start(self, tag, attrs, closed):
        node = _Node(tag, dict((key, value or "") for key, value in attrs), self.stack[-1], closed=closed)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if not closed:
            self.stack.append(node)

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, tag in c.BODY_VOID_TAGS)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack[index].closed = True
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _article_nodes(node):
    if isinstance(node, dict):
        types = node.get("@type", ())
        types = (types,) if isinstance(types, str) else types
        if isinstance(types, (list, tuple)) and any(value in c.JSON_LD_ARTICLE_TYPES for value in types if isinstance(value, str)):
            if isinstance(node.get("articleBody"), str):
                yield node
        for value in node.values():
            yield from _article_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _article_nodes(value)


def _schema_article(node: _Node) -> bool:
    return any(value.rstrip("/").rsplit("/", 1)[-1] in c.JSON_LD_ARTICLE_TYPES
               for value in node.attrs.get("itemtype", "").split())


def _metadata(dom: _Dom, article: dict, root: _Node) -> tuple[bool, str]:
    """존재하는 기사 신원끼리의 충돌만 검사한다. 외부 요청 URL을 추정하지 않는다."""
    urls: set[str] = set()
    titles: set[str] = set()

    def add_url(value):
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str) and value.strip():
            normalized = canonical_url(value)
            if normalized:
                urls.add(normalized)
            else:
                # 명시됐지만 비교할 수 없는 신원도 승격의 근거로 쓰지 않는다.
                urls.add("invalid:" + value)

    for key in ("url", "@id", "mainEntityOfPage"):
        add_url(article.get(key))
    if isinstance(article.get("headline"), str):
        titles.add(_clean(article["headline"]))
    owners = [root] + [node for node in root.ancestors() if _schema_article(node)]
    for owner in owners:
        add_url(owner.attrs.get("itemid"))
        for node in owner.descendants():
            # 부모 구획의 다른 독립 기사에서 신원을 빌리지 않는다.
            between = []
            for parent in node.ancestors():
                if parent is owner:
                    break
                between.append(parent)
            if any(_schema_article(parent) for parent in between):
                continue
            if "headline" in node.attrs.get("itemprop", "").split():
                title = _clean(node.attrs.get("content") or node.text())
                if title:
                    titles.add(title)
    for node in dom.nodes:
        if node.tag == "meta":
            name = (node.attrs.get("property") or node.attrs.get("name") or "").casefold()
            if name == "og:url":
                add_url(node.attrs.get("content"))
            elif name == "og:title" and node.attrs.get("content"):
                titles.add(_clean(node.attrs["content"]))
        elif node.tag == "link" and "canonical" in node.attrs.get("rel", "").casefold().split():
            add_url(node.attrs.get("href"))
    if len(urls) > 1 or len(titles) > 1 or any(value.startswith("invalid:") for value in urls):
        return False, ""
    return True, next(iter(urls), "")


def _related_widget(node: _Node, article_url: str) -> bool:
    if node.tag not in c.BODY_DOM_RELATED_CONTAINER_TAGS:
        return False
    descendants = list(node.descendants())
    headings = [item for item in descendants if item.tag in c.BODY_DOM_HEADING_TAGS]
    if len(headings) != 1 or "".join(headings[0].text().split()) not in c.BODY_DOM_RELATED_HEADINGS:
        return False
    links = [item for item in descendants if item.tag == "a" and item.attrs.get("href")]
    if len(links) < c.BODY_DOM_RELATED_MIN_LINKS:
        return False
    for link in links:
        href = link.attrs["href"].strip()
        if href.startswith("#"):
            return False
        target = canonical_url(urljoin(article_url, href))
        if not target or target == article_url:
            return False
    # 제목과 링크 밖에 기자 문장이나 설명이 있으면 위젯으로 단정하지 않는다.
    allowed = {headings[0], *links}
    for item in [node, *descendants]:
        if item in allowed or any(parent in allowed for parent in item.ancestors()):
            continue
        if any(isinstance(child, str) and child.strip() for child in item.children):
            return False
    return True


def recover_article_body(raw_html: str, summary: str, excluded: set[str]) -> str:
    """길이 비교 대신 동일 요약을 담은 독립 문단과 후속 문단을 선택한다."""
    if not summary:
        return ""
    dom = _Dom()
    dom.feed(raw_html)
    roots = [node for node in dom.nodes if "articleBody" in node.attrs.get("itemprop", "").split()]
    if len(roots) != 1 or not roots[0].closed:
        return ""
    root = roots[0]
    articles = []
    for node in dom.nodes:
        if node.tag != "script" or node.attrs.get("type", "").casefold().strip() != "application/ld+json":
            continue
        try:
            payload = json.loads("".join(value for value in node.children if isinstance(value, str)))
        except (ValueError, TypeError):
            continue
        articles.extend(_article_nodes(payload))
    # 같은 요약을 공유하는 복수 기사도 어느 기사의 DOM인지 추정하지 않는다.
    if len(articles) != 1 or metadata_body_text(articles[0]["articleBody"]) != summary:
        return ""
    matched, article_url = _metadata(dom, articles[0], root)
    if not matched:
        return ""
    descendants = list(root.descendants())
    if any(_schema_article(node) or (
        node.tag == "article" and any(child.tag in c.BODY_DOM_PARAGRAPH_TAGS for child in node.descendants())
    ) for node in descendants):
        return ""
    widgets = {node for node in descendants if _related_widget(node, article_url)}
    blocked = widgets | {node for node in descendants if node.tag in c.BODY_PAGE_CHROME_TAGS or node.tag == "figure"}
    blocks = []
    paragraphs = []
    for node in descendants:
        if node.tag not in c.BODY_DOM_PARAGRAPH_TAGS | c.BODY_DOM_HEADING_TAGS:
            continue
        if node in blocked or any(parent in blocked for parent in node.ancestors()):
            continue
        if not node.closed or any(parent.tag in c.BODY_DOM_PARAGRAPH_TAGS for parent in node.ancestors()):
            return ""
        text = node.text(blocked)
        if not text:
            continue
        # 링크 제목만인 p는 기자 문단의 추가 근거가 아니다.
        links = [item for item in node.descendants() if item.tag == "a"]
        if links and _clean(" ".join(link.text() for link in links)) == text:
            return ""
        if node.tag in c.BODY_DOM_PARAGRAPH_TAGS:
            paragraphs.append(text)
        elif "".join(text.split()) in c.BODY_DOM_RELATED_HEADINGS:
            # 구조가 불명확한 추천 묶음을 기자 제목으로 승격하지 않는다.
            return ""
        blocks.append(text)
    if len(paragraphs) < c.BODY_DOM_MIN_PARAGRAPHS or not any(summary in text for text in paragraphs):
        return ""
    if not any(text not in summary and summary not in text for text in paragraphs):
        return ""
    body = "\n".join(blocks)
    if contains_excluded_text(body, excluded) or summary not in body:
        return ""
    return body
