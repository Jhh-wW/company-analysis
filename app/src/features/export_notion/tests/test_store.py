"""Notion export operation persistence and CAS concurrency tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.features.export_notion import store
from src.features.pipeline.port import Grade, Report
from src.features.storage import db


def _report(*, company: str = "우리엔") -> Report:
    return Report(
        company=company,
        job="영업",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        requirements=[],
        sources=[],
        citations=[],
        cells={},
        shortfall_reasons=[],
        generated_at="2026-08-18",
    )


def test_보고서_digest는_안정적이고_내용이_바뀌면_달라진다():
    first = store.report_digest(_report())
    again = store.report_digest(_report())
    changed = store.report_digest(_report(company="다른회사"))

    assert first == again
    assert len(first) == 64
    assert first != changed


def test_성공은_재시작뒤에도_같은_page를_재사용한다(tmp_path: Path):
    path = tmp_path / "notion.db"
    digest = "a" * 64
    with db.connect(path) as conn:
        claimed = store.claim(conn, "job-1", digest)
        assert claimed.claimed is True
        assert store.finish(
            conn,
            "job-1",
            digest,
            claimed.record.revision,
            state=store.STATE_SUCCEEDED,
            page_id="page-one",
            page_url="https://notion.example/page-one",
        )

    # A new connection models a process restart.  It must not claim a second
    # create-page call and must retain the original capability URL.
    with db.connect(path) as conn:
        repeated = store.claim(conn, "job-1", digest)
    assert repeated.claimed is False
    assert repeated.record.state == store.STATE_SUCCEEDED
    assert repeated.record.page_id == "page-one"
    assert repeated.record.page_url == "https://notion.example/page-one"


def _concurrent_claim(path: Path, barrier: threading.Barrier, **kwargs) -> bool:
    barrier.wait()
    with db.connect(path) as conn:
        return store.claim(conn, "same-job", "b" * 64, **kwargs).claimed


def test_동시_첫요청은_DB_unique로_딱_하나만_claim한다(tmp_path: Path):
    path = tmp_path / "concurrent.db"
    with db.connect(path):
        pass
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_concurrent_claim, path, barrier) for _ in range(2)]
    assert sum(future.result() for future in futures) == 1


def test_unknown은_자동재시도하지_않고_명시적_CAS도_한요청만_허용한다(
    tmp_path: Path,
):
    path = tmp_path / "retry.db"
    digest = "b" * 64
    with db.connect(path) as conn:
        first = store.claim(conn, "same-job", digest)
        assert store.finish(
            conn,
            "same-job",
            digest,
            first.record.revision,
            state=store.STATE_UNKNOWN,
            error_kind="timeout",
        )
    with db.connect(path) as conn:
        automatic = store.claim(conn, "same-job", digest)
    assert automatic.claimed is False
    assert automatic.record.state == store.STATE_UNKNOWN

    barrier = threading.Barrier(2)
    kwargs = {"explicit_retry": True, "expected_revision": 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_concurrent_claim, path, barrier, **kwargs) for _ in range(2)
        ]
    assert sum(future.result() for future in futures) == 1
    with db.connect(path) as conn:
        current = store.load(conn, "same-job", digest)
    assert current is not None
    assert current.state == store.STATE_IN_PROGRESS
    assert current.revision == 2


def test_오래된_in_progress는_unknown으로_바꾸고_자동호출하지_않는다(
    tmp_path: Path,
):
    path = tmp_path / "crash.db"
    digest = "c" * 64
    with db.connect(path) as conn:
        store.claim(conn, "crashed-job", digest, now=10.0)
    with db.connect(path) as conn:
        recovered = store.claim(
            conn,
            "crashed-job",
            digest,
            now=10.0 + store.STALE_IN_PROGRESS_AFTER_SEC,
        )
    assert recovered.claimed is False
    assert recovered.record.state == store.STATE_UNKNOWN
    assert recovered.record.error_kind == "interrupted"
