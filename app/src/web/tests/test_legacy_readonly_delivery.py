"""과거 공개 링크가 오늘 코드와 GET 부작용에 의해 바뀌지 않는 계약."""

from __future__ import annotations

import contextlib
import sqlite3
import uuid
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.features.storage import db as storage_db
from src.features.storage import constants as storage_constants
from src.features.storage import reports as report_store
from src.web import job_runtime, report_delivery_adapter
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


def _bare_report_app() -> FastAPI:
    """운영 lifespan이 DB를 준비하기 전의 GET 자체만 시험한다."""

    bare = FastAPI()
    bare.include_router(reports_router.router)
    return bare


def _database_dump(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as conn:
        return tuple(conn.iterdump())


def _forbid_writable_connection(*_args, **_kwargs):
    raise AssertionError("공개 보고서 GET이 쓰기 연결을 열었습니다")


def _watch_readonly_total_changes(monkeypatch) -> list[tuple[int, int]]:
    real = storage_db.connect_readonly_existing
    observed: list[tuple[int, int]] = []

    @contextlib.contextmanager
    def watched(*args, **kwargs):
        with real(*args, **kwargs) as conn:
            if conn is None:
                yield None
                return
            before = conn.total_changes
            try:
                yield conn
            finally:
                observed.append((before, conn.total_changes))

    monkeypatch.setattr(storage_db, "connect_readonly_existing", watched)
    return observed


def test_DB가_아예없는_공개GET은_파일이나_폴더를_만들지않고_503으로_구분한다(
    monkeypatch,
) -> None:
    db_path = storage_db.default_db_path().parent / "never-created" / "test.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    assert not db_path.exists()
    assert not db_path.parent.exists()
    monkeypatch.setattr(storage_db, "connect", _forbid_writable_connection)

    locator = "10" * 16
    with TestClient(_bare_report_app(), base_url="https://testserver") as client:
        result = client.get(f"/result/{locator}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{locator}", follow_redirects=False)

    assert result.status_code == pdf.status_code == 503
    assert result.headers["x-report-store-status"] == "missing"
    assert pdf.headers["x-report-store-status"] == "missing"
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_불완전한_DB의_공개GET은_schema와_행을_보충하지않고_503으로_구분한다(
    monkeypatch,
) -> None:
    db_path = storage_db.default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE preserved_marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO preserved_marker(value) VALUES ('그대로')")
    before = _database_dump(db_path)
    observed = _watch_readonly_total_changes(monkeypatch)
    monkeypatch.setattr(storage_db, "connect", _forbid_writable_connection)

    locator = "11" * 16
    with TestClient(_bare_report_app(), base_url="https://testserver") as client:
        result = client.get(f"/result/{locator}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{locator}", follow_redirects=False)

    assert result.status_code == pdf.status_code == 503
    assert result.headers["x-report-store-status"] == "incomplete"
    assert pdf.headers["x-report-store-status"] == "incomplete"
    assert _database_dump(db_path) == before
    assert observed and all(before == after == 0 for before, after in observed)


def test_읽을수없는_DB의_공개GET은_파일을_덮지않고_503으로_구분한다(
    monkeypatch,
) -> None:
    db_path = storage_db.default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    original = b"this-is-not-a-sqlite-database"
    db_path.write_bytes(original)
    monkeypatch.setattr(storage_db, "connect", _forbid_writable_connection)

    locator = "12" * 16
    with TestClient(_bare_report_app(), base_url="https://testserver") as client:
        result = client.get(f"/result/{locator}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{locator}", follow_redirects=False)

    assert result.status_code == pdf.status_code == 503
    assert result.headers["x-report-store-status"] == "unreadable"
    assert pdf.headers["x-report-store-status"] == "unreadable"
    assert db_path.read_bytes() == original


def test_정상_DB의_없는번호_GET도_schema와_total_changes를_바꾸지않는다(
    monkeypatch,
) -> None:
    db_path = storage_db.default_db_path()
    with storage_db.connect():
        pass
    session = auth_logic.create_session(
        "admin@example.com", True, subject="test:readonly-missing-admin"
    )
    before = _database_dump(db_path)
    observed = _watch_readonly_total_changes(monkeypatch)
    monkeypatch.setattr(storage_db, "connect", _forbid_writable_connection)

    locator = "13" * 16
    with TestClient(_bare_report_app(), base_url="https://testserver") as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        result = client.get(f"/result/{locator}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{locator}", follow_redirects=False)

    assert result.status_code == pdf.status_code == 303
    assert _database_dump(db_path) == before
    assert observed and all(before == after == 0 for before, after in observed)


def test_legacy_HTML은_현재검사와_renderer를_한번도_부르지않고_저장payload를_보인다(
    monkeypatch,
) -> None:
    report_id = uuid.uuid4().hex
    report = replace(
        _demo_report(),
        company="저장 당시 회사 이름",
        generated_at="2026-08-28",
    )
    with storage_db.connect() as conn:
        report_store.save(
            conn,
            report_id,
            "legacy-corp",
            report.job,
            report,
            created_at="2026-08-28T10:20:30+09:00",
        )
        raw_payload = str(
            conn.execute(
                f"SELECT payload_json FROM {report_store.TABLE_REPORTS} "
                "WHERE report_id=?",
                (report_id,),
            ).fetchone()[0]
        )
    session = auth_logic.create_session(
        "admin@example.com", True, subject="test:legacy-readonly-admin"
    )
    before = _database_dump(storage_db.default_db_path())
    calls: list[str] = []

    def forbidden(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("legacy GET이 현재 검사나 renderer를 불렀습니다")

    monkeypatch.setattr(reports_router, "_report_for_output", forbidden)
    monkeypatch.setattr(reports_router, "_release_state", forbidden)
    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", forbidden)
    monkeypatch.setattr(report_store, "_normalize_legacy_report", forbidden)
    job_runtime._JOBS.clear()

    loaded = report_delivery_adapter.load_legacy_public_report(report_id)
    assert loaded is not None and loaded.payload_json == raw_payload
    with TestClient(_bare_report_app(), base_url="https://testserver") as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get(f"/result/{report_id}", follow_redirects=False)

    assert response.status_code == 200
    assert "저장 당시 회사 이름" in response.text
    assert "과거 방식으로 저장된 본문을 그대로" in response.text
    assert "내용 생성 2026-08-28" in response.text
    assert "PDF 원본 확인 불가" in response.text
    assert calls == []
    assert _database_dump(storage_db.default_db_path()) == before


def test_로그인한_legacy_HTML_GET도_세션정리나_조회이력을_쓰지않는다(
    monkeypatch,
) -> None:
    report_id = uuid.uuid4().hex
    report = replace(_demo_report(), generated_at="2026-08-28")
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "legacy-corp", report.job, report)
    session = auth_logic.create_session("admin@example.com", True)
    before = _database_dump(storage_db.default_db_path())
    observed = _watch_readonly_total_changes(monkeypatch)
    monkeypatch.setattr(storage_db, "connect", _forbid_writable_connection)

    with TestClient(_bare_report_app(), base_url="https://testserver") as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get(f"/result/{report_id}", follow_redirects=False)

    assert response.status_code == 200
    assert _database_dump(storage_db.default_db_path()) == before
    assert observed and all(before == after == 0 for before, after in observed)
