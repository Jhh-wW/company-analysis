"""표지 실적 띠 계약 — 표 값 그대로·빈 자리 금지·화면과 PDF 일치.

정본: ``docs/출력물 기준/90_공통_규칙/디자인과_PDF_QA.md`` 1절·6-1절.

★ 왜 실제 ``build_three_year_table`` 출력을 쓰나 — 표지에 올려도 되는 숫자는
  그 함수가 전자공시 원수치로 fail-closed 하게 만든 표뿐이다. 손으로 지어낸
  표로 시험하면 「표가 실제로 만드는 모양」이 바뀌어도 시험이 안 깨진다.
"""

from __future__ import annotations

import io
import re
import uuid
from typing import Any

import pdfplumber
import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.company_performance.logic import build_three_year_table
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.export_pdf.logic import build_pdf
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SummaryItem,
)
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard.cover_metrics import (
    COVER_METRIC_LABELS,
    PERIOD_HEADER,
    cover_metrics,
)
from src.web import job_runtime
from src.web.main import app


_CITE = "조각 1·재무"
_PERIODS = (
    "2025.01.01 ~ 2025.12.31",
    "2024.01.01 ~ 2024.12.31",
    "2023.01.01 ~ 2023.12.31",
)
#: 표지 띠가 «최신» 행을 쓰는지 보려고 세 해의 원값을 크게 벌려 둔다.
_REVENUE = ("821850000000", "601790000000", "566500000000")
_OPERATING_INCOME = ("155250000000", "128260000000", "169440000000")

#: 화면 표지 띠에서 값만 골라내는 표기. 틀이 바뀌면 시험이 먼저 깨져야 한다.
_WEB_METRIC_VALUE = re.compile(
    r'<span class="cover-metric-value">([^<]+)</span>'
)
_COVER_BAND_MARKUP = 'class="cover-metrics"'


def _dart_row(
    account_id: str, account_nm: str, amounts: tuple[str, str, str]
) -> dict[str, Any]:
    return {
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": account_id,
        "account_nm": account_nm,
        "bsns_year": "2025",
        "reprt_code": "11011",
        "currency": "KRW",
        "thstrm_dt": _PERIODS[0],
        "thstrm_amount": amounts[0],
        "frmtrm_dt": _PERIODS[1],
        "frmtrm_amount": amounts[1],
        "bfefrmtrm_dt": _PERIODS[2],
        "bfefrmtrm_amount": amounts[2],
    }


def _financials() -> dict[str, Any]:
    return {
        "status": "000",
        "list": [
            _dart_row("ifrs-full_Revenue", "매출액", _REVENUE),
            _dart_row("dart_OperatingIncomeLoss", "영업이익", _OPERATING_INCOME),
        ],
    }


@pytest.fixture(scope="module")
def performance_table() -> ReportTable:
    table = build_three_year_table(_financials(), cite=_CITE)
    assert table is not None, "시험 전제가 깨졌다 — 실적표가 만들어져야 한다."
    return table


def _sections(table: ReportTable | None) -> list[ReportSection]:
    """실적표만 있고 없고가 다른 두 보고서를 같은 뼈대로 만든다."""

    return [
        ReportSection(
            cell="identity",
            title="기업 정체성",
            display_number="1",
            lines=[("공식 자료 원문", _CITE)],
            prose_lines=[("회사는 공식 자료에 사업 범위를 밝혀 두었습니다.[1]", "")],
            prose_paragraphs=["회사는 공식 자료에 사업 범위를 밝혀 두었습니다.[1]"],
        ),
        ReportSection(
            cell="past_changes",
            title="3개년 주요 변화와 실행",
            display_number="4",
            tag="#과거",
            lines=[("공식 자료 원문", _CITE)],
            prose_lines=[("완료 사업연도 실적은 공식 자료로 확인했습니다.[1]", "")],
            prose_paragraphs=["완료 사업연도 실적은 공식 자료로 확인했습니다.[1]"],
            tables=[table] if table is not None else [],
        ),
        ReportSection(
            cell="future_strategy",
            title="성장 전략",
            display_number="6",
            tag="#미래",
            lines=[("공식 자료 원문", _CITE)],
            prose_lines=[("회사는 공식 발표에서 다음 계획을 밝혔습니다.[1]", "")],
            prose_paragraphs=["회사는 공식 발표에서 다음 계획을 밝혔습니다.[1]"],
        ),
    ]


def _report(table: ReportTable | None) -> Report:
    """v2 스키마 보고서 하나. PDF는 v2 3검사만 다시 통과하면 된다."""

    return Report(
        company="시험상사",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=_sections(table),
        citations=[
            Source(
                number=1,
                kind=SourceKind.FILING,
                label="사업보고서",
                title="사업보고서",
                publisher="시험상사",
                source_type="공식 공시",
                disclosed_at="2026-03-20",
                used_in=["identity", "past_changes", "future_strategy"],
            )
        ],
        generated_at="2026-08-24",
        schema_version=ENGINE_V2_SCHEMA_VERSION,
        as_of_date="2026-08-24",
        summary_items=[
            SummaryItem(text="회사는 공식 자료에 사업 범위를 밝혀 두었습니다.", section_id="identity"),
            SummaryItem(text="완료 사업연도 실적은 공식 자료로 확인했습니다.", section_id="past_changes"),
            SummaryItem(text="회사는 공식 발표에서 다음 계획을 밝혔습니다.", section_id="future_strategy"),
        ],
    )


def _result_html(monkeypatch: pytest.MonkeyPatch, report: Report) -> str:
    """실제 결과 화면을 그대로 받아 온다 — 틀과 등록 배선까지 함께 지킨다."""

    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
    job_id = f"band-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _report_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)

    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get(f"/result/{job_id}")

    assert response.status_code == 200
    return response.text


def _pdf_cover_text(report: Report) -> str:
    with pdfplumber.open(io.BytesIO(build_pdf(report))) as document:
        return " ".join((document.pages[0].extract_text() or "").split())


# ══════════════════════════════════════════════════════════
# ① 표 값을 «그대로» 쓴다 (계산 금지)
# ══════════════════════════════════════════════════════════


def test_표지_띠는_실적표_최신_사업연도_행을_글자_그대로_쓴다(
    performance_table: ReportTable,
) -> None:
    metrics = cover_metrics(_report(performance_table))
    latest = performance_table.rows[0]

    assert [item.label for item in metrics.items] == list(COVER_METRIC_LABELS)
    for item in metrics.items:
        column = performance_table.headers.index(item.label)
        assert item.value == latest[column]
        assert item.unit == performance_table.display_unit
    assert metrics.title.startswith(f"{latest[0]} {PERIOD_HEADER}")
    assert metrics.cite == performance_table.cite


def test_표지_띠는_과거_사업연도_행을_쓰지_않는다(
    performance_table: ReportTable,
) -> None:
    metrics = cover_metrics(_report(performance_table))
    older_values = {
        cell for row in performance_table.rows[1:] for cell in row
    }
    latest_values = set(performance_table.rows[0])

    shown = {item.value for item in metrics.items}
    assert shown <= latest_values
    assert not (shown & (older_values - latest_values))


def test_표지_띠는_새_숫자를_만들지_않는다(
    performance_table: ReportTable,
) -> None:
    """띠에 인쇄되는 모든 글자는 표 첫 행이나 표 머리글에서 온 것뿐이다."""

    metrics = cover_metrics(_report(performance_table))
    allowed = set(performance_table.rows[0]) | set(performance_table.headers)
    allowed.add(performance_table.display_unit)

    for item in metrics.items:
        assert item.label in allowed
        assert item.value in allowed
        assert item.unit in allowed
    # 비율·증감률은 어떤 형태로도 표지에 오르지 않는다.
    printed = metrics.title + "".join(
        item.label + item.value + item.unit for item in metrics.items
    )
    assert "%" not in printed
    assert "증감" not in printed and "성장" not in printed


# ══════════════════════════════════════════════════════════
# ② 실적표가 없는 회사 — 빈 자리 금지
# ══════════════════════════════════════════════════════════


def test_실적표가_없으면_표지_띠_값이_비어_있다() -> None:
    metrics = cover_metrics(_report(None))

    assert not metrics
    assert metrics.items == ()
    assert metrics.title == ""


def test_실적표가_없으면_화면_표지에_띠_자리가_아예_없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _result_html(monkeypatch, _report(None))

    assert _COVER_BAND_MARKUP not in body
    assert _WEB_METRIC_VALUE.search(body) is None


def test_실적표가_없으면_PDF_표지에_띠가_없다(
    performance_table: ReportTable,
) -> None:
    cover_text = _pdf_cover_text(_report(None))

    for label in COVER_METRIC_LABELS:
        assert label not in cover_text
    for value in performance_table.rows[0][1:]:
        assert value not in cover_text


# ══════════════════════════════════════════════════════════
# ③ 화면과 PDF가 같은 값을 쓴다
# ══════════════════════════════════════════════════════════


def test_화면_표지에_실적표와_같은_값이_크게_나온다(
    monkeypatch: pytest.MonkeyPatch,
    performance_table: ReportTable,
) -> None:
    body = _result_html(monkeypatch, _report(performance_table))
    metrics = cover_metrics(_report(performance_table))

    assert _COVER_BAND_MARKUP in body
    assert _WEB_METRIC_VALUE.findall(body) == [item.value for item in metrics.items]
    for item in metrics.items:
        assert item.label in body
    assert metrics.title in body


def test_PDF_표지에_실적표와_같은_값이_나온다(
    performance_table: ReportTable,
) -> None:
    report = _report(performance_table)
    cover_text = _pdf_cover_text(report)
    metrics = cover_metrics(report)

    for item in metrics.items:
        assert item.label in cover_text
        assert item.value in cover_text


def test_화면과_PDF_표지가_같은_값을_쓴다(
    monkeypatch: pytest.MonkeyPatch,
    performance_table: ReportTable,
) -> None:
    report = _report(performance_table)
    body = _result_html(monkeypatch, report)
    cover_text = _pdf_cover_text(report)

    web_values = _WEB_METRIC_VALUE.findall(body)
    assert web_values, "화면 표지 띠가 없으면 두 채널을 비교할 수 없다."
    for value in web_values:
        assert value in cover_text
    assert web_values == [item.value for item in cover_metrics(report).items]
