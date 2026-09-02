"""Notion route expiry, idempotency and explicit-retry behaviour."""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.render import render_report
from src.features.export_pdf.release import PDFReleaseBlockedError
from src.features.export_notion import logic as notion_logic
from src.features.export_notion import store as notion_store
from src.features.export_notion.notion import NotionExportResult
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.features.report_standard.public_projection import build_public_projection
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


def test_자동검사중단이면_Notion_adapter를_호출하지_않는다(
    notion_admin, monkeypatch
):
    client, _report, csrf = notion_admin
    calls: list[bool] = []
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(
        reports_router,
        "_release_state",
        lambda **_kwargs: (_ for _ in ()).throw(
            PDFReleaseBlockedError("GATE_STOPPED: forced check failure")
        ),
    )

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("미승인 보고서를 Notion으로 보내면 안 됩니다")

    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden)
    response = client.post("/notion/unapproved-job", data={"csrf_token": csrf})

    assert response.status_code == 409
    assert "자동검사가 중단" in response.text
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


def test_외부_adapter의_javascript_페이지주소는_저장도_화면링크도_되지_않는다(
    notion_admin, monkeypatch
):
    client, report, csrf = notion_admin
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)

    def poisoned_result(*_args, **_kwargs):
        return NotionExportResult(
            success=True,
            page_id="created-page",
            page_url="javascript:alert(document.domain)",
        )

    monkeypatch.setattr(reports_router, "send_report_to_notion", poisoned_result)

    response = client.post("/notion/unsafe-url-job", data={"csrf_token": csrf})

    assert response.status_code == 200
    assert "javascript:" not in response.text
    assert "노션에서 열기" not in response.text
    digest = notion_store.report_digest(report)
    with storage_db.connect() as conn:
        record = notion_store.load(conn, "unsafe-url-job", digest)
    assert record is not None
    assert record.page_url == ""


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
    job_id = f"cancel-job-{uuid.uuid4().hex}"
    # 이 시험은 TestClient 시작 수명주기를 우회해 route를 직접 호출한다.
    # 운영과 같은 fail-closed 공개 상태 조회가 가능하도록 저장소를 먼저 연다.
    with storage_db.connect():
        pass
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
            "path": f"/notion/{job_id}",
            "raw_path": f"/notion/{job_id}".encode("ascii"),
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
                job_id,
                csrf_token="test",
                retry_revision="",
                confirm_duplicate="",
            )
        )
        worker_started = await asyncio.to_thread(started.wait, 2)
        if not worker_started and route_task.done():
            response = route_task.result()
            pytest.fail(
                f"Notion 작업자 시작 전 응답: status={response.status_code}"
            )
        assert worker_started
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
        record = notion_store.load(conn, job_id, digest)
    assert record is not None
    assert record.state == notion_store.STATE_SUCCEEDED
    assert record.page_id == "page-after-cancel"


def test_claim_commit직후_요청취소도_supervisor가_adapter와_finish를_잇는다(
    monkeypatch,
):
    """OUT-NOTION-08: claim 반환값이 폐기되던 정확한 취소 경계를 고정한다."""
    report = _demo_report()
    job_id = f"claim-cancel-job-{uuid.uuid4().hex}"
    with storage_db.connect():
        pass
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
            "path": f"/notion/{job_id}",
            "raw_path": f"/notion/{job_id}".encode("ascii"),
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
                job_id,
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
                return notion_store.load(conn, job_id, digest)

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
        record = notion_store.load(conn, job_id, digest)
    assert adapter_calls == [True]
    assert record is not None
    assert record.state == notion_store.STATE_SUCCEEDED
    assert record.page_id == "page-after-claim-cancel"


# ══════════════════════════════════════════════════════════
# 엔진 v2 — 봉인 블록이 생겼으니 409를 푼다 (설계 017 결정 D-6)
# ══════════════════════════════════════════════════════════


def _sealed_v2_report():
    """공개 봉인 블록(``public_projection``)을 실은 엔진 v2 보고서.

    ★ 손으로 지은 ``Report``가 아니라 ``render_report()``를 «실제로» 통과시킨
      보고서를 쓴다 — 인용 번호를 언제 숨기는지 같은 진짜 규칙이 재현되지
      않으면 그 규칙 때문에 생긴 결함을 그물이 통과한다.
    """

    fragments = {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."}
    }
    sections = []
    for section_id in SECTION_IDS:
        if section_id == "identity":
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(
                        ComposedSentence(
                            text="반도체 검사 장비를 주력으로 한다.",
                            citations=("1",),
                            grade=GRADE_CONFIRMED,
                        ),
                        ComposedSentence(
                            text="검사 장비 수요는 앞으로도 이어질 것으로 보인다.",
                            citations=("1",),
                            grade=GRADE_INTERPRETED,
                        ),
                    ),
                )
            )
        else:
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(),
                    notice=NOTICE_INSUFFICIENT_EVIDENCE,
                )
            )
    summary = tuple(
        ComposedSentence(text=text, citations=("1",), grade=GRADE_CONFIRMED)
        for text in ("요약 하나다.", "요약 둘이다.", "요약 셋이다.")
    )
    report = render_report(
        "가나다전자",
        ComposedReport(sections=tuple(sections), summary=summary),
        fragments,
        None,
    )
    return replace(report, public_projection=build_public_projection(report))


def _paragraph_texts(blocks):
    return [
        "".join(part["text"]["content"] for part in block["paragraph"]["rich_text"])
        for block in blocks
        if block["type"] == "paragraph"
    ]


def test_v2_Notion_POST는_409가_아니라_블록으로_전송한다(monkeypatch):
    """결정 D-6 — 「노션용 변환기가 없다」는 409를 조각 S6 완료와 동시에 푼다.

    ★ 설계 017 §02-6과 §03 결정표 D6은 이 409를 「슬라이스 6 전까지 유지」로
      못 박아 두었다. 이유는 «변환기가 없다»였다. 이제 v2 보고서가 공개 봉인
      블록을 들고 오고 ``build_blocks``가 그 블록만 읽으므로 그 전제가
      사라졌다. 그래서 이 시험은 예전 409 고정 시험을 뒤집는다
      (``test_reports_v2_output.py``의 같은 자리도 함께 뒤집었다).
    ★ 권한·CSRF·만료 판정은 그대로다 — 그 경계는 옆 시험들이 지킨다.
    """

    report = _sealed_v2_report()
    job_id = f"notion-v2-sealed-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    sent = []

    def capture(target, *_args, **_kwargs):
        sent.append(target)
        return NotionExportResult(
            success=True,
            page_id="v2-sealed-page",
            page_url="https://notion.example/v2-sealed-page",
        )

    monkeypatch.setattr(reports_router, "send_report_to_notion", capture)
    session = auth_logic.create_session("admin@example.com", True)
    csrf = auth_logic.csrf_token_for_session(session.token)

    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.post(f"/notion/{job_id}", data={"csrf_token": csrf})

    assert response.status_code == 200
    assert response.headers.get("X-Notion-Export-Status") != "unsupported-engine-v2"
    assert "노션 내보내기를 지원하지 않습니다" not in response.text
    assert len(sent) == 1, "v2 보고서가 외부 어댑터까지 가지 않았습니다"

    # 「보냈다」로 끝내지 않는다 — 실제로 «봉인 블록»이 나가는지 본다.
    exported = sent[0]
    assert exported.public_projection is not None
    sealed_texts = [
        text
        for block in exported.public_projection.sections
        for _ordinal, text in block.display.paragraphs
    ]
    assert sealed_texts, "봉인된 문단이 없으면 이 시험은 아무것도 안 지킨다"
    나간_문단 = set(_paragraph_texts(notion_logic.build_blocks(exported)))
    assert set(sealed_texts) <= 나간_문단

    with storage_db.connect() as conn:
        record = notion_store.load(
            conn, job_id, notion_store.report_digest(exported)
        )
    assert record is not None
    assert record.state == notion_store.STATE_SUCCEEDED
    assert record.page_id == "v2-sealed-page"


def test_공개블록이_없는_옛_v2_저장본은_전송결과모름_대신_사실대로_닫힌다(
    monkeypatch,
):
    """409를 «전부» 푸는 것이 아니다 — 옮길 수 없는 저장본은 그렇다고 말한다.

    ★ 왜 필요한가(실측) — 공개 블록이 없는 v2 보고서를 그냥 통과시키면 옛
      v1 변환기가 출고 차단 예외를 내고, 작업자가 그 예외를 삼켜
      「노션 전송 결과를 확인하지 못했습니다」로 기록한다. 한 번도 나간 적
      없는 전송이 «결과 모름»으로 남는다 — 409보다 나쁜 거짓말이다.
    """

    report = replace(_sealed_v2_report(), public_projection=None)
    job_id = f"notion-v2-unsealed-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("옮길 수 없는 보고서를 어댑터까지 보냈습니다")

    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden)
    monkeypatch.setattr(reports_router.notion_store, "report_digest", forbidden)
    session = auth_logic.create_session("admin@example.com", True)
    csrf = auth_logic.csrf_token_for_session(session.token)

    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.post(f"/notion/{job_id}", data={"csrf_token": csrf})

    assert response.status_code == 409
    assert "이 보고서는 노션으로 보낼 수 없습니다" in response.text
    assert "전송 결과를 확인하지 못했습니다" not in response.text
    # 화면에 내부 용어가 새지 않는다.
    for 내부_용어 in ("public_projection", "projection", "봉인", "schema"):
        assert 내부_용어 not in response.text
