"""DART의 닫힌 표만 행·셀 순서를 보존한 한 문단으로 바꾼다.

표의 원문 숫자와 셀 텍스트를 계산·합성하지 않는다. 불완전한 구조는 새 근거로
승격하지 않고 호출자의 기존 태그-개행 변환에 그대로 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from features.evidence_collection import table_text_constants as c


_END_TABLE = re.compile(r"</\s*table\s*>", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class TableReplacement:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Cell:
    text: str
    rowspan: int
    colspan: int


@dataclass
class _Table:
    start: int
    rows: list[list[_Cell]] = field(default_factory=list)
    row: list[_Cell] | None = None
    cell_parts: list[str] | None = None
    cell_tag: str = ""
    cell_last_nonspace: str = ""
    inline_boundary: bool = False
    cell_span: tuple[int, int] = (1, 1)
    caption_parts: list[str] | None = None
    caption: str = ""
    cell_count: int = 0
    stored_chars: int = 0
    invalid: bool = False


def _span(attrs: list[tuple[str, str | None]], key: str) -> int | None:
    values = [value for name, value in attrs if name == key]
    if len(values) > 1:
        return None
    if not values:
        return 1
    value = values[0]
    if value is None or not value.isdecimal() or len(value) > 2:
        return None
    number = int(value)
    return number if 1 <= number <= c.MAX_CELL_SPAN else None


def _compact(parts: list[str]) -> str:
    return _SPACE.sub(" ", "".join(parts)).strip()


def _owned_grid(rows: list[list[_Cell]], *, max_chars: int) -> list[list[str]] | None:
    """명시 rowspan·colspan이 소유한 격자에 원 셀 텍스트만 놓는다."""
    future: dict[int, tuple[str, int]] = {}
    width: int | None = None
    grid: list[list[str]] = []
    owned_chars = 0
    for row in rows:
        occupied = {column: value for column, (value, _) in future.items()}
        next_future = {
            column: (value, remaining - 1)
            for column, (value, remaining) in future.items() if remaining > 1
        }
        column = 0
        for cell in row:
            while column in occupied:
                column += 1
            if column + cell.colspan > c.MAX_TABLE_COLUMNS:
                return None
            for used in range(column, column + cell.colspan):
                if used in occupied:
                    return None
                occupied[used] = cell.text
                if cell.rowspan > 1:
                    next_future[used] = (cell.text, cell.rowspan - 1)
            column += cell.colspan
        if not occupied:
            return None
        row_width = max(occupied) + 1
        if set(occupied) != set(range(row_width)):
            return None
        if width is None:
            width = row_width
        elif row_width != width:
            return None
        if len(grid) * row_width + row_width > c.MAX_TABLE_CELLS:
            return None
        expanded = [occupied[index] for index in range(row_width)]
        owned_chars += sum(map(len, expanded))
        if owned_chars > max_chars:
            return None
        grid.append(expanded)
        future = next_future
    return grid if not future else None


class _TableParser(HTMLParser):
    def __init__(self, document: str, *, max_window_chars: int) -> None:
        super().__init__(convert_charrefs=False)
        self.document = document
        self.line_starts = [0, *(match.end() for match in re.finditer("\n", document))]
        self.max_window_chars = max_window_chars
        self.depth = 0
        self.table: _Table | None = None
        self.replacements: list[TableReplacement] = []
        self.table_count = 0
        self.replacement_chars = 0
        self.over_budget = False

    def _offset(self) -> int:
        line, column = self.getpos()
        if line < 1 or line > len(self.line_starts):
            raise ValueError("표 원문 좌표가 범위를 벗어났습니다")
        offset = self.line_starts[line - 1] + column
        if offset < 0 or offset > len(self.document):
            raise ValueError("표 원문 좌표가 범위를 벗어났습니다")
        return offset

    def _within_table_limit(self, table: _Table) -> bool:
        if self._offset() - table.start > c.MAX_TABLE_MARKUP_CHARS:
            table.invalid = True
        return not table.invalid

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.table_count += 1
                self.table = _Table(self._offset())
            elif self.table is not None:
                self.table.invalid = True
            return
        table = self.table
        if table is None or self.depth != 1 or not self._within_table_limit(table):
            return
        if tag == "tr":
            if table.row is not None or table.cell_parts is not None or table.caption_parts is not None:
                table.invalid = True
            else:
                table.row = []
        elif tag in {"td", "th"}:
            if table.row is None or table.cell_parts is not None:
                table.invalid = True
                return
            rowspan = _span(attrs, "rowspan")
            colspan = _span(attrs, "colspan")
            if rowspan is None or colspan is None:
                table.invalid = True
                return
            table.cell_span = (rowspan, colspan)
            table.cell_parts = []
            table.cell_tag = tag
            table.cell_last_nonspace = ""
            table.inline_boundary = False
        elif tag == "caption":
            if table.row is not None or table.caption_parts is not None or table.caption:
                table.invalid = True
            else:
                table.caption_parts = []
        elif tag in {"script", "style"}:
            table.invalid = True
        elif table.cell_parts is not None:
            if tag in c.CELL_LINE_BREAK_TAGS:
                table.cell_parts.append(" ")
            else:
                table.inline_boundary = True
        elif table.caption_parts is not None:
            table.caption_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self.depth == 0:
                return
            self.depth -= 1
            if self.depth == 0:
                self._finish_table()
            return
        table = self.table
        if table is None or self.depth != 1 or not self._within_table_limit(table):
            return
        if tag in {"td", "th"}:
            if table.cell_parts is None or table.row is None or tag != table.cell_tag:
                table.invalid = True
                return
            table.row.append(_Cell(_compact(table.cell_parts), *table.cell_span))
            table.cell_count += 1
            table.cell_parts = None
            table.cell_tag = ""
            table.cell_last_nonspace = ""
            table.inline_boundary = False
            if table.cell_count > c.MAX_TABLE_CELLS:
                table.invalid = True
        elif tag == "tr":
            if table.row is None or table.cell_parts is not None:
                table.invalid = True
                return
            table.rows.append(table.row)
            table.row = None
            if len(table.rows) > c.MAX_TABLE_ROWS:
                table.invalid = True
        elif tag == "caption":
            if table.caption_parts is None:
                table.invalid = True
            else:
                table.caption = _compact(table.caption_parts)
                table.caption_parts = None
        elif table.cell_parts is not None:
            if tag in c.CELL_LINE_BREAK_TAGS:
                table.cell_parts.append(" ")
            else:
                table.inline_boundary = True
        elif table.caption_parts is not None:
            table.caption_parts.append(" ")

    def handle_data(self, data: str) -> None:
        table = self.table
        if table is None or self.depth != 1 or not self._within_table_limit(table):
            return
        if table.cell_parts is not None:
            if table.inline_boundary and data:
                following = data.lstrip()
                # 장식 태그 양쪽의 숫자·수치기호가 맞닿으면 공백도 별개의
                # 두 수치처럼 읽힐 수 있어 표 전체를 구조화하지 않는다.
                if table.cell_last_nonspace and following and (
                    table.cell_last_nonspace.isdigit() or following[0].isdigit()
                    or table.cell_last_nonspace in c.NUMERIC_JOIN_CHARS
                    or following[0] in c.NUMERIC_JOIN_CHARS
                ):
                    table.invalid = True
                    return
            table.stored_chars += len(data)
            if table.stored_chars > self.max_window_chars:
                table.invalid = True
                return
            table.cell_parts.append(data)
            if data.rstrip():
                table.cell_last_nonspace = data.rstrip()[-1]
            table.inline_boundary = False
        elif table.caption_parts is not None:
            table.stored_chars += len(data)
            if table.stored_chars > self.max_window_chars:
                table.invalid = True
                return
            table.caption_parts.append(data)
        elif data.strip():
            # 표 안이지만 셀·caption 밖의 조건/각주를 잃으면 새 문맥이 된다.
            table.invalid = True

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self.table is not None:
            self.table.invalid = True

    def _finish_table(self) -> None:
        table = self.table
        self.table = None
        if (table is None or table.invalid or self.over_budget
                or table.row is not None or table.cell_parts is not None):
            return
        if table.caption_parts is not None or not table.rows:
            return
        grid = _owned_grid(table.rows, max_chars=self.max_window_chars)
        if grid is None:
            return
        end_match = _END_TABLE.match(self.document, self._offset())
        if end_match is None or end_match.end() - table.start > c.MAX_TABLE_MARKUP_CHARS:
            return
        row_texts = [" | ".join(row) for row in grid]
        body = " ; ".join(([table.caption] if table.caption else []) + row_texts)
        if not body or len(body) > self.max_window_chars:
            return
        if (len(self.replacements) >= c.MAX_REPLACEMENTS_PER_DOCUMENT
                or self.replacement_chars + len(body) > c.MAX_REPLACEMENT_CHARS_PER_DOCUMENT):
            self.over_budget = True
            self.replacements.clear()
            return
        self.replacement_chars += len(body)
        self.replacements.append(TableReplacement(table.start, end_match.end(), body))


def table_replacements(document: str, *, max_window_chars: int) -> tuple[TableReplacement, ...]:
    """완결·직사각형·단일창 표만 선택한다. 나머지는 기존 변환으로 되돌린다."""
    parser = _TableParser(document, max_window_chars=max_window_chars)
    try:
        parser.feed(document)
        parser.close()
    except (ValueError, AssertionError):
        return ()
    if parser.table_count > c.MAX_TABLES_PER_DOCUMENT or parser.over_budget:
        return ()
    return tuple(parser.replacements)
