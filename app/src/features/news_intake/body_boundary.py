"""명시적 보조 컴포넌트를 HTML 구조 단위로 제외한다. 네트워크는 쓰지 않는다."""
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

from src.features.news_intake import constants as c


def _auxiliary(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        value = " ".join((value or "").casefold().split())
        if name in c.BODY_COMPONENT_ATTRIBUTES:
            if any(token in c.BODY_AUXILIARY_COMPONENTS for token in value.replace("_", "-").split()):
                return True
        elif name == "aria-label" and value in c.BODY_AUXILIARY_LABELS:
            return True
    return False


def contains_excluded_text(text: str, excluded: set[str]) -> bool:
    """다른 폴백에 복사된 위젯 문장만 막으며 정상 AI 보도 단어는 검사하지 않는다."""
    compact = "".join(unescape(text).split())
    return any(value in compact for value in excluded)


@dataclass
class _Frame:
    tag: str
    blocked: bool
    auxiliary: bool
    text_start: int


class BodyBoundaryParser(HTMLParser):
    """중첩·void·self-closing 요소의 수명을 구분해 뒤쪽 정상 본문을 보존한다."""
    def __init__(self, *, excluded_tags: frozenset[str] = frozenset()) -> None:
        super().__init__(convert_charrefs=False)
        self.excluded_tags = excluded_tags
        self.stack: list[_Frame] = []
        self.parts: list[str] = []
        self.visible_chunks: list[str] = []
        self.excluded_text: set[str] = set()
        self.auxiliary_chunks: list[str] = []

    def _remember(self, value: str) -> None:
        normalized = " ".join(unescape(value).split())
        # 본문 허용 길이와 같은 단위로 기록한 뒤 일치 비교용 공백만 제거한다.
        if len(normalized) >= c.BODY_MIN_CHARS:
            self.excluded_text.add("".join(normalized.split()))

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, closed: bool) -> None:
        parent = self.stack[-1] if self.stack else None
        auxiliary = bool(parent and parent.auxiliary) or _auxiliary(attrs)
        blocked = bool(parent and parent.blocked) or auxiliary or tag in self.excluded_tags
        if not blocked:
            self.parts.append(self.get_starttag_text() or "")
            if tag in c.BODY_TEXT_BLOCK_TAGS:
                self.visible_chunks.append(" ")
        elif not parent or not parent.blocked:
            # 양옆 본문이 하나의 단어로 붙지 않게 제외 구획의 경계를 남긴다.
            self.parts.append(" ")
        if not closed and tag not in c.BODY_VOID_TAGS:
            self.stack.append(_Frame(tag, blocked, auxiliary, len(self.auxiliary_chunks)))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=True)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag != tag:
                continue
            frame = self.stack[index]
            for closed in self.stack[index:]:
                if closed.auxiliary:
                    self._remember(" ".join(self.auxiliary_chunks[closed.text_start:]))
            del self.stack[index:]
            if not frame.blocked:
                self.parts.append(f"</{tag}>")
                if tag in c.BODY_TEXT_BLOCK_TAGS:
                    self.visible_chunks.append(" ")
            return

    def _data(self, raw: str, visible: str) -> None:
        parent = self.stack[-1] if self.stack else None
        if parent and parent.blocked:
            if parent.auxiliary and not any(frame.tag in c.BODY_NON_TEXT_TAGS for frame in self.stack):
                self.auxiliary_chunks.append(visible)
                self._remember(visible)
            return
        self.parts.append(raw)
        if not any(frame.tag in c.BODY_NON_TEXT_TAGS for frame in self.stack):
            self.visible_chunks.append(visible)

    def handle_data(self, data: str) -> None:
        self._data(data, data)

    def handle_entityref(self, name: str) -> None:
        raw = f"&{name};"
        self._data(raw, unescape(raw))

    def handle_charref(self, name: str) -> None:
        raw = f"&#{name};"
        self._data(raw, unescape(raw))

    def close(self) -> None:
        super().close()
        for frame in self.stack:
            if frame.auxiliary:
                self._remember(" ".join(self.auxiliary_chunks[frame.text_start:]))

    @property
    def html(self) -> str:
        return "".join(self.parts)


def body_boundary(raw_html: str, *, excluded_tags: frozenset[str] = frozenset()) -> BodyBoundaryParser:
    parser = BodyBoundaryParser(excluded_tags=excluded_tags)
    parser.feed(raw_html)
    parser.close()
    return parser


def metadata_body_text(value: str) -> str:
    """articleBody 안의 HTML도 같은 경계를 거친 뒤 글자로 읽는다."""
    parser = body_boundary(value, excluded_tags=c.BODY_NON_TEXT_TAGS)
    return " ".join("".join(parser.visible_chunks).split())
