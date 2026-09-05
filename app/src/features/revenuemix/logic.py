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

from src.core.revenue_table_switch import revenue_table_v2_enabled
from src.features.revenuemix.constants import (
    HEADERS,
    KNOWN_TABLE_HEADS,
    MAX_ROWS,
    MULTI_YEAR_CAPTION_BY_AXIS,
    MULTI_YEAR_MAX_PERIODS,
    MULTI_YEAR_RATIO_HEADER_FORMAT,
    PRODUCT_CAPTION,
    PRODUCT_HEADS,
    RATIO_HEAD_RE,
    REGION_CAPTION,
    REGION_HEADS,
    ROW_RE,
    ROW_RE_V2,
    SCAN_CHARS,
    SUBTOTAL_WORDS,
    V2_FALLBACK_HEADER_CHARS,
    V2_HEADER_LOOKBACK,
    V2_HEADER_RUN_GAP,
    V2_MIN_ROWS,
    V2_REVENUE_WORDS,
    V2_ROW_SCAN_CHARS,
    V2_SCORE_CONSOLIDATED,
    V2_SCORE_KNOWN_HEAD,
    V2_SCORE_LOOKBACK,
    V2_SCORE_SEPARATE,
    V2_ZONE_BOUNDARY_RE,
)
from src.shared.revenue_table_provenance import (
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    REVENUE_HEADS_BY_AXIS,
    RevenueAxis,
    build_revenue_multi_year_row_evidence,
    build_revenue_row_evidence,
    displayed_percent_total_is_complete,
    is_revenue_total_name,
    is_revenue_total_name_v2,
    normalize_revenue_name,
    revenue_amounts_sum_to_total,
    revenue_percent_total_is_complete_v2,
    revenue_ratio_numeric_check,
    revenue_row_pattern_v2,
    revenue_signed_decimal,
    revenue_table_headers,
    revenue_text_axis,
    revenue_units_in,
    sha256_text,
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


@dataclass(frozen=True)
class _PeriodPair:
    year: str
    amount: str
    ratio: str
    amount_span: tuple[int, int]
    ratio_span: tuple[int, int]


@dataclass(frozen=True)
class _MultiYearSourceRow:
    name: str
    periods: tuple[_PeriodPair, ...]
    raw_match: str
    start: int
    end: int
    name_span: tuple[int, int]
    source_index: int


@dataclass(frozen=True)
class _ParsedMultiYearRows:
    rows: tuple[_MultiYearSourceRow, ...]
    total: Optional[_MultiYearSourceRow]
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


class MultiYearRevenueTablePayload(TypedDict):
    """구분 행과 오름차순 연도 비중 열을 가진 구성 변화 transport."""

    axis: RevenueAxis
    caption: str
    headers: list[str]
    rows: list[list[str]]
    cite: str
    raw_rows: list[list[str]]
    evidence_rows: list[str]
    numeric_checks: list[list[str]]
    raw_unit: str


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


def years_of(block: str) -> tuple[str, ...]:
    """머리말의 연도를 원문 순서로, 중복 없이 최대 3개 읽는다."""

    return tuple(dict.fromkeys(_YEAR_RE.findall(block)))[:MULTI_YEAR_MAX_PERIODS]


def year_of(block: str) -> str:
    """이 표의 첫 연도. 못 찾으면 빈 문자열."""

    years = years_of(block)
    return years[0] if years else ""


def _build_v1(filing_text: str, cite: str = "") -> list[RevenueTablePayload]:
    """표제 목록으로 표를 찾는 «옛» 경로. 스위치가 꺼져 있으면 이쪽이다.

    ⚠️ 여기는 손대지 않는다. 「스위치를 끄면 지금과 똑같다」를 증명하는 것이
      이 함수의 유일한 임무다.
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


# ══════════════════════════════════════════════════════════════════════
# v2 — 표 «모양»으로 찾기
# ══════════════════════════════════════════════════════════════════════
#
# 순서는 이렇다.
#   ① 비중 열 이름(비중·비율·구성비)이 붙은 자리를 전부 머리말 후보로 모은다
#   ② 머리말 앞뒤를 잘라 「머리말 + 행 + 합계」 한 덩어리로 만든다
#   ③ **행 금액의 합 == 합계 행**을 검산한다 (반올림 없음)
#   ④ 비중 열이 있으므로 비중 합도 100±0.5%p 안인지 본다
#   ⑤ 머리말·첫 행에서 축(제품/지역)을 읽는다
#   ⑥ 같은 내용의 표는 한 번만, 축마다 점수가 가장 높은 하나만 남긴다


class RevenueTableDiagnostics(TypedDict):
    """표를 못 찾았을 때 호출자가 「왜 없는지」 말할 수 있게 하는 기록.

    ★ 예외를 던지지 않는다 — 표는 보고서의 «덤»이라 없다고 조사를 멈추면 안
      된다. 대신 후보가 몇 개였고 무엇 때문에 떨어졌는지를 남긴다.
    """

    경로: str
    후보_표_수: int
    채택_표_수: int
    탈락_사유: dict[str, int]


class MultiYearRevenueTableDiagnostics(TypedDict):
    """다개년 후보와 연도별 검산 제외 결과."""

    경로: str
    후보_표_수: int
    채택_표_수: int
    탈락_사유: dict[str, int]
    제외_연도: dict[str, list[str]]


@dataclass(frozen=True)
class _V2Candidate:
    axis: RevenueAxis
    unit: str
    header_start: int
    header_end: int
    header: str
    parsed: _ParsedRows
    score: int
    fingerprint: str


@dataclass(frozen=True)
class _MultiYearCandidate:
    axis: RevenueAxis
    unit: str
    header_start: int
    header_end: int
    header: str
    years: tuple[str, ...]
    selected_years: tuple[str, ...]
    excluded_years: tuple[str, ...]
    parsed: _ParsedMultiYearRows
    score: int
    fingerprint: str


def _v2_header_runs(filing_text: str) -> tuple[tuple[int, int], ...]:
    """비중 열 이름이 잇달아 나오는 구간을 머리말 후보로 모은다."""

    runs: list[tuple[int, int]] = []
    current: Optional[tuple[int, int]] = None
    for match in RATIO_HEAD_RE.finditer(filing_text):
        if current is not None and match.start() - current[1] <= V2_HEADER_RUN_GAP:
            current = (current[0], match.end())
            continue
        if current is not None:
            runs.append(current)
        current = (match.start(), match.end())
    if current is not None:
        runs.append(current)
    return tuple(runs)


def _v2_zone_start(filing_text: str, ratio_start: int) -> int:
    """머리말이 시작하는 자리를 되짚는다.

    ★ 「(단위 : 백만원)」이 표 바로 앞에 1행짜리 표로 따로 실리는 것이 DART
      서식의 규칙이라(0단계 D-2) 이것이 가장 믿을 만한 경계다.
    """

    window_start = max(0, ratio_start - V2_HEADER_LOOKBACK)
    window = filing_text[window_start:ratio_start]
    boundaries = list(V2_ZONE_BOUNDARY_RE.finditer(window))
    if boundaries:
        return window_start + boundaries[-1].start()
    fallback = max(0, ratio_start - V2_FALLBACK_HEADER_CHARS)
    space = filing_text.find(" ", fallback, ratio_start)
    return space + 1 if space >= 0 else fallback


def _source_row_v2(
    match: re.Match[str], rows_start: int, source_index: int
) -> _SourceRow:
    """v2 행 하나를 원문 좌표째로 봉인한다.

    ★ 금액·비중의 «원문 표기»를 그대로 둔다 — 삼성전자 「△301,146」의 △를
      떼어내면 화면에서 음수가 양수로 보인다. 부호를 수로 바꾸는 것은 검산
      안에서만 한다.
    """

    match_start = match.start()
    return _SourceRow(
        public=(clean_name(match.group(1)), match.group(2), f"{match.group(3)}%"),
        raw_match=match.group(0),
        start=rows_start + match.start(),
        end=rows_start + match.end(),
        field_spans={
            "name": (match.start(1) - match_start, match.end(1) - match_start),
            "amount": (match.start(2) - match_start, match.end(2) - match_start),
            "ratio": (match.start(3) - match_start, match.end(3) - match_start),
        },
        source_index=source_index,
    )


def _parse_rows_v2(block: str, rows_start: int) -> _ParsedRows:
    """머리말 뒤 덩어리에서 구성 행과 «첫» 합계 행을 뽑는다.

    ⚠️ v1과 달리 «이름이 같다고 버리지 않는다» — 진영 매출표에는 「기타」가
      두 번 나오고(제품 기타·상품 기타), 하나를 버리면 금액 합이 합계와
      어긋나 표 전체가 떨어진다.
    """

    rows: list[_SourceRow] = []
    total: Optional[_SourceRow] = None
    overflow = False
    for source_index, match in enumerate(ROW_RE_V2.finditer(block)):
        source_row = _source_row_v2(match, rows_start, source_index)
        name = source_row.public[0]
        if not name or _is(name, SUBTOTAL_WORDS):
            continue
        if is_revenue_total_name_v2(name):
            total = source_row
            break
        if len(rows) >= MAX_ROWS:
            overflow = True
            continue
        rows.append(source_row)
    return _ParsedRows(tuple(rows), total, overflow)


def _source_row_multi_year(
    match: re.Match[str],
    rows_start: int,
    source_index: int,
    years: tuple[str, ...],
) -> _MultiYearSourceRow:
    """다개년 행 하나의 이름과 모든 금액·비중 쌍을 원문 좌표째 보존한다."""

    match_start = match.start()
    periods: list[_PeriodPair] = []
    for index, year in enumerate(years):
        amount_group = 2 + index * 2
        ratio_group = amount_group + 1
        periods.append(
            _PeriodPair(
                year=year,
                amount=match.group(amount_group),
                ratio=f"{match.group(ratio_group)}%",
                amount_span=(
                    match.start(amount_group) - match_start,
                    match.end(amount_group) - match_start,
                ),
                ratio_span=(
                    match.start(ratio_group) - match_start,
                    match.end(ratio_group) - match_start,
                ),
            )
        )
    return _MultiYearSourceRow(
        name=clean_name(match.group(1)),
        periods=tuple(periods),
        raw_match=match.group(0),
        start=rows_start + match.start(),
        end=rows_start + match.end(),
        name_span=(
            match.start(1) - match_start,
            match.end(1) - match_start,
        ),
        source_index=source_index,
    )


def _parse_rows_multi_year(
    block: str,
    rows_start: int,
    years: tuple[str, ...],
) -> _ParsedMultiYearRows:
    """머리말 연도 수와 쌍 수가 정확히 같은 행만 다개년 행으로 받는다."""

    pattern = revenue_row_pattern_v2(len(years))
    rows: list[_MultiYearSourceRow] = []
    total: Optional[_MultiYearSourceRow] = None
    overflow = False
    for source_index, match in enumerate(pattern.finditer(block)):
        source_row = _source_row_multi_year(match, rows_start, source_index, years)
        name = source_row.name
        if not name or _is(name, SUBTOTAL_WORDS):
            continue
        if is_revenue_total_name_v2(name):
            total = source_row
            break
        if len(rows) >= MAX_ROWS:
            overflow = True
            continue
        rows.append(source_row)
    return _ParsedMultiYearRows(tuple(rows), total, overflow)


def _v2_mentions_revenue(header: str, parsed: _ParsedRows) -> bool:
    """이 표가 «매출»을 말하고 있는지 본다.

    ⚠️ 이 관문이 없으면 은행 보고서의 「자금조달실적」처럼 비중 열이 있고
      금액 합도 맞는 «매출이 아닌» 표가 매출표로 올라온다.
    """

    tail = (parsed.total,) if parsed.total is not None else ()
    names = " ".join(row.public[0] for row in (*parsed.rows, *tail))
    haystack = re.sub(r"\s+", "", f"{header} {names}")
    return any(word in haystack for word in V2_REVENUE_WORDS)


def _multi_year_mentions_revenue(
    header: str, parsed: _ParsedMultiYearRows
) -> bool:
    tail = (parsed.total,) if parsed.total is not None else ()
    names = " ".join(row.name for row in (*parsed.rows, *tail))
    haystack = re.sub(r"\s+", "", f"{header} {names}")
    return any(word in haystack for word in V2_REVENUE_WORDS)


def _v2_score(zone_text: str, axis: RevenueAxis) -> int:
    """표제·연결 여부로 후보에 가산점을 준다 (판정이 아니라 «선호»다)."""

    score = 0
    if any(head in zone_text for head in REVENUE_HEADS_BY_AXIS[axis]):
        score += V2_SCORE_KNOWN_HEAD
    if "연결" in zone_text:
        score += V2_SCORE_CONSOLIDATED
    elif "별도" in zone_text:
        score += V2_SCORE_SEPARATE
    return score


def _v2_candidate(
    filing_text: str,
    run: tuple[int, int],
    block_end: int,
    reasons: dict[str, int],
) -> Optional[_V2Candidate]:
    """머리말 후보 하나를 표로 세울 수 있는지 검산한다."""

    def reject(사유: str) -> None:
        """왜 떨어졌는지 세어 둔다 — 「표가 없다」만으로는 고칠 수가 없다."""
        reasons[사유] = reasons.get(사유, 0) + 1

    ratio_start, header_end = run
    header_start = _v2_zone_start(filing_text, ratio_start)
    header = filing_text[header_start:header_end]
    parsed = _parse_rows_v2(filing_text[header_end:block_end], header_end)
    if len(parsed.rows) < V2_MIN_ROWS:
        reject("행 부족")
        return None
    if parsed.overflow:
        reject("행 넘침")
        return None
    if parsed.total is None:
        reject("합계 없음")
        return None
    if not revenue_amounts_sum_to_total(
        (row.public[1] for row in parsed.rows), parsed.total.public[1]
    ):
        reject("금액 합 불일치")
        return None
    if not revenue_percent_total_is_complete_v2(
        row.public[2] for row in parsed.rows
    ):
        reject("비중 합 불일치")
        return None
    if not _v2_mentions_revenue(header, parsed):
        reject("매출 표현 없음")
        return None
    # 단위를 못 읽거나 두 개가 엇갈리면 «만들지 않는다». 환산도 하지 않는다.
    # 숫자는 원문 그대로여도 열 이름의 단위가 틀리면 독자가 100배로 읽는다.
    units = revenue_units_in(header)
    if not units:
        reject("단위 미확인")
        return None
    if len(units) > 1:
        reject("단위 충돌")
        return None
    # 축은 인용 조각(머리말 + 행)이 정한다. 머리말이 축을 말하면 반드시
    # 같아야 하고(반박 금지), 말하지 않으면 첫 행들이 대신 말한다.
    excerpt = filing_text[header_start:parsed.total.end]
    axis = revenue_text_axis(excerpt)
    header_axis = revenue_text_axis(header)
    if axis is None or (header_axis is not None and header_axis != axis):
        reject("축 불명")
        return None
    return _V2Candidate(
        axis=axis,
        unit=units[0],
        header_start=header_start,
        header_end=header_end,
        header=header,
        parsed=parsed,
        score=_v2_score(
            filing_text[max(0, header_start - V2_SCORE_LOOKBACK):header_end], axis
        ),
        fingerprint=sha256_text(
            "|".join(
                "\t".join(row.public) for row in (*parsed.rows, parsed.total)
            )
        ),
    )


def _multi_year_candidate(
    filing_text: str,
    run: tuple[int, int],
    block_end: int,
    reasons: dict[str, int],
) -> Optional[_MultiYearCandidate]:
    """후보를 연도별로 따로 검산하고 두 해 이상 남을 때만 채택한다."""

    def reject(사유: str) -> None:
        reasons[사유] = reasons.get(사유, 0) + 1

    ratio_start, header_end = run
    header_start = _v2_zone_start(filing_text, ratio_start)
    header = filing_text[header_start:header_end]
    all_years = tuple(dict.fromkeys(_YEAR_RE.findall(header)))
    if len(all_years) < 2:
        reject("비교 연도 부족")
        return None
    if len(all_years) > MULTI_YEAR_MAX_PERIODS:
        reject("연도 열 초과")
        return None
    years = years_of(header)
    parsed = _parse_rows_multi_year(
        filing_text[header_end:block_end], header_end, years
    )
    if len(parsed.rows) < V2_MIN_ROWS:
        reject("행 부족")
        return None
    if parsed.overflow:
        reject("행 넘침")
        return None
    if parsed.total is None:
        reject("합계 없음")
        return None
    if not _multi_year_mentions_revenue(header, parsed):
        reject("매출 표현 없음")
        return None

    units = revenue_units_in(header)
    if not units:
        reject("단위 미확인")
        return None
    if len(units) > 1:
        reject("단위 충돌")
        return None
    excerpt = filing_text[header_start:parsed.total.end]
    axis = revenue_text_axis(excerpt)
    header_axis = revenue_text_axis(header)
    if axis is None or (header_axis is not None and header_axis != axis):
        reject("축 불명")
        return None

    selected: list[str] = []
    excluded: list[str] = []
    for position, year in enumerate(years):
        amounts_match = revenue_amounts_sum_to_total(
            (row.periods[position].amount for row in parsed.rows),
            parsed.total.periods[position].amount,
        )
        ratios_match = revenue_percent_total_is_complete_v2(
            row.periods[position].ratio for row in parsed.rows
        )
        total_is_complete = (
            revenue_signed_decimal(parsed.total.periods[position].ratio)
            == Decimal(100)
        )
        if amounts_match and ratios_match and total_is_complete:
            selected.append(year)
        else:
            excluded.append(year)
            reasons["연도 검산 실패"] = reasons.get("연도 검산 실패", 0) + 1
    if len(selected) < 2:
        reject("유효 연도 부족")

    all_rows = parsed.rows + (parsed.total,)
    return _MultiYearCandidate(
        axis=axis,
        unit=units[0],
        header_start=header_start,
        header_end=header_end,
        header=header,
        years=years,
        selected_years=tuple(selected),
        excluded_years=tuple(excluded),
        parsed=parsed,
        score=_v2_score(
            filing_text[max(0, header_start - V2_SCORE_LOOKBACK):header_end], axis
        ),
        fingerprint=sha256_text(
            "|".join(
                "\t".join(
                    (row.name,)
                    + tuple(
                        value
                        for period in row.periods
                        for value in (period.amount, period.ratio)
                    )
                )
                for row in all_rows
            )
        ),
    )


def _v2_payload(
    filing_text: str, cite: str, candidate: _V2Candidate
) -> RevenueTablePayload:
    """검산을 통과한 후보를 «행별 원문 근거»까지 붙여 표로 만든다."""

    parsed = candidate.parsed
    assert parsed.total is not None            # 후보 판정에서 이미 확인했다
    headers = revenue_table_headers(candidate.unit)
    source_rows = parsed.rows + (parsed.total,)
    rows = [list(row.public) for row in source_rows]
    excerpt_start = candidate.header_start
    excerpt_end = parsed.total.end
    evidence_rows = [
        build_revenue_row_evidence(
            filing_text=filing_text,
            header_start=candidate.header_start,
            header_end=candidate.header_end,
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
            axis=candidate.axis,
            headers=headers,
        )
        for index, row in enumerate(source_rows)
    ]
    해 = year_of(candidate.header)
    caption = (
        PRODUCT_CAPTION if candidate.axis == REVENUE_AXIS_PRODUCT else REGION_CAPTION
    )
    return {
        "axis": candidate.axis,
        "caption": f"{caption}{f' ({해}년)' if 해 else ''}",
        # 금액 열 이름만 단위를 따라간다 — 캡션·비중 열·표 하단 문구는 그대로다.
        "headers": list(headers),
        "rows": rows,
        "cite": cite,
        "raw_rows": [list(row) for row in rows],
        "evidence_rows": evidence_rows,
    }


def _build_v2(
    filing_text: str, cite: str = ""
) -> tuple[list[RevenueTablePayload], "RevenueTableDiagnostics"]:
    """표 모양으로 찾는 «새» 경로. 스위치가 켜졌을 때만 쓴다."""

    runs = _v2_header_runs(filing_text)
    reasons: dict[str, int] = {}
    candidates: list[_V2Candidate] = []
    seen: set[str] = set()
    for index, run in enumerate(runs):
        next_run = runs[index + 1] if index + 1 < len(runs) else None
        block_end = min(
            run[1] + V2_ROW_SCAN_CHARS,
            _v2_zone_start(filing_text, next_run[0])
            if next_run is not None
            else len(filing_text),
        )
        if block_end <= run[1]:
            continue
        candidate = _v2_candidate(filing_text, run, block_end, reasons)
        if candidate is None:
            continue
        if candidate.fingerprint in seen:
            reasons["중복 표"] = reasons.get("중복 표", 0) + 1
            continue
        seen.add(candidate.fingerprint)
        candidates.append(candidate)

    tables: list[RevenueTablePayload] = []
    for axis in (REVENUE_AXIS_PRODUCT, REVENUE_AXIS_REGION):
        same_axis = [item for item in candidates if item.axis == axis]
        if not same_axis:
            continue
        best = min(same_axis, key=lambda item: (-item.score, item.header_start))
        tables.append(_v2_payload(filing_text, cite, best))
    diagnostics: RevenueTableDiagnostics = {
        "경로": "v2",
        "후보_표_수": len(runs),
        "채택_표_수": len(tables),
        "탈락_사유": reasons,
    }
    return tables, diagnostics


def _multi_year_payload(
    filing_text: str,
    cite: str,
    candidate: _MultiYearCandidate,
) -> MultiYearRevenueTablePayload:
    """연도별 검산을 통과한 비중만 오름차순 고유 열로 만든다."""

    parsed = candidate.parsed
    assert parsed.total is not None
    selected_years = tuple(sorted(candidate.selected_years, key=int))
    selected_positions = tuple(
        candidate.years.index(year) for year in selected_years
    )
    headers = [HEADERS[0]] + [
        MULTI_YEAR_RATIO_HEADER_FORMAT.format(year=year)
        for year in selected_years
    ]
    source_rows = parsed.rows + (parsed.total,)
    rows = [
        [row.name]
        + [row.periods[position].ratio for position in selected_positions]
        for row in source_rows
    ]
    raw_rows = [
        [row.name]
        + [row.periods[position].amount for position in selected_positions]
        for row in source_rows
    ]
    numeric_checks = [
        [
            revenue_ratio_numeric_check(row.periods[position].ratio)
            for position in selected_positions
        ]
        for row in source_rows
    ]
    excerpt_start = candidate.header_start
    excerpt_end = parsed.total.end

    def period_spans(row: _MultiYearSourceRow) -> tuple[dict[str, tuple[int, int]], ...]:
        return tuple(
            {"amount": period.amount_span, "ratio": period.ratio_span}
            for period in row.periods
        )

    evidence_rows = [
        build_revenue_multi_year_row_evidence(
            filing_text=filing_text,
            header_start=candidate.header_start,
            header_end=candidate.header_end,
            excerpt_start=excerpt_start,
            excerpt_end=excerpt_end,
            row_raw_match=row.raw_match,
            row_start=row.start,
            row_end=row.end,
            row_name_span=row.name_span,
            row_period_spans=period_spans(row),
            source_index=row.source_index,
            selected_index=index,
            public_row=rows[index],
            raw_row=raw_rows[index],
            numeric_checks=numeric_checks[index],
            row_count=len(parsed.rows),
            total_raw_match=parsed.total.raw_match,
            total_start=parsed.total.start,
            total_end=parsed.total.end,
            total_name_span=parsed.total.name_span,
            total_period_spans=period_spans(parsed.total),
            years=candidate.years,
            selected_years=selected_years,
            axis=candidate.axis,
            headers=headers,
        )
        for index, row in enumerate(source_rows)
    ]
    caption = MULTI_YEAR_CAPTION_BY_AXIS[candidate.axis]
    return {
        "axis": candidate.axis,
        "caption": f"{caption} ({selected_years[0]}~{selected_years[-1]})",
        "headers": headers,
        "rows": rows,
        "cite": cite,
        "raw_rows": raw_rows,
        "evidence_rows": evidence_rows,
        "numeric_checks": numeric_checks,
        "raw_unit": candidate.unit,
    }


def build_multi_year_with_diagnostics(
    filing_text: str,
    cite: str = "",
) -> tuple[
    list[MultiYearRevenueTablePayload],
    MultiYearRevenueTableDiagnostics,
]:
    """공시 원문에서 2~3개년 구성 변화 표와 제외 연도를 함께 돌려준다."""

    runs = _v2_header_runs(filing_text)
    reasons: dict[str, int] = {}
    candidates: list[_MultiYearCandidate] = []
    seen: set[str] = set()
    for index, run in enumerate(runs):
        next_run = runs[index + 1] if index + 1 < len(runs) else None
        block_end = min(
            run[1] + V2_ROW_SCAN_CHARS,
            _v2_zone_start(filing_text, next_run[0])
            if next_run is not None
            else len(filing_text),
        )
        if block_end <= run[1]:
            continue
        candidate = _multi_year_candidate(filing_text, run, block_end, reasons)
        if candidate is None:
            continue
        if candidate.fingerprint in seen:
            reasons["중복 표"] = reasons.get("중복 표", 0) + 1
            continue
        seen.add(candidate.fingerprint)
        candidates.append(candidate)

    tables: list[MultiYearRevenueTablePayload] = []
    excluded_years: dict[str, list[str]] = {}
    for axis in (REVENUE_AXIS_PRODUCT, REVENUE_AXIS_REGION):
        same_axis = [item for item in candidates if item.axis == axis]
        if not same_axis:
            continue
        eligible = [item for item in same_axis if len(item.selected_years) >= 2]
        best_observed = min(
            same_axis, key=lambda item: (-item.score, item.header_start)
        )
        if not eligible:
            if best_observed.excluded_years:
                excluded_years[axis] = list(best_observed.excluded_years)
            continue
        best = min(eligible, key=lambda item: (-item.score, item.header_start))
        tables.append(_multi_year_payload(filing_text, cite, best))
        if best.excluded_years:
            excluded_years[axis] = list(best.excluded_years)
    return tables, {
        "경로": "v2-multi-year",
        "후보_표_수": len(runs),
        "채택_표_수": len(tables),
        "탈락_사유": reasons,
        "제외_연도": excluded_years,
    }


def build_multi_year(
    filing_text: str, cite: str = ""
) -> list[MultiYearRevenueTablePayload]:
    """공시 원문에서 연도가 든 고유 열의 구성 변화 표를 만든다.

    머리말에 비교 가능한 연도가 두 개 이상 없거나 연도별 검산 뒤 한 해만
    남으면 단년 표와 중복되므로 빈 목록을 돌려준다. 기존 ``build()``와 달리
    명시적으로 다개년 표를 요청하는 공개 함수라 rollout 스위치를 읽지 않는다.
    """

    return build_multi_year_with_diagnostics(filing_text, cite)[0]


def build_with_diagnostics(
    filing_text: str, cite: str = ""
) -> tuple[list[RevenueTablePayload], RevenueTableDiagnostics]:
    """표와 «왜 못 찾았는지»를 함께 돌려준다.

    ★ ``build()``의 서명은 그대로 둔다 — 이미 여러 곳이 부르고 있다.
      진단이 필요한 호출자만 이쪽을 쓴다.
    """

    if revenue_table_v2_enabled():
        return _build_v2(filing_text, cite)
    tables = _build_v1(filing_text, cite)
    return tables, {
        "경로": "v1",
        "후보_표_수": len(tables),
        "채택_표_수": len(tables),
        "탈락_사유": {},
    }


def build(filing_text: str, cite: str = "") -> list[RevenueTablePayload]:
    """공시 원문에서 매출 구성 표를 만든다.

    Args:
        filing_text: 사업보고서 원문 전체.
        cite: 출처 표기.

    Returns:
        표 정의 목록 (`caption`·`headers`·`rows`·`cite`). 못 찾으면 빈 목록.

    ★ **못 찾으면 빈 목록이다.** 억지로 만들지 않는다 —
      비중을 우리가 계산해서 채우면 그 순간 공시와 어긋난다.
    ★ 스위치 ``REVENUE_TABLE_V2``가 정확히 ``"1"``일 때만 새 경로를 탄다.
    """

    return build_with_diagnostics(filing_text, cite)[0]
