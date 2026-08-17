"""`Report` → 노션 블록(JSON) 변환 — 순수 함수만 모은 곳.

★ 네트워크·시간·난수를 쓰지 않는다. 같은 `Report`를 넣으면 항상 같은 블록 목록이
  나온다. 그래야 화면(result.html)과 「내용이 같은가」(P3 — 확정/07_출력/3_기준/
  01_성공기준.md)를 코드로 비교할 수 있다.

★ 배치 순서·문구는 `src/web/templates/result.html`을 그대로 옮겨 적었다.
  화면이 바뀌면 이 파일도 같이 고쳐야 한다 — 갈리면 화면이 이긴다
  (확정/07_출력/1_흐름/01_세형태.md 「정본은 화면이다」).

정본:
  - 확정/07_출력/2_규칙/01_배치와근거표기.md
  - 확정/07_출력/3_기준/01_성공기준.md (P3)
"""

from __future__ import annotations

from typing import Any, cast

from src.core.citations import citation_marker
from src.core.constants import CELL_LABELS, RAW_SOURCE_LABEL, RAW_SOURCE_NOTE
from src.features.export_notion import constants
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
)
from src.features.provenance.sources import Source, render_sources

#: 노션 블록 하나를 표현하는 dict. Notion API의 block object 형태를 그대로 따른다.
NotionBlock = dict[str, Any]


# ══════════════════════════════════════════════════════════
# 리치 텍스트 · 기본 블록 조각
# ══════════════════════════════════════════════════════════


def _rich_text(text: str) -> list[dict[str, Any]]:
    """노션 rich_text 배열을 만든다.

    ★ 노션은 rich_text 항목 하나(content)에 2,000자 제한이 있다 — 넘으면
      나눠 담는다. 빈 문자열은 빈 배열로 낸다(노션이 허용하는 형태).
    """
    if not text:
        return []
    limit = constants.MAX_RICH_TEXT_LENGTH
    chunks = [text[i : i + limit] for i in range(0, len(text), limit)]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _paragraph(text: str) -> NotionBlock:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


def _heading_1(text: str) -> NotionBlock:
    return {
        "object": "block",
        "type": "heading_1",
        "heading_1": {"rich_text": _rich_text(text)},
    }


def _heading_2(text: str) -> NotionBlock:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _rich_text(text)},
    }


def _bulleted(text: str) -> NotionBlock:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(text)},
    }


def _callout(text: str, icon_emoji: str) -> NotionBlock:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"type": "emoji", "emoji": icon_emoji},
        },
    }


def _table_row(cells: list[str]) -> NotionBlock:
    return {
        "object": "block",
        "type": "table_row",
        "table_row": {"cells": [_rich_text(c) for c in cells]},
    }


def _table_block(headers: list[str], rows: list[list[str]]) -> NotionBlock:
    """노션 표 블록 하나. 머리글 행 + 데이터 행을 한 번에 자식으로 담는다.

    ★ 노션 표는 행(table_row)을 표 블록 생성 시점에 «함께» 넣어야 한다 — 나중에
      따로 추가하려면 표를 아는 상태에서 다시 호출해야 해 번거롭다.
    """
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(headers),
            "has_column_header": True,
            "has_row_header": False,
            "children": [_table_row(headers)] + [_table_row(r) for r in rows],
        },
    }


# ══════════════════════════════════════════════════════════
# 본문 — 회사명·부제 (result.html <h1>/<p class="lede">)
# ══════════════════════════════════════════════════════════


def _lede_text(report: Report) -> str:
    text = f"{report.job} · {report.corp_type}"
    if report.generated_at:
        text = f"{text} · {report.generated_at} 생성"
    return text


# ══════════════════════════════════════════════════════════
# 상단 라벨 — 부분 보고서일 때만 (result.html .grade)
# ══════════════════════════════════════════════════════════


def _grade_blocks(report: Report, grade_note: str) -> list[NotionBlock]:
    if report.grade is Grade.COMPLETE:
        return []
    icon = (
        constants.GRADE_ICON_PARTIAL
        if report.grade is Grade.PARTIAL
        else constants.GRADE_ICON_INCOMPLETE
    )
    blocks: list[NotionBlock] = [_callout(grade_note, icon)]
    blocks.extend(_bulleted(reason) for reason in report.shortfall_reasons)
    return blocks


# ══════════════════════════════════════════════════════════
# 요구역량(5번 칸) — 공고 원문 목록이라 다른 칸과 다르게 다룬다
# (result.html의 requirements_block() 매크로와 같은 모양)
# ══════════════════════════════════════════════════════════


def _requirements_blocks(requirements: list[str]) -> list[NotionBlock]:
    title = f"{constants.REQUIREMENTS_CELL}. {CELL_LABELS[constants.REQUIREMENTS_CELL]}"
    blocks: list[NotionBlock] = [
        _heading_2(title),
        _paragraph(constants.REQUIREMENTS_NOTE),
    ]
    if requirements:
        blocks.extend(_bulleted(r) for r in requirements)
    else:
        blocks.append(_paragraph(constants.REQUIREMENTS_EMPTY_TEXT))
    return blocks


# ══════════════════════════════════════════════════════════
# 항목(칸) 하나 — 채워졌으면 문장·표, 비었으면 사유
# ══════════════════════════════════════════════════════════


def _table_blocks(table: ReportTable) -> list[NotionBlock]:
    """숫자·회계 표 하나. 문장으로 바꾸지 않고 «표 그대로» 낸다 (결정기록 D13)."""
    caption = f"{table.caption} 〔{table.cite}〕" if table.cite else table.caption
    blocks: list[NotionBlock] = [_paragraph(caption)]
    if table.headers:
        blocks.append(_table_block(table.headers, table.rows))
    return blocks


def _section_blocks(section: ReportSection) -> list[NotionBlock]:
    blocks: list[NotionBlock] = [_heading_2(f"{section.cell}. {section.title}")]
    if not section.is_filled:
        reason = section.empty_reason or constants.EMPTY_SECTION_FALLBACK
        blocks.append(_paragraph(f"{constants.EMPTY_SECTION_PREFIX}{reason}"))
        return blocks
    if section.prose_lines:
        # ★ 작가 내부 sid가 아니라 그 번호가 가리킨 실제 출처를 문장마다 표시한다(P-118).
        prose = " ".join(
            f"{text} {marker}" if (marker := citation_marker(cite)) else text
            for text, cite in section.prose_lines
        )
        blocks.append(_paragraph(prose))
        if section.lines:
            blocks.append(
                _paragraph(
                    f"{RAW_SOURCE_LABEL} ({len(section.lines)}문장) — {RAW_SOURCE_NOTE}"
                )
            )
            blocks.extend(
                _bulleted(f"{text} {marker}" if (marker := citation_marker(cite)) else text)
                for text, cite in section.lines
            )
    else:
        for text, cite in section.lines:
            marker = citation_marker(cite)
            blocks.append(_bulleted(f"{text} {marker}" if marker else text))
    for table in section.tables:
        blocks.extend(_table_blocks(table))
    return blocks


# ══════════════════════════════════════════════════════════
# 출처 목록 — provenance.sources.render_sources()의 결과를 그대로 쓴다 (P3)
# ★ 여기서 새로 포맷을 만들지 않는다 — 그러면 화면·노션이 따로 놀 수 있다.
# ══════════════════════════════════════════════════════════


def _source_list_blocks(citations: list[object]) -> list[NotionBlock]:
    # Report.citations는 순환 참조를 피하려고 list[object]로 열어 뒀지만, 실제로는
    # provenance.sources.Source 목록이다 (port.py 주석 참고).
    sources = cast("list[Source]", citations)
    rendered = render_sources(sources)
    # 첫 줄(SOURCES_HEADER == "[출처]")은 위에서 heading_2로 이미 냈으므로 건너뛴다.
    body_lines = rendered.splitlines()[1:]
    return [_paragraph(line) for line in body_lines if line.strip()]


# ══════════════════════════════════════════════════════════
# 수집 현황 — 사용자 화면에는 «소스별»만 낸다 (result.html "어디서 가져왔나")
# ══════════════════════════════════════════════════════════


def _collection_table_block(sources: list[SourceStatus]) -> NotionBlock:
    headers = list(constants.COLLECTION_TABLE_HEADERS)
    rows = [
        [s.name, constants.SOURCE_STATE_LABELS.get(s.state, s.state), s.detail]
        for s in sources
    ]
    return _table_block(headers, rows)


# ══════════════════════════════════════════════════════════
# 전체 조립
# ══════════════════════════════════════════════════════════


def build_page_title(report: Report) -> str:
    """노션 페이지 제목 — `회사명 · 직무 (YYYY-MM-DD)`.

    정본: 확정/07_출력/2_규칙/01_배치와근거표기.md §파일명 규칙

    ★ 워드 파일명과 같은 근거로 `report.generated_at`을 쓴다(전송 시각이 아니다) —
      «다시 내보내기»를 해도 같은 제목이 나와야 한다
      (확정/07_출력/1_흐름/01_세형태.md §다시 내보내기).
    """
    date_part = f" ({report.generated_at})" if report.generated_at else ""
    return f"{report.company} · {report.job}{date_part}"


def build_blocks(report: Report, *, grade_note: str = "") -> list[NotionBlock]:
    """`Report` 하나를 노션 페이지에 넣을 블록 목록으로 바꾼다.

    배치 순서는 화면과 같다 (확정/07_출력/2_규칙/01_배치와근거표기.md):
        [회사명·부제] → [상단 라벨(부분 보고서만)] → [보고서 본문] → [출처] → [수집 현황]

    Args:
        report: 화면에 낸 것과 같은 보고서 데이터.
        grade_note: 상단 라벨에 쓸 문구. `report.grade`가 완성이 아닐 때만 쓰인다.
            main.py가 화면(`grade_message()`)에 넘긴 것과 «같은 문자열»을 그대로
            넘겨야 한다 — 여기서 다시 만들면 화면과 노션의 문구가 갈릴 수 있다 (P3).

    Returns:
        노션 API가 받는 block object 목록. 100개를 넘으면 부르는 쪽(`notion.py`)이
        나눠 보낸다 — 이 함수는 나누지 않는다(순수 변환만 한다).
    """
    blocks: list[NotionBlock] = [
        _heading_1(report.company),
        _paragraph(_lede_text(report)),
    ]
    blocks.extend(_grade_blocks(report, grade_note))

    shown_requirements = False
    for section in report.sections:
        if section.cell == constants.REQUIREMENTS_CELL:
            blocks.extend(_requirements_blocks(report.requirements))
            shown_requirements = True
            continue
        blocks.extend(_section_blocks(section))
    if not shown_requirements:
        # 기록에 5번 칸 자체가 없어도 요구역량은 빠뜨리지 않는다 (result.html과 동일).
        blocks.extend(_requirements_blocks(report.requirements))

    if report.citations:
        blocks.append(_heading_2(constants.SOURCES_HEADING))
        blocks.append(_paragraph(constants.SOURCES_SUBTITLE))
        blocks.extend(_source_list_blocks(report.citations))

    if report.sources:
        blocks.append(_heading_2(constants.COLLECTION_HEADING))
        blocks.append(_collection_table_block(report.sources))

    return blocks
