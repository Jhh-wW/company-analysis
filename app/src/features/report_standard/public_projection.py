"""완성된 ``Report``에서 공개 봉인 블록(``PublicReportProjection``)을 만든다.

★ 이 파일이 하는 일 — 지금까지 웹·PDF·Notion이 «렌더할 때마다 각자» 계산하던
  표시 파생 블록(도식·읽는 법·3개년 띠·표지 지표·요약 번호·문단 번호·부록
  검증 라벨·부록 행 문자열)을 «봉인 시점에 한 번» 계산해 블록에 담는다.
  순수 함수 자체는 하나도 고치지 않는다 — 호출 «시점»만 렌더에서 봉인으로
  옮긴다. 그래야 세 채널이 같은 값에서 나왔다고 기계로 말할 수 있다.

★ 새 사실·새 문장을 만들지 않는다. 이 모듈이 내는 모든 문자열은 ① ``report``
  안에 이미 있는 값이거나 ② 같은 feature의 순수 함수(``visualization``·
  ``period_summary``·``cover_metrics``·``section_content``)가 그 값에서 만든
  결과다. 예외는 «표시 관례»뿐이고 전부 지금 렌더러가 이미 붙이던 것이다 —
  문단 번호 ``1.``(result.html의 ``.pno`` · export_pdf의
  ``_paragraph_number_markup``), 요약 번호 ``01``, 부록 구분자 ``·``,
  값이 없을 때의 ``—``. 각각 어디서 왔는지 아래 상수 주석에 적어 뒀다.

★ 자료형·불변식·digest는 ``shared/report_generation/public_projection.py``(S1)에
  있고 이 모듈은 그것을 «채우기만» 한다. S1이 부분적으로만 닫아 둔 I3·I4·I6은
  원본 ``Report``를 들고 있는 이 모듈이 완전하게 검사한다:
    · I3 — 모든 ``report.fact_records``가 정확히 한 장의 ledger에 속한다.
    · I4 — 장별 기여 ∪ 요약 기여 == ``report.source_grades``.
    · I6 — 부록 검증 라벨 == ``source_verification_label()`` 결과.

★ 경계 — ``report_standard``는 feature다. ``web``·``export_pdf``·
  ``export_notion``·``composer``를 import하지 않는다. ``shared``와
  ``pipeline.port`` 자료형, 그리고 같은 feature 안 모듈만 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final, Optional

from src.features.pipeline.port import Report, ReportSection, ReportTable
from src.features.provenance.sources import Source, visible_citations
from src.features.report_standard.constants import SECTION_BY_ID
from src.features.report_standard.cover_metrics import cover_metrics
from src.features.report_standard.period_summary import period_summary_from_table
from src.features.report_standard.section_content import (
    # ★ 밑줄로 시작하는 두 값을 «같은 feature 안에서» 그대로 가져다 쓴다.
    #   문장 끝 «— 해석» 표지와 그 등급 이름은 이미 composer → section_content로
    #   한 번 옮겨 적힌 값이라(그쪽 상수 주석 참조) 여기서 또 한 벌 적으면
    #   세 곳이 되고, 셋 중 하나만 바뀌면 조용히 갈라진다.
    _V2_INTERPRETATION_MARKER,
    _V2_INTERPRETED_GRADE,
    source_verification_label,
    summary_topic,
)
from src.features.report_standard.visualization import (
    Card,
    ChartPoint,
    ChartSeries,
    TableVisualization,
    table_visualization,
)
from src.shared.report_generation.canonical import (
    report_public_content_projection,
    table_public_projection,
)
from src.shared.report_generation.models import canonical_value
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    SECTION_IDS,
    PublicCitationRow,
    PublicCoverMetricsBlock,
    PublicPeriodSummaryBlock,
    PublicProjectionError,
    PublicReportProjection,
    PublicSectionContentBlock,
    PublicSectionDisplay,
    PublicSectionLedger,
    PublicSummaryRow,
    PublicTableBlock,
    PublicVisualBlock,
)


# ══════════════════════════════════════════════════════════
# 표시 관례 상수 — 전부 «지금 렌더러가 쓰는 값»을 옮긴 것이다
# ══════════════════════════════════════════════════════════

#: 지문 A(``canonical.py``의 ``public_content_projection``)가 덮는 보고서 메타.
#: header는 이 키들을 «그 함수 결과에서 그대로» 옮겨 담는다 — 따로 조립하면
#: 지문 A와 header가 조용히 갈라진다.
_HEADER_META_KEYS: Final[tuple[str, ...]] = (
    "company",
    "company_id",
    "job",
    "corp_type",
    "generated_at",
    "schema_version",
    "as_of_date",
    "analysis_period",
    "latest_performance_period",
    "grade",
    "shortfall_reasons",
    "quality_contract_version",
    "safety_decision",
    "publication_policy",
)

#: 도식 비율의 표시 자릿수. 화면이 쓰는 ``'%.4f'|format(item.ratio)``
#: (``web/templates/result.html``)와 «같은 자릿수»다. float를 그대로 실으면
#: canonical이 거부하므로 여기서 십진 문자열로 굳힌다.
_RATIO_FORMAT: Final[str] = ".4f"

#: 추이 도식 계열의 «위험(손실)» 표식. S1 자료형이 이 자리를 문자열로
#: 받으므로(bool 불가) 참을 ``risk``, 거짓을 빈 글자로 적는다 — 빈 글자는
#: 템플릿에서 거짓으로 읽혀 ``{% if %}``가 지금과 같이 동작한다.
_RISK_FLAG: Final[str] = "risk"

#: 문단 번호. 웹 ``.pno``(``{{ loop.index }}.``)와 PDF
#: ``_paragraph_number_markup``(``f"{position}."``)이 쓰는 모양 그대로다.
_PARAGRAPH_ORDINAL_SUFFIX: Final[str] = "."

#: 핵심 요약 번호. 지금 세 채널이 각자 ``%02d``로 찍는 그 번호다.
_SUMMARY_ORDINAL_WIDTH: Final[int] = 2

#: 부록 한 칸 안에서 여러 값을 잇는 구분자(자료명·기준일·사용 장 모두 동일).
_APPENDIX_JOINER: Final[str] = " · "

#: 값이 없는 부록 칸에 찍는 글자. 빈 칸을 남기지 않는다는 계약이다.
_APPENDIX_EMPTY: Final[str] = "—"

#: 기준일을 하나도 모를 때의 부록 상태 문구.
_UNKNOWN_AS_OF_TEXT: Final[str] = "기준일 미확인"

_PUBLISHED_SUFFIX: Final[str] = " 보도"
_DISCLOSED_SUFFIX: Final[str] = " 공시"
_COLLECTED_SUFFIX: Final[str] = " 확인"

#: 장 번호 표시 접미사(부록 「본문 사용 장」 칸).
_SECTION_LABEL_SUFFIX: Final[str] = "장"

#: 부분 보고서 고지 — 이제 «항상 비어 있다».
#:
#: ★ 왜 비웠나 (사용자 결정, 2026-09-05): 서비스가 출시됐고 보고서는 더 이상
#:   「임시」가 아니다. 「안전 확인 중」·「새 검증은 아직 끝나지 않았습니다」·
#:   「확인하지 못했습니다」는 전부 «우리가 무엇을 아직 못 했는지»를 적은
#:   과정·변명 문구다. 독자가 할 수 있는 일이 없는 정보는 화면에서 뺀다.
#: ★ 지운 것은 «표시»뿐이다. 판정(등급·안전 결정)과 내부 사유
#:   (``shortfall_reasons``·``safety_decision``·``publication_policy``)는
#:   header에 그대로 실려 저장·관리자 화면·진단에서 계속 읽힌다.
#: ★ 자리(``grade_notice`` 필드)는 남긴다 — 이미 봉인된 옛 저장본이 값을 갖고
#:   있고, 그 봉인을 깨지 않은 채로 «채널이 그리지 않게» 하는 것이 목표다.
_NO_NOTICE: Final[tuple[str, str]] = ("", "")

#: 본문 표시 문장에 박힌 인용 번호 ``[1]``을 읽는다. ``section_content.py``의
#: ``_CITATION_NUMBER_PATTERN``과 같은 모양이다.
_CITATION_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[(\d+)\]")

#: 해석이 아닌 문장의 등급. ``composer.constants.GRADE_CONFIRMED`` 값이며
#: 이 모듈은 «본문 글자에서 되짚을 때»만 쓴다(아래 등급 기여 주석 참조).
_V2_CONFIRMED_GRADE: Final[str] = "확인"


# ══════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════


def build_public_projection(report: Report) -> PublicReportProjection:
    """완성된 보고서 하나를 공개 봉인 블록으로 바꾼다.

    Args:
        report: 렌더까지 끝난 ``pipeline.port.Report``. 아홉 장이 정본 순서로
            들어 있어야 하고, 표에는 지문 B가 붙인 ``manifest_ref``가 있어야
            한다.

    Returns:
        웹·PDF·Notion이 «그대로 배치만» 하면 되는 ``PublicReportProjection``.

    Raises:
        PublicProjectionError: 장 구성·감사 장부·등급 기여가 원본 ``Report``와
            어긋날 때. 조용히 고치지 않고 닫는다 — 어긋난 채로 저장하면 화면과
            장부가 다른 보고서가 남는다.
    """

    _require_canonical_sections(report)

    citation_rows = _citation_rows(report)
    citation_numbers = {row.number for row in citation_rows}
    ledgers = _section_ledgers(report, citation_numbers)

    sections = tuple(
        PublicSectionContentBlock(
            version=PUBLIC_PROJECTION_VERSION,
            display=_section_display(section),
            ledger=ledgers.section_ledgers[section.cell],
        )
        for section in report.sections
    )

    return PublicReportProjection(
        version=PUBLIC_PROJECTION_VERSION,
        header=_header(report),
        cover_metrics=_cover_metrics_block(report),
        summary=_summary_rows(report),
        sections=sections,
        citations=citation_rows,
        summary_source_grade_contribution=ledgers.summary_contribution,
        grade_notice=_grade_notice(report),
    )


# ══════════════════════════════════════════════════════════
# 헤더·표지·요약·고지
# ══════════════════════════════════════════════════════════


def _require_canonical_sections(report: Report) -> None:
    """장 순서·id가 정본 아홉 장과 같은지 먼저 닫는다.

    S1도 같은 검사를 하지만, 여기서 먼저 닫아야 «어느 장이 빠졌는지»를
    말할 수 있다 — S1은 프로젝션이 다 만들어진 뒤에야 본다.
    """

    cells = tuple(str(section.cell) for section in report.sections)
    if cells != SECTION_IDS:
        raise PublicProjectionError(
            f"I1: 보고서 장 구성이 정본 아홉 장과 다릅니다 (받은 값: {cells})"
        )


def _header(report: Report) -> dict[str, object]:
    """지문 A가 덮는 메타 그대로 + 출고 모드."""

    content = report_public_content_projection(report)
    header: dict[str, object] = {key: content[key] for key in _HEADER_META_KEYS}
    # release_mode는 지문 A에 없다(검증 payload에만 있다). 화면이 「지금 어떤
    # 모드로 낸 보고서인지」를 블록만 보고 말할 수 있어야 해서 여기 싣는다.
    header["release_mode"] = str(report.release_mode or "")
    return header


def _cover_metrics_block(report: Report) -> Optional[PublicCoverMetricsBlock]:
    """표지 실적 띠 — 순수 함수가 못 고르면 블록도 없다(빈 자리 금지)."""

    metrics = cover_metrics(report)
    if not metrics:
        return None
    return PublicCoverMetricsBlock(
        title=metrics.title,
        cite=metrics.cite,
        items=tuple((item.label, item.value, item.unit) for item in metrics.items),
    )


def _summary_rows(report: Report) -> tuple[PublicSummaryRow, ...]:
    """핵심 요약 — 번호·주제어·장 번호를 세 채널이 각자 만들지 않게 한 번만 붙인다."""

    rows: list[PublicSummaryRow] = []
    for position, item in enumerate(report.summary_items, start=1):
        section_id = str(item.section_id or "")
        spec = SECTION_BY_ID.get(section_id)
        # ★ 빈 section_id = 「장 없음」 — render의
        #   ``_summary_source_section``이 인용 없는 요약 문장에 «틀린 장을
        #   가리키느니 비운다»는 뜻으로 남기는 값이다. 가리킬 장이 없으니 장
        #   번호도 비운다(지어내지 않는다). 빈 글자가 «아닌데» 정본 밖이면
        #   그건 오타·옛 id라 그대로 닫는다.
        if spec is None and section_id != "":
            raise PublicProjectionError(
                f"요약 항목이 정본 장 목록 밖을 가리킵니다: {section_id!r}"
            )
        rows.append(
            PublicSummaryRow(
                ordinal=f"{position:0{_SUMMARY_ORDINAL_WIDTH}d}",
                topic=summary_topic(section_id),
                section_display_number="" if spec is None else spec.display_number,
                text=str(item.text or ""),
                section_id=section_id,
            )
        )
    return tuple(rows)


def _grade_notice(report: Report) -> tuple[str, str]:
    """독자용 고지는 만들지 않는다 — 항상 빈 고지를 봉인한다.

    ★ 등급이나 정책을 «읽지 않는» 것이 요점이다. 정책마다 다른 문구를 고르던
      갈래가 남아 있으면 언젠가 그중 하나가 다시 화면에 붙는다. 등급 판정
      자체는 그대로다(``report.grade``·``publication_policy``는 header에 실린다).
    """

    return _NO_NOTICE


# ══════════════════════════════════════════════════════════
# 장 표시(display)
# ══════════════════════════════════════════════════════════


def _section_display(section: ReportSection) -> PublicSectionDisplay:
    tables = tuple(_table_block(table) for table in section.tables)
    visuals: list[PublicVisualBlock] = []
    period_summary: Optional[PublicPeriodSummaryBlock] = None
    for index, table in enumerate(section.tables):
        chart = table_visualization(table)
        if chart is not None:
            visuals.append(_visual_block(index, chart))
        if period_summary is None:
            band = _period_summary_block(table)
            if band is not None:
                period_summary = band

    return PublicSectionDisplay(
        cell=str(section.cell),
        display_number=str(section.display_number or ""),
        title=str(section.title or ""),
        tag=str(section.tag or ""),
        paragraphs=_paragraphs(section),
        sentences=tuple(
            (str(text), str(cite)) for text, cite in section.prose_lines
        ),
        empty_reason=str(section.empty_reason or ""),
        guidance_lines=tuple(str(line) for line in section.guidance_lines),
        tables=tables,
        visuals=tuple(visuals),
        period_summary=period_summary,
    )


def _paragraphs(section: ReportSection) -> tuple[tuple[str, str], ...]:
    """문단 번호를 봉인 시점에 «한 번» 매긴다.

    ★ 지금은 웹(``result.html``의 ``.pno``)과 PDF(``_paragraph_number_markup``)가
      각자 같은 계산을 한다. 한쪽만 고치면 「3번 문단 보세요」가 어긋난다 —
      실제로 두 번 어긋난 적이 있어 여기로 옮긴다.
    ★ ``prose_paragraphs``가 비면 옛 저장본이다. 그때는 지금 렌더러가 하는
      대로 문장을 한 문단으로 이어 붙인다(뒤로 호환).
    """

    texts = [str(text) for text in section.prose_paragraphs]
    if not texts:
        joined = " ".join(str(text) for text, _cite in section.prose_lines)
        texts = [joined] if joined else []
    return tuple(
        (f"{position}{_PARAGRAPH_ORDINAL_SUFFIX}", text)
        for position, text in enumerate(texts, start=1)
    )


def _table_block(table: ReportTable) -> PublicTableBlock:
    """표 7필드 + 지문 B 참조. 7필드는 지문 A와 «같은 함수»에서 나온다."""

    projected = table_public_projection(table)
    manifest_ref = str(getattr(table, "manifest_ref", "") or "")
    if not manifest_ref:
        # 지문 B(공개 구조 manifest)가 붙인 참조가 없으면 표 구조 위조를
        # projection만으로 잡을 수 없다(설계 결정 D7). 조용히 빈 값으로 넘기지
        # 않고 닫는다 — 어떤 표가 봉인되지 않았는지 여기서 말해 준다.
        raise PublicProjectionError(
            f"표 manifest_ref가 비어 있습니다 (표: {projected['caption']!r})"
        )
    return PublicTableBlock(
        caption=str(projected["caption"]),
        headers=tuple(str(value) for value in projected["headers"]),
        rows=tuple(
            tuple(str(cell) for cell in row) for row in projected["rows"]
        ),
        cite=str(projected["cite"]),
        numeric=bool(projected["numeric"]),
        presentation=str(projected["presentation"]),
        display_unit=str(projected["display_unit"]),
        manifest_ref=manifest_ref,
    )


def _ratio_text(value: float) -> str:
    """도식 비율을 화면과 «같은 자릿수»의 십진 문자열로 굳힌다."""

    return format(value, _RATIO_FORMAT)


def _point_mapping(point: ChartPoint) -> dict[str, object]:
    return {
        "label": point.label,
        "display": point.display,
        "ratio_text": _ratio_text(point.ratio),
        "below": point.below,
    }


def _series_row(series: ChartSeries) -> tuple[str, str, tuple[Mapping[str, object], ...]]:
    return (
        series.label,
        _RISK_FLAG if series.risk else "",
        tuple(_point_mapping(point) for point in series.points),
    )


def _card_row(card: Card) -> tuple[str, Mapping[str, object]]:
    """카드 한 장. 줄 «순서»가 뜻을 가지므로 dict가 아니라 배열로 담는다.

    canonical 직렬화는 Mapping의 key를 정렬하므로 라벨을 key로 쓰면 화면 줄
    순서가 조용히 바뀐다.
    """

    return (card.title, {"fields": [[field.label, field.value] for field in card.fields]})


def _visual_block(table_index: int, chart: TableVisualization) -> PublicVisualBlock:
    return PublicVisualBlock(
        table_index=table_index,
        kind=chart.kind,
        caption=chart.caption,
        unit=chart.unit,
        note=chart.note,
        reading=chart.reading,
        items=tuple(
            (point.label, point.display, _ratio_text(point.ratio), point.below)
            for point in chart.items
        ),
        series=tuple(_series_row(series) for series in chart.series),
        flows=chart.flows,
        cards=tuple(_card_row(card) for card in chart.cards),
    )


def _period_summary_block(table: ReportTable) -> Optional[PublicPeriodSummaryBlock]:
    """3개년 변화 요약 띠 — 실적표가 아니면 순수 함수가 빈 결과를 준다."""

    band = period_summary_from_table(table)
    if not band:
        return None
    return PublicPeriodSummaryBlock(
        title=band.title,
        cite=band.cite,
        items=tuple(
            (
                item.label,
                item.base_period,
                item.base_value,
                item.latest_period,
                item.latest_value,
                item.unit,
                item.change,
                item.change_kind,
                item.direction,
                item.note,
            )
            for item in band.items
        ),
    )


# ══════════════════════════════════════════════════════════
# 부록(citations)
# ══════════════════════════════════════════════════════════


def _source_label_display(source: Source) -> str:
    """문서명 + 발행 주체. 웹·PDF가 «이미 같은 규칙»으로 만들던 값이다."""

    label = (source.title or source.label).strip()
    publisher = source.publisher.strip()
    if publisher and publisher.casefold() not in label.casefold():
        return f"{label}{_APPENDIX_JOINER}{publisher}"
    return label


def _source_status_display(source: Source) -> str:
    """기준일과 자료 상태. 지금 PDF 부록이 쓰는 조립 규칙 그대로다.

    ★ 웹 v2는 ``fact_status``를 빼고 그린다(채널이 갈라지던 자리다).
      봉인은 «더 많이 말하는» PDF 쪽을 정본으로 삼는다 — 다운로드본이 정본이고,
      빼는 것은 사실을 감추는 방향이라 되돌리기 어렵다.
    """

    parts: list[str] = []
    if source.published_at:
        parts.append(f"{source.published_at}{_PUBLISHED_SUFFIX}")
    elif source.disclosed_at:
        parts.append(f"{source.disclosed_at}{_DISCLOSED_SUFFIX}")
    elif source.collected_at:
        parts.append(f"{source.collected_at}{_COLLECTED_SUFFIX}")
    else:
        parts.append(_UNKNOWN_AS_OF_TEXT)
    for value in (source.domain, source.source_type, source.fact_status):
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return _APPENDIX_JOINER.join(parts)


def _source_used_in_display(source: Source) -> str:
    """「본문 사용 장」 칸. 장 번호는 정본 표(``SECTION_BY_ID``)에서 온다."""

    labels: list[str] = []
    for section_id in source.used_in:
        spec = SECTION_BY_ID.get(str(section_id).strip())
        label = (
            f"{spec.display_number}{_SECTION_LABEL_SUFFIX}"
            if spec is not None
            else str(section_id).strip()
        )
        if label and label not in labels:
            labels.append(label)
    return _APPENDIX_JOINER.join(labels) or _APPENDIX_EMPTY


def _citation_rows(report: Report) -> tuple[PublicCitationRow, ...]:
    """부록 표 한 행씩. 같은 번호는 지금 렌더러처럼 한 번만 낸다."""

    rows: list[PublicCitationRow] = []
    seen_numbers: set[int] = set()
    for source in visible_citations(report.citations):
        if source.number in seen_numbers:
            continue
        seen_numbers.add(source.number)
        source_dict = canonical_value(source)
        if not isinstance(source_dict, dict):  # pragma: no cover - dataclass 계약 방어
            raise PublicProjectionError("부록 Source를 canonical 객체로 만들 수 없습니다")
        rows.append(
            PublicCitationRow(
                number=source.number,
                label_display=_source_label_display(source),
                url=str(source.url or ""),
                status_display=_source_status_display(source),
                # I6 — 순수 함수 결과를 그대로 봉인한다. 렌더 시점에 다시
                # 세면 채널마다 다른 라벨이 나올 수 있다.
                verification_label=source_verification_label(report, source.source_id),
                location=str(source.location or "").strip() or _APPENDIX_EMPTY,
                used_in_display=_source_used_in_display(source),
                source=source_dict,
            )
        )
    return tuple(rows)


# ══════════════════════════════════════════════════════════
# 장부(ledger) — FactRecord 배정과 등급 기여
# ══════════════════════════════════════════════════════════


class _Ledgers:
    """장별 ledger와 요약 등급 기여를 함께 나르는 작은 그릇."""

    __slots__ = ("section_ledgers", "summary_contribution")

    def __init__(
        self,
        section_ledgers: Mapping[str, PublicSectionLedger],
        summary_contribution: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> None:
        self.section_ledgers = section_ledgers
        self.summary_contribution = summary_contribution


def _assign_fact_records(report: Report) -> dict[str, tuple[Mapping[str, object], ...]]:
    """I3(완전 검사) — 모든 FactRecord를 «정확히 한 장»에 배정한다."""

    records_by_id: dict[str, object] = {}
    for fact in report.fact_records:
        fact_id = str(getattr(fact, "fact_id", "") or "")
        if not fact_id:
            raise PublicProjectionError("I3: fact_id가 빈 FactRecord가 있습니다")
        if fact_id in records_by_id:
            raise PublicProjectionError(f"I3: fact_id가 중복입니다: {fact_id}")
        records_by_id[fact_id] = fact

    owner_of: dict[str, str] = {}
    by_section: dict[str, tuple[Mapping[str, object], ...]] = {}
    for section in report.sections:
        records: list[Mapping[str, object]] = []
        for fact_id in section.fact_ids:
            fact_id = str(fact_id)
            if fact_id not in records_by_id:
                raise PublicProjectionError(
                    f"I3: {section.cell} 장이 장부에 없는 fact_id를 가리킵니다: {fact_id}"
                )
            if fact_id in owner_of:
                raise PublicProjectionError(
                    f"I3: fact_id {fact_id}를 {owner_of[fact_id]}·{section.cell} 두 장이 나눠 가집니다"
                )
            owner_of[fact_id] = str(section.cell)
            fact = records_by_id[fact_id]
            section_owner = str(getattr(fact, "section_owner", "") or "")
            if section_owner and section_owner != str(section.cell):
                raise PublicProjectionError(
                    f"I3: fact_id {fact_id}의 주인은 {section_owner}인데 "
                    f"{section.cell} 장에 실렸습니다"
                )
            record = canonical_value(fact)
            if not isinstance(record, dict):  # pragma: no cover - dataclass 계약 방어
                raise PublicProjectionError("FactRecord를 canonical 객체로 만들 수 없습니다")
            records.append(record)
        by_section[str(section.cell)] = tuple(records)

    orphans = sorted(set(records_by_id) - set(owner_of))
    if orphans:
        raise PublicProjectionError(
            f"I3: 어느 장에도 속하지 않은 FactRecord가 있습니다: {orphans}"
        )
    return by_section


def _visible_grades(text: str, known_numbers: Mapping[str, Sequence[str]]) -> tuple[str, tuple[str, ...]]:
    """표시 문장 하나가 «화면 글자만으로» 말해 주는 (등급, 인용 번호들)."""

    grade = (
        _V2_INTERPRETED_GRADE
        if text.endswith(_V2_INTERPRETATION_MARKER)
        else _V2_CONFIRMED_GRADE
    )
    numbers = tuple(
        number
        for number in dict.fromkeys(_CITATION_NUMBER_PATTERN.findall(text))
        if number in known_numbers and grade in known_numbers[number]
    )
    return grade, numbers


def _grade_contributions(
    report: Report, citation_numbers: set[int]
) -> tuple[dict[str, dict[str, set[str]]], dict[str, set[str]]]:
    """I4 — 등급 기여를 장과 요약으로 나눈다. 합치면 ``source_grades``와 같다.

    ★ 어디까지 정확한가 (한계를 숨기지 않는다) — render는 «번호를 보였는지와
      무관하게» 등급을 ``report.source_grades``에 «합쳐서» 싣는다
      (``composer/render.py``의 ``source_grades`` 조립). 완성된 보고서에는
      「어느 장의 어느 문장이 그 등급을 남겼는가」가 «장별로» 남아 있지 않다.
      그래서 이 함수는 두 단계로 나눈다:
        ① 화면 글자로 «확실히 아는» 것 — 문장에 번호가 보이면 그 장의 기여다.
           절충안 인용 규칙이 번호를 안 숨긴 경우가 여기 해당하고, 보통이다.
        ② 남은 등급(번호가 숨겨져 누가 남겼는지 못 짚는 것) — 그 자료를 실제로
           «쓴» 장 전부(``Source.used_in``, 표시와 무관한 사실)에 남긴다. 쓴 장이
           하나도 없으면 요약이 쓴 것이므로 요약 기여로 간다.
      ②는 «장별로는 넓게 잡힐 수 있다»(두 장이 같은 자료를 썼고 한쪽 번호만
      숨었으면 두 장 모두에 그 등급이 붙는다). 합계는 언제나 정확하고, 좁게
      잡아 등급을 «잃는» 방향으로는 틀리지 않는다 — 감사 장부에서 사라지는
      쪽이 더 위험하기 때문이다.
    """

    known: dict[str, list[str]] = {}
    for number, grades in report.source_grades.items():
        cleaned = tuple(dict.fromkeys(str(grade) for grade in grades))
        if not cleaned:
            continue
        if int(number) not in citation_numbers:
            raise PublicProjectionError(
                f"I4: 등급이 달린 출처 번호 {number}가 부록에 없습니다"
            )
        known[str(number)] = list(cleaned)

    by_section: dict[str, dict[str, set[str]]] = {
        str(section.cell): {} for section in report.sections
    }
    summary: dict[str, set[str]] = {}

    # ① 화면 글자로 확실히 아는 기여.
    for section in report.sections:
        bucket = by_section[str(section.cell)]
        for text, _cite in section.prose_lines:
            grade, numbers = _visible_grades(str(text), known)
            for number in numbers:
                bucket.setdefault(number, set()).add(grade)
    for item in report.summary_items:
        grade, numbers = _visible_grades(str(item.text or ""), known)
        for number in numbers:
            summary.setdefault(number, set()).add(grade)

    # ② 남은 등급 — 그 자료를 실제로 쓴 장(또는 요약)에 남긴다.
    owners_by_number = _owners_by_number(report)
    for number, grades in known.items():
        claimed: set[str] = set(summary.get(number, set()))
        for bucket in by_section.values():
            claimed |= bucket.get(number, set())
        residual = set(grades) - claimed
        if not residual:
            continue
        owners = owners_by_number.get(number, ())
        if owners:
            for cell in owners:
                by_section[cell].setdefault(number, set()).update(residual)
        else:
            summary.setdefault(number, set()).update(residual)

    return by_section, summary


def _owners_by_number(report: Report) -> dict[str, tuple[str, ...]]:
    """출처 번호 → 그 자료를 «본문에서 쓴» 장 목록.

    ``Source.used_in``은 render가 「번호를 보였는지와 무관하게」 적어 둔 값이라
    (``composer/render.py``의 used_sections 주석) 표시 방식에 흔들리지 않는다.
    """

    known_cells = {str(section.cell) for section in report.sections}
    owners: dict[str, tuple[str, ...]] = {}
    for source in visible_citations(report.citations):
        number = str(source.number)
        if number in owners:
            continue
        owners[number] = tuple(
            cell
            for cell in dict.fromkeys(str(value).strip() for value in source.used_in)
            if cell in known_cells
        )
    return owners


def _ordered_contribution(
    contribution: Mapping[str, set[str]], known_order: Mapping[str, Sequence[str]]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """저장 순서를 못 박는다 — 번호는 숫자 크기순, 등급은 원본 순서."""

    return tuple(
        (
            number,
            tuple(
                grade for grade in known_order.get(number, ()) if grade in contribution[number]
            ),
        )
        for number in sorted(contribution, key=lambda value: (int(value), value))
        if contribution[number]
    )


def _section_ledgers(report: Report, citation_numbers: set[int]) -> _Ledgers:
    records_by_section = _assign_fact_records(report)
    by_section, summary = _grade_contributions(report, citation_numbers)
    known_order = {
        str(number): tuple(dict.fromkeys(str(grade) for grade in grades))
        for number, grades in report.source_grades.items()
    }

    ledgers: dict[str, PublicSectionLedger] = {}
    for section in report.sections:
        cell = str(section.cell)
        ledgers[cell] = PublicSectionLedger(
            fact_ids=tuple(str(fact_id) for fact_id in section.fact_ids),
            fact_records=records_by_section[cell],
            source_grade_contribution=_ordered_contribution(
                by_section[cell], known_order
            ),
        )
    return _Ledgers(ledgers, _ordered_contribution(summary, known_order))


__all__ = ["build_public_projection"]
