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

from typing import Any

from src.core.citations import citation_marker
from src.core.constants import section_display_heading
from src.features.export_notion import constants
from src.features.pipeline.port import (
    Report,
    ReportSection,
    ReportTable,
)
from src.features.provenance.sources import Source
from src.features.report_standard import SECTION_BY_ID, build_published_report

#: 노션 블록 하나를 표현하는 dict. Notion API의 block object 형태를 그대로 따른다.
NotionBlock = dict[str, Any]
NotionRichText = list[dict[str, Any]]
NotionCell = str | NotionRichText


# ══════════════════════════════════════════════════════════
# 리치 텍스트 · 기본 블록 조각
# ══════════════════════════════════════════════════════════


def _rich_text(text: str, *, href: str = "") -> NotionRichText:
    """노션 rich_text 배열을 만든다.

    ★ 노션은 rich_text 항목 하나(content)에 2,000자 제한이 있다 — 넘으면
      나눠 담는다. 빈 문자열은 빈 배열로 낸다(노션이 허용하는 형태).
    """
    if not text:
        return []
    limit = constants.MAX_RICH_TEXT_LENGTH
    chunks = [text[i : i + limit] for i in range(0, len(text), limit)]
    safe_href = href.strip() if href.strip().startswith(("https://", "http://")) else ""
    items: NotionRichText = []
    for chunk in chunks:
        text_payload: dict[str, Any] = {"content": chunk}
        if safe_href:
            text_payload["link"] = {"url": safe_href}
        items.append({"type": "text", "text": text_payload})
    return items


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


def _table_row(cells: list[NotionCell]) -> NotionBlock:
    return {
        "object": "block",
        "type": "table_row",
        "table_row": {
            "cells": [cell if isinstance(cell, list) else _rich_text(cell) for cell in cells]
        },
    }


def _table_block(headers: list[str], rows: list[list[NotionCell]]) -> NotionBlock:
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
    values = [
        report.corp_type.strip(),
        f"{report.as_of_date.strip()} 기준" if report.as_of_date.strip() else "",
        report.analysis_period.strip(),
        (
            f"최신 실적 {report.latest_performance_period.strip()}"
            if report.latest_performance_period.strip()
            else ""
        ),
    ]
    return " · ".join(value for value in values if value)


# ══════════════════════════════════════════════════════════
# 항목(칸) 하나 — 채워졌으면 문장·표, 비었으면 사유
# ══════════════════════════════════════════════════════════


def _table_blocks(table: ReportTable) -> list[NotionBlock]:
    """숫자·회계 표 하나. 문장으로 바꾸지 않고 «표 그대로» 낸다 (결정기록 D13)."""
    marker = citation_marker(table.cite)
    caption = f"{table.caption} {marker}" if marker else table.caption
    blocks: list[NotionBlock] = [_paragraph(caption)]
    if table.headers:
        blocks.append(_table_block(table.headers, table.rows))
    return blocks


def _section_blocks(section: ReportSection) -> list[NotionBlock]:
    blocks: list[NotionBlock] = [_heading_2(_section_heading(section))]
    if not section.is_filled:
        return blocks
    if section.prose_lines:
        # ★ 작가 내부 sid가 아니라 그 번호가 가리킨 실제 출처를 문장마다 표시한다(P-118).
        prose = " ".join(
            f"{text} {marker}" if (marker := citation_marker(cite)) else text
            for text, cite in section.prose_lines
        )
        blocks.append(_paragraph(prose))
    for table in section.tables:
        blocks.extend(_table_blocks(table))
    return blocks


def _section_heading(section: ReportSection) -> str:
    if section.display_number:
        tag = f"  {section.tag}" if section.tag else ""
        return f"{section.display_number}. {section.title}{tag}"
    return section_display_heading(section.cell, section.title)


def _summary_blocks(report: Report) -> list[NotionBlock]:
    if not report.summary_items:
        return []
    rows: list[list[str]] = []
    for index, item in enumerate(report.summary_items, start=1):
        spec = SECTION_BY_ID.get(item.section_id)
        rows.append(
            [
                f"{index:02d}",
                item.text.strip(),
                f"{spec.display_number}장" if spec is not None else "",
            ]
        )
    return [_heading_2("핵심 요약"), _table_block(["#", "요약", "관련 장"], rows)]


# ══════════════════════════════════════════════════════════
# 출처 목록 — 최종 보고서에 필요한 문서명·기준일·검증 상태만 표로 낸다.
# ══════════════════════════════════════════════════════════


def _source_list_blocks(citations: list[object]) -> list[NotionBlock]:
    sources: list[Source] = []
    seen_numbers: set[int] = set()
    for item in citations:
        if not isinstance(item, Source) or item.number in seen_numbers:
            continue
        seen_numbers.add(item.number)
        sources.append(item)
    if not sources:
        return []
    rows: list[list[NotionCell]] = [
        [
            str(source.number),
            _rich_text(_source_label(source), href=source.url),
            _source_status(source),
            source.location.strip() or "—",
            _source_used_sections(source),
        ]
        for source in sources
    ]
    return [
        _table_block(
            ["#", "자료", "기준일·상태", "원문 위치", "본문 사용 장"],
            rows,
        )
    ]


def _source_label(source: Source) -> str:
    label = (source.title or source.label).strip()
    publisher = source.publisher.strip()
    if publisher and publisher.casefold() not in label.casefold():
        return f"{label} · {publisher}"
    return label


def _source_status(source: Source) -> str:
    parts: list[str] = []
    if source.published_at:
        parts.append(f"{source.published_at} 보도")
    elif source.disclosed_at:
        parts.append(f"{source.disclosed_at} 공시")
    elif source.collected_at:
        parts.append(f"{source.collected_at} 확인")
    else:
        parts.append("기준일 미확인")
    for value in (source.domain, source.source_type, source.fact_status):
        cleaned = value.strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return " · ".join(parts)


def _source_used_sections(source: Source) -> str:
    labels: list[str] = []
    for section_id in source.used_in:
        spec = SECTION_BY_ID.get(section_id)
        label = f"{spec.display_number}장" if spec is not None else section_id.strip()
        if label and label not in labels:
            labels.append(label)
    return " · ".join(labels) or "—"


# ══════════════════════════════════════════════════════════
# 전체 조립
# ══════════════════════════════════════════════════════════


def build_page_title(report: Report) -> str:
    """노션 페이지 제목 — `회사명 분석 보고서 (YYYY-MM-DD)`.

    정본: 확정/07_출력/2_규칙/01_배치와근거표기.md §파일명 규칙

    ★ 워드 파일명과 같은 근거로 `report.generated_at`을 쓴다(전송 시각이 아니다) —
      «다시 내보내기»를 해도 같은 제목이 나와야 한다
      (확정/07_출력/1_흐름/01_세형태.md §다시 내보내기).
    """
    date_part = f" ({report.generated_at})" if report.generated_at else ""
    return f"{report.company} 분석 보고서{date_part}"


def build_blocks(report: Report, *, grade_note: str = "") -> list[NotionBlock]:
    """`Report` 하나를 노션 페이지에 넣을 블록 목록으로 바꾼다.

    배치 순서는 화면과 같다:
        [회사명·보고서명] → [보고서 본문] → [출처와 검증 상태]

    Args:
        report: 화면에 낸 것과 같은 보고서 데이터.
        grade_note: 이전 호출자와의 호환을 위해 남긴 인수. 완성도·작성 과정 문구는
            최종 보고서에 넣지 않으므로 출력에는 사용하지 않는다.

    Returns:
        노션 API가 받는 block object 목록. 100개를 넘으면 부르는 쪽(`notion.py`)이
        나눠 보낸다 — 이 함수는 나누지 않는다(순수 변환만 한다).
    """
    # Notion도 화면·PDF와 같은 canonical 공개본만 표현한다.
    report = build_published_report(report)

    blocks: list[NotionBlock] = [_heading_1(report.company), _heading_1("분석 보고서")]
    lede = _lede_text(report)
    if lede:
        blocks.append(_paragraph(lede))
    blocks.extend(_summary_blocks(report))

    for section in report.sections:
        blocks.extend(_section_blocks(section))

    source_blocks = _source_list_blocks(report.citations)
    if source_blocks:
        blocks.append(_heading_2(constants.SOURCES_HEADING))
        blocks.append(_paragraph(constants.SOURCES_SUBTITLE))
        blocks.extend(source_blocks)

    return blocks
