"""화면 시험에서 HTML 꾸밈을 빼고 사람이 보는 글자만 꺼낸다."""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser


class _ClassCounter(HTMLParser):
    """렌더링된 태그의 class 토큰만 센다."""

    def __init__(self, wanted: str) -> None:
        super().__init__()
        self.wanted = wanted
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        classes = dict(attrs).get("class") or ""
        if self.wanted in classes.split():
            self.count += 1


def visible_text(body: str) -> str:
    """스크립트·스타일·태그를 빼고 HTML 기호를 원래 글자로 되돌린다.

    ★ 화면 문구 시험이 ``<strong>`` 같은 꾸밈 변화 때문에 깨지면 실제 기능을
      지키지 못한다. 사람이 읽는 글자만 비교해야 그 함정을 피한다.
    """
    without_code = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    return html_lib.unescape(re.sub(r"<[^>]+>", "\n", without_code))


def class_count(body: str, class_name: str) -> int:
    """렌더링된 HTML에서 특정 class를 가진 요소 수를 센다."""
    parser = _ClassCounter(class_name)
    parser.feed(body)
    return parser.count
