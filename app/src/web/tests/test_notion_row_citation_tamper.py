"""행별 출처 변조를 실제 저장·legacy 공개 경계가 차단하는지 확인한다."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.tests.test_section_public_manifest import _run_full
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime
from src.web import report_delivery_adapter
from src.web.main import app
from src.web.routers import reports as reports_router


def _tampered_row_cites(report):
    projection = report.public_projection
    assert projection is not None
    section = next(
        item for item in projection.sections
        if item.display.cell == "business_model"
    )
    table = next(
        item for item in section.display.tables
        if item.presentation == "flow"
    )
    assert table.row_cites == (("[2]",), ("[20]",))
    tampered = replace(table, row_cites=(("[99]",), ("[100]",)))
    display = replace(
        section.display,
        tables=(tampered, *section.display.tables[1:]),
    )
    sections = tuple(
        replace(item, display=display)
        if item.display.cell == section.display.cell
        else item
        for item in projection.sections
    )
    return replace(report, public_projection=replace(projection, sections=sections))


def test_생성증거와_다른_행인용은_자기지문을_다시계산해도_저장조회에서_차단된다():
    output, _writer, _reviewer, _diagram = _run_full(flow=True)
    original = output.report
    tampered = _tampered_row_cites(original)
    job_id = f"notion-row-citation-tamper-{uuid.uuid4().hex}"

    with storage_db.connect() as conn:
        report_store.save(conn, job_id, original.company_id, "", original)
        assert report_store.attach_public_projection(conn, job_id, original).public_projection == original.public_projection
        # 잘못된 표만 저장하고 그 표의 자체 지문도 다시 계산한 경우다. 생성자가
        # 승인한 원래 지문과 대조하는 검사는 그대로 유지되어야 한다.
        report_store.save_public_projection(
            conn, job_id, tampered.public_projection, created_at="2026-09-08T00:00:00",
        )
        with pytest.raises(ValueError, match="생성 증거의 지문"):
            report_store.attach_public_projection(conn, job_id, original)


def test_Notion_POST가_신규FULL을_legacy로_격하해_변조근거를_전송하지_않는다(monkeypatch):
    """loader를 대역으로 바꾸지 않고 실제 저장본·권한·CSRF·공개 검사를 지난다."""
    output, _writer, _reviewer, _diagram = _run_full(flow=True)
    report = _tampered_row_cites(output.report)
    job_id = f"notion-new-raw-{uuid.uuid4().hex}"
    with storage_db.connect() as conn:
        report_store.save(conn, job_id, report.company_id, "", report)

    with pytest.raises(report_delivery_adapter.DeliveryAdapterError, match="공개 봉인"):
        report_delivery_adapter.load_legacy_public_report(job_id)

    sent = []

    def no_send(*args, **kwargs):
        sent.append(args)
        raise AssertionError("검증하지 못한 신규 보고서는 Notion 전송기에 도달하면 안 됩니다")

    monkeypatch.setattr(reports_router, "send_report_to_notion", no_send)
    session = auth_logic.create_session("admin@example.com", True)
    csrf = auth_logic.csrf_token_for_session(session.token)

    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.post(f"/notion/{job_id}", data={"csrf_token": csrf})

    assert response.status_code == 503
    assert sent == []
