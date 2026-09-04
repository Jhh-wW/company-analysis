"""매출 구성 비중을 공시에서 뜯어 표로 만든다.

★★ **베껴 오는 것뿐이다.** 사업보고서가 비중을 이미 계산해 놓았으므로
  우리는 더하지도 나누지도 않는다. 지어낼 자리가 아예 없다.
⚠️ 비중을 «우리가» 계산하면 반올림 규칙이 공시와 달라져 합이 100%가 안 맞는다.

★ 시계도 네트워크도 AI도 없다. 전부 순수 함수라 통째로 시험할 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, TypedDict

from src.features.revenuemix.constants import (
    HEADERS,
    KNOWN_TABLE_HEADS,
    MAX_ROWS,
    PRODUCT_CAPTION,
    PRODUCT_HEADS,
    REGION_CAPTION,
    REGION_HEADS,
    ROW_RE,
    SCAN_CHARS,
    SUBTOTAL_WORDS,
)
from src.shared.revenue_table_provenance import (
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    RevenueAxis,
    build_revenue_row_evidence,
    displayed_percent_total_is_complete,
    is_revenue_total_name,
    normalize_revenue_name,
)

#: 연도 — 「2025년 제21기」에서 앞의 네 자리.
_YEAR_RE = re.compile(r"(20\d{2})\s*년")


def clean_name(raw: str) -> str:
    """행 이름에서 표 머리말·군더더기를 지운다.

    ★ 표가 «한 줄로 눌린» 글이라 머리말(「매 출 액」·「비 중」)이
      다음 행의 이름 앞에 그대로 붙어 온다. 그대로 두면 표가 못 읽는 글이 된다.
    """
    name = normalize_revenue_name(raw)
    # ★ 여기서 자르지 않는다 (제품 결정) — 화면·PDF가 줄바꿈으로 흘려 받는다.
    #   ROW_RE 캡처 그룹이 이미 원본을 40자로 묶어 두므로 별도 상한이 없어도
    #   이름이 무한정 길어지지 않는다 (constants.MAX_NAME_CHARS 자리의 주석 참조).
    return name


def _is(name: str, words: tuple[str, ...]) -> bool:
    compact = re.sub(r"\s+", "", name)
    return any(compact == re.sub(r"\s+", "", word) for word in words)


#: 표 «머리말»의 마지막 칸 — 여기까지 잘라 낸다.
#: ★ 왜 필요한가 — 표가 한 줄로 눌려 있어 머리말(「매 출 액 비 중 …」)이
#:   첫 행 이름 앞에 그대로 붙는다. 실측 — 첫 행이 「**액** 음반/음원 …」으로 나왔다.
_HEADER_TAIL_RE = re.compile(r"비\s*중")

#: 머리말을 찾을 범위(글자). 이보다 뒤의 「비중」은 머리말이 아니라 본문이다.
_HEADER_ZONE: int = 320


@dataclass(frozen=True)
class _Block:
    header: str
    rows_text: str
    rows_start: int


@dataclass(frozen=True)
class _HeadingOccurrence:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _SourceRow:
    public: tuple[str, str, str]
    raw_match: str
    start: int
    end: int
    field_spans: dict[str, tuple[int, int]]
    source_index: int


@dataclass(frozen=True)
class _ParsedRows:
    rows: tuple[_SourceRow, ...]
    total: Optional[_SourceRow]
    overflow: bool


class RevenueTablePayload(TypedDict):
    """생산자가 보존하는 매출 구성표 transport.

    ``axis``는 캡션에서 나중에 다시 추측하는 표시값이 아니라 원문 표제를
    선택한 순간 확정되는 필수 자료형이다. AI 전 binder가 이 값과 봉인 근거를
    대조한 뒤 기존 ReportTable 호환 경계에서는 제거한다.
    """

    axis: RevenueAxis
    caption: str
    headers: list[str]
    rows: list[list[str]]
    cite: str
    raw_rows: list[list[str]]
    evidence_rows: list[str]


def _heading_occurrences(
    filing_text: str, heads: tuple[str, ...]
) -> tuple[_HeadingOccurrence, ...]:
    """표제의 모든 occurrence를 원문 순서로 돌려준다.

    ``str.find`` 한 번만 쓰면 목차에 나온 첫 표제를 실제 표로 오인한다. 또한
    제품표와 지역표가 짧게 붙으면 고정 320자 머리말 창 안에 두 표의 ``비중``이
    함께 들어간다. 모든 알려진 표제를 먼저 좌표로 만든 뒤 다음 좌표에서 현재
    후보를 자르면 두 문제가 같은 원인(경계 없는 고정 길이 scan)에서 해결된다.
    """

    occurrences: dict[tuple[int, int], _HeadingOccurrence] = {}
    for head in dict.fromkeys(heads):
        start = 0
        while True:
            index = filing_text.find(head, start)
            if index < 0:
                break
            occurrence = _HeadingOccurrence(index, index + len(head), head)
            occurrences[(occurrence.start, occurrence.end)] = occurrence
            start = index + 1
    return tuple(
        sorted(occurrences.values(), key=lambda item: (item.start, -len(item.text)))
    )


def _parsed_rows_are_complete(parsed: _ParsedRows) -> bool:
    if len(parsed.rows) < 2 or parsed.total is None or parsed.overflow:
        return False
    try:
        total_ratio = Decimal(parsed.total.public[2].removesuffix("%"))
    except (InvalidOperation, ValueError):
        return False
    return total_ratio == Decimal(100) and displayed_percent_total_is_complete(
        row.public[2] for row in parsed.rows
    )


def _find_block(filing_text: str, heads: tuple[str, ...]) -> _Block:
    """현재 표제·머리말·행·첫 합계를 같은 경계 안에서 찾아 돌려준다."""

    target_heads = tuple(dict.fromkeys(heads))
    all_occurrences = _heading_occurrences(
        filing_text,
        tuple(dict.fromkeys((*KNOWN_TABLE_HEADS, *target_heads))),
    )
    targets = tuple(
        occurrence
        for occurrence in all_occurrences
        if occurrence.text in target_heads
    )
    boundary_starts = tuple(sorted({item.start for item in all_occurrences}))

    for occurrence in targets:
        following = next(
            (start for start in boundary_starts if start > occurrence.start),
            len(filing_text),
        )
        block_end = min(occurrence.start + SCAN_CHARS, following)
        block = filing_text[occurrence.start:block_end]
        # 첫 행 금액 뒤의 본문 「비중」을 머리말 끝으로 고르지 않는다. ROW_RE는
        # 눌린 머리말 꼬리를 첫 행 이름과 함께 group(1)로 물 수 있으므로 match
        # 시작이 아니라 첫 금액(group 2) 직전까지 살펴야 마지막 열 머리말이
        # 후보에 남는다.
        first_row = ROW_RE.search(block)
        header_zone_end = min(
            len(block),
            _HEADER_ZONE,
            first_row.start(2) if first_row is not None else len(block),
        )
        headers = list(_HEADER_TAIL_RE.finditer(block[:header_zone_end]))
        if not headers:
            continue
        header_end = headers[-1].end()
        found = _Block(
            block[:header_end],
            block[header_end:],
            occurrence.start + header_end,
        )
        # 목차 occurrence나 제목만 있는 표를 건너뛰고, 첫 합계까지 한 후보 안에
        # 실제로 닫히는 표만 선택한다. build()도 같은 계약을 다시 확인한다.
        if _parsed_rows_are_complete(
            _parse_rows_with_source(found.rows_text, found.rows_start)
        ):
            return found
    return _Block("", "", 0)


def find_block(filing_text: str, heads: tuple[str, ...]) -> tuple[str, str]:
    """표제를 찾아 (머리말, 행 부분)으로 갈라 돌려준다. 못 찾으면 ("", "").

    ★ **머리말을 버리지 않고 돌려준다** — 연도(「2025년 제21기 (당 기)」)가
      거기 있기 때문이다. 그냥 잘라 버렸더니 뒤에 남은 「2023년」을 주워
      **2025년 숫자에 2023년 딱지**가 붙었다 (실측으로 잡힘).
    """
    found = _find_block(filing_text, heads)
    return found.header, found.rows_text


def _source_row(match: re.Match[str], rows_start: int, source_index: int) -> _SourceRow:
    raw_match = match.group(0)
    match_start = match.start()
    return _SourceRow(
        public=(clean_name(match.group(1)), match.group(2), f"{match.group(3)}%"),
        raw_match=raw_match,
        start=rows_start + match.start(),
        end=rows_start + match.end(),
        field_spans={
            "name": (match.start(1) - match_start, match.end(1) - match_start),
            "amount": (match.start(2) - match_start, match.end(2) - match_start),
            "ratio": (match.start(3) - match_start, match.end(3) - match_start),
        },
        source_index=source_index,
    )


def _parse_rows_with_source(block: str, rows_start: int = 0) -> _ParsedRows:
    rows: list[_SourceRow] = []
    total: Optional[_SourceRow] = None
    names: set[str] = set()
    overflow = False
    for source_index, match in enumerate(ROW_RE.finditer(block)):
        source_row = _source_row(match, rows_start, source_index)
        name = source_row.public[0]
        if not name or _is(name, SUBTOTAL_WORDS):
            continue
        if is_revenue_total_name(name):
            total = source_row
            break
        if name in names:
            continue
        names.add(name)
        if len(rows) >= MAX_ROWS:
            # 합계를 찾을 때까지 계속 읽되, 잘린 12행을 완성 표라고 내보내지 않는다.
            overflow = True
            continue
        rows.append(source_row)
    return _ParsedRows(tuple(rows), total, overflow)


def parse_rows(block: str) -> tuple[list[list[str]], Optional[list[str]]]:
    """덩어리에서 «첫 해» 행들을 뽑는다.

    Args:
        block: 표제 뒤 덩어리.

    Returns:
        ([[구분, 매출액, 비중]…], 합계행 또는 None).

    ★ 한 행에 3개 연도가 나란히 있다. **맨 앞(당기)만** 쓴다 —
      정규식이 이름 바로 뒤의 첫 「숫자 + 비중」 짝만 물게 되어 있다.
    ★ 소계는 **버린다.** 안 버리면 비중을 다 더했을 때 200%가 된다.
    """
    parsed = _parse_rows_with_source(block)
    rows = [list(row.public) for row in parsed.rows]
    # overflow면 합계를 숨긴다. 호출자는 이 결과를 완성 표로 오해할 수 없다.
    total = None if parsed.overflow or parsed.total is None else list(parsed.total.public)
    return rows, total


def year_of(block: str) -> str:
    """이 표가 몇 년치인지. 못 찾으면 빈 문자열."""
    m = _YEAR_RE.search(block)
    return m.group(1) if m else ""


def build(filing_text: str, cite: str = "") -> list[RevenueTablePayload]:
    """공시 원문에서 매출 구성 표를 만든다.

    Args:
        filing_text: 사업보고서 원문 전체.
        cite: 출처 표기.

    Returns:
        표 정의 목록 (`caption`·`headers`·`rows`·`cite`). 못 찾으면 빈 목록.

    ★ **못 찾으면 빈 목록이다.** 억지로 만들지 않는다 —
      비중을 우리가 계산해서 채우면 그 순간 공시와 어긋난다.
    """
    out: list[RevenueTablePayload] = []
    for axis, heads, caption in (
        (REVENUE_AXIS_PRODUCT, PRODUCT_HEADS, PRODUCT_CAPTION),
        (REVENUE_AXIS_REGION, REGION_HEADS, REGION_CAPTION),
    ):
        found = _find_block(filing_text, heads)
        if not found.header or not found.rows_text:
            continue
        parsed = _parse_rows_with_source(found.rows_text, found.rows_start)
        if not _parsed_rows_are_complete(parsed):
            continue                      # 한 줄·부분·넘친 표는 「구성」이 아니다
        assert parsed.total is not None  # complete 판정 뒤의 타입 좁히기
        source_rows = parsed.rows + (parsed.total,)
        rows = [list(row.public) for row in source_rows]
        header_start = found.rows_start - len(found.header)
        excerpt_start = header_start
        excerpt_end = parsed.total.end
        evidence_rows = [
            build_revenue_row_evidence(
                filing_text=filing_text,
                header_start=header_start,
                header_end=found.rows_start,
                excerpt_start=excerpt_start,
                excerpt_end=excerpt_end,
                row_raw_match=row.raw_match,
                row_start=row.start,
                row_end=row.end,
                row_field_spans=row.field_spans,
                source_index=row.source_index,
                selected_index=index,
                public_row=row.public,
                row_count=len(parsed.rows),
                total_raw_match=parsed.total.raw_match,
                total_start=parsed.total.start,
                total_end=parsed.total.end,
                total_field_spans=parsed.total.field_spans,
                selection=(
                    "explicit-total-row"
                    if row is parsed.total
                    else "first-current-period-pair"
                ),
                axis=axis,
            )
            for index, row in enumerate(source_rows)
        ]
        # ★ 연도는 «머리말»에서 읽는다 — 거기 첫 연도가 당기다.
        해 = year_of(found.header)
        out.append({
            "axis": axis,
            "caption": f"{caption}{f' ({해}년)' if 해 else ''}",
            "headers": list(HEADERS),
            "rows": rows,
            "cite": cite,
            # raw/evidence는 같은 행 index로 움직인다. ``axis``는 AI 전 결속
            # 전용 transport 필드라 검증 뒤 기존 ReportTable 경계에서 제거된다.
            "raw_rows": [list(row) for row in rows],
            "evidence_rows": evidence_rows,
        })
    return out
