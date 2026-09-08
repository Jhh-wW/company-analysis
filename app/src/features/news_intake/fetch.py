"""기사 본문 읽기의 «네트워크 없는» 규칙 — 주소 후보·본문 폴백·해독 건강도.

★ 이 파일에는 실제 접속이 하나도 없다. 파이프라인이 받아 온 글자와 주소
  문자열만 다루므로 시험이 픽스처만으로 모든 겹을 잠글 수 있다.
★ 특정 언론사 도메인 목록을 두지 «않는다». 규칙은 마크업 구조(JSON-LD·
  ``<article>``·메타 태그)와 주소 표기 규칙으로만 쓴다.
"""

from __future__ import annotations

import json
import datetime as dt
import re
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import replace
from html.parser import HTMLParser
from typing import Final

from src.features.news_intake import constants as c
from src.features.news_intake.models import NewsBodyFetchResult, NewsCandidate
from src.shared.report_quality.source_identity import canonical_url


#: 본문 후보에서 통째로 빼는 태그. 스크립트·양식은 사람이 읽는 글이 아니다.
_SKIP_TAGS: Final[frozenset[str]] = frozenset(
    {"script", "style", "noscript", "template", "form"}
)
#: 기사 안에서도 광고·추천 묶음이 들어가는 boilerplate 구획.
_BOILERPLATE_TAGS: Final[frozenset[str]] = frozenset(
    {"nav", "header", "footer", "aside", "figure"}
)
_WWW_PREFIX: Final[str] = "www."
_HTTPS_SCHEME: Final[str] = "https"
_HTTP_SCHEME: Final[str] = "http"
#: JSON-LD에서 기사 본문을 담는 표준 열쇠(schema.org NewsArticle).
_JSON_LD_BODY_KEY: Final[str] = "articleBody"
_JSON_LD_SCRIPT_TYPE: Final[str] = "application/ld+json"
#: 메타 설명을 담는 표준 속성. og 쪽을 먼저 본다 — 언론사가 기사마다
#: 채우는 값이라 일반 ``description``보다 기사에 가깝다.
_META_DESCRIPTION_KEYS: Final[tuple[str, ...]] = ("og:description", "description")
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def _collapse(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


# ---------------------------------------------------------------- 주소 후보


def _url_variant(url: str, variant: str) -> str:
    """표기만 바꾼 같은 기사 주소. 만들 수 없으면 빈 문자열."""

    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    scheme = (parsed.scheme or "").casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if scheme not in {_HTTP_SCHEME, _HTTPS_SCHEME} or not host:
        return ""
    if variant == c.URL_VARIANT_AS_GIVEN:
        return url
    if variant == c.URL_VARIANT_HTTPS_UPGRADE:
        # 평문에서 암호화로만 올린다. 반대 방향은 절대 만들지 않는다.
        if scheme != _HTTP_SCHEME:
            return ""
        new_scheme = _HTTPS_SCHEME
        new_host = host
    elif variant == c.URL_VARIANT_WWW_TOGGLE:
        new_scheme = _HTTPS_SCHEME if scheme == _HTTP_SCHEME else scheme
        new_host = (
            host[len(_WWW_PREFIX) :]
            if host.startswith(_WWW_PREFIX)
            else _WWW_PREFIX + host
        )
        if not new_host or new_host.startswith("."):
            return ""
    else:
        return ""
    netloc = new_host if parsed.port is None else f"{new_host}:{parsed.port}"
    return urllib.parse.urlunsplit(
        (new_scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


def article_url_variants(url: str) -> tuple[str, ...]:
    """``URL_VARIANT_ORDER`` 순서로 만든 «표기만 다른» 같은 기사 주소들.

    리다이렉트를 따라가서 얻는 게 아니라 처음부터 그 주소로 요청하므로,
    변형마다 그 origin의 robots.txt를 새로 확인하게 된다.
    """

    clean = str(url or "").strip()
    if not clean:
        return ()
    seen: set[str] = set()
    variants: list[str] = []
    for variant in c.URL_VARIANT_ORDER:
        candidate = _url_variant(clean, variant)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        variants.append(candidate)
        if len(variants) >= c.MAX_URL_VARIANTS:
            break
    return tuple(variants)


def body_fetch_urls(candidate: NewsCandidate) -> tuple[str, ...]:
    """``BODY_FETCH_URL_FIELD_ORDER`` 순서로 고른 «서로 다른» 기사 주소들.

    같은 주소가 두 칸에 들어 있으면(검색 결과에 흔하다) 한 번만 남긴다.
    """

    seen: set[str] = set()
    urls: list[str] = []
    for field_name in c.BODY_FETCH_URL_FIELD_ORDER:
        value = str(getattr(candidate, field_name, "") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        urls.append(value)
    return tuple(urls)


def rebind_candidate_to_url(candidate: NewsCandidate, url: str) -> NewsCandidate:
    """본문을 «실제로 읽은» 주소로 기사 신원을 다시 묶는다.

    근거 조각의 계약은 「이 글자는 이 주소의 문서에서 나왔다」이다. 언론사
    원문이 막혀 다른 주소에서 본문을 읽었는데 출처만 언론사 원문으로
    적으면, 그 주소를 열어도 그 문장이 없을 수 있다 — 조용한 거짓말이다.
    그래서 주소·발행처를 읽은 쪽으로 맞춘다.

    같은 주소(정규화 뒤 동일)면 원래 후보를 그대로 돌려준다.
    정규화할 수 없는 주소면 신원을 만들 수 없으므로 역시 그대로 둔다.
    """

    canonical = canonical_url(url)
    if not canonical or canonical == candidate.source_url:
        return candidate
    publisher = (urllib.parse.urlsplit(canonical).hostname or "").casefold()
    if not publisher:
        return candidate
    return replace(candidate, source_url=canonical, publisher=publisher)


# ------------------------------------------------------------- 해독 건강도


def decode_looks_broken(text: str) -> bool:
    """대체문자 비율이 상한을 넘으면 해독이 깨진 것으로 본다.

    받아 온 글자를 만드는 전송 계층이 ``errors="replace"``로 해독하므로,
    잘못된 인코딩으로 읽힌 문서는 예외 없이 U+FFFD 범벅이 되어 돌아온다.
    그대로 두면 깨진 글자가 근거 조각이 되므로 여기서 잘라 낸다.
    """

    raw = str(text or "")
    if not raw:
        return False
    replacements = raw.count(c.DECODE_REPLACEMENT_CHAR)
    if not replacements:
        return False
    return replacements / len(raw) > c.DECODE_REPLACEMENT_RATIO_LIMIT


# ------------------------------------------------------------- 본문 폴백


class _JsonLdArticleBody(HTMLParser):
    """``<script type="application/ld+json">`` 원문만 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._chunks: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        values = {key: (value or "") for key, value in attrs}
        if values.get("type", "").strip().casefold() == _JSON_LD_SCRIPT_TYPE:
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


def _json_ld_article_body(raw_html: str) -> str:
    """기사 타입의 단일 articleBody만 받는다. 여러 기사 중 원문을 추측하지 않는다."""

    parser = _JsonLdArticleBody()
    parser.feed(raw_html)
    bodies: set[str] = set()
    for block in parser.blocks:
        try:
            payload = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        for value in _iter_article_bodies(payload):
            bodies.add(_collapse(value))
    # 추천 기사 등 여러 문서의 본문이 있으면 길이로 원문을 추측하지 않는다.
    return next(iter(bodies)) if len(bodies) == 1 else ""


def _iter_article_bodies(node: object) -> Iterable[str]:
    found: list[str] = []
    stack: list[object] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            body = current.get(_JSON_LD_BODY_KEY)
            raw_types = current.get("@type", ())
            types = (raw_types,) if isinstance(raw_types, str) else raw_types
            article_type = isinstance(types, (tuple, list)) and any(value in c.JSON_LD_ARTICLE_TYPES for value in types if isinstance(value, str))
            if article_type and isinstance(body, str) and body.strip():
                found.append(body.strip())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


class _ArticleTagText(HTMLParser):
    """``<article>`` 안쪽 글자만 모은다(스크립트·boilerplate 제외)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._article_depth = 0
        self._skip_depth = 0
        self._boilerplate_depth = 0
        self._chunks: list[str] = []
        self._article_blocks: list[str] = []
        self.root_articles = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            if self._article_depth == 0:
                self.root_articles += 1
                self._chunks = []
            self._article_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BOILERPLATE_TAGS:
            self._boilerplate_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            if self._article_depth > 0:
                self._article_depth -= 1
                if self._article_depth == 0:
                    self._article_blocks.append(_collapse(" ".join(self._chunks)))
            return
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BOILERPLATE_TAGS and self._boilerplate_depth > 0:
            self._boilerplate_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._article_depth <= 0 or self._skip_depth or self._boilerplate_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    @property
    def text(self) -> str:
        blocks = {block for block in self._article_blocks if block}
        if self._article_depth:
            blocks.add(_collapse(" ".join(self._chunks)))
        return next(iter(blocks)) if len(blocks) == 1 else ""


def _article_tag_text(raw_html: str) -> str:
    parser = _ArticleTagText()
    parser.feed(raw_html)
    return parser.text


class _MetaDescription(HTMLParser):
    """``og:description``·``description`` 메타 값을 열쇠별로 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        values = {key.casefold(): (value or "") for key, value in attrs}
        name = (values.get("property") or values.get("name") or "").strip().casefold()
        if name not in _META_DESCRIPTION_KEYS or name in self.values:
            return
        content = _collapse(values.get("content", ""))
        if content:
            self.values[name] = content

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _meta_description(raw_html: str) -> str:
    """우선순위 열쇠부터 보되, 너무 짧은 값에서 «멈추지» 않는다.

    실측: 어떤 언론사는 ``og:description``에 사진 설명 한 마디(16자)만 넣고
    기사 요약은 일반 ``description``에만 담았다. 우선순위 값에서 그냥
    멈추면 그 기사는 본문 0자가 된다.
    """

    parser = _MetaDescription()
    parser.feed(raw_html)
    longest = ""
    for key in _META_DESCRIPTION_KEYS:
        value = parser.values.get(key, "")
        if len(value) >= c.BODY_MIN_CHARS:
            return value
        if len(value) > len(longest):
            longest = value
    return longest


PrimaryExtract = Callable[[str], str]


def extract_article_text(
    raw_html: str,
    *,
    primary_extract: PrimaryExtract,
) -> tuple[str, str]:
    """``BODY_EXTRACTION_STAGE_ORDER`` 순서로 본문을 시도한다.

    Args:
        raw_html: 이미 받아 온 기사 페이지 글자.
        primary_extract: 첫 겹(본문 구간) 추출기. 파이프라인이 공식 웹
            수집기의 ``extract_usable_ranges``를 주입한다 — 이 기능 폴더가
            다른 기능 폴더를 직접 import하지 않게 하려는 주입이다.

    Returns:
        (본문, 단계 코드). 어느 겹에서도 못 얻으면 ``("", "")``.
    """

    article_parser = _ArticleTagText()
    article_parser.feed(raw_html)
    extractors: dict[str, PrimaryExtract] = {
        c.BODY_STAGE_USABLE_RANGES: lambda value: (
            primary_extract(_without_page_chrome(value)) if article_parser.root_articles <= 1 else ""
        ),
        c.BODY_STAGE_JSON_LD: _json_ld_article_body,
        c.BODY_STAGE_ARTICLE_TAG: _article_tag_text,
        c.BODY_STAGE_META_DESCRIPTION: _meta_description,
    }
    for stage in c.BODY_EXTRACTION_STAGE_ORDER:
        extractor = extractors.get(stage)
        if extractor is None:
            continue
        try:
            text = str(extractor(raw_html) or "").strip()
        except Exception:  # noqa: BLE001 - 한 겹이 깨져도 다음 겹은 시도한다
            continue
        if len(text) >= c.BODY_MIN_CHARS:
            return text, stage
    return "", ""


class _WithoutPageChrome(HTMLParser):
    """범용 추출기로 넘기기 전에 제목·양식·탐색 구역을 제거한다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.blocked: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.blocked:
            if tag == self.blocked[-1]:
                self.depth += 1
            return
        if tag in {"title", "nav", "header", "footer", "aside", "form", "script", "style", "noscript"}:
            self.blocked.append(tag)
            self.depth = 1
            return
        self.parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag: str) -> None:
        if self.blocked:
            if tag == self.blocked[-1]:
                self.depth -= 1
                if not self.depth:
                    self.blocked.pop()
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.blocked:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.blocked:
            self.parts.append(f"&#{name};")


def _without_page_chrome(raw_html: str) -> str:
    parser = _WithoutPageChrome()
    parser.feed(raw_html)
    return "".join(parser.parts)


class _PublishedMeta(HTMLParser):
    """발행일로 명시한 메타만 읽는다. 수정일·검색등록일은 섞지 않는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        fields = {name.casefold(): (value or "") for name, value in attrs}
        key = (fields.get("property") or fields.get("name") or fields.get("itemprop") or "").casefold()
        if key in c.ARTICLE_PUBLISHED_META_KEYS:
            self.values.append(fields.get("content", ""))


def extract_article_published_on(raw_html: str) -> str:
    """기사 HTML의 발행일만 반환한다. 여러 발행일이 충돌하면 추정하지 않는다."""

    parser = _PublishedMeta()
    parser.feed(raw_html)
    ld = _JsonLdArticleBody()
    ld.feed(raw_html)
    values = list(parser.values)
    for block in ld.blocks:
        try:
            stack: list[object] = [json.loads(block)]
        except (ValueError, TypeError):
            continue
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                raw_types = node.get("@type", ())
                types = (raw_types,) if isinstance(raw_types, str) else raw_types
                if isinstance(types, (tuple, list)) and any(isinstance(value, str) and value in c.JSON_LD_ARTICLE_TYPES for value in types):
                    published = node.get("datePublished")
                    if isinstance(published, str):
                        values.append(published)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    dates: set[str] = set()
    for value in values:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:$|T|\s)", value.strip())
        if not match:
            continue
        try:
            dates.add(dt.date.fromisoformat(match.group(1)).isoformat())
        except ValueError:
            continue
    return next(iter(dates)) if len(dates) == 1 else ""


# --------------------------------------------------- 사유 코드와 결과 정규화


def http_status_code(status: int) -> str:
    """``fetch_http_403``처럼 상태를 그대로 담은 사유 코드."""

    return f"{c.FETCH_HTTP_CODE_PREFIX}{int(status)}"


def normalize_body_result(value: object) -> NewsBodyFetchResult:
    """본문 함수가 돌려준 값을 ``NewsBodyFetchResult`` 하나로 맞춘다.

    시험이 주입하는 «글자 또는 None» 함수를 그대로 쓸 수 있게 남겨 둔다 —
    그런 함수는 사유를 모르므로 예전과 같은 ``fetch_failed``로 센다.
    """

    if isinstance(value, NewsBodyFetchResult):
        return value
    if isinstance(value, str) and value.strip():
        return NewsBodyFetchResult(text=value, stage=c.BODY_STAGE_PROVIDED)
    return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_FAILED)
