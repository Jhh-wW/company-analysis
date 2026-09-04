"""신규 staging과 승인 Delivery를 legacy/raw 출력으로 격하하지 않는다."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.tests.test_section_public_manifest import _run_full
from src.features.export_notion.notion import NotionExportResult
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.features.report_delivery import store as delivery_store
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.web import job_runtime, report_delivery_adapter, report_publication
from src.web.main import app
from src.web.routers import reports as reports_router


def _report():
    pipeline = DemoPipeline()
    sample = next(item for item in available_companies() if item["is_report"])
    user_input = UserInput(
        company=sample["company"],
        job=sample["job"],
        region="",
        posting_text="",
    )
    result = pipeline.run(user_input, pipeline.find_company(user_input))
    assert result.outcome is Outcome.REPORT and result.report is not None
    return replace(result.report, generated_at=clock.iso_now_kst())


def _login_admin(client: TestClient) -> str:
    session = auth_logic.create_session(
        "admin@example.com",
        True,
        subject=f"test:staged-public-export:{uuid.uuid4().hex}",
    )
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return auth_logic.csrf_token_for_session(session.token)


def test_intent없는_staging뒤_다른사건도_결과_PDF_Notion에서_legacy가_아니다(
    monkeypatch,
):
    """뒤에 일반 사건·상태 손상이 있어도 미출고 생명주기는 그대로 닫힌다."""

    report = _report()
    report_id = f"staged-no-intent-{uuid.uuid4().hex}"
    payload_json = report_store.report_to_json(report)
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id=report_id,
            corp_id="staged-corp",
            job=report.job,
            report=report,
            engine_epoch_digest=(
                build_identity_contract.process_engine_build_identity().epoch_digest
            ),
        )
        dashboard_store.stage_report(
            conn,
            report_id=report_id,
            corp_type=report.corp_type,
            now_iso=clock.iso_now_kst(),
            payload_json=payload_json,
        )
        failed_at = clock.now_kst()
        delivery_store.mark_delivery_required(
            conn,
            public_id=report_id,
            required_at=failed_at,
        )
        delivery_store.mark_delivery_failed(
            conn,
            public_id=report_id,
            failure_code="artifact_finalization_failed",
            failed_at=failed_at,
        )
        # 손상·부분 정리로 실패 intent 한 행만 사라진 정확한 우회 조건이다.
        conn.execute(
            f"DELETE FROM {delivery_store.TABLE_DELIVERY_INTENTS} WHERE public_id=?",
            (report_id,),
        )
        # 예전/내부 호출자가 staging 뒤 일반 운영 사건을 붙인 모양도 재현한다.
        # 이 사건이 뒤에 왔다고 출고 생명주기가 완료된 것은 아니다.
        dashboard_store.record_error(
            conn,
            report_id=report_id,
            actor_email="member@example.com",
            area="출처",
            reason="출고 전 원문 확인 요청",
            now_iso=clock.iso_now_kst(),
        )
        # ``blocked``만 보던 옛 경계를 실제로 통과시키는 상태 손상까지 겹친다.
        conn.execute(
            f"UPDATE {dashboard_store.TABLE_REPORT_STATES} "
            "SET status=?, blocked=0 WHERE report_id=?",
            (dashboard_store.REPORT_STATUS_NORMAL, report_id),
        )
        assert dashboard_store.report_is_unpublished_staging(conn, report_id)
        assert delivery_store.load_delivery_intent(conn, report_id) is None

    # 라우트의 사전 차단을 우회해 adapter를 직접 불러도 같은 raw가 과거
    # 보고서로 격하되면 안 된다. 이 내부 경계가 TOCTOU의 마지막 방어선이다.
    with pytest.raises(
        report_delivery_adapter.DeliveryAdapterError,
        match="legacy로 열지 않습니다",
    ):
        report_delivery_adapter.load_legacy_public_report(report_id)

    legacy_calls: list[str] = []
    notion_calls: list[str] = []

    def forbidden_legacy(*_args, **_kwargs):
        legacy_calls.append("called")
        raise AssertionError("신규 staging을 legacy 원본으로 읽었습니다")

    def forbidden_notion(*_args, **_kwargs):
        notion_calls.append("called")
        raise AssertionError("미출고 staging을 외부 Notion으로 보냈습니다")

    monkeypatch.setattr(
        reports_router.report_delivery_adapter,
        "load_legacy_public_report",
        forbidden_legacy,
    )
    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden_notion)
    monkeypatch.setattr(
        reports_router.request_helpers,
        "require_report_access",
        lambda _request, _report_id, **_kwargs: None,
    )

    with TestClient(app) as client:
        csrf = _login_admin(client)
        progress = client.get(f"/api/progress/{report_id}")
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        notion = client.post(
            f"/notion/{report_id}",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )

    assert progress.status_code == 410
    assert "finished" not in progress.json()
    assert "next_url" not in progress.json()
    assert result.status_code == pdf.status_code == notion.status_code == 409
    assert legacy_calls == []
    assert notion_calls == []
    assert report.company not in result.text
    assert report.company not in pdf.text
    assert report.company not in notion.text


def test_COMPLETE열만_손상승격돼도_publish사건없는_staging은_공개하지않는다(
    monkeypatch,
):
    report = _report()
    report_id = f"staged-false-complete-{uuid.uuid4().hex}"
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id=report_id,
            corp_id="staged-corp",
            job=report.job,
            report=report,
            engine_epoch_digest=(
                build_identity_contract.process_engine_build_identity().epoch_digest
            ),
        )
        dashboard_store.stage_report(
            conn,
            report_id=report_id,
            corp_type=report.corp_type,
            now_iso=clock.iso_now_kst(),
            payload_json=report_store.report_to_json(report),
        )
        # 실제 loader는 Delivery 없는 가짜 COMPLETE 자체를 손상으로 거절한다.
        # 여기서는 loader 뒤 한 열만 신뢰하는 호출 회귀까지 독립적으로 고정한다.
        monkeypatch.setattr(
            report_publication.delivery_store,
            "load_delivery_intent",
            lambda _conn, _report_id: SimpleNamespace(
                state=delivery_store.DELIVERY_INTENT_COMPLETE
            ),
        )
        assert not report_publication.report_is_published_or_legacy(
            conn, report_id
        )


def test_출고행이_전부유실된_신규FULL_raw도_세채널과_progress가_legacy로열지않는다(
    monkeypatch,
):
    """생성 도장은 DB 보조행 전체 유실 뒤에도 신규 raw임을 증명한다."""

    output, _writer, _reviewer, _diagram = _run_full()
    report = output.report
    assert report.generation_evidence is not None
    assert report.generation_evidence.public_projection_sha256
    report_id = f"full-raw-no-publication-{uuid.uuid4().hex}"
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id=report_id,
            corp_id=report.company_id,
            job=report.job,
            report=report,
            engine_epoch_digest=(
                build_identity_contract.process_engine_build_identity().epoch_digest
            ),
        )
        assert dashboard_store.report_publication_lifecycle(conn, report_id) == ""
        assert delivery_store.load_delivery_intent(conn, report_id) is None
        assert delivery_store.load_delivery_by_public_id(conn, report_id) is None
        assert not report_publication.report_is_published_or_legacy(conn, report_id)

    with pytest.raises(
        report_delivery_adapter.DeliveryAdapterError,
        match="과거 저장본 화면으로 열지 않습니다",
    ):
        report_delivery_adapter.load_legacy_public_report(report_id)

    external_calls: list[str] = []

    def forbidden(*_args, **_kwargs):
        external_calls.append("called")
        raise AssertionError("신규 FULL raw를 다시 렌더하거나 외부로 보냈습니다")

    monkeypatch.setattr(reports_router, "_release_state", forbidden)
    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden)
    monkeypatch.setattr(
        reports_router.request_helpers,
        "require_report_access",
        lambda _request, _report_id, **_kwargs: None,
    )

    with TestClient(app) as client:
        csrf = _login_admin(client)
        progress = client.get(f"/api/progress/{report_id}")
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        notion = client.post(
            f"/notion/{report_id}",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )

    assert progress.status_code == 410
    assert "finished" not in progress.json()
    assert "next_url" not in progress.json()
    assert result.status_code == pdf.status_code == notion.status_code == 503
    assert external_calls == []
    assert report.company not in result.text
    assert report.company not in pdf.text
    assert report.company not in notion.text


def test_Notion은_intent행만_없어도_남아있는_승인Delivery를_raw보다_먼저쓴다(
    monkeypatch,
):
    approved = _report()
    raw_drift = replace(approved, company=approved.company + " 변조본")
    report_id = f"notion-orphan-intent-{uuid.uuid4().hex}"
    expires_at = clock.now_kst() + dt.timedelta(days=1)
    monkeypatch.setattr(
        reports_router.report_delivery_adapter,
        "load_public_delivery_intent",
        lambda _report_id: None,
    )
    monkeypatch.setattr(
        reports_router,
        "_stored_public_delivery",
        lambda _report_id: SimpleNamespace(
            report=approved,
            delivery=SimpleNamespace(expires_at=expires_at),
        ),
    )
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _report_id: raw_drift)
    monkeypatch.setattr(
        reports_router,
        "_release_state",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("승인 Delivery를 오늘 renderer/checker로 다시 심사했습니다")
        ),
    )
    sent = []

    def capture(target, *_args, **_kwargs):
        sent.append(target)
        return NotionExportResult(
            success=True,
            page_id="approved-delivery-page",
            page_url="https://notion.example/approved-delivery-page",
        )

    monkeypatch.setattr(reports_router, "send_report_to_notion", capture)
    with TestClient(app) as client:
        csrf = _login_admin(client)
        response = client.post(
            f"/notion/{report_id}",
            data={"csrf_token": csrf},
        )

    assert response.status_code == 200
    assert sent == [approved]
    assert sent[0] is not raw_drift
