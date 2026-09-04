"""HTML/sitemap.xml에서 본문 구간·제목·링크·구조화 데이터를 뽑는다.

★ 실제 네트워크 접속은 이 파일에 없다 — 이미 받아 온 글자만 다룬다.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Optional
from xml.etree import ElementTree

from src.features.homepage.constants import (
    EXCLUDED_EXTENSIONS,
    PRIORITY_PATH_KEYWORDS,
    WIDE_MAX_CHARS_PER_RANGE,
    WIDE_MAX_LINKS_PER_PAGE,
    WIDE_MAX_LINK_URL_CHARS,
    WIDE_MAX_SITEMAP_ENTRIES,
    WIDE_MAX_USABLE_RANGES_PER_DOCUMENT,
    WIDE_MIN_CHARS_PER_RANGE,
    WIDE_ROOT_IDENTITY_SUPPLEMENT_PATH_MARKERS,
)

#: 본문에서 아예 빼는 boilerplate 구획. usable_ranges는 이 태그 밖의
#: 글자만 담는다(「본문 구간 — nav·footer boilerplate 제외」).
_BOILERPLATE_TAGS = frozenset({"nav", "header", "footer", "aside"})
_SKIP_TAGS = frozenset({"script", "style", "noscript"})
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "br", "dd", "div", "dl", "dt",
        "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "li",
        "main", "ol", "p", "section", "table", "td", "th", "tr", "ul",
    }
)


class _BoilerplateAwareExtractor(HTMLParser):
    """<nav>/<header>/<footer>/<aside> 밖의 블록 텍스트만 구간으로 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._boilerplate_depth = 0
        self._current: list[str] = []
        self.ranges: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BOILERPLATE_TAGS:
            self._boilerplate_depth += 1
        elif self._skip_depth == 0 and self._boilerplate_depth == 0 and tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BOILERPLATE_TAGS and self._boilerplate_depth > 0:
            self._boilerplate_depth -= 1
        elif self._skip_depth == 0 and self._boilerplate_depth == 0 and tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            stripped = data.strip()
            if stripped:
                self.title = stripped
        if self._skip_depth == 0 and self._boilerplate_depth == 0 and data.strip():
            self._current.append(data.strip())

    def _flush(self) -> None:
        if self._current:
            text = re.sub(r"\s+", " ", " ".join(self._current)).strip()
            if text:
                self.ranges.append(text)
            self._current = []

    def get_ranges(self) -> tuple[str, ...]:
        self._flush()
        return tuple(self.ranges)


def extract_usable_ranges(raw_html: str) -> tuple[str, str]:
    """(usable_ranges 후보 전체, title)을 뽑는다.

    boilerplate(nav/header/footer/aside) 밖의 블록 텍스트만 후보로 남기고,
    너무 짧은 조각은 버린다. 상한(개수·글자수)은 호출자가 적용한다
    (이 함수는 순수 추출만 한다).
    """
    parser = _BoilerplateAwareExtractor()
    parser.feed(raw_html)
    ranges = parser.get_ranges()

    cleaned = tuple(
        html.unescape(text)[:WIDE_MAX_CHARS_PER_RANGE]
        for text in ranges
        if len(text) >= WIDE_MIN_CHARS_PER_RANGE
    )
    seen: set[str] = set()
    deduped: list[str] = []
    for text in cleaned:
        if text in seen:
            continue
        seen.add(text)
        deduped.append(text)
        if len(deduped) >= WIDE_MAX_USABLE_RANGES_PER_DOCUMENT:
            break
    return tuple(deduped), html.unescape(parser.title)


class _LinkExtractor(HTMLParser):
    """유효한 <a href>의 우선순위 top-k만 고정 메모리로 모은다.

    단순히 앞 N개에서 멈추면 상품 링크가 많은 작은 쇼핑몰의 footer에 있는
    회사소개·개인정보 페이지를 항상 잃는다. 전체 href를 저장하지 않으면서도
    신원·보고서에 유용한 경로가 뒤에 나타나면 일반 링크를 밀어내게 한다.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url
        self._next_index = 0
        self._selected: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _rank(url: str) -> int:
        parsed = urllib.parse.urlsplit(url)
        # hostname의 ``company`` 같은 흔한 낱말이 모든 링크를 같은 우선순위로
        # 만들지 않게 실제 탐색 대상인 path/query만 채점한다.
        lowered = urllib.parse.unquote(
            urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
        ).casefold()
        markers = (
            *WIDE_ROOT_IDENTITY_SUPPLEMENT_PATH_MARKERS,
            *PRIORITY_PATH_KEYWORDS,
        )
        return next(
            (index for index, marker in enumerate(markers) if marker.casefold() in lowered),
            len(markers),
        )

    @property
    def links(self) -> tuple[str, ...]:
        return tuple(
            url
            for url, (_rank, index) in sorted(
                self._selected.items(), key=lambda item: item[1][1]
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href or href.strip().lower().startswith(
            ("javascript:", "data:", "mailto:", "tel:")
        ):
            return
        absolute = urllib.parse.urljoin(self._base_url, href).split("#", 1)[0]
        if not absolute or len(absolute) > WIDE_MAX_LINK_URL_CHARS:
            return
        try:
            parsed = urllib.parse.urlparse(absolute)
        except ValueError:
            return
        if parsed.scheme not in ("http", "https"):
            return
        if any(parsed.path.casefold().endswith(ext) for ext in EXCLUDED_EXTENSIONS):
            return
        if absolute in self._selected:
            return
        index = self._next_index
        self._next_index += 1
        ranked = (self._rank(absolute), index)
        if len(self._selected) < WIDE_MAX_LINKS_PER_PAGE:
            self._selected[absolute] = ranked
            return
        worst_url, worst_rank = max(
            self._selected.items(), key=lambda item: item[1]
        )
        if ranked < worst_rank:
            del self._selected[worst_url]
            self._selected[absolute] = ranked


def extract_links(raw_html: str, base_url: str) -> tuple[str, ...]:
    """페이지 안 첫 N개 절대 URL(호스트 제한은 호출자가 판정)."""
    parser = _LinkExtractor(base_url)
    parser.feed(raw_html)
    return parser.links


class _JsonLdExtractor(HTMLParser):
    """<script type="application/ld+json"> 원문만 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._chunks: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "script":
            return
        values = {key: (value or "") for key, value in attrs}
        if values.get("type", "").strip().lower() == "application/ld+json":
            self._capture = True
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self._capture = False
            text = "".join(self._chunks).strip()
            if text:
                self.blocks.append(text)

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._chunks.append(data)


_JSONLD_TEXT_MIN_CHARS = 4


def extract_json_ld_ranges(raw_html: str) -> tuple[str, ...]:
    """JSON-LD(Organization 등)에서 사람이 읽는 문자열 값만 usable_range 후보로 뽑는다.

    파싱에 실패한 블록은 조용히 건너뛴다(출처를 특정할 수 없는 값은 만들지 않는다).
    """
    parser = _JsonLdExtractor()
    parser.feed(raw_html)
    ranges: list[str] = []
    for block in parser.blocks:
        try:
            payload = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        for value in _iter_jsonld_strings(payload):
            if len(value) >= _JSONLD_TEXT_MIN_CHARS:
                ranges.append(value[:WIDE_MAX_CHARS_PER_RANGE])
    return tuple(ranges[:WIDE_MAX_USABLE_RANGES_PER_DOCUMENT])


_JSONLD_SKIP_KEYS = frozenset({"@context", "@type", "@id", "url", "image", "logo", "sameAs"})


def _iter_jsonld_strings(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _JSONLD_SKIP_KEYS:
                continue
            found.extend(_iter_jsonld_strings(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_iter_jsonld_strings(item))
    elif isinstance(node, str):
        stripped = node.strip()
        if stripped and not stripped.lower().startswith(("http://", "https://")):
            found.append(stripped)
    return found


_INLINE_DATA_SCRIPT_IDS = ("__NEXT_DATA__", "__NUXT_DATA__")
_INLINE_TEXT_MIN_CHARS = 8


class _InlineDataExtractor(HTMLParser):
    """id가 지정된 인라인 SPA 데이터 <script>의 원문만, id별로 모은다."""

    def __init__(self, script_ids: tuple[str, ...]) -> None:
        super().__init__()
        self._script_ids = script_ids
        self._capture_id = ""
        self._chunks: list[str] = []
        self.blocks: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "script":
            return
        values = {key: (value or "") for key, value in attrs}
        script_id = values.get("id", "").strip()
        if script_id in self._script_ids:
            self._capture_id = script_id
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture_id:
            text = "".join(self._chunks).strip()
            if text:
                self.blocks[self._capture_id] = text
            self._capture_id = ""

    def handle_data(self, data: str) -> None:
        if self._capture_id:
            self._chunks.append(data)


def extract_inline_spa_ranges(raw_html: str) -> tuple[str, ...]:
    """`__NEXT_DATA__` 등 «출처를 특정할 수 있는» 인라인 SPA JSON만 본문 후보로 쓴다.

    ★ id가 있는 <script> 태그 하나에서만 나온 값이므로 출처(그 페이지의 그
      script 블록)를 항상 특정할 수 있다. JSON 파싱이 실패하면(형식이 예상과
      다르면) 그 블록은 조용히 버린다 — 억지로 추측하지 않는다.
    """
    parser = _InlineDataExtractor(_INLINE_DATA_SCRIPT_IDS)
    parser.feed(raw_html)
    ranges: list[str] = []
    for text in parser.blocks.values():
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        for value in _iter_jsonld_strings(payload):
            if len(value) >= _INLINE_TEXT_MIN_CHARS:
                ranges.append(value[:WIDE_MAX_CHARS_PER_RANGE])
    return tuple(ranges[:WIDE_MAX_USABLE_RANGES_PER_DOCUMENT])


_SITEMAP_TAGS = ("{http://www.sitemaps.org/schemas/sitemap/0.9}loc", "loc")


def parse_sitemap_urls(raw_xml: str) -> tuple[str, ...]:
    """sitemap.xml(또는 sitemap index)에서 <loc> URL만 뽑는다.

    형식이 깨졌으면(파싱 실패) 빈 튜플을 돌려준다 — 예외를 밖으로 던지지 않는다
    (sitemap은 있으면 좋고 없어도 되는 발견 보조 경로다).
    """
    text = (raw_xml or "").strip()
    if not text:
        return ()
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return ()
    urls: list[str] = []
    for element in root.iter():
        tag = element.tag
        local_tag = tag.rsplit("}", 1)[-1] if "}" in tag else tag
        if local_tag != "loc":
            continue
        value = (element.text or "").strip()
        if value:
            urls.append(value)
        if len(urls) >= WIDE_MAX_SITEMAP_ENTRIES:
            break
    return tuple(urls)
