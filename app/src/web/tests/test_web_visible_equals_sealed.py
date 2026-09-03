"""결과 화면 본문에 «봉인 블록에 없는 문단»이 한 줄도 섞이지 않는지 본다.

★ 이 파일이 막는 것
  화면이 자기만 아는 문장을 한 줄 얹는 일이다. 기존 화면 시험은 «봉인 문장이
  나왔는가»(있음)와 «금지 문구가 없는가»(없음)만 봤다. 그래서 「추가 조언」
  같은 한 문단을 본문에 끼워 넣어도 아무 시험이 깨지지 않았다.

★ 그래서 여기서는 «정확히 같음»을 본다
  화면 본문 문단은 ``<span class="prose-text">`` 하나가 한 문단이다. 그 목록을
  장 순서 그대로 모아 봉인 블록의 문단 목록과 «길이까지» 맞댄다. 한 줄이라도
  더 그리면 목록 길이가 달라져 깨진다.

★ 채널 «모양»은 허용한다
  화면은 본문 속 ``[1]``을 작은 위첨자 링크로, 문장 끝 ``— 해석``을 둥근
  배지로 그린다. 표식을 없애는 게 아니라 모양만 바꾸는 것이므로, 맞대기 전에
  ``channel_neutral``로 그 차이만 걷어 낸다(규칙은 그 함수의 설명에 있다).

★ 재료·렌더 길은 다른 봉인 시험이 이미 지어 두었다. 같은 재료를 써야 「같은
  봉인에서 같은 글자가 나왔다」를 말할 수 있으므로 그대로 가져다 쓴다.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from src.features.export_pdf.tests.test_pdf_visible_equals_sealed import (
    assert_two_paragraphs_per_section,
    channel_neutral,
    sealed_paragraph_rows,
    sealed_two_paragraph_report,
)
from src.web.tests.test_three_channels_share_sealed_blocks import (
    _render_from_stored_delivery,
)


class _ProseTextCollector(HTMLParser):
    """장별 본문 문단(``span.prose-text``)의 «사람이 읽는 글자»를 순서대로 모은다.

    ★ 왜 태그를 직접 훑나 — 문단 안에 인용 링크(``a.ref``)와 해석 배지
      (``span.grade-tag``)가 «중첩»으로 들어간다. 정규식으로 잘라 내면 안쪽
      ``</span>``에서 먼저 끊겨 문단이 반토막 난다. 표준 parser로 여는 태그
      깊이를 세면 중첩이 몇 겹이든 문단 하나를 온전히 모은다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str]] = []
        self._cell = ""
        self._section_depth = 0
        self._prose_depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "section":
            if self._section_depth:
                self._section_depth += 1
            elif "report-section" in classes:
                self._section_depth = 1
                self._cell = attributes.get("data-report-cell") or ""
        if self._prose_depth:
            self._prose_depth += 1
            return
        if self._section_depth and tag == "span" and "prose-text" in classes:
            self._prose_depth = 1
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._prose_depth:
            self._prose_depth -= 1
            if self._prose_depth == 0:
                self.rows.append((self._cell, "".join(self._buffer)))
        elif tag == "section" and self._section_depth:
            self._section_depth -= 1
            if self._section_depth == 0:
                self._cell = ""

    def handle_data(self, data: str) -> None:
        if self._prose_depth:
            self._buffer.append(data)


def _prose_paragraphs_on_screen(body: str) -> list[tuple[str, str]]:
    parser = _ProseTextCollector()
    parser.feed(body)
    return parser.rows


def test_화면_본문_문단_목록은_봉인_문단_목록과_정확히_같다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = sealed_two_paragraph_report()
    projection = report.public_projection
    assert projection is not None
    assert_two_paragraphs_per_section(projection)

    body = _render_from_stored_delivery(
        report, monkeypatch, report_id="web-sealed-paragraph-parity"
    )

    on_screen = [
        (cell, channel_neutral(text))
        for cell, text in _prose_paragraphs_on_screen(body)
    ]
    sealed = [
        (cell, channel_neutral(text))
        for cell, _ordinal, text in sealed_paragraph_rows(projection)
    ]

    assert on_screen == sealed
