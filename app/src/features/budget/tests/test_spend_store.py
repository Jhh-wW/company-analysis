"""P-126 — 비용 장부가 재시작 뒤에도 링크·사용자·관리자별로 이어지는가."""

from __future__ import annotations

import datetime as dt
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Barrier

import pytest

from src.features.budget import spend_store
from src.features.budget.constants import (
    SPEND_PHASE_IDENTIFY,
    SPEND_PHASE_OCR,
    SPEND_PHASE_PIPELINE,
)

_오늘 = dt.date(2026, 8, 17)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    spend_store.ensure_schema(connection)
    yield connection
    connection.close()


def _적기(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    bucket: str,
    amount: float,
) -> bool:
    return spend_store.append_spend(
        conn,
        run_id=run_id,
        phase=phase,
        day=_오늘,
        bucket=bucket,
        cost_krw=amount,
        created_at="2026-08-17T10:00:00",
    )


def test_링크_사용자_관리자_통장을_따로_복원한다(conn: sqlite3.Connection):
    통장들 = {
        "a1b2c3d4e5f60718": 100.0,
        "user:friend@gmail.com": 200.0,
        "user:admin@gmail.com": 300.0,
    }
    단계들 = (SPEND_PHASE_IDENTIFY, SPEND_PHASE_OCR, SPEND_PHASE_PIPELINE)
    for index, ((bucket, amount), phase) in enumerate(zip(통장들.items(), 단계들)):
        assert _적기(
            conn, run_id=f"run-{index}", phase=phase, bucket=bucket, amount=amount
        )

    snapshot = spend_store.load_day(conn, _오늘)

    assert snapshot.total_krw == 600.0
    assert snapshot.by_bucket == {
        spend_store.bucket_id(bucket): amount for bucket, amount in 통장들.items()
    }


def test_이메일과_열쇠_원문은_DB에_남기지_않는다(conn: sqlite3.Connection):
    email_bucket = "user:person@example.com"
    assert _적기(
        conn,
        run_id="privacy",
        phase=SPEND_PHASE_IDENTIFY,
        bucket=email_bucket,
        amount=10.0,
    )

    row = conn.execute(
        f"SELECT bucket_id FROM {spend_store.TABLE_SPEND_EVENTS}"
    ).fetchone()

    assert row is not None
    assert row[0] == spend_store.bucket_id(email_bucket)
    assert email_bucket not in row[0]


def test_같은_요청_단계는_두번_더하지_않는다(conn: sqlite3.Connection):
    kwargs = dict(
        run_id="same",
        phase=SPEND_PHASE_PIPELINE,
        bucket="user:a@example.com",
        amount=55.0,
    )

    assert _적기(conn, **kwargs) is True
    assert _적기(conn, **kwargs) is False
    assert spend_store.load_day(conn, _오늘).total_krw == 55.0


def test_요청별_단계합과_통장을_함께_복원한다(conn: sqlite3.Connection):
    bucket = "user:one@example.com"
    _적기(
        conn, run_id="one", phase=SPEND_PHASE_IDENTIFY, bucket=bucket, amount=10.0
    )
    _적기(conn, run_id="one", phase=SPEND_PHASE_OCR, bucket=bucket, amount=20.0)

    snapshot = spend_store.load_day(conn, _오늘)

    assert snapshot.by_run == {"one": 30.0}
    assert snapshot.bucket_by_run == {"one": spend_store.bucket_id(bucket)}


def test_같은_요청을_다른_통장으로_바꾸지_못한다(conn: sqlite3.Connection):
    _적기(
        conn,
        run_id="fixed-bucket",
        phase=SPEND_PHASE_IDENTIFY,
        bucket="user:first@example.com",
        amount=10.0,
    )

    with pytest.raises(ValueError):
        _적기(
            conn,
            run_id="fixed-bucket",
            phase=SPEND_PHASE_OCR,
            bucket="user:second@example.com",
            amount=20.0,
        )


def test_다른_날_비용은_오늘_복원하지_않는다(conn: sqlite3.Connection):
    _적기(
        conn,
        run_id="today",
        phase=SPEND_PHASE_IDENTIFY,
        bucket="bucket",
        amount=10.0,
    )
    spend_store.append_spend(
        conn,
        run_id="yesterday",
        phase=SPEND_PHASE_IDENTIFY,
        day=_오늘 - dt.timedelta(days=1),
        bucket="bucket",
        cost_krw=999.0,
        created_at="2026-08-16T10:00:00",
    )

    assert spend_store.load_day(conn, _오늘).total_krw == 10.0


def test_모르는_단계와_음수는_저장전에_거부한다(conn: sqlite3.Connection):
    with pytest.raises(ValueError):
        _적기(conn, run_id="bad-phase", phase="모름", bucket="x", amount=1.0)
    with pytest.raises(ValueError):
        _적기(
            conn,
            run_id="bad-cost",
            phase=SPEND_PHASE_OCR,
            bucket="x",
            amount=-1.0,
        )


@pytest.mark.parametrize("amount", [math.nan, math.inf, -math.inf])
def test_NaN과_무한대는_원장에_쓸_수_없다(
    conn: sqlite3.Connection, amount: float
):
    with pytest.raises(ValueError):
        _적기(
            conn,
            run_id="not-finite",
            phase=SPEND_PHASE_IDENTIFY,
            bucket="bucket",
            amount=amount,
        )


@pytest.mark.parametrize(
    ("changed_day", "changed_bucket", "changed_cost"),
    [
        (_오늘 + dt.timedelta(days=1), "bucket", 10.0),
        (_오늘, "other-bucket", 10.0),
        (_오늘, "bucket", 11.0),
    ],
)
def test_같은_요청단계라도_날짜_통장_금액이_다르면_멱등이_아니다(
    conn: sqlite3.Connection,
    changed_day: dt.date,
    changed_bucket: str,
    changed_cost: float,
):
    _적기(
        conn,
        run_id="same-phase",
        phase=SPEND_PHASE_IDENTIFY,
        bucket="bucket",
        amount=10.0,
    )

    with pytest.raises(ValueError):
        spend_store.append_spend(
            conn,
            run_id="same-phase",
            phase=SPEND_PHASE_IDENTIFY,
            day=changed_day,
            bucket=changed_bucket,
            cost_krw=changed_cost,
            created_at="2026-08-17T11:00:00",
        )


def test_진행중_표식과_확정비용은_한_트랜잭션으로_마감한다(
    conn: sqlite3.Connection,
):
    assert spend_store.begin_inflight(
        conn,
        run_id="atomic",
        phase=SPEND_PHASE_PIPELINE,
        day=_오늘,
        bucket="bucket",
        started_at="2026-08-17T10:00:00",
    )

    inserted = spend_store.finish_inflight(
        conn,
        run_id="atomic",
        phase=SPEND_PHASE_PIPELINE,
        day=_오늘,
        bucket="bucket",
        cost_krw=25.0,
        created_at="2026-08-17T10:01:00",
    )

    assert inserted is True
    assert spend_store.load_day(conn, _오늘).by_run == {"atomic": 25.0}
    assert spend_store.load_unresolved_day(conn, _오늘) == frozenset()


def test_마감_트랜잭션을_롤백하면_표식은_남고_비용은_안_남는다(
    conn: sqlite3.Connection,
):
    spend_store.begin_inflight(
        conn,
        run_id="rollback",
        phase=SPEND_PHASE_OCR,
        day=_오늘,
        bucket="bucket",
        started_at="2026-08-17T10:00:00",
    )
    conn.commit()
    conn.execute("BEGIN")
    spend_store.finish_inflight(
        conn,
        run_id="rollback",
        phase=SPEND_PHASE_OCR,
        day=_오늘,
        bucket="bucket",
        cost_krw=20.0,
        created_at="2026-08-17T10:01:00",
    )
    conn.rollback()

    assert spend_store.load_day(conn, _오늘).total_krw == 0.0
    assert spend_store.load_unresolved_day(conn, _오늘) == frozenset(
        {spend_store.bucket_id("bucket")}
    )


def test_확정_0원도_표식을_지운다(conn: sqlite3.Connection):
    spend_store.begin_inflight(
        conn,
        run_id="zero",
        phase=SPEND_PHASE_IDENTIFY,
        day=_오늘,
        bucket="bucket",
        started_at="2026-08-17T10:00:00",
    )

    inserted = spend_store.finish_inflight(
        conn,
        run_id="zero",
        phase=SPEND_PHASE_IDENTIFY,
        day=_오늘,
        bucket="bucket",
        cost_krw=0.0,
        created_at="2026-08-17T10:01:00",
    )

    assert inserted is False
    assert spend_store.load_unresolved_day(conn, _오늘) == frozenset()


def test_어제_미확정_표식은_오늘_통장을_막지_않는다(conn: sqlite3.Connection):
    spend_store.begin_inflight(
        conn,
        run_id="yesterday-inflight",
        phase=SPEND_PHASE_IDENTIFY,
        day=_오늘 - dt.timedelta(days=1),
        bucket="bucket",
        started_at="2026-08-16T23:59:00",
    )

    assert spend_store.load_unresolved_day(conn, _오늘) == frozenset()


def test_미확정_행은_요청_단계_통장까지_안정된_순서로_조회한다(
    conn: sqlite3.Connection,
):
    bucket = "user:active@example.com"
    entries = (
        ("z-run", SPEND_PHASE_PIPELINE, "2026-08-17T10:00:02"),
        ("b-run", SPEND_PHASE_OCR, "2026-08-17T10:00:01"),
        ("a-run", SPEND_PHASE_IDENTIFY, "2026-08-17T10:00:01"),
    )
    for run_id, phase, started_at in entries:
        assert spend_store.begin_inflight(
            conn,
            run_id=run_id,
            phase=phase,
            day=_오늘,
            bucket=bucket,
            started_at=started_at,
        )
    assert spend_store.begin_inflight(
        conn,
        run_id="other-day",
        phase=SPEND_PHASE_IDENTIFY,
        day=_오늘 + dt.timedelta(days=1),
        bucket=bucket,
        started_at="2026-08-18T00:00:00",
    )

    rows = spend_store.list_inflight_day(conn, _오늘)

    assert isinstance(rows, tuple)
    assert [(row.run_id, row.phase) for row in rows] == [
        ("a-run", SPEND_PHASE_IDENTIFY),
        ("b-run", SPEND_PHASE_OCR),
        ("z-run", SPEND_PHASE_PIPELINE),
    ]
    assert all(row.day == _오늘 for row in rows)
    assert all(row.bucket_id == spend_store.bucket_id(bucket) for row in rows)
    assert [row.started_at for row in rows] == [
        "2026-08-17T10:00:01",
        "2026-08-17T10:00:01",
        "2026-08-17T10:00:02",
    ]
    with pytest.raises(FrozenInstanceError):
        setattr(rows[0], "run_id", "changed")


def test_요청번호로_현재_미확정단계만_조회한다(conn: sqlite3.Connection):
    private_bucket = "user:private@example.com"
    spend_store.begin_inflight(
        conn,
        run_id="ocr-run",
        phase=SPEND_PHASE_OCR,
        day=_오늘,
        bucket=private_bucket,
        started_at="2026-08-17T10:00:00",
    )
    spend_store.begin_inflight(
        conn,
        run_id="other-run",
        phase=SPEND_PHASE_PIPELINE,
        day=_오늘,
        bucket="other-bucket",
        started_at="2026-08-17T10:01:00",
    )

    assert spend_store.get_inflight_phase(conn, " ocr-run ") == SPEND_PHASE_OCR
    assert spend_store.get_inflight_phase(conn, "missing-run") is None


def test_한요청에_미확정단계가_둘이면_임의로_고르지_않는다(
    conn: sqlite3.Connection,
):
    for phase in (SPEND_PHASE_OCR, SPEND_PHASE_PIPELINE):
        spend_store.begin_inflight(
            conn,
            run_id="broken-run",
            phase=phase,
            day=_오늘,
            bucket="same-bucket",
            started_at="2026-08-17T10:00:00",
        )

    with pytest.raises(ValueError, match="둘 이상"):
        spend_store.get_inflight_phase(conn, "broken-run")


def test_다섯_연결이_동시에_begin_finish해도_행과_합계가_정확하다(
    tmp_path: Path,
):
    path = tmp_path / "five-connections.db"
    setup = sqlite3.connect(path, timeout=5)
    try:
        setup.execute("PRAGMA journal_mode=WAL")
        spend_store.ensure_schema(setup)
        setup.commit()
    finally:
        setup.close()

    run_ids = tuple(f"concurrent-{index}" for index in range(5))
    amounts = {run_id: float((index + 1) * 10) for index, run_id in enumerate(run_ids)}
    bucket = "user:five@example.com"
    begin_barrier = Barrier(5)

    def begin(run_id: str) -> bool:
        worker = sqlite3.connect(path, timeout=5)
        try:
            begin_barrier.wait(timeout=10)
            inserted = spend_store.begin_inflight(
                worker,
                run_id=run_id,
                phase=SPEND_PHASE_PIPELINE,
                day=_오늘,
                bucket=bucket,
                started_at=f"2026-08-17T10:00:0{run_id[-1]}",
            )
            worker.commit()
            return inserted
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=5) as pool:
        begin_results = list(pool.map(begin, run_ids))

    assert begin_results == [True] * 5
    check = sqlite3.connect(path, timeout=5)
    try:
        inflight = spend_store.list_inflight_day(check, _오늘)
        assert [row.run_id for row in inflight] == list(run_ids)
        assert all(row.bucket_id == spend_store.bucket_id(bucket) for row in inflight)
    finally:
        check.close()

    finish_barrier = Barrier(5)

    def finish(run_id: str) -> bool:
        worker = sqlite3.connect(path, timeout=5)
        try:
            finish_barrier.wait(timeout=10)
            inserted = spend_store.finish_inflight(
                worker,
                run_id=run_id,
                phase=SPEND_PHASE_PIPELINE,
                day=_오늘,
                bucket=bucket,
                cost_krw=amounts[run_id],
                created_at="2026-08-17T10:01:00",
            )
            worker.commit()
            return inserted
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=5) as pool:
        finish_results = list(pool.map(finish, run_ids))

    assert finish_results == [True] * 5
    check = sqlite3.connect(path, timeout=5)
    try:
        assert spend_store.list_inflight_day(check, _오늘) == ()
        snapshot = spend_store.load_day(check, _오늘)
        assert snapshot.total_krw == sum(amounts.values())
        assert snapshot.by_run == amounts
        assert snapshot.run_ids == frozenset(run_ids)
    finally:
        check.close()


def test_진행중_표식의_단계나_통장이_다르면_마감하지_않는다(
    conn: sqlite3.Connection,
):
    spend_store.begin_inflight(
        conn,
        run_id="fixed-marker",
        phase=SPEND_PHASE_IDENTIFY,
        day=_오늘,
        bucket="bucket",
        started_at="2026-08-17T10:00:00",
    )

    with pytest.raises(ValueError):
        spend_store.finish_inflight(
            conn,
            run_id="fixed-marker",
            phase=SPEND_PHASE_IDENTIFY,
            day=_오늘,
            bucket="other-bucket",
            cost_krw=10.0,
            created_at="2026-08-17T10:01:00",
        )
    with pytest.raises(ValueError):
        spend_store.finish_inflight(
            conn,
            run_id="fixed-marker",
            phase=SPEND_PHASE_OCR,
            day=_오늘,
            bucket="bucket",
            cost_krw=10.0,
            created_at="2026-08-17T10:01:00",
        )
    assert spend_store.load_unresolved_day(conn, _오늘)


def test_월비용은_JSONL이_아니라_원장에서_합산한다(conn: sqlite3.Connection):
    spend_store.append_spend(
        conn,
        run_id="august",
        phase=SPEND_PHASE_IDENTIFY,
        day=dt.date(2026, 8, 31),
        bucket="bucket",
        cost_krw=120.5,
        created_at="2026-08-31T23:59:00",
    )
    spend_store.append_spend(
        conn,
        run_id="september",
        phase=SPEND_PHASE_PIPELINE,
        day=dt.date(2026, 9, 1),
        bucket="bucket",
        cost_krw=300.0,
        created_at="2026-09-01T00:01:00",
    )

    august = spend_store.load_month(conn, dt.date(2026, 8, 17))

    assert august.total_krw == 120.5
    assert august.ledger_since == "2026-08-31"


def test_월미확정은_단계가_아니라_요청_수로_센다(conn: sqlite3.Connection):
    for phase in (SPEND_PHASE_IDENTIFY, SPEND_PHASE_OCR):
        spend_store.begin_inflight(
            conn,
            run_id="same-run",
            phase=phase,
            day=_오늘,
            bucket="bucket",
            started_at="2026-08-17T10:00:00",
        )
    spend_store.begin_inflight(
        conn,
        run_id="other-run",
        phase=SPEND_PHASE_PIPELINE,
        day=_오늘,
        bucket="bucket",
        started_at="2026-08-17T10:00:00",
    )

    summary = spend_store.load_month(conn, _오늘)

    assert summary.total_krw == 0.0
    assert summary.unresolved_runs == 2
    assert summary.ledger_since == _오늘.isoformat()


def test_월미확정은_정상_active를_빼되_같은요청의_고아표식은_센다(
    conn: sqlite3.Connection,
):
    bucket = "bucket"
    stored_bucket = spend_store.bucket_id(bucket)
    phases = (SPEND_PHASE_IDENTIFY, SPEND_PHASE_OCR)
    for phase in phases:
        spend_store.begin_inflight(
            conn,
            run_id="mixed-run",
            phase=phase,
            day=_오늘,
            bucket=bucket,
            started_at="2026-08-17T10:00:00",
        )

    one_active = {
        (_오늘.isoformat(), stored_bucket, "mixed-run", SPEND_PHASE_IDENTIFY)
    }
    mixed = spend_store.load_month(conn, _오늘, known_active=one_active)
    all_active = spend_store.load_month(
        conn,
        _오늘,
        known_active={
            (_오늘.isoformat(), stored_bucket, "mixed-run", phase)
            for phase in phases
        },
    )

    assert mixed.unresolved_runs == 1
    assert all_active.unresolved_runs == 0


def test_월미확정은_다른_달을_섞지_않는다(conn: sqlite3.Connection):
    spend_store.begin_inflight(
        conn,
        run_id="last-month",
        phase=SPEND_PHASE_IDENTIFY,
        day=dt.date(2026, 7, 31),
        bucket="bucket",
        started_at="2026-07-31T23:59:00",
    )

    summary = spend_store.load_month(conn, _오늘)

    assert summary.unresolved_runs == 0
    assert summary.ledger_since == "2026-07-31"
