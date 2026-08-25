"""PDF 다운로드 웹 경계의 선행 차단과 fail-safe 계약."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_pdf.logic import PDFGenerationError
from src.features.export_pdf.automatic_release import AutomaticGateStopped
from src.features.export_pdf.release import prepare_pdf_release
from src.features.export_pdf import release_store as pdf_release_store
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.web import job_runtime
from src.web.main import app
from src.web.routers import reports as reports_router
from src.features.storage import db as storage_db


_REAL_RELEASE_STATE = reports_router._release_state


def _fake_candidate():
    return SimpleNamespace(
        pdf_sha256="a" * 64,
        pages=(SimpleNamespace(number=1, png_sha256="b" * 64),),
        expected_fact_ids=("fact-1",),
    )


def test_PDF후보캐시는_같은내용만_재사용하고_내용변경은_다시만든다(monkeypatch):
    reports_router._PDF_CANDIDATE_CACHE.clear()
    reports_router._PDF_CANDIDATE_CACHE_BYTES = 0
    calls: list[str] = []

    def digest(report):
        return report.digest

    def prepare(report):
        calls.append(report.digest)
        return SimpleNamespace(pdf_bytes=b"pdf", pages=())

    monkeypatch.setattr(reports_router.notion_store, "report_digest", digest)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", prepare)
    first = SimpleNamespace(digest="first")
    changed = SimpleNamespace(digest="changed")

    one = reports_router._candidate_for_report("report-1", first)
    two = reports_router._candidate_for_report("report-1", first)
    three = reports_router._candidate_for_report("report-1", changed)

    assert one is two
    assert three is not one
    assert calls == ["first", "changed"]


def _prepare_identity_approval_route(monkeypatch, *, job_id: str):
    report = object()
    candidate = _fake_candidate()
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_report_for_output", lambda value: value)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", lambda _report: candidate)
    monkeypatch.setattr(
        reports_router,
        "_candidate_for_report",
        lambda _job_id, _report: candidate,
    )
    monkeypatch.setenv(
        auth_constants.ENV_ADMIN_EMAILS,
        "old-alias@example.com,new-alias@example.com,editor@example.com",
    )
    return candidate


def test_수동PDF승인_POST는_기존링크로도_영구종료되어_우회할수없다(monkeypatch):
    job_id = f"pdf-no-participants-{uuid.uuid4().hex}"
    candidate = _prepare_identity_approval_route(monkeypatch, job_id=job_id)
    monkeypatch.delenv(auth_constants.ENV_PDF_RELEASE_PARTICIPANTS, raising=False)
    session = auth_logic.create_session(
        "old-alias@example.com", True, subject="test:fact-person"
    )
    with TestClient(app) as client:
        response = client.post(
            f"/review/pdf/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
            data={
                "csrf_token": auth_logic.csrf_token_for_session(session.token),
                "pdf_sha256": candidate.pdf_sha256,
                "review_role": "fact",
                "confirm_role": "yes",
                "reviewed_fact_ids": ["fact-1"],
                "fact_failed_count": "0",
            },
            follow_redirects=False,
        )
    assert response.status_code == 410
    with storage_db.connect() as conn:
        assert pdf_release_store.load_participant_ledger(
            conn, report_id=job_id, pdf_sha256=candidate.pdf_sha256
        ) is None


def test_옛수동승인요청은_어떤계정과역할로도_기록을만들지않는다(monkeypatch):
    job_id = f"pdf-alias-person-{uuid.uuid4().hex}"
    candidate = _prepare_identity_approval_route(monkeypatch, job_id=job_id)
    participants = {
        "author": "test:author",
        "producer": "test:producer",
        "fact": "test:same-person",
        "editorial": "test:editor",
        "visual": "test:visual",
    }
    monkeypatch.setenv(
        auth_constants.ENV_PDF_RELEASE_PARTICIPANTS,
        json.dumps(participants),
    )
    first = auth_logic.create_session(
        "old-alias@example.com", True, subject="test:same-person"
    )
    alias = auth_logic.create_session(
        "new-alias@example.com", True, subject="test:same-person"
    )
    editor = auth_logic.create_session(
        "editor@example.com", True, subject="test:editor"
    )
    with TestClient(app) as client:
        fact = client.post(
            f"/review/pdf/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: first.token},
            data={
                "csrf_token": auth_logic.csrf_token_for_session(first.token),
                "pdf_sha256": candidate.pdf_sha256,
                "review_role": "fact",
                "confirm_role": "yes",
                "reviewed_fact_ids": ["fact-1"],
                "fact_failed_count": "0",
            },
            follow_redirects=False,
        )
        # 최초 승인에서 PDF별 원장을 잠근 뒤에는 환경 설정이 사라져도 저장된
        # 배정을 쓰며, 이메일 별칭이 그 역할을 바꿀 수 없다.
        monkeypatch.delenv(auth_constants.ENV_PDF_RELEASE_PARTICIPANTS)
        editorial = client.post(
            f"/review/pdf/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: alias.token},
            data={
                "csrf_token": auth_logic.csrf_token_for_session(alias.token),
                "pdf_sha256": candidate.pdf_sha256,
                "review_role": "editorial",
                "confirm_role": "yes",
            },
            follow_redirects=False,
        )
        assigned_editorial = client.post(
            f"/review/pdf/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: editor.token},
            data={
                "csrf_token": auth_logic.csrf_token_for_session(editor.token),
                "pdf_sha256": candidate.pdf_sha256,
                "review_role": "editorial",
                "confirm_role": "yes",
            },
            follow_redirects=False,
        )
    assert fact.status_code == 410
    assert editorial.status_code == 410
    assert assigned_editorial.status_code == 410
    with storage_db.connect() as conn:
        decisions = pdf_release_store.load_role_decisions(
            conn, report_id=job_id, pdf_sha256=candidate.pdf_sha256
        )
    assert decisions == ()


def test_옛_DOCX_다운로드는_내용을_내리지않고_PDF를_안내한다():
    with TestClient(app) as client:
        response = client.get("/download/" + "a" * 32)

    assert response.status_code == 410
    assert "PDF 보고서 받기" in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "application/vnd.openxmlformats" not in response.headers.get(
        "content-type", ""
    )


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


@pytest.mark.parametrize(
    ("state", "expected_status"),
    (("missing", 303), ("expired", 410), ("storage", 503)),
)
def test_PDF_선행차단은_generator를_호출하지_않는다(
    monkeypatch,
    state,
    expected_status,
):
    report = _demo_report()
    calls = 0

    def forbidden_generator(_report):
        nonlocal calls
        calls += 1
        raise AssertionError("선행 차단 뒤 PDF generator를 호출했습니다")

    def unavailable(_job_id):
        raise job_runtime.ReportStoreUnavailable("시험용 저장소 장애")

    job_runtime._JOBS.clear()
    monkeypatch.setattr(reports_router, "prepare_pdf_release", forbidden_generator)
    if state == "missing":
        monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: None)
        monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    elif state == "expired":
        monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
        monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: True)
    else:
        monkeypatch.setattr(job_runtime, "_load_saved_report", unavailable)

    with TestClient(app) as client:
        response = client.get(
            f"/download/pdf/{state}-report", follow_redirects=False
        )

    assert response.status_code == expected_status
    assert calls == 0
    assert "content-disposition" not in response.headers
    if state == "storage":
        assert response.headers["retry-after"] == "3"
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "error",
    (
        PDFGenerationError("RAW_REPORT_SENTENCE_expected"),
        RuntimeError("storage.db SELECT RAW_REPORT_SENTENCE_unexpected"),
    ),
    ids=("expected", "unexpected"),
)
def test_PDF_generator_오류는_원문없는_generic_503이다(
    monkeypatch,
    caplog,
    error,
):
    report = _demo_report()
    company_secret = report.company
    job_id = f"pdf-generation-failure-{uuid.uuid4().hex}"

    def failed_generator(_report):
        raise error

    def filename_must_not_run(_report):
        raise AssertionError("생성 실패 뒤 파일명을 만들면 안 됩니다")

    job_runtime._JOBS.clear()
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_release_state", _REAL_RELEASE_STATE)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", failed_generator)
    monkeypatch.setattr(
        reports_router,
        "build_pdf_download_filename",
        filename_must_not_run,
    )
    caplog.set_level("INFO", logger=reports_router.__name__)

    with TestClient(app) as client:
        response = client.get(
            f"/download/pdf/{job_id}",
            follow_redirects=False,
            headers={"X-Request-ID": "pdf/test request 01"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"] == "pdf_test_request_01"
    assert "content-disposition" not in response.headers
    assert "PDF 보고서를 잠시 만들 수 없습니다" in response.text
    assert "잠시 후 다시 받아 주세요" in response.text
    assert company_secret not in response.text
    assert "RAW_REPORT_SENTENCE" not in response.text
    assert "storage.db" not in response.text
    assert "RAW_REPORT_SENTENCE" not in caplog.text
    assert "storage.db" not in caplog.text
    assert company_secret not in caplog.text
    assert f"error_type={type(error).__name__}" in caplog.text


def test_필수자동검사를_통과하면_웹과PDF가_같이자동출고된다(monkeypatch):
    report = _demo_report()
    job_id = "pdf-release-unapproved"
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_release_state", _REAL_RELEASE_STATE)

    with TestClient(app) as client:
        download = client.get(f"/download/pdf/{job_id}", follow_redirects=False)
        public_result = client.get(f"/result/{job_id}", follow_redirects=False)

    assert download.status_code == 200
    assert public_result.status_code == 200
    assert "수동" not in public_result.text
    assert "내부 검수 미리보기" not in public_result.text
    assert download.headers["content-type"] == "application/pdf"
    assert len(download.headers["x-pdf-release-record"]) == 64


def test_자동검사하나실패하면_웹PDFNotion을_모두차단한다(monkeypatch):
    report = _demo_report()
    job_id = f"auto-gate-stopped-{uuid.uuid4().hex}"
    session = auth_logic.create_session("admin@example.com", True)

    def stopped(**_kwargs):
        raise AutomaticGateStopped(("forced mandatory check failure",))

    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_release_state", stopped)
    cookies = {auth_constants.SESSION_COOKIE_NAME: session.token}
    with TestClient(app) as client:
        web = client.get(f"/result/{job_id}", cookies=cookies)
        pdf = client.get(f"/download/pdf/{job_id}", cookies=cookies)
        notion = client.post(
            f"/notion/{job_id}",
            cookies=cookies,
            data={"csrf_token": auth_logic.csrf_token_for_session(session.token)},
        )

    assert web.status_code == pdf.status_code == notion.status_code == 409
    for response in (web, pdf, notion):
        assert "필수 검사 중 하나라도" in response.text
        assert "현재 보고서 화면은 그대로" not in response.text


def test_수동승인은410이고_자동검사객체만_웹과PDF를_연다(monkeypatch):
    report = _demo_report()
    job_id = f"pdf-release-approved-{uuid.uuid4().hex}"
    release_errors: list[str] = []
    real_pending_response = reports_router._pdf_review_pending_response

    def capture_release_error(request, error, **kwargs):
        release_errors.append(str(error))
        return real_pending_response(request, error, **kwargs)

    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_release_state", _REAL_RELEASE_STATE)
    monkeypatch.setattr(
        reports_router,
        "_pdf_review_pending_response",
        capture_release_error,
    )
    monkeypatch.setenv(
        auth_constants.ENV_ADMIN_EMAILS,
        "fact@example.com,editor@example.com,visual@example.com",
    )
    participant_subjects = {
        "author": "test:author",
        "producer": "test:producer",
        "fact": "test:fact",
        "editorial": "test:editor",
        "visual": "test:visual",
    }
    monkeypatch.setenv(
        auth_constants.ENV_PDF_RELEASE_PARTICIPANTS,
        json.dumps(participant_subjects),
    )
    sessions = {
        role: auth_logic.create_session(
            f"{role}@example.com",
            True,
            subject=participant_subjects[
                "editorial" if role == "editor" else role
            ],
        )
        for role in ("fact", "editor", "visual")
    }
    cookies = {
        role: {auth_constants.SESSION_COOKIE_NAME: session.token}
        for role, session in sessions.items()
    }
    with TestClient(app) as client:
        review = client.get(f"/review/pdf/{job_id}", cookies=cookies["fact"])
        assert review.status_code == 410

        fact_approved = client.post(
            f"/review/pdf/{job_id}",
            cookies=cookies["fact"],
            data={
                "csrf_token": auth_logic.csrf_token_for_session(sessions["fact"].token),
                "pdf_sha256": "a" * 64,
                "review_role": "fact",
                "confirm_role": "yes",
                "reviewed_fact_ids": ["fact-1"],
                "fact_failed_count": "0",
            },
            follow_redirects=False,
        )
        assert fact_approved.status_code == 410
        assert client.get(f"/result/{job_id}", follow_redirects=False).status_code == 200

        editorial_approved = client.post(
            f"/review/pdf/{job_id}",
            cookies=cookies["editor"],
            data={
                "csrf_token": auth_logic.csrf_token_for_session(sessions["editor"].token),
                "pdf_sha256": "a" * 64,
                "review_role": "editorial",
                "confirm_role": "yes",
            },
            follow_redirects=False,
        )
        assert editorial_approved.status_code == 410
        assert client.get(f"/result/{job_id}", follow_redirects=False).status_code == 200

        approved = client.post(
            f"/review/pdf/{job_id}",
            cookies=cookies["visual"],
            data={
                "csrf_token": auth_logic.csrf_token_for_session(sessions["visual"].token),
                "pdf_sha256": "a" * 64,
                "review_role": "visual",
                "confirm_role": "yes",
                "visual_all_pages": "yes",
            },
            follow_redirects=False,
        )
        public_result = client.get(f"/result/{job_id}", follow_redirects=False)
        download = client.get(f"/download/pdf/{job_id}", follow_redirects=False)

    assert approved.status_code == 410
    assert public_result.status_code == 200
    assert "내부 검수 미리보기" not in public_result.text
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert len(download.headers["x-pdf-sha256"]) == 64
    assert len(download.headers["x-pdf-release-record"]) == 64
