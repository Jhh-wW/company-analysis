"""결과 화면 본문에 «봉인 블록에 없는 문단»이 한 줄도 섞이지 않는지 본다.

★ 이 파일이 막는 것
  화면이 자기만 아는 문장을 한 줄 얹는 일이다. 기존 화면 시험은 «봉인 문장이
  나왔는가»(있음)와 «금지 문구가 없는가»(없음)만 봤다. 그래서 「추가 조언」
  같은 한 문단을 본문에 끼워 넣어도 아무 시험이 깨지지 않았다.

★ 그래서 여기서는 «정확히 같음»을 본다
  화면 본문은 ``<p class="prose">`` 한 상자가 한 문단이다. 장 안의 그 상자를
  순서대로 모아 (장, 문단 번호, 글자)로 봉인 목록과 «길이까지» 맞댄다. 문단
  상자를 한 줄이라도 더 그리면 목록이 길어져 깨진다.

★ 상자 «안»이 아니라 «상자»를 센다
  안쪽 ``span.prose-text``만 세면 그 span 없이 글자만 담은 문단 한 줄이 그물을
  통째로 빠져나간다. 같은 화면 파일에 실제로 그런 모양으로 문단을 그리는 갈래가
  있어 가상의 걱정이 아니다. 그래서 기준을 «문단 상자»로 잡는다.

★ 이 그물이 못 보는 것
  문단 상자가 «아닌» 태그로 장 안에 글자를 그리면(예: 안내 상자, 목록) 이
  목록에 잡히지 않는다. 남은 구멍이며, 이 파일은 «화면 본문 문단»에 대해서만
  「정확히 같음」을 말한다.

★ 채널 «모양»은 허용한다
  화면은 본문 속 ``[1]``을 작은 위첨자 링크로, 문장 끝 ``— 해석``을 둥근
  배지로 그린다. 표식을 없애는 게 아니라 모양만 바꾸는 것이므로, 맞대기 전에
  ``channel_neutral``로 그 차이만 걷어 낸다(규칙은 그 함수의 설명에 있다).

★ 재료·렌더 길은 다른 봉인 시험이 이미 지어 두었다. 같은 재료를 써야 「같은
  봉인에서 같은 글자가 나왔다」를 말할 수 있으므로 그대로 가져다 쓴다.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import NamedTuple

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


class ScreenParagraph(NamedTuple):
    """화면이 그린 본문 문단 하나."""

    cell: str
    """어느 장에서 나왔는지 (``data-report-cell``)."""

    number: str
    """장식용 문단 번호(``span.pno``)의 글자. 봉인이 준 값이다."""

    text: str
    """번호를 뺀 문단 본문 글자."""

    prose_text_spans: int
    """상자 안에 든 ``span.prose-text`` 개수. 모양이 무너졌는지 보는 값이다."""


class _ProseParagraphCollector(HTMLParser):
    """장별 본문 문단 상자(``p.prose``)를 순서 그대로 모은다.

    ★ 왜 상자를 기준으로 세나 — 안쪽 ``span.prose-text``만 세면 그 span 없이
      그린 문단이 안 잡힌다. 상자를 세면 «몇 문단을 그렸나»가 그대로 드러난다.

    ★ 상자 안 글자는 둘로만 가른다 — 장식용 문단 번호(``span.pno``)와 나머지
      본문이다. 번호는 봉인이 준 값이므로 버리지 않고 따로 모아 함께 맞댄다.

    ★ 중첩은 깊이로 센다 — 문단 안에 인용 링크(``a.ref``)와 해석 배지
      (``span.grade-tag``)가 중첩으로 들어간다. 정규식으로 잘라 내면 안쪽
      ``</span>``에서 먼저 끊겨 문단이 반토막 난다. 표준 parser로 여는 태그
      깊이를 세면 중첩이 몇 겹이든 문단 하나를 온전히 모은다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[ScreenParagraph] = []
        self._cell = ""
        self._section_depth = 0
        self._prose_depth = 0
        self._number_depth = 0
        self._number: list[str] = []
        self._body: list[str] = []
        self._prose_text_spans = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "section":
            if self._section_depth:
                self._section_depth += 1
            elif "report-section" in classes:
                self._section_depth = 1
                self._cell = attributes.get("data-report-cell") or ""
            return
        if self._prose_depth:
            self._prose_depth += 1
            if tag == "span" and "prose-text" in classes:
                self._prose_text_spans += 1
            if not self._number_depth and tag == "span" and "pno" in classes:
                self._number_depth = self._prose_depth
            return
        if self._section_depth and tag == "p" and "prose" in classes:
            self._prose_depth = 1
            self._number = []
            self._body = []
            self._prose_text_spans = 0

    def handle_endtag(self, tag: str) -> None:
        if self._prose_depth:
            if self._number_depth == self._prose_depth:
                self._number_depth = 0
            self._prose_depth -= 1
            if self._prose_depth == 0:
                self.paragraphs.append(
                    ScreenParagraph(
                        cell=self._cell,
                        number="".join(self._number),
                        text="".join(self._body),
                        prose_text_spans=self._prose_text_spans,
                    )
                )
            return
        if tag == "section" and self._section_depth:
            self._section_depth -= 1
            if self._section_depth == 0:
                self._cell = ""

    def handle_data(self, data: str) -> None:
        if not self._prose_depth:
            return
        if self._number_depth:
            self._number.append(data)
        else:
            self._body.append(data)


def _prose_paragraphs_on_screen(body: str) -> list[ScreenParagraph]:
    parser = _ProseParagraphCollector()
    parser.feed(body)
    return parser.paragraphs


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
    drawn = _prose_paragraphs_on_screen(body)

    on_screen = [
        (paragraph.cell, paragraph.number.strip(), channel_neutral(paragraph.text))
        for paragraph in drawn
    ]
    sealed = [
        (cell, ordinal.strip(), channel_neutral(text))
        for cell, ordinal, text in sealed_paragraph_rows(projection)
    ]

    assert on_screen == sealed

    # ★ 문단 상자마다 본문 span이 «하나씩» 들어 있어야 한다. 글자만 맞고 모양이
    #   무너지면(본문 span을 잃으면) 화면 스타일과 위첨자 인용이 함께 무너지는데,
    #   글자 비교만으로는 그 순간이 드러나지 않는다.
    assert [paragraph.prose_text_spans for paragraph in drawn] == [1] * len(sealed)
