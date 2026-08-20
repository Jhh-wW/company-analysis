"""Notion route expiry, idempotency and explicit-retry behaviour."""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_notion import store as notion_store
from src.features.export_notion.notion import NotionExportResult
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.features.storage import db as storage_db
from src.web import job_runtime
from src.web.main import app
from src.web.routers import reports as reports_router


def _demo_report():
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
    return result.report


@pytest.fixture(autouse=True)
def _approved_pdf_release_boundary(monkeypatch):
    """이 파일은 Notion 멱등성을 보므로 PDF 승인 자체는 전용 시험에서 검증한다."""

    monkeypatch.setattr(
        reports_router,
        "_release_state",
        lambda **_kwargs: (object(), object()),
    )


@pytest.fixture
def notion_admin(monkeypatch):
    report = _demo_report()
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    session = auth_logic.create_session("admin@example.com", True)
    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        yield (
            client,
            report,
            auth_logic.csrf_token_for_session(session.token),
        )


def test_만료보고서는_410이고_adapter를_한번도_호출하지_않는다(
    notion_admin, monkeypatch
):
    client, _report, csrf = notion_admin
    calls: list[bool] = []
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: True)

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("만료 보고서에 adapter를 호출하면 안 됩니다")

    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden)
    response = client.post("/notion/expired-job", data={"csrf_token": csrf})

    assert response.status_code == 410
    assert "기간" in response.text
    assert calls == []


def test_PDF_출고승인전에는_Notion_adapter를_호출하지_않는다(
    notion_admin, monkeypatch
):
    client, _report, csrf = notion_admin
    calls: list[bool] = []
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(
        reports_router,
        "_release_state",
        lambda **_kwargs: (object(), None),
    )

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("미승인 보고서를 Notion으로 보내면 안 됩니다")

    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden)
    response = client.post("/notion/unapproved-job", data={"csrf_token": csrf})

    assert response.status_code == 409
    assert "최종 검수 중" in response.text
    assert calls == []


def test_성공한_페이지는_새로고침과_재접속에도_재사용하고_adapter는_1회다(
    notion_admin, monkeypatch
):
    client, report, csrf = notion_admin
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    calls: list[bool] = []
    adapter_had_loop: list[bool] = []

    def success(*_args, **_kwargs):
        calls.append(True)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            adapter_had_loop.append(False)
        else:
            adapter_had_loop.append(True)
        return NotionExportResult(
            success=True,
            page_id="persisted-page",
            page_url="https://notion.example/persisted-page",
        )

    monkeypatch.setattr(reports_router, "send_report_to_notion", success)
    first = client.post("/notion/stable-job", data={"csrf_token": csrf})
    second = client.post("/notion/stable-job", data={"csrf_token": csrf})

    assert first.status_code == second.status_code == 200
    assert calls == [True]
    assert adapter_had_loop == [False], "동기 adapter는 async event loop 밖이어야 한다"
    assert "이미 보낸 노션 페이지" in second.text
    assert "https://notion.example/persisted-page" in second.text
    assert 'target="_blank" rel="noreferrer noopener"' in second.text
    assert second.headers["referrer-policy"] == "same-origin"

    digest = notion_store.report_digest(report)
    with storage_db.connect() as conn:
        record = notion_store.load(conn, "stable-job", digest)
    assert record is not None
    assert record.state == notion_store.STATE_SUCCEEDED
    assert record.page_id == "persisted-page"


@pytest.mark.parametrize(
    ("failed_result", "expected_text"),
    [
        (
            NotionExportResult(
                success=False,
                partial=True,
                page_id="partial-page",
                page_url="https://notion.example/partial-page",
                error="일부 실패",
            ),
            "일부만 만들어졌습니다",
        ),
        (
            NotionExportResult(
                success=False,
                uncertain=True,
                error="timeout",
            ),
            "전송 결과를 확인할 수 없습니다",
        ),
    ],
)
def test_partial_unknown은_자동중복을_막고_확인한_CAS재시도만_허용한다(
    notion_admin, monkeypatch, failed_result, expected_text
):
    client, _report, csrf = notion_admin
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    results = iter(
        [
            failed_result,
            NotionExportResult(
                success=True,
                page_id="explicit-retry-page",
                page_url="https://notion.example/explicit-retry-page",
            ),
        ]
    )
    calls: list[bool] = []

    def adapter(*_args, **_kwargs):
        calls.append(True)
        return next(results)

    monkeypatch.setattr(reports_router, "send_report_to_notion", adapter)
    first = client.post("/notion/retry-job", data={"csrf_token": csrf})
    automatic = client.post("/notion/retry-job", data={"csrf_token": csrf})

    assert first.status_code == 200
    assert automatic.status_code == 409
    assert expected_text in automatic.text
    assert "중복 페이지가 생길 수 있음을 확인" in automatic.text
    assert calls == [True]

    # Missing confirmation cannot move the persisted revision.
    unconfirmed = client.post(
        "/notion/retry-job",
        data={"csrf_token": csrf, "retry_revision": "1"},
    )
    assert unconfirmed.status_code == 409
    assert calls == [True]

    explicit = client.post(
        "/notion/retry-job",
        data={
            "csrf_token": csrf,
            "retry_revision": "1",
            "confirm_duplicate": "yes",
        },
    )
    assert explicit.status_code == 200
    assert "노션으로 보냈습니다" in explicit.text
    assert calls == [True, True]

    # Replaying the old confirmation is blocked by the revision CAS and reuses
    # the success page instead of making a third page.
    replay = client.post(
        "/notion/retry-job",
        data={
            "csrf_token": csrf,
            "retry_revision": "1",
            "confirm_duplicate": "yes",
        },
    )
    assert replay.status_code == 200
    assert "이미 보낸 노션 페이지" in replay.text
    assert calls == [True, True]


def test_클라이언트취소뒤에도_worker가_결과를_저장해_중복을_막는다(monkeypatch):
    report = _demo_report()
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        reports_router.request_helpers,
        "require_admin_action",
        lambda _request, _token: None,
    )
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)

    def delayed_success(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=3)
        return NotionExportResult(
            success=True,
            page_id="page-after-cancel",
            page_url="https://notion.example/page-after-cancel",
        )

    monkeypatch.setattr(reports_router, "send_report_to_notion", delayed_success)
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/notion/cancel-job",
            "raw_path": b"/notion/cancel-job",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
        }
    )

    async def scenario() -> None:
        route_task = asyncio.create_task(
            reports_router.send_to_notion(
                request,
                "cancel-job",
                csrf_token="test",
                retry_revision="",
                confirm_duplicate="",
            )
        )
        assert await asyncio.to_thread(started.wait, 2)
        route_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await route_task
        release.set()

        # The shielded worker owns a strong reference and settles the DB even
        # though the disconnected request itself remains cancelled.
        for _ in range(100):
            if not reports_router._NOTION_EXPORT_WORKERS:
                break
            await asyncio.sleep(0.01)
        assert not reports_router._NOTION_EXPORT_WORKERS

    asyncio.run(scenario())

    digest = notion_store.report_digest(report)
    with storage_db.connect() as conn:
        record = notion_store.load(conn, "cancel-job", digest)
    assert record is not None
    assert record.state == notion_store.STATE_SUCCEEDED
    assert record.page_id == "page-after-cancel"


def test_claim_commit직후_요청취소도_supervisor가_adapter와_finish를_잇는다(
    monkeypatch,
):
    """OUT-NOTION-08: claim 반환값이 폐기되던 정확한 취소 경계를 고정한다."""
    report = _demo_report()
    claim_committed = threading.Event()
    allow_claim_return = threading.Event()
    adapter_calls: list[bool] = []
    original_claim = reports_router._claim_notion_export

    monkeypatch.setattr(
        reports_router.request_helpers,
        "require_admin_action",
        lambda _request, _token: None,
    )
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)

    def claim_then_delay_return(*args, **kwargs):
        decision = original_claim(*args, **kwargs)
        claim_committed.set()
        assert allow_claim_return.wait(timeout=3)
        return decision

    def success(*_args, **_kwargs):
        adapter_calls.append(True)
        return NotionExportResult(
            success=True,
            page_id="page-after-claim-cancel",
            page_url="https://notion.example/page-after-claim-cancel",
        )

    monkeypatch.setattr(reports_router, "_claim_notion_export", claim_then_delay_return)
    monkeypatch.setattr(reports_router, "send_report_to_notion", success)
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/notion/claim-cancel-job",
            "raw_path": b"/notion/claim-cancel-job",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
        }
    )

    async def scenario() -> None:
        route_task = asyncio.create_task(
            reports_router.send_to_notion(
                request,
                "claim-cancel-job",
                csrf_token="test",
                retry_revision="",
                confirm_duplicate="",
            )
        )
        assert await asyncio.to_thread(claim_committed.wait, 2)

        # The DB context has committed but the worker thread has not returned
        # its ClaimResult to the supervisor yet: this was the original hole.
        digest = notion_store.report_digest(report)

        def load_claimed():
            with storage_db.connect() as conn:
                return notion_store.load(conn, "claim-cancel-job", digest)

        claimed = await asyncio.to_thread(load_claimed)
        assert claimed is not None and claimed.state == notion_store.STATE_IN_PROGRESS
        assert adapter_calls == []

        route_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await route_task
        assert reports_router._NOTION_EXPORT_WORKERS

        allow_claim_return.set()
        for _ in range(100):
            if not reports_router._NOTION_EXPORT_WORKERS:
                break
            await asyncio.sleep(0.01)
        assert not reports_router._NOTION_EXPORT_WORKERS

    asyncio.run(scenario())

    digest = notion_store.report_digest(report)
    with storage_db.connect() as conn:
        record = notion_store.load(conn, "claim-cancel-job", digest)
    assert adapter_calls == [True]
    assert record is not None
    assert record.state == notion_store.STATE_SUCCEEDED
    assert record.page_id == "page-after-claim-cancel"
