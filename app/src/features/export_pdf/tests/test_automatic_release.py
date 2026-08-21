from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest

from src.features.export_pdf import automatic_release
from src.features.export_pdf.automatic_release import (
    AutomaticGateStopped,
    automatic_release_pdf,
    report_sha256,
    restore_automatic_release,
)
from src.features.export_pdf.release import prepare_pdf_release
from src.features.export_pdf import release_store
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput


_AT = "2026-08-21T12:00:00+09:00"


def _report():
    sample = next(item for item in available_companies() if item["is_report"])
    user_input = UserInput(
        company=sample["company"],
        job=sample["job"],
        region="",
        posting_text="",
    )
    pipeline = DemoPipeline()
    result = pipeline.run(user_input, pipeline.find_company(user_input))
    assert result.outcome is Outcome.REPORT and result.report is not None
    return result.report


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_필수자동검사가_전부통과한_같은_hash만_자동출고한다():
    report = _report()
    candidate = prepare_pdf_release(report)

    released = automatic_release_pdf(report, candidate, released_at=_AT)

    assert released.content == candidate.pdf_bytes
    assert released.record.report_sha256 == report_sha256(report)
    assert released.record.pdf_sha256 == candidate.pdf_sha256
    assert released.record.page_png_sha256s == tuple(
        page.png_sha256 for page in candidate.pages
    )
    assert all(check.passed for check in released.record.checks)


def test_자동검사하나라도_실패하면_GATE_STOPPED한다(monkeypatch):
    report = _report()
    candidate = prepare_pdf_release(report)
    monkeypatch.setattr(
        automatic_release,
        "_candidate_integrity_problems",
        lambda _candidate: (("forced render failure",), ()),
    )

    with pytest.raises(AutomaticGateStopped, match="GATE_STOPPED"):
        automatic_release_pdf(report, candidate, released_at=_AT)


def test_검사후_report_hash가_바뀌면_저장기록으로_출고하지않는다():
    report = _report()
    candidate = prepare_pdf_release(report)
    released = automatic_release_pdf(report, candidate, released_at=_AT)
    changed = replace(report, company=report.company + " 변경")

    with pytest.raises(AutomaticGateStopped, match="보고서 지문"):
        restore_automatic_release(changed, candidate, released.record)


def test_자동출고테이블은_기존수동승인감사자료를_삭제하지않는다():
    report = _report()
    candidate = prepare_pdf_release(report)
    released = automatic_release_pdf(report, candidate, released_at=_AT)
    conn = _conn()
    try:
        conn.execute(
            "CREATE TABLE pdf_release_records ("
            "report_id TEXT, pdf_sha256 TEXT, approval_json TEXT, "
            "approval_created_at TEXT, release_json TEXT, release_sha256 TEXT, "
            "released_at TEXT, PRIMARY KEY(report_id, pdf_sha256))"
        )
        conn.execute(
            "INSERT INTO pdf_release_records VALUES (?, ?, '{}', ?, NULL, NULL, NULL)",
            ("legacy", "f" * 64, _AT),
        )
        stored = release_store.save_automatic_release(
            conn,
            report_id="auto-1",
            released_pdf=released,
        )
        assert stored == released.record
        assert conn.execute(
            "SELECT COUNT(*) FROM pdf_release_records WHERE report_id='legacy'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_같은_job의_보고서나_PDF_hash가_바뀌면_재자동출고하지않는다():
    report = _report()
    candidate = prepare_pdf_release(report)
    released = automatic_release_pdf(report, candidate, released_at=_AT)
    conn = _conn()
    try:
        release_store.save_automatic_release(
            conn, report_id="immutable-job", released_pdf=released
        )
        with pytest.raises(AutomaticGateStopped, match="지문이 변경"):
            release_store.load_automatic_release_record(
                conn,
                report_id="immutable-job",
                report_sha256="e" * 64,
                pdf_sha256=candidate.pdf_sha256,
            )
    finally:
        conn.close()


def test_같은_report_hash여도_검수뒤_PDF_bytes가_바뀌면_기존자동출고를_덮어쓰지않는다():
    report = _report()
    candidate = prepare_pdf_release(report)
    released = automatic_release_pdf(report, candidate, released_at=_AT)
    changed_pdf_bytes = candidate.pdf_bytes + b"\n"
    changed_candidate = replace(
        candidate,
        pdf_bytes=changed_pdf_bytes,
        pdf_sha256=hashlib.sha256(changed_pdf_bytes).hexdigest(),
    )
    changed_release = automatic_release_pdf(
        report,
        changed_candidate,
        released_at=_AT,
    )
    conn = _conn()
    try:
        stored = release_store.save_automatic_release(
            conn,
            report_id="immutable-pdf-job",
            released_pdf=released,
        )

        assert changed_release.record.report_sha256 == stored.report_sha256
        assert changed_release.record.pdf_sha256 != stored.pdf_sha256
        with pytest.raises(AutomaticGateStopped, match="GATE_STOPPED.*지문이 변경"):
            release_store.save_automatic_release(
                conn,
                report_id="immutable-pdf-job",
                released_pdf=changed_release,
            )

        # 실패한 재출고가 기존 레코드를 갱신하거나 둘째 행을 만들지 않는다.
        rows = conn.execute(
            f"SELECT report_sha256, pdf_sha256 FROM {release_store.AUTOMATIC_TABLE_NAME} "
            "WHERE report_id=?",
            ("immutable-pdf-job",),
        ).fetchall()
        assert [(row["report_sha256"], row["pdf_sha256"]) for row in rows] == [
            (stored.report_sha256, stored.pdf_sha256)
        ]
    finally:
        conn.close()
