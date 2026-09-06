"""저장된 공시 원문(HTML/XML)에서 표를 행·칸 구조 그대로 읽는 모듈.

파이프라인이 쓰는 ``read_filing_text``는 태그를 지우고 **모든 공백을 한 칸으로
접는다**. 그래서 표의 행·칸 경계가 사라지고, 칸 구분으로 이름을 뽑던 규칙이
실제 공시에서 후보를 하나도 못 만들었다. 여기서는 원문 파일을 다시 열어
``TABLE``/``TR``/``TD`` 구조를 그대로 읽는다.

표준 라이브러리(:mod:`html.parser`)만 쓴다 — 공시 원문은 XML 선언을 달고도
이름 안의 ``&``처럼 해제되지 않은 문자를 그대로 담고 있어, 엄격한 XML 파서로는
열리지 않는다(실측).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from .constants import (
    FILING_TEXT_ENCODINGS,
    MAX_TABLE_CELL_CHARS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_HEADING_CHARS,
    MAX_TABLE_ROWS,
    MAX_TABLE_SPAN,
    MAX_TABLE_TITLE_CHARS,
    MAX_TABLE_TITLE_LOOKBACK_LINES,
    TABLE_CELL_TAGS,
    TABLE_HEADING_RE,
    TABLE_LINE_BREAK_TAGS,
    TABLE_ROW_TAG,
    TABLE_TAG,
    WHITESPACE_RE,
)


@dataclass(frozen=True, slots=True)
class FilingTable:
    """공시 표 하나. 모든 행은 ROWSPAN/COLSPAN을 채운 뒤라 열 수가 같다."""

    title: str
    rows: tuple[tuple[str, ...], ...]


def _collapse(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip()[:MAX_TABLE_CELL_CHARS]


def _span(raw: str | None) -> int:
    """ROWSPAN/COLSPAN 값을 읽는다.

    이상한 값은 1로 보고, 아주 큰 값도 ``MAX_TABLE_SPAN``에서 끊는다 — 어긋난
    마크업 한 칸이 표 전체를 채우며 메모리·시간을 먹지 못하게 하려는 것이다.
    """

    if not raw:
        return 1
    try:
        span = int(str(raw).strip())
    except ValueError:
        return 1
    if span < 1:
        return 1
    return min(span, MAX_TABLE_SPAN)


class _TableCollector(HTMLParser):
    """``TABLE`` 안의 칸만 모으고, 표 밖 글자는 제목 후보로만 쌓는다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[FilingTable] = []
        self._outside: list[str] = []
        self._previous_title: str = ""
        # 표가 칸 안에 또 있을 수 있어 스택으로 둔다 — 안쪽 표도 별도 표로 남긴다.
        self._stack: list[dict[str, object]] = []

    # -- 표 뼈대 ---------------------------------------------------------
    def _open_table(self) -> None:
        self._stack.append(
            {
                "cells": {},
                "occupied": set(),
                "row": -1,
                "column": 0,
                "width": 0,
                "title": self._take_title(),
                "cell": None,
            }
        )

    def _close_table(self) -> None:
        if not self._stack:
            return
        self._flush_cell()
        table = self._stack.pop()
        rows = self._materialize(table)
        if rows:
            title = str(table["title"])
            self.tables.append(FilingTable(title=title, rows=rows))
            if title:
                self._previous_title = title
        self._outside.clear()

    @staticmethod
    def _materialize(table: dict[str, object]) -> tuple[tuple[str, ...], ...]:
        cells: dict[tuple[int, int], str] = table["cells"]  # type: ignore[assignment]
        if not cells:
            return ()
        height = min(int(table["row"]) + 1, MAX_TABLE_ROWS)
        width = min(int(table["width"]), MAX_TABLE_COLUMNS)
        if height < 1 or width < 1:
            return ()
        return tuple(
            tuple(cells.get((row, column), "") for column in range(width))
            for row in range(height)
        )

    def _take_title(self) -> str:
        """표 바로 앞 글자의 마지막 줄을 제목으로 본다.

        마지막 줄이 문단처럼 길면 제목이 아니므로, 위쪽에서 절 표제 모양의
        짧은 줄을 찾는다. 표가 잇달아 나오면(예: 단위 표 다음의 본표) 사이에
        글자가 없어 제목을 새로 잡을 수 없다. 그때는 직전 표 제목을 물려받는다.
        """

        lines = [
            _collapse(line)
            for line in "".join(self._outside).splitlines()
            if line.strip()
        ]
        self._outside.clear()
        if not lines:
            return self._previous_title
        last = lines[-1]
        if len(last) <= MAX_TABLE_HEADING_CHARS:
            return last[:MAX_TABLE_TITLE_CHARS]
        for line in reversed(lines[-MAX_TABLE_TITLE_LOOKBACK_LINES:]):
            if len(line) <= MAX_TABLE_HEADING_CHARS and TABLE_HEADING_RE.match(line):
                return line
        return last[:MAX_TABLE_TITLE_CHARS]

    # -- 칸 ---------------------------------------------------------------
    def _flush_cell(self) -> None:
        if not self._stack:
            return
        table = self._stack[-1]
        cell = table["cell"]
        if cell is None:
            return
        table["cell"] = None
        row = int(cell["row"])  # type: ignore[index]
        column = int(cell["column"])  # type: ignore[index]
        rowspan = int(cell["rowspan"])  # type: ignore[index]
        colspan = int(cell["colspan"])  # type: ignore[index]
        text = _collapse("".join(cell["chunks"]))  # type: ignore[index]
        cells: dict[tuple[int, int], str] = table["cells"]  # type: ignore[assignment]
        occupied: set[tuple[int, int]] = table["occupied"]  # type: ignore[assignment]
        for offset_row in range(rowspan):
            if row + offset_row >= MAX_TABLE_ROWS:
                break
            for offset_column in range(colspan):
                if column + offset_column >= MAX_TABLE_COLUMNS:
                    break
                spot = (row + offset_row, column + offset_column)
                occupied.add(spot)
                # ROWSPAN/COLSPAN은 값을 아래·오른쪽으로 «채운다» — 그래야
                # 회사명 칸이 빈 행에서도 어느 회사인지 알 수 있다.
                cells[spot] = text
        table["column"] = min(column + colspan, MAX_TABLE_COLUMNS)
        table["width"] = max(int(table["width"]), int(table["column"]))

    def _open_cell(self, attrs: list[tuple[str, str | None]]) -> None:
        if not self._stack:
            return
        self._flush_cell()
        table = self._stack[-1]
        if int(table["row"]) < 0:
            # TR 없이 바로 나온 칸도 한 행으로 받는다.
            table["row"] = 0
            table["column"] = 0
        attributes = {key.lower(): value for key, value in attrs}
        row = int(table["row"])
        column = int(table["column"])
        occupied: set[tuple[int, int]] = table["occupied"]  # type: ignore[assignment]
        while (row, column) in occupied and column < MAX_TABLE_COLUMNS:
            column += 1
        table["cell"] = {
            "row": row,
            "column": column,
            "rowspan": _span(attributes.get("rowspan")),
            "colspan": _span(attributes.get("colspan")),
            "chunks": [],
        }

    # -- HTMLParser 갈고리 -------------------------------------------------
    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == TABLE_TAG:
            self._flush_cell()
            self._open_table()
            return
        if not self._stack:
            if tag in TABLE_LINE_BREAK_TAGS:
                self._outside.append("\n")
            return
        if tag == TABLE_ROW_TAG:
            self._flush_cell()
            table = self._stack[-1]
            table["row"] = int(table["row"]) + 1
            table["column"] = 0
            return
        if tag in TABLE_CELL_TAGS:
            self._open_cell(attrs)
            return
        if tag in TABLE_LINE_BREAK_TAGS:
            cell = self._stack[-1]["cell"]
            if cell is not None:
                # 칸 안의 여러 문단은 한 칸의 이어진 글자로 본다.
                cell["chunks"].append(" ")  # type: ignore[index]

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in TABLE_LINE_BREAK_TAGS:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == TABLE_TAG:
            self._close_table()
            return
        if not self._stack:
            if tag in TABLE_LINE_BREAK_TAGS:
                self._outside.append("\n")
            return
        if tag in TABLE_CELL_TAGS:
            self._flush_cell()
        elif tag in TABLE_LINE_BREAK_TAGS:
            cell = self._stack[-1]["cell"]
            if cell is not None:
                cell["chunks"].append(" ")  # type: ignore[index]

    def handle_data(self, data: str) -> None:
        if not self._stack:
            self._outside.append(data)
            return
        cell = self._stack[-1]["cell"]
        if cell is not None:
            cell["chunks"].append(data)  # type: ignore[index]

    def close(self) -> None:  # noqa: D102 - 부모 설명 그대로
        super().close()
        # 닫히지 않은 표도 버리지 않는다 — 공시 원문은 종종 태그가 어긋난다.
        while self._stack:
            self._close_table()


def _decode(raw: bytes) -> str:
    """``read_filing_text``와 같은 순서로 해독한다(utf-8 → cp949 → euc-kr)."""

    for encoding in FILING_TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_filing_tables(markup: str) -> tuple[FilingTable, ...]:
    """HTML/XML 글자에서 표를 읽는다. 표가 없으면 빈 tuple."""

    if not isinstance(markup, str) or not markup.strip():
        return ()
    collector = _TableCollector()
    try:
        collector.feed(markup)
        collector.close()
    except AssertionError:
        # html.parser는 깨진 마크업에서 내부 단언으로 죽을 수 있다.
        # 표를 못 읽는 것은 조사 실패가 아니므로 여기까지 읽은 표만 쓴다.
        pass
    return tuple(collector.tables)


def read_filing_tables(path: str | Path) -> tuple[FilingTable, ...]:
    """저장된 공시 원문 파일에서 표를 읽는다.

    파일이 없거나 읽을 수 없으면 빈 tuple을 돌려준다 — 호출부는 이때
    공백 접힌 평문 파서로 되돌아간다. 이름 표는 «있으면 좋은» 추가물이므로
    여기서 예외를 올려 조사 전체를 멈추게 하지 않는다.

    ``ValueError``도 함께 닫는다 — 경로에 널 문자가 섞이면 ``OSError``가
    아니라 ``ValueError``가 난다.
    """

    if not path:
        return ()
    try:
        raw = Path(path).read_bytes()
    except (OSError, ValueError):
        return ()
    return parse_filing_tables(_decode(raw))


__all__ = ["FilingTable", "parse_filing_tables", "read_filing_tables"]
