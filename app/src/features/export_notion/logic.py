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

from collections.abc import Mapping
from typing import Any

from src.core.citations import citation_marker
from src.shared.report_quality.models import PublicationPolicy
from src.core.constants import section_display_heading
from src.features.export_notion import constants
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
)
from src.features.provenance.sources import Source, visible_citations
from src.features.report_standard import SECTION_BY_ID, build_published_report
from src.features.report_standard.section_content import (
    masthead_lines,
    section_content_blocks,
    source_verification_label,
    summary_topic,
)
from src.shared.report_generation.public_projection import (
    PublicCitationRow,
    PublicCoverMetricsBlock,
    PublicPeriodSummaryBlock,
    PublicReportProjection,
    PublicSectionDisplay,
    PublicTableBlock,
)

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


def _heading_3(text: str) -> NotionBlock:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": _rich_text(text)},
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


def _lede_text(
    *,
    corp_type: str,
    as_of_date: str,
    analysis_period: str,
    latest_performance_period: str,
) -> str:
    """부제 한 줄. v1은 ``Report``에서, v2는 봉인 header에서 같은 값을 넘긴다.

    ★ 두 갈래가 «한 함수»를 쓴다 — 한쪽만 고치면 같은 보고서의 부제가 채널
      안에서 갈라진다.
    """

    as_of = as_of_date.strip()
    latest = latest_performance_period.strip()
    values = [
        corp_type.strip(),
        f"{as_of} 기준" if as_of else "",
        analysis_period.strip(),
        f"최신 실적 {latest}" if latest else "",
    ]
    return " · ".join(value for value in values if value)


def _report_lede_text(report: Report) -> str:
    return _lede_text(
        corp_type=report.corp_type,
        as_of_date=report.as_of_date,
        analysis_period=report.analysis_period,
        latest_performance_period=report.latest_performance_period,
    )


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


def _section_blocks(report: Report, section: ReportSection) -> list[NotionBlock]:
    blocks: list[NotionBlock] = [_heading_2(_section_heading(section))]
    if not section.is_filled:
        return blocks
    detail_blocks = section_content_blocks(report, section)
    if detail_blocks:
        for detail in detail_blocks:
            markers = " ".join(f"[{number}]" for number in detail.source_numbers)
            title = " ".join(value for value in (detail.title, markers) if value)
            if detail.tone == "limitation":
                title = f"확인 범위 · {title}"
            blocks.append(_heading_3(title))
            blocks.append(
                _table_block(
                    ["항목", "확인 내용"],
                    [[field.label, field.value] for field in detail.fields],
                )
            )
    elif section.prose_lines:
        # ★ 작가 내부 sid가 아니라 그 번호가 가리킨 실제 출처를 문장마다 표시한다(P-118).
        prose = " ".join(
            f"{text} {marker}" if (marker := citation_marker(cite)) else text
            for text, cite in section.prose_lines
        )
        blocks.append(_paragraph(prose))
    for table in section.tables:
        blocks.extend(_table_blocks(table))
    return blocks


def _heading_text(*, cell: str, display_number: str, title: str, tag: str) -> str:
    """장 제목 한 줄. v1(``ReportSection``)과 v2(봉인 display)가 같이 쓴다."""

    if display_number:
        suffix = f"  {tag}" if tag else ""
        return f"{display_number}. {title}{suffix}"
    return section_display_heading(cell, title)


def _section_heading(section: ReportSection) -> str:
    return _heading_text(
        cell=section.cell,
        display_number=section.display_number,
        title=section.title,
        tag=section.tag,
    )


def _summary_blocks(report: Report) -> list[NotionBlock]:
    if not report.summary_items:
        return []
    rows: list[list[str]] = []
    for index, item in enumerate(report.summary_items, start=1):
        spec = SECTION_BY_ID.get(item.section_id)
        rows.append(
            [
                f"{index:02d}",
                summary_topic(item.section_id),
                item.text.strip(),
                f"{spec.display_number}장" if spec is not None else "",
            ]
        )
    return [
        _heading_2(constants.SUMMARY_HEADING),
        _table_block(list(constants.SUMMARY_TABLE_HEADERS), rows),
    ]


# ══════════════════════════════════════════════════════════
# 출처 목록 — 최종 보고서에 필요한 문서명·기준일·검증 상태만 표로 낸다.
# ══════════════════════════════════════════════════════════


def _source_list_blocks(report: Report) -> list[NotionBlock]:
    sources: list[Source] = []
    seen_numbers: set[int] = set()
    for item in visible_citations(report.citations):
        if item.number in seen_numbers:
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
            source_verification_label(report, source.source_id),
            source.location.strip() or "—",
            _source_used_sections(source),
        ]
        for source in sources
    ]
    return [_table_block(list(constants.CITATION_TABLE_HEADERS), rows)]


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
# v2 — 공개 봉인 블록«만» 읽는 갈래 (설계 017 §07 조각 S6)
# ══════════════════════════════════════════════════════════
# ★ 아래 함수들은 ``report``를 «인수로 받지 않는다». 봉인 블록만 받는다.
#   그래야 노션이 화면·PDF와 다른 값을 스스로 계산할 방법이 구조적으로
#   없어진다 — 지금까지 갈라진 원인이 바로 그 「각자 계산」이었다
#   (설계 §01-6 G1·G2·G9).
# ★ 이 갈래는 «글자를 만들지 않는다». 블록 값을 그대로 배치하고, 표의 열
#   이름처럼 보고서 내용이 아닌 라벨만 ``constants``에서 가져온다.


def _v2_table_blocks(table: PublicTableBlock) -> list[NotionBlock]:
    """봉인된 표 한 장 — 설명 줄 + 표 블록. 글자는 전부 블록 값이다."""

    marker = citation_marker(table.cite)
    caption = f"{table.caption} {marker}" if marker else table.caption
    blocks: list[NotionBlock] = [_paragraph(caption)]
    if table.headers:
        blocks.append(
            _table_block(
                list(table.headers), [list(row) for row in table.rows]
            )
        )
    return blocks


def _v2_period_summary_blocks(
    band: PublicPeriodSummaryBlock,
) -> list[NotionBlock]:
    """3개년 변화 요약 띠. 칸 값은 봉인된 문자열 그대로다."""

    if not band.items:
        return []
    marker = citation_marker(band.cite)
    caption = f"{band.title} {marker}" if marker else band.title
    rows = [
        [
            label,
            base_period,
            base_value,
            latest_period,
            latest_value,
            unit,
            change,
            note,
        ]
        for (
            label,
            base_period,
            base_value,
            latest_period,
            latest_value,
            unit,
            change,
            _change_kind,
            _direction,
            note,
        ) in band.items
    ]
    blocks: list[NotionBlock] = []
    if caption.strip():
        blocks.append(_paragraph(caption))
    blocks.append(_table_block(list(constants.PERIOD_SUMMARY_TABLE_HEADERS), rows))
    return blocks


def _v2_cover_metrics_blocks(
    metrics: PublicCoverMetricsBlock | None,
) -> list[NotionBlock]:
    """표지 실적 띠. 순수 함수가 지표를 못 고르면 봉인에도 없고 여기도 없다."""

    if metrics is None or not metrics.items:
        return []
    marker = citation_marker(metrics.cite)
    caption = f"{metrics.title} {marker}" if marker else metrics.title
    blocks: list[NotionBlock] = []
    if caption.strip():
        blocks.append(_paragraph(caption))
    blocks.append(
        _table_block(
            list(constants.COVER_METRICS_TABLE_HEADERS),
            [[label, value, unit] for label, value, unit in metrics.items],
        )
    )
    return blocks


def _v2_section_blocks(display: PublicSectionDisplay) -> list[NotionBlock]:
    """장 하나 — 제목 → 문단 → 3개년 띠 → 표·읽는 법.

    ★ 문단은 «문단 단위»로 낸다. 봉인이 이미 문단을 나눠 두었으므로 노션이
      다시 이어 붙이거나 사실 카드 표로 바꾸지 않는다(G1).
    ★ 도식은 표 블록 + ``reading`` 문단으로 낸다. 노션에는 막대 그림을 그릴
      자리가 없어 «모양»은 다르지만 글자는 봉인 값 그대로다(G2).
    ★ 3개년 띠를 표보다 «먼저» 놓는 것은 화면(result.html)이 막대 위에
      두는 것과 같은 이유다 — 그림을 보기 전에 무엇이 얼마나 변했는지 먼저
      읽게 한다. 봉인 블록에는 띠가 «어느 표»에서 나왔는지가 없으므로 장마다
      한 번, 표 앞에 둔다.
    """

    blocks: list[NotionBlock] = [
        _heading_2(
            _heading_text(
                cell=display.cell,
                display_number=display.display_number,
                title=display.title,
                tag=display.tag,
            )
        )
    ]
    for _ordinal, text in display.paragraphs:
        blocks.append(_paragraph(text))
    if display.period_summary is not None:
        blocks.extend(_v2_period_summary_blocks(display.period_summary))
    visual_by_table = {visual.table_index: visual for visual in display.visuals}
    for index, table in enumerate(display.tables):
        blocks.extend(_v2_table_blocks(table))
        visual = visual_by_table.get(index)
        if visual is not None and visual.reading:
            blocks.append(_paragraph(visual.reading))
    return blocks


def _v2_summary_blocks(projection: PublicReportProjection) -> list[NotionBlock]:
    """핵심 요약 — 번호·주제어·장 번호를 노션이 다시 매기지 않는다."""

    if not projection.summary:
        return []
    rows = [
        [row.ordinal, row.topic, row.text, row.section_display_number]
        for row in projection.summary
    ]
    return [
        _heading_2(constants.SUMMARY_HEADING),
        _table_block(list(constants.SUMMARY_TABLE_HEADERS), rows),
    ]


def _v2_citation_rows(rows: tuple[PublicCitationRow, ...]) -> list[list[NotionCell]]:
    return [
        [
            str(row.number),
            _rich_text(row.label_display, href=row.url),
            row.status_display,
            row.verification_label,
            row.location,
            row.used_in_display,
        ]
        for row in rows
    ]


def _v2_source_list_blocks(
    projection: PublicReportProjection,
) -> list[NotionBlock]:
    """부록 — 번호 중복 제거·검증 라벨까지 봉인이 이미 끝낸 결과를 옮긴다."""

    if not projection.citations:
        return []
    return [
        _heading_2(constants.SOURCES_HEADING),
        _paragraph(constants.SOURCES_SUBTITLE),
        _table_block(
            list(constants.CITATION_TABLE_HEADERS),
            _v2_citation_rows(projection.citations),
        ),
    ]


def _v2_header_text(header: Mapping[str, object], key: str) -> str:
    return str(header.get(key, "") or "")


def _v2_grade_notice_blocks(
    projection: PublicReportProjection,
) -> list[NotionBlock]:
    """부분 보고서 고지 — 채널마다 다른 이름으로 부르지 않게 블록에서 읽는다.

    ★ 2026-08-29에 노션만 같은 보고서를 더 후하게 불렀던 사고가 있었다. 이제
      제목·설명 두 줄이 봉인에 들어 있어 세 채널이 같은 글자를 쓴다.
    """

    title, detail = projection.grade_notice
    if not title:
        return []
    reasons = projection.header.get("shortfall_reasons") or ()
    blocks: list[NotionBlock] = [_heading_2(title)]
    if detail:
        blocks.append(_paragraph(detail))
    blocks.extend(_paragraph(str(reason)) for reason in reasons)
    return blocks


def _v2_blocks(report: Report, projection: PublicReportProjection) -> list[NotionBlock]:
    """봉인 블록 하나만 읽어 노션 페이지 블록을 만든다.

    ``report``는 마스트헤드 두 줄에만 쓴다 — ``masthead_lines()``는 웹·PDF·
    노션이 «같은 두 줄»을 쓰라고 D-S4a가 일부러 공유시킨 함수이고, 봉인
    블록에는 아직 그 자리가 없다. 나머지 글자는 전부 ``projection``에서 온다.
    """

    header = projection.header
    masthead_company, masthead_meta = masthead_lines(report)
    blocks: list[NotionBlock] = [
        _heading_2(masthead_company),
        _paragraph(masthead_meta),
        _heading_1(_v2_header_text(header, "company")),
        _heading_1("분석 보고서"),
    ]
    blocks.extend(_v2_grade_notice_blocks(projection))
    lede = _lede_text(
        corp_type=_v2_header_text(header, "corp_type"),
        as_of_date=_v2_header_text(header, "as_of_date"),
        analysis_period=_v2_header_text(header, "analysis_period"),
        latest_performance_period=_v2_header_text(
            header, "latest_performance_period"
        ),
    )
    if lede:
        blocks.append(_paragraph(lede))
    blocks.extend(_v2_cover_metrics_blocks(projection.cover_metrics))
    blocks.extend(_v2_summary_blocks(projection))
    for block in projection.sections:
        blocks.extend(_v2_section_blocks(block.display))
    blocks.extend(_v2_source_list_blocks(projection))
    return blocks


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
    # ★ 갈래는 «공개 봉인 블록이 있는가»로 나뉜다(설계 017 §07 조각 S6).
    #   봉인이 있으면 그 블록«만» 읽는다 — 노션이 화면·PDF와 따로 계산할 여지를
    #   없앤다. 봉인이 없는 저장본(v1·옛 v2)은 지금 경로 그대로다(§02-6).
    projection = report.public_projection
    if projection is not None:
        return _v2_blocks(report, projection)

    # Notion도 화면·PDF와 같은 canonical 공개본만 표현한다.
    report = build_published_report(report)

    # 표지 다음 첫 본문 페이지 맨 위 마스트헤드(D-S4a) — 웹·PDF와 같은
    # masthead_lines() 문자열을 그대로 첫 두 블록으로 낸다. 새 사실을
    # 만들지 않고, 아래 이어지는 회사명·보고서명 heading_1 쌍은 그대로 둔다.
    masthead_company, masthead_meta = masthead_lines(report)
    blocks: list[NotionBlock] = [
        _heading_2(masthead_company),
        _paragraph(masthead_meta),
        _heading_1(report.company),
        _heading_1("분석 보고서"),
    ]
    if report.grade is Grade.PARTIAL:
        # ★ 2026-08-29 — 노션만 정책과 «무관하게» 「검증된 부분 보고서」라고 불렀다.
        #   같은 보고서를 웹·PDF 는 「안전 확인 중인 임시 부분 보고서」라고 부른다
        #   (`web/routers/reports.py::_report_grade_note`). 한 보고서가 채널마다
        #   다른 이름으로 불리면, 그중 하나는 반드시 사실보다 후하게 말하는 것이다.
        #   여기서는 노션이 더 후했다 — 아직 표·도식을 확인하지 못한 보고서를
        #   「검증된」이라고 불렀다. 웹·PDF 와 같은 기준으로 맞춘다.
        확인_중 = (
            report.publication_policy
            == PublicationPolicy.LEGACY_SHADOW_EXCEPTION.value
        )
        blocks.extend(
            [
                _heading_2(
                    "안전 확인 중인 임시 부분 보고서"
                    if 확인_중
                    else "검증된 부분 보고서(부분 완성)"
                ),
                _paragraph(
                    "확인되지 않은 숫자 문장은 제외했지만 모든 문장·표·도식의 "
                    "확인은 아직 끝나지 않았습니다."
                    if 확인_중
                    else "공식 근거로 확인된 항목만 담았습니다. "
                    "확인되지 않은 내용은 추측해 채우지 않았습니다."
                ),
                *(_paragraph(reason) for reason in report.shortfall_reasons),
            ]
        )
    lede = _report_lede_text(report)
    if lede:
        blocks.append(_paragraph(lede))
    blocks.extend(_summary_blocks(report))

    for section in report.sections:
        blocks.extend(_section_blocks(report, section))

    source_blocks = _source_list_blocks(report)
    if source_blocks:
        blocks.append(_heading_2(constants.SOURCES_HEADING))
        blocks.append(_paragraph(constants.SOURCES_SUBTITLE))
        blocks.extend(source_blocks)

    return blocks
