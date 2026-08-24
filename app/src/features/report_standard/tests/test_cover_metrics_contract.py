"""표지 실적 띠의 «배선»과 «정본 좌표» 계약.

값이 맞는지는 ``test_cover_metrics.py``가 본다. 여기서는 그 값이 실제로 화면과
PDF까지 도달하는 데 필요한 두 가지를 잠근다.

1. 화면 틀은 ``cover_metrics``를 방어 없이 호출한다. 등록을 빠뜨리면 결과 화면이
   통째로 깨지므로, 등록 자체를 시험이 지킨다.
2. PDF 띠의 glyph는 정본이 정한 영역(상단에서 138~166mm) 안에 있어야 한다.
   제목 블록(72~118mm)·핵심 요약(190~262mm)을 침범하면 표지 규격이 깨진다.
"""

from __future__ import annotations

import io

import pdfplumber
import pytest

from src.core.citations import citation_marker, citation_number
from src.features.export_pdf import logic as export_pdf_logic
from src.features.export_pdf.logic import build_pdf
from src.features.report_standard.cover_metrics import (
    COVER_METRIC_LABELS,
    cover_metrics,
)
from src.features.report_standard.publish import _forbidden_text_problem
from src.features.report_standard.tests.test_cover_metrics import (
    _report,
    performance_table,  # noqa: F401 — pytest fixture 재사용
)
from src.web import request_helpers


_PT_PER_MM = 72.0 / 25.4


def test_화면_틀이_쓰는_이름으로_순수_함수가_등록돼_있다() -> None:
    """틀은 ``cover_metrics(report)``를 그대로 부른다 — 없으면 화면이 깨진다."""

    registered = request_helpers._ctx.__globals__["cover_metrics"]

    assert registered is cover_metrics


def test_표지_띠_라벨에_출고_차단어가_없다() -> None:
    """``KPI``·``핵심성과지표`` 같은 말은 라벨로 쓰지 않는다.

    ``publish.py``의 ``_FORBIDDEN_JOB_TOPIC``은 그 낱말을 지원자 «직무» 소재로
    보고 차단한다. 표지 장식 문구는 그 검사 범위 밖이지만, 같은 제품 안에서
    차단어를 화면에 쓰지 않는다는 계약을 여기서 잠근다.
    """

    for label in COVER_METRIC_LABELS:
        assert _forbidden_text_problem(label) == ""
        assert label.isascii() is False, "라벨은 표의 한국어 열 이름을 그대로 쓴다"


def test_PDF_표지_띠는_정본_좌표_안에_그려진다(
    performance_table,  # noqa: F811 — 위에서 가져온 fixture
) -> None:
    report = _report(performance_table)
    metrics = cover_metrics(report)
    assert metrics, "시험 전제가 깨졌다 — 띠가 있어야 좌표를 볼 수 있다."

    with pdfplumber.open(io.BytesIO(build_pdf(report))) as document:
        words = document.pages[0].extract_words()

    wanted = {item.value for item in metrics.items} | set(COVER_METRIC_LABELS)
    band_words = [word for word in words if word["text"] in wanted]
    assert len(band_words) >= len(wanted)

    top_mm = min(float(word["top"]) for word in band_words) / _PT_PER_MM
    bottom_mm = max(float(word["bottom"]) for word in band_words) / _PT_PER_MM
    assert export_pdf_logic._COVER_METRICS_TOP_MM <= top_mm
    assert bottom_mm <= export_pdf_logic._COVER_METRICS_BOTTOM_MM


def test_PDF_표지_띠는_제목과_핵심요약_영역을_침범하지_않는다(
    performance_table,  # noqa: F811
) -> None:
    """정본 1절의 세 영역은 서로 겹치지 않는다 — 띠가 여백의 일부만 쓴다."""

    assert 118.0 <= export_pdf_logic._COVER_METRICS_TOP_MM
    assert export_pdf_logic._COVER_METRICS_BOTTOM_MM <= 190.0
    assert (
        export_pdf_logic._COVER_METRICS_TOP_MM
        < export_pdf_logic._COVER_METRICS_BOTTOM_MM
    )


#: 정본 1절의 제목 블록 끝과 핵심 요약 시작 — 그 사이가 띠가 쓰는 여백이다.
_TITLE_BLOCK_BOTTOM_MM = 118.0
_SUMMARY_TOP_MM = 190.0


def _gap_words(report) -> list[dict]:
    """표지에서 제목 블록과 핵심 요약 «사이»에 실제로 찍힌 글자들."""

    with pdfplumber.open(io.BytesIO(build_pdf(report))) as document:
        words = document.pages[0].extract_words()
    return [
        word
        for word in words
        if _TITLE_BLOCK_BOTTOM_MM < float(word["top"]) / _PT_PER_MM < _SUMMARY_TOP_MM
    ]


def test_PDF_여백에_찍힌_글자는_모두_띠_영역_안에_있다(
    performance_table,  # noqa: F811
) -> None:
    """띠 «제목»까지 포함해 실제 glyph가 정본 영역 밖으로 새지 않는지 본다.

    값·라벨만 보면 제목 줄이 위로 새어도 시험이 안 깨진다. 실제로 그랬다 —
    Paragraph ascender 때문에 선언 좌표보다 1.5mm 위에 찍혔다(실측).
    """

    words = _gap_words(_report(performance_table))
    assert words, "띠가 있어야 할 자리에 글자가 하나도 없다."

    for word in words:
        top_mm = float(word["top"]) / _PT_PER_MM
        bottom_mm = float(word["bottom"]) / _PT_PER_MM
        assert export_pdf_logic._COVER_METRICS_TOP_MM <= top_mm, (word["text"], top_mm)
        assert bottom_mm <= export_pdf_logic._COVER_METRICS_BOTTOM_MM, (
            word["text"],
            bottom_mm,
        )


def test_실적표가_없으면_PDF_여백에_글자가_하나도_없다() -> None:
    """빈 자리 금지 — 제목만 남기거나 「—」로 채우지 않고 예전 여백 그대로 둔다."""

    assert _gap_words(_report(None)) == []


def test_PDF_표지_띠는_4장_실적표와_같은_출처_번호를_쓴다(
    performance_table,  # noqa: F811
) -> None:
    """표지에 «새 출처»를 만들지 않는다 — 부록 번호와 1:1이 유지된다."""

    report = _report(performance_table)
    marker = citation_marker(cover_metrics(report).cite)
    assert marker, "시험 전제가 깨졌다 — 실적표에 출처 번호가 있어야 한다."

    with pdfplumber.open(io.BytesIO(build_pdf(report))) as document:
        cover_text = " ".join((document.pages[0].extract_text() or "").split())

    assert marker in cover_text
    assert citation_number(performance_table.cite) == citation_number(
        cover_metrics(report).cite
    )


@pytest.mark.parametrize("label", COVER_METRIC_LABELS)
def test_표지_띠_라벨은_실적표_열_이름_그대로다(
    label: str,
    performance_table,  # noqa: F811
) -> None:
    assert label in performance_table.headers
