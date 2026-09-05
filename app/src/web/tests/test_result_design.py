"""보고서 전용 디자인이 내용과 작업 경로를 건드리지 않는지 확인한다."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_notion import constants as notion_constants
from src.features.export_pdf.logic import (
    build_content_disposition as build_pdf_content_disposition,
    build_download_filename as build_pdf_download_filename,
)
from src.features.export_pdf.release import (
    ApprovalDecision,
    prepare_pdf_release,
)
from src.features.export_pdf import release_store as pdf_release_store
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.web import job_runtime
from src.web.main import app
from src.features.storage import db as storage_db


STYLE = Path(__file__).parents[1] / "static" / "style.css"
TEMPLATE = Path(__file__).parents[1] / "templates" / "result.html"
_APPROVED_AT = "2026-08-19T21:30:00+09:00"

_DESIGN_TOKENS_V1 = {
    "--ink": "#111111",
    "--ink-2": "#333333",
    "--grey-6": "#666666",
    "--grey-4": "#999999",
    "--grey-2": "#CCCCCC",
    "--grey-1": "#F5F5F5",
    "--paper": "#FBFBFB",
    "--white": "#FFFFFF",
    "--chart-1": "#B3B3B3",
    "--chart-2": "#8C8C8C",
    "--chart-3": "#666666",
    "--chart-4": "#444444",
    "--chart-5": "#222222",
}


def _demo_report(*, with_table: bool = False):
    """저장 보고서 경로로 바로 그릴 수 있는 실제 데모 보고서 하나."""
    pipeline = DemoPipeline()
    for sample in available_companies():
        if not sample["is_report"]:
            continue
        user_input = UserInput(
            company=sample["company"],
            job=sample["job"],
            region="",
            posting_text="",
        )
        result = pipeline.run(user_input, pipeline.find_company(user_input))
        assert result.outcome is Outcome.REPORT and result.report is not None
        if with_table and not any(section.tables for section in result.report.sections):
            continue
        return result.report
    raise AssertionError("조건에 맞는 데모 보고서가 없습니다")


def _serve_current_delivery(monkeypatch, job_id: str, report) -> None:
    """디자인 시험을 legacy 재렌더가 아닌 불변 current GET에 연결한다."""

    from src.web.routers import reports as reports_router

    stored = reports_router._StoredPublicDelivery(
        delivery=SimpleNamespace(
            expires_at=dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
        ),
        report=report,
        pdf_bytes=b"%PDF-1.4\n% immutable design fixture\n",
        pdf_sha256="a" * 64,
        artifact_id="artifact_design_fixture",
        release_record_sha256="b" * 64,
    )
    monkeypatch.setattr(
        reports_router,
        "_stored_public_delivery",
        lambda public_id: stored if public_id == job_id else None,
    )


def _approve_report(job_id: str, report) -> None:
    candidate = prepare_pdf_release(report)
    page_hashes = tuple(page.png_sha256 for page in candidate.pages)
    reviewed_pages = tuple(page.number for page in candidate.pages)
    role_decisions = (
        pdf_release_store.PdfRoleDecision(
            role="fact",
            pdf_sha256=candidate.pdf_sha256,
            page_png_sha256s=page_hashes,
            reviewed_pages=reviewed_pages,
            expected_fact_ids=candidate.expected_fact_ids,
            reviewed_fact_ids=candidate.expected_fact_ids,
            fact_failed_count=0,
            decision=ApprovalDecision(
                True, "user:11111111111111111111", _APPROVED_AT
            ),
        ),
        pdf_release_store.PdfRoleDecision(
            role="editorial",
            pdf_sha256=candidate.pdf_sha256,
            page_png_sha256s=page_hashes,
            reviewed_pages=reviewed_pages,
            expected_fact_ids=candidate.expected_fact_ids,
            decision=ApprovalDecision(
                True, "user:22222222222222222222", _APPROVED_AT
            ),
        ),
        pdf_release_store.PdfRoleDecision(
            role="visual",
            pdf_sha256=candidate.pdf_sha256,
            page_png_sha256s=page_hashes,
            reviewed_pages=reviewed_pages,
            expected_fact_ids=candidate.expected_fact_ids,
            decision=ApprovalDecision(
                True, "user:33333333333333333333", _APPROVED_AT
            ),
            visual_review_kind="human",
        ),
    )
    with storage_db.connect() as conn:
        pdf_release_store.ensure_participant_ledger(
            conn,
            report_id=job_id,
            pdf_sha256=candidate.pdf_sha256,
            participants={
                "author": "user:44444444444444444444",
                "producer": "user:55555555555555555555",
                "fact": "user:11111111111111111111",
                "editorial": "user:22222222222222222222",
                "visual": "user:33333333333333333333",
            },
            assigned_at=_APPROVED_AT,
        )
        for role_decision in role_decisions:
            pdf_release_store.save_role_decision(
                conn,
                report_id=job_id,
                role_decision=role_decision,
            )
        approval = pdf_release_store.load_complete_approval(
            conn,
            report_id=job_id,
            pdf_sha256=candidate.pdf_sha256,
        )
        assert approval is not None
        pdf_release_store.finalize_release(
            conn,
            report_id=job_id,
            candidate=candidate,
            approval=approval,
            created_at=_APPROVED_AT,
            released_at=_APPROVED_AT,
        )


def test_보고서만_전용_문서_레이아웃을_쓴다(monkeypatch):
    from src.web.routers import reports as reports_router

    report = _demo_report()
    job_id = f"result-design-preview-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    _serve_current_delivery(monkeypatch, job_id, report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(
        reports_router,
        "_release_state",
        lambda **_kwargs: (object(), None),
    )
    monkeypatch.setattr(reports_router, "is_notion_configured", lambda: True)
    session = auth_logic.create_session("admin@example.com", True)

    with TestClient(app) as client:
        response = client.get(
            f"/result/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )

    assert response.status_code == 200
    assert '<body class="result-page">' in response.text
    assert '<main id="main-content"' in response.text
    assert 'class="wrap wide result"' in response.text
    assert 'tabindex="-1"' in response.text
    assert '<article class="report-paper">' in response.text
    assert '<header class="report-cover">' in response.text
    assert 'data-report-title="' + report.company + ' 분석 보고서"' in response.text
    assert f'data-report-date="{report.as_of_date} 기준"' in response.text
    assert 'class="report-summary"' in response.text
    assert 'id="report-summary-title">핵심 요약</h2>' in response.text
    assert 'class="summary-number"' in response.text
    assert (
        f'<h1><span>{report.company}</span><span>분석 보고서</span></h1>'
        in response.text
    )
    assert f"내용 생성 {report.generated_at}" in response.text
    assert 'class="report-section"' in response.text
    assert 'class="scroll report-scroll"' in response.text
    assert 'id="report-citations-title"><span class="no">부록</span><span class="txt">출처와 검증 상태</span><span class="section-badge" aria-hidden="true"></span>' in response.text
    assert '<th scope="col">자료</th>' in response.text
    assert '<th scope="col">기준일·자료 상태</th>' in response.text
    assert '<th scope="col">사실 검증</th>' in response.text
    assert '<th scope="col">원문 위치</th>' in response.text
    assert '<th scope="col">본문 사용 장</th>' in response.text
    assert " · 수집 " not in response.text
    assert 'class="rawsrc"' not in response.text
    assert "확인된 회사 사실" not in response.text
    assert "프로그램이 제안" not in response.text
    assert "어디서 가져왔나" not in response.text

    badge_values = re.findall(
        r'<span class="no">([^<]+)</span><span class="txt">', response.text
    )
    expected_badges = [str(section.display_number) for section in report.sections]
    assert badge_values == [*expected_badges, "부록"]
    assert response.text.count(
        '<span class="section-badge" aria-hidden="true"></span>'
    ) == len(expected_badges) + 1

    # 자동출고된 같은 객체에는 수동 preview가 없고 활성 PDF 경로만 보인다.
    assert "아직 공개·공유·내보내기 승인 전" not in response.text
    assert f'href="/review/pdf/{job_id}"' not in response.text
    assert f'href="/download/pdf/{job_id}"' in response.text
    assert "워드로 내려받기" not in response.text
    assert 'href="/">다른 회사 분석하기</a>' in response.text
    assert f'action="/notion/{job_id}"' in response.text
    if 'data-report-cell="附"' in response.text:
        assert "참고. 참고 지표" not in response.text
        assert '<span class="no">참고</span><span class="txt">지표</span>' in response.text


def test_관리자에게_노션_미설정을_실행가능한_버튼처럼_보이지_않는다(monkeypatch):
    report = _demo_report()
    job_id = "result-notion-unconfigured"
    job_runtime._JOBS.pop(job_id, None)
    _serve_current_delivery(monkeypatch, job_id, report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.delenv(notion_constants.ENV_NOTION_TOKEN, raising=False)
    monkeypatch.delenv(notion_constants.ENV_NOTION_PARENT_PAGE_ID, raising=False)
    session = auth_logic.create_session("admin@example.com", True)
    _approve_report(job_id, report)

    with TestClient(app) as client:
        response = client.get(
            f"/result/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )

    assert response.status_code == 200
    assert "노션 설정 필요" in response.text
    assert 'aria-describedby="notion-setup-note"' in response.text
    assert 'id="notion-setup-note"' in response.text
    assert "노션 연결 설정이 아직 없습니다" in response.text
    assert f'action="/notion/{job_id}"' not in response.text


def test_PDF_다운로드_파일명은_헤더주입과_경로문자를_허용하지_않는다():
    report = replace(
        _demo_report(),
        company='하이브"\r\nX-Injected: yes/..\\',
    )
    disposition = build_pdf_content_disposition(build_pdf_download_filename(report))
    assert disposition.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in disposition
    assert "\r" not in disposition and "\n" not in disposition
    assert ":" not in disposition
    assert "/" not in disposition and "\\" not in disposition


def test_관리자이고_노션설정이_완전할때만_전송폼이_열린다(monkeypatch):
    report = _demo_report()
    job_id = "result-notion-configured"
    _serve_current_delivery(monkeypatch, job_id, report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setenv(notion_constants.ENV_NOTION_TOKEN, "test-secret")
    monkeypatch.setenv(notion_constants.ENV_NOTION_PARENT_PAGE_ID, "test-parent")
    session = auth_logic.create_session("admin@example.com", True)
    _approve_report(job_id, report)

    with TestClient(app) as client:
        response = client.get(
            f"/result/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )

    assert response.status_code == 200
    assert f'action="/notion/{job_id}"' in response.text
    assert "노션으로 보내기" in response.text
    assert "노션 설정 필요" not in response.text
    assert "test-secret" not in response.text
    assert "test-parent" not in response.text


def test_보고서_표는_제목행과_가로스크롤_경계를_가진다(monkeypatch):
    report = _demo_report(with_table=True)
    job_id = "result-design-table"
    job_runtime._JOBS.pop(job_id, None)
    _serve_current_delivery(monkeypatch, job_id, report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)

    with TestClient(app) as client:
        html = client.get(
            f"/result/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        ).text

    assert "<thead" in html and "<tbody>" in html
    assert '<thead class="report-table-head">' in html
    assert 'scope="col"' in html
    assert any(
        f'data-report-cell="{section.cell}"' in html
        for section in report.sections
        if section.tables
    )
    assert not any(f'data-report-cell="{cell}"' in html for cell in ("5", "6", "7", "8"))

    # 가로로 긴 표는 키보드 사용자도 스크롤 영역에 들어갈 수 있어야 하고,
    # 무엇의 표인지 화면에 보이는 제목으로 읽혀야 한다.
    scroll_tags = re.findall(r'<div class="scroll(?: report-scroll)?"[^>]*>', html)
    assert scroll_tags
    assert all('tabindex="0"' in tag for tag in scroll_tags)
    assert all('role="region"' in tag for tag in scroll_tags)
    scroll_labels = [
        label
        for tag in scroll_tags
        for label in re.findall(r'aria-labelledby="([^"]+)"', tag)
    ]
    assert len(scroll_labels) == len(scroll_tags)
    document_ids = set(re.findall(r'\bid="([^"]+)"', html))
    assert all(label in document_ids for label in scroll_labels)

    # 표가 도표로 바뀌어도 보고서 원본 표 하나당 캡션은 정확히 하나여야 한다.
    # 모든 캡션 ID는 유일하고, 표와 figure가 같은 원표를 중복 출력하면 안 된다.
    table_caption_ids = re.findall(
        r'<div class="cap" id="(report-table-[^"]+-caption)">', html
    )
    figure_caption_ids = re.findall(
        r'<figcaption class="cap" id="(report-table-[^"]+-caption)">', html
    )
    caption_ids = [*table_caption_ids, *figure_caption_ids]
    expected_caption_count = sum(
        len(section.tables) for section in report.sections
    )
    assert len(caption_ids) == expected_caption_count
    assert len(caption_ids) == len(set(caption_ids))
    labelled_tables = re.findall(
        r'<table class="(?:nums|texts)" aria-labelledby="(report-table-[^"]+-caption)">',
        html,
    )
    labelled_figures = re.findall(
        r'<figure class="[^"]*\breport-visual\b[^"]*" '
        r'aria-labelledby="(report-table-[^"]+-caption)">',
        html,
    )
    assert labelled_tables == table_caption_ids
    assert labelled_figures == figure_caption_ids
    assert set(labelled_tables).isdisjoint(labelled_figures)
    assert set(labelled_tables) | set(labelled_figures) == set(caption_ids)


def test_보고서_CSS는_모바일과_인쇄를_따로_보호한다():
    css = STYLE.read_text(encoding="utf-8")

    assert ".result-page {" in css
    assert "--result-card-radius: 24px;" in css
    assert ".result-page .report-paper" in css
    assert ".result-page .report-cover" in css
    assert ".result-page .report-title-block" in css
    assert ".result-page .report-summary" in css
    assert ".result-page .summary-number" in css
    assert ".result-page .section-tag" in css
    root = css[css.index(":root {") : css.index("}", css.index(":root {"))]
    for name, value in _DESIGN_TOKENS_V1.items():
        assert f"{name}: {value};" in root
    assert ".result-page .ui-only" in css
    assert ".result-page .report-actions" in css
    assert "@media (max-width: 720px)" in css
    assert "@media (max-width: 700px)" in css
    assert "@media print" in css
    assert ".result-page .report-actions," in css
    assert "display: none !important;" in css
    assert ".result-page .rawsrc" not in css
    assert "break-after: page;" in css
    assert "break-after: avoid-page;" in css
    assert "break-inside: avoid-page;" in css


def test_보고서_표와_수치카드는_단색_토큰만_쓴다():
    css = STYLE.read_text(encoding="utf-8")

    assert ".result-page th {" in css
    assert "background: var(--ink);" in css
    assert "color: var(--white);" in css
    assert "border-bottom: .5px solid var(--grey-2);" in css
    assert ".result-page tbody td:first-child { background: var(--grey-1); }" in css
    assert ".result-page td.num { text-align: right; }" in css
    assert "border-top: 1px solid var(--ink);" in css
    assert "border-bottom: 1px solid var(--ink);" in css
    assert ".result-page .cover-metric {" in css
    assert "font-size: 22px;" in css
    assert ".result-page .summary-number {" in css


def test_흐름표는_3단계부터_5단계까지만_쉐브론을_쓴다():
    template = TEMPLATE.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    condition = (
        'class="flow-row"{% if flow|length >= 3 and flow|length <= 5 %} '
        'data-flow-style="chevron"{% endif %}'
    )
    assert template.count(condition) == 2
    assert '.result-page .flow-row[data-flow-style="chevron"]' in css
    assert "clip-path: polygon(" in css
    for index in range(1, 6):
        assert f"var(--chart-{index})" in css
    # 두 단계 이하는 기존 네모와 화살표 규칙으로 남아야 한다.
    assert ".result-page .flow-row li:not(:last-child)::after" in css
    assert 'content: "→";' in css
