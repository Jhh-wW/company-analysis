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
    COVER_METRIC_CANDIDATES,
    COVER_METRIC_LABELS,
    COVER_METRIC_MAX,
    COVER_METRIC_MIN,
    PERIOD_HEADER,
    cover_metrics,
)
from src.web import job_runtime
from src.web.main import app
from src.web.tests.report_route_support import serve_legacy_report_snapshot


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


#: 은행 손익계산서 모양 — 「매출액」에 해당하는 계정이 «아예 없다».
#:   실측(우리은행): 17개 행에서 영업이익·당기순이익만 나왔다.
_NET_INCOME = ("118400000000", "97300000000", "129900000000")


def _bank_financials() -> dict[str, Any]:
    return {
        "status": "000",
        "list": [
            _dart_row("dart_OperatingIncomeLoss", "영업이익", _OPERATING_INCOME),
            _dart_row("ifrs-full_ProfitLoss", "당기순이익", _NET_INCOME),
        ],
    }


def test_매출액이_없는_표도_표지_띠를_그린다() -> None:
    """★ 제품 결정 ① — 없는 지표를 지어내지 말고 있는 것으로 그린다.

    예전에는 매출액·영업이익이 «둘 다» 있어야 띠를 그려서, 매출액 계정이 없는
    은행은 표지 실적 박스가 통째로 사라졌다.
    ⚠️ 이 시험이 깨지면 은행·보험 업종의 표지가 다시 비어 나간다.
    """
    table = build_three_year_table(_bank_financials(), cite=_CITE)
    assert table is not None, "시험 전제 — 은행 모양 표가 만들어져야 한다"

    metrics = cover_metrics(_report(table))

    assert [item.label for item in metrics.items] == ["영업이익", "당기순이익"]
    assert len(metrics.items) == 2


def _three_metric_table() -> ReportTable:
    """매출액·영업이익·당기순이익이 «다» 있는 보통 회사의 실적표."""

    full = _financials()
    full["list"].append(
        _dart_row("ifrs-full_ProfitLoss", "당기순이익", _NET_INCOME)
    )
    table = build_three_year_table(full, cite=_CITE)
    assert table is not None, "시험 전제 — 세 지표짜리 표가 만들어져야 한다"
    return table


def test_세_지표가_다_있으면_당기순이익까지_세_칸이다() -> None:
    """★ 제품 결정 ② (2026-09-06) — 있는 것을 다 보여 준다.

    예전에는 세 지표가 다 있어도 앞 «둘»만 실었다. 그런데 적자 전환처럼 표지에서
    가장 먼저 보여야 할 사실이 순이익 칸에 있는 회사가 실제로 있다
    (실측: 하이브 2025 당기순이익 -2,544억원이 표지에서 안 보였다).
    ⚠️ 이 시험이 깨지면 그 회사의 표지가 다시 「매출·영업이익만 있는 표지」가 된다.
    """
    metrics = cover_metrics(_report(_three_metric_table()))

    assert [item.label for item in metrics.items] == [
        "매출액",
        "영업이익",
        "당기순이익",
    ]
    assert len(metrics.items) == 3


def test_지표가_둘뿐이면_두_칸_그대로다() -> None:
    """★ 칸 수는 «표에 있는 만큼»이다 — 없는 지표를 지어내 채우지 않는다."""
    table = build_three_year_table(_financials(), cite=_CITE)
    assert table is not None

    metrics = cover_metrics(_report(table))

    assert [item.label for item in metrics.items] == ["매출액", "영업이익"]
    assert len(metrics.items) == 2


def test_지표가_하나뿐인_표는_실적표로_보지_않는다() -> None:
    """★ 관문 — 「사업연도」 열이 있는 아무 숫자표나 표지에 올리지 않는다.

    ``build_three_year_table``은 이런 표를 «만들지 않는다». 그래서 손으로
    조립해, 관문이 실제로 막는지를 본다.
    """
    table = ReportTable(
        caption="전자공시 최근 세 사업연도 주요 실적",
        headers=[PERIOD_HEADER, "매출액"],
        rows=[["2025", "8,219"], ["2024", "6,018"]],
        cite=_CITE,
        numeric=True,
        display_unit="억원",
    )

    metrics = cover_metrics(_report(table))

    assert not metrics
    assert metrics.items == ()


def test_띠_후보는_닫힌_목록이고_관문과_칸수는_다른_값이다() -> None:
    """★ 안전선 — 표에 있는 아무 열이나 표지에 크게 띄우지 않는다.

    ★ 두 상수를 «리터럴»로 못 박는 이유 — 예전에는 상수 하나가 「표를 알아보는
      관문」과 「올리는 칸 수」를 겸했다. 두 값을 서로에게서 유도하면 다시
      하나로 붙어도 시험이 못 잡는다.
    """
    assert COVER_METRIC_CANDIDATES == ("매출액", "영업이익", "당기순이익")
    assert COVER_METRIC_LABELS == ("매출액", "영업이익")
    assert COVER_METRIC_MIN == 2
    assert COVER_METRIC_MAX == 3
    assert COVER_METRIC_MIN < COVER_METRIC_MAX, (
        "관문과 칸 수가 같아지면 지표가 둘뿐인 회사의 표지 띠가 사라진다"
    )


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
    serve_legacy_report_snapshot(monkeypatch, report, report_id=job_id)
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


def test_세_칸짜리_회사는_세_채널_모두_당기순이익_칸을_그린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 고르는 함수는 하나인데 그리는 곳은 셋이다 — 한 곳만 두 칸이면 안 된다.

    ⚠️ 이 시험이 깨지면 같은 보고서가 채널마다 다른 칸 수를 보여 준다.
    """
    from src.features.export_notion.logic import (  # noqa: PLC0415
        _v2_cover_metrics_blocks,
    )
    from src.features.report_standard.public_projection import (  # noqa: PLC0415
        _cover_metrics_block,
    )

    report = _report(_three_metric_table())
    metrics = cover_metrics(report)
    values = [item.value for item in metrics.items]
    assert len(values) == 3, "시험 전제 — 세 칸짜리 표본이어야 한다"

    # 화면
    body = _result_html(monkeypatch, report)
    assert _WEB_METRIC_VALUE.findall(body) == values
    assert "당기순이익" in body

    # PDF
    cover_text = _pdf_cover_text(report)
    for label, value in zip(
        [item.label for item in metrics.items], values, strict=True
    ):
        assert label in cover_text
        assert value in cover_text

    # 노션 — 봉인 블록을 지나 표 세 행이 된다.
    block = _cover_metrics_block(report)
    assert block is not None and len(block.items) == 3
    notion_blocks = _v2_cover_metrics_blocks(block)
    table = next(
        item for item in notion_blocks if item.get("type") == "table"
    )
    rows = table["table"]["children"]
    # 머리행 1 + 지표 3
    assert len(rows) == 4


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
