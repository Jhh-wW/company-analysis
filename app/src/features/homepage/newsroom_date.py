"""뉴스룸 HTML의 화면 글자 날짜를 제한된 4단계로 판정한다.

프로그램 단계는 제목과 같은 작은 HTML 범위만 보고, AI 예비 단계는 목록
페이지에서만 호출한다. AI가 돌려준 글자는 원문과 문맥을 다시 대조하므로 호출
결과만으로 날짜를 승격하지 않는다. 실제 provider와 저장소는 호출자가 함수로
주입해 이 기능이 네트워크나 다른 feature를 직접 소유하지 않게 한다.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Mapping, TypeAlias

from src.features.homepage.constants import (
    NEWSROOM_DATE_AI_MAX_CHARS,
    NEWSROOM_DATE_CONTEXT_CHARS,
    NEWSROOM_DATE_ORIGINS,
    NEWSROOM_DATE_PATTERNS,
    NEWSROOM_DATE_PROMPT_VERSION,
    NEWSROOM_DATE_REASON_CODES,
    NEWSROOM_DATE_SINGLE_ARTICLE_TITLE_COUNT,
)


NewsroomDateAiCall: TypeAlias = Callable[[str], str]
NewsroomDateCacheLoad: TypeAlias = Callable[[str], Mapping[str, str] | None]
NewsroomDateCacheSave: TypeAlias = Callable[[str, Mapping[str, str]], None]
NewsroomDateSwitch: TypeAlias = bool | Callable[[], bool]

_SKIP_TAGS = frozenset({"script", "style", "noscript"})
_BLOCK_TAGS = frozenset(
    {
        "article",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "header",
        "li",
        "main",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }
)
_HEADING_TITLE_TAGS = frozenset({"h1", "h2", "h3"})
_DOCUMENT_TITLE_TAG = "title"
_BOLD_TITLE_TAGS = frozenset({"b", "strong"})
_ITEM_CONTAINER_TAGS = frozenset({"article", "li"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_DATE_PATTERNS = tuple(re.compile(pattern) for pattern in NEWSROOM_DATE_PATTERNS)


@dataclass(frozen=True)
class NewsroomDateResult:
    """뉴스룸 날짜 한 건의 값·출처·도달 단계·닫힌 사유 코드."""

    date: str
    origin: str
    stage: int
    reason_code: str

    def __post_init__(self) -> None:
        if self.reason_code not in NEWSROOM_DATE_REASON_CODES:
            raise ValueError("알 수 없는 뉴스룸 날짜 사유 코드입니다")
        if self.stage not in {1, 2, 3, 4}:
            raise ValueError("뉴스룸 날짜 단계는 1부터 4까지여야 합니다")
        if self.date and self.origin not in NEWSROOM_DATE_ORIGINS:
            raise ValueError("날짜가 있으면 판정 출처가 필요합니다")
        if not self.date and self.origin:
            raise ValueError("빈 날짜에는 판정 출처를 붙일 수 없습니다")


@dataclass
class _HtmlNode:
    tag: str
    parent: int | None
    parts: list[str | int] = field(default_factory=list)


class _NewsroomHtmlParser(HTMLParser):
    """화면 글자 순서와 제목 주변의 작은 DOM 범위를 함께 보존한다."""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[_HtmlNode] = []
        self._stack: list[int] = []
        self._skip_depth = 0
        self._visible_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if normalized in _BLOCK_TAGS:
            self._visible_chunks.append("\n")
        parent = self._stack[-1] if self._stack else None
        node_index = len(self.nodes)
        self.nodes.append(_HtmlNode(tag=normalized, parent=parent))
        if parent is not None:
            self.nodes[parent].parts.append(node_index)
        if normalized not in _VOID_TAGS:
            self._stack.append(node_index)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        normalized = tag.casefold()
        if normalized not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if normalized in _BLOCK_TAGS:
            self._visible_chunks.append("\n")
        for position in range(len(self._stack) - 1, -1, -1):
            if self.nodes[self._stack[position]].tag == normalized:
                del self._stack[position:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        if self._stack:
            self.nodes[self._stack[-1]].parts.append(data)
        self._visible_chunks.append(data)

    def visible_text(self) -> str:
        raw = html.unescape(
            "".join(
                chunk if chunk == "\n" else f"{chunk} "
                for chunk in self._visible_chunks
            )
        )
        lines = [
            re.sub(r"[^\S\r\n]+", " ", line).strip()
            for line in raw.splitlines()
        ]
        return "\n".join(line for line in lines if line)

    def node_text(self, node_index: int) -> str:
        chunks: list[str] = []

        def visit(index: int) -> None:
            for part in self.nodes[index].parts:
                if isinstance(part, int):
                    visit(part)
                else:
                    chunks.append(part)

        visit(node_index)
        return _normalize_whitespace(" ".join(chunks))


def _normalize_whitespace(value: str) -> str:
    return " ".join(html.unescape(value).split())


def _parse_match(match: re.Match[str]) -> str | None:
    year, month, day = (int(value) for value in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _dates_in_text(value: str) -> tuple[str, ...]:
    found: list[str] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(value):
            parsed = _parse_match(match)
            if parsed and parsed not in found:
                found.append(parsed)
    return tuple(found)


def _parse_date_text(value: str) -> str | None:
    candidate = value.strip()
    for pattern in _DATE_PATTERNS:
        match = pattern.fullmatch(candidate)
        if match is not None:
            return _parse_match(match)
    return None


def _title_nodes(parser: _NewsroomHtmlParser) -> tuple[int, ...]:
    primary = [
        index
        for index, node in enumerate(parser.nodes)
        if node.tag in _HEADING_TITLE_TAGS and parser.node_text(index)
    ]
    if not primary:
        primary = [
            index
            for index, node in enumerate(parser.nodes)
            if node.tag == _DOCUMENT_TITLE_TAG and parser.node_text(index)
        ][:1]
    candidates = list(primary)
    heading_containers = {
        container
        for index in primary
        if (container := _nearest_item_container(parser, index)) is not None
    }
    bold_containers: set[int] = set()
    for index, node in enumerate(parser.nodes):
        if node.tag not in _BOLD_TITLE_TAGS or not parser.node_text(index):
            continue
        container = _nearest_item_container(parser, index)
        if (
            container is None
            or container in heading_containers
            or container in bold_containers
        ):
            continue
        # 목록 항목마다 제목 태그 대신 쓰인 첫 굵은 글만 더한다. 같은 항목의
        # 뒤쪽 강조 글자는 제목이 아니라 본문일 수 있으므로 무시한다.
        candidates.append(index)
        bold_containers.add(container)
    if not candidates:
        # 제목 태그가 없는 게시판은 첫 굵은 글자를 제목으로 쓰기도 한다. 굵은
        # 본문 강조를 전부 제목으로 오인하지 않도록 첫 한 건만 대체로 쓴다.
        candidates = [
            index
            for index, node in enumerate(parser.nodes)
            if node.tag in _BOLD_TITLE_TAGS and parser.node_text(index)
        ][:1]
    unique: list[int] = []
    seen_text: set[str] = set()
    for index in candidates:
        title_text = parser.node_text(index).casefold()
        if title_text in seen_text:
            continue
        seen_text.add(title_text)
        unique.append(index)
    return tuple(unique)


def _nearest_item_container(
    parser: _NewsroomHtmlParser,
    node_index: int,
) -> int | None:
    parent = parser.nodes[node_index].parent
    while parent is not None:
        if parser.nodes[parent].tag in _ITEM_CONTAINER_TAGS:
            return parent
        parent = parser.nodes[parent].parent
    return None


def _title_scope_node(parser: _NewsroomHtmlParser, title_index: int) -> int:
    anchor = title_index
    parent = parser.nodes[anchor].parent
    if parent is not None and parser.nodes[parent].tag == "a":
        anchor = parent
        parent = parser.nodes[anchor].parent
    return parent if parent is not None else anchor


def _program_evidence(raw_html: str) -> tuple[tuple[str, ...], int, str]:
    parser = _NewsroomHtmlParser()
    parser.feed(raw_html)
    titles = _title_nodes(parser)
    found: list[str] = []
    for title_index in titles:
        scope_text = parser.node_text(_title_scope_node(parser, title_index))
        for candidate in _dates_in_text(scope_text):
            if candidate not in found:
                found.append(candidate)
    return tuple(found), len(titles), parser.visible_text()


def find_program_newsroom_dates(raw_html: str) -> tuple[str, ...]:
    """제목의 형제·부모 범위에서 찾은 유효 날짜를 문서 순서로 반환한다."""

    found, _title_count, _page_text = _program_evidence(raw_html)
    return found


def newsroom_date_cache_key(page_text: str) -> str:
    """프롬프트 버전과 잘리지 않은 화면 글자 SHA-256으로 캐시 키를 만든다."""

    digest = hashlib.sha256(page_text.encode("utf-8")).hexdigest()
    return f"{NEWSROOM_DATE_PROMPT_VERSION}:{digest}"


def _ai_prompt(page_text: str) -> str:
    limited = page_text[:NEWSROOM_DATE_AI_MAX_CHARS]
    return (
        "다음 뉴스룸 목록 페이지에서 게시일로 보이는 날짜 글자 한 건을 고르세요. "
        "입력·등록·게시·작성 표지가 붙은 날짜를 우선하고, 페이지에 적힌 날짜 글자를 "
        f"바꾸지 말고 앞뒤 {NEWSROOM_DATE_CONTEXT_CHARS}자 문맥과 함께 돌려주세요. "
        '다른 설명 없이 {"date_text":"...","context":"..."} JSON 객체만 답하세요.\n'
        "[페이지 글자]\n"
        f"{limited}"
    )


def _switch_is_enabled(ai_enabled: NewsroomDateSwitch) -> bool:
    return ai_enabled() is True if callable(ai_enabled) else ai_enabled is True


def _cached_result(
    payload: Mapping[str, str] | None,
    *,
    page_text: str,
    today: date,
) -> NewsroomDateResult | None:
    if payload is None:
        return None
    cached_date = str(payload.get("date", "")).strip()
    origin = str(payload.get("origin", "")).strip()
    context = str(payload.get("context", ""))
    try:
        parsed = date.fromisoformat(cached_date)
    except ValueError:
        return None
    if origin not in NEWSROOM_DATE_ORIGINS or parsed > today:
        return None
    normalized_page = _normalize_whitespace(page_text)
    normalized_context = _normalize_whitespace(context)
    if not normalized_context or normalized_context not in normalized_page:
        return None
    if cached_date not in _dates_in_text(normalized_context):
        return None
    if _parse_date_text(normalized_context) is not None:
        return None
    return NewsroomDateResult(cached_date, origin, 4, "cache_hit")


def _context_matches_date(
    *,
    page_text: str,
    date_text: str,
    context: str,
) -> bool:
    context_start = page_text.find(context)
    date_in_context = context.find(date_text)
    if context_start < 0 or date_in_context < 0:
        return False
    absolute_date_start = context_start + date_in_context
    absolute_date_end = absolute_date_start + len(date_text)
    has_left_context = bool(context[:date_in_context])
    has_right_context = bool(context[date_in_context + len(date_text):])
    return (
        (absolute_date_start == 0 or has_left_context)
        and (absolute_date_end == len(page_text) or has_right_context)
    )


def _ai_response_result(
    raw_response: object,
    *,
    page_text: str,
    today: date,
) -> tuple[NewsroomDateResult, str]:
    try:
        decoded = json.loads(raw_response) if isinstance(raw_response, str) else None
    except (TypeError, ValueError):
        decoded = None
    if not isinstance(decoded, dict):
        return NewsroomDateResult("", "", 3, "ai_unparseable"), ""
    date_text = decoded.get("date_text")
    context = decoded.get("context")
    if not isinstance(date_text, str) or not isinstance(context, str):
        return NewsroomDateResult("", "", 3, "ai_unparseable"), ""

    normalized_page = _normalize_whitespace(page_text)
    normalized_date = _normalize_whitespace(date_text)
    normalized_context = _normalize_whitespace(context)
    if not normalized_date or normalized_date not in normalized_page:
        return NewsroomDateResult("", "", 3, "ai_not_in_page"), ""
    if (
        not normalized_context
        or not _context_matches_date(
            page_text=normalized_page,
            date_text=normalized_date,
            context=normalized_context,
        )
    ):
        return NewsroomDateResult("", "", 3, "ai_context_mismatch"), ""
    parsed = _parse_date_text(normalized_date)
    if parsed is None:
        return NewsroomDateResult("", "", 3, "ai_unparseable"), ""
    if date.fromisoformat(parsed) > today:
        return NewsroomDateResult("", "", 3, "ai_future_date"), ""
    return NewsroomDateResult(parsed, "ai_assisted", 3, "ai_verified"), context


def extract_newsroom_date(
    raw_html: str,
    *,
    ai_call: NewsroomDateAiCall | None = None,
    ai_enabled: NewsroomDateSwitch = False,
    cache_load: NewsroomDateCacheLoad | None = None,
    cache_save: NewsroomDateCacheSave | None = None,
    today: date | None = None,
) -> NewsroomDateResult:
    """뉴스룸 HTML에서 검증된 날짜 한 건만 반환한다.

    순서는 위치 제한 프로그램 판정 → 목록 페이지 AI 예비 → 원문 존재 대조 →
    캐시 저장이다. 제목이 한 건 이하인 기사 페이지는 프로그램 단계 뒤 즉시
    끝내므로 스위치·AI·캐시를 조회하지 않는다.
    """

    found, title_count, page_text = _program_evidence(raw_html)
    if len(found) == 1:
        return NewsroomDateResult(found[0], "program", 1, "program_found")
    program_reason = "program_conflict" if len(found) > 1 else "not_found"
    if title_count <= NEWSROOM_DATE_SINGLE_ARTICLE_TITLE_COUNT:
        return NewsroomDateResult("", "", 1, program_reason)
    if not _switch_is_enabled(ai_enabled):
        reason = (
            "program_conflict"
            if program_reason == "program_conflict"
            else "ai_skipped_switch_off"
        )
        return NewsroomDateResult("", "", 2, reason)

    resolved_today = today or date.today()
    cache_key = newsroom_date_cache_key(page_text)
    if cache_load is not None:
        cached = _cached_result(
            cache_load(cache_key),
            page_text=page_text,
            today=resolved_today,
        )
        if cached is not None:
            return cached
    if ai_call is None:
        return NewsroomDateResult("", "", 2, program_reason)
    try:
        raw_response: object = ai_call(_ai_prompt(page_text))
    except Exception:
        return NewsroomDateResult("", "", 3, "ai_unparseable")
    result, context = _ai_response_result(
        raw_response,
        page_text=page_text,
        today=resolved_today,
    )
    if result.date and cache_save is not None:
        cache_save(
            cache_key,
            {
                "date": result.date,
                "origin": result.origin,
                "context": context,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    return result
