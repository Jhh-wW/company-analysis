"""유료 앞단 관측 상태가 재시작·중복·동시 처리에도 한 번만 마감되는가."""

from __future__ import annotations

import inspect
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.features.observability import lifecycle
from src.features.observability.constants import (
    CACHE_HIT_NONE,
    CORP_TYPE_UNKNOWN,
    END_STEP_COMPLETE,
)
from src.features.observability.records import RunRecord


_AT = "2026-08-17T10:00:00"
_EXPIRES = "2026-08-17T11:00:00"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    lifecycle.ensure_schema(connection)
    yield connection
    connection.close()


def _begin(
    conn: sqlite3.Connection,
    *,
    run_id: str = "run-1",
    at: str = _AT,
    job: str = "영업",
    cost: float = 10.0,
    elapsed: float = 1.5,
    model: str = "lookup-model",
    expires_at: str = _EXPIRES,
) -> bool:
    return lifecycle.begin_pending(
        conn,
        run_id=run_id,
        at=at,
        job=job,
        confirmed_cost_krw=cost,
        elapsed_sec=elapsed,
        model=model,
        expires_at=expires_at,
    )


def _record(
    *,
    run_id: str = "run-1",
    at: str = "2026-08-17T10:10:00",
    job: str = "영업",
    cost: float = 60.0,
    elapsed: float = 9.0,
    model: str = "lookup-model + pipeline-model",
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        at=at,
        corp_type=CORP_TYPE_UNKNOWN,
        job=job,
        end_step=END_STEP_COMPLETE,
        cache_hit=CACHE_HIT_NONE,
        fragments_collected=0,
        fragments_cited=0,
        sentences_made=0,
        sentences_passed=0,
        cells_filled=0,
        cells_missing=[],
        cells_suspect=[],
        grade="",
        human_check="",
        cost_krw=cost,
        elapsed_sec=elapsed,
        model=model,
    )


def test_대기표_API와스키마에_금지된_원문필드가_없다(conn: sqlite3.Connection):
    params = set(inspect.signature(lifecycle.begin_pending).parameters)
    assert params == {
        "conn",
        "run_id",
        "at",
        "job",
        "confirmed_cost_krw",
        "elapsed_sec",
        "model",
        "expires_at",
    }

    current_columns = {
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({lifecycle.TABLE_RUN_LIFECYCLE})"
        ).fetchall()
    }
    assert current_columns == {
        "run_id",
        "state",
        "at",
        "job",
        "confirmed_cost_krw",
        "elapsed_sec",
        "model",
        "expires_at",
        "final_record_json",
    }
    audit_columns = {
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({lifecycle.TABLE_RUN_AUDIT})"
        ).fetchall()
    }
    assert audit_columns == {
        "event_id",
        "run_id",
        "from_state",
        "to_state",
        "event_at",
        "record_sha256",
    }

    forbidden = (
        "company",
        "address",
        "posting",
        "image",
        "email",
        "share",
        "token",
        "prompt",
        "phone",
    )
    for column in current_columns | audit_columns:
        assert not any(piece in column.lower() for piece in forbidden), column


@pytest.mark.parametrize(
    "unsafe_job",
    [
        "person@example.com",
        "담당자(person@example.com)",
        "마케팅 010-1234-5678",
        "900101-1234567",
        "가" * (lifecycle.MAX_JOB_CHARS + 1),
    ],
)
def test_직무의_개인정보모양과_과도한길이를_저장전에_거부한다(
    conn: sqlite3.Connection, unsafe_job: str
):
    with pytest.raises(lifecycle.LifecycleError):
        _begin(conn, job=unsafe_job)

    count = conn.execute(
        f"SELECT COUNT(*) FROM {lifecycle.TABLE_RUN_LIFECYCLE}"
    ).fetchone()[0]
    assert count == 0


def test_safe_job은_pending과final이_공유할_정규화경계다():
    assert lifecycle.safe_job("  사업   기획  ") == "사업 기획"
    with pytest.raises(lifecycle.UnsafePendingDataError):
        lifecycle.safe_job("담당자 person@example.com")


def test_같은대기값은_멱등이고_다른값은_덮어쓰지않는다(
    conn: sqlite3.Connection,
):
    assert _begin(conn) is True
    assert _begin(conn) is False

    with pytest.raises(lifecycle.StateConflictError):
        _begin(conn, cost=11.0)

    assert lifecycle.get_entry(conn, "run-1").confirmed_cost_krw == 10.0
    assert [event.to_state for event in lifecycle.list_audit(conn, "run-1")] == [
        lifecycle.STATE_PENDING
    ]


def test_pending_running_final은_한행에서_한방향으로만_움직인다(
    conn: sqlite3.Connection,
):
    assert _begin(conn)
    assert lifecycle.mark_running(
        conn, "run-1", event_at="2026-08-17T10:01:00"
    )
    assert not lifecycle.consume_pending(
        conn, "run-1", event_at="2026-08-17T10:01:01"
    )

    final = _record()
    assert lifecycle.finalize_once(
        conn,
        final,
        event_at="2026-08-17T10:10:01",
        expected_state=lifecycle.STATE_RUNNING,
    )
    assert not lifecycle.finalize_once(conn, final)
    assert lifecycle.read_final(conn, "run-1") == final

    row_count = conn.execute(
        f"SELECT COUNT(*) FROM {lifecycle.TABLE_RUN_LIFECYCLE} WHERE run_id = ?",
        ("run-1",),
    ).fetchone()[0]
    assert row_count == 1
    assert [event.to_state for event in lifecycle.list_audit(conn, "run-1")] == [
        lifecycle.STATE_PENDING,
        lifecycle.STATE_RUNNING,
        lifecycle.STATE_FINAL,
    ]


def test_같은요청의_다른최종값과_final재개를_거부한다(
    conn: sqlite3.Connection,
):
    _begin(conn)
    final = _record()
    assert lifecycle.finalize_once(
        conn, final, expected_state=lifecycle.STATE_PENDING
    )

    with pytest.raises(lifecycle.StateConflictError):
        lifecycle.finalize_once(conn, _record(cost=61.0))
    with pytest.raises(lifecycle.StateConflictError):
        lifecycle.mark_running(
            conn, "run-1", event_at="2026-08-17T10:11:00"
        )
    with pytest.raises(lifecycle.StateConflictError):
        _begin(conn)


def test_expected_state가_확인만료와_실행완료의_경합을_가른다(
    conn: sqlite3.Connection,
):
    _begin(conn)
    with pytest.raises(lifecycle.StateConflictError):
        lifecycle.finalize_once(
            conn, _record(), expected_state=lifecycle.STATE_RUNNING
        )

    assert lifecycle.get_entry(conn, "run-1").state == lifecycle.STATE_PENDING
    assert lifecycle.finalize_once(
        conn, _record(), expected_state=lifecycle.STATE_PENDING
    )


def test_대기표없이_끝난_식별오류도_직접_final_한개로_마감한다(
    conn: sqlite3.Connection,
):
    final = _record(run_id="identify-error", cost=10.0, elapsed=2.0)

    assert lifecycle.finalize_once(conn, final)
    assert not lifecycle.finalize_once(conn, final)
    assert lifecycle.read_final(conn, "identify-error") == final
    assert [
        (event.from_state, event.to_state)
        for event in lifecycle.list_audit(conn, "identify-error")
    ] == [(None, lifecycle.STATE_FINAL)]


def test_list_final은_최종값만_at과run_id_순서로_돌려준다(
    conn: sqlite3.Connection,
):
    _begin(conn, run_id="pending")
    later = _record(run_id="z-run", at="2026-08-17T11:00:00")
    same_time_b = _record(run_id="b-run", at="2026-08-17T10:00:00")
    same_time_a = _record(run_id="a-run", at="2026-08-17T10:00:00")
    lifecycle.finalize_once(conn, later)
    lifecycle.finalize_once(conn, same_time_b)
    lifecycle.finalize_once(conn, same_time_a)

    assert lifecycle.list_final(conn) == [same_time_a, same_time_b, later]


def test_list_final은_깨진_JSON을_조용히_건너뛰지않는다(
    conn: sqlite3.Connection,
):
    lifecycle.finalize_once(conn, _record(run_id="broken"))
    conn.execute(
        f"UPDATE {lifecycle.TABLE_RUN_LIFECYCLE} "
        "SET final_record_json = ? WHERE run_id = ?",
        ("{깨진-json", "broken"),
    )

    with pytest.raises(lifecycle.LifecycleCorruptionError):
        lifecycle.list_final(conn)


def test_최종값은_앞단에서_확정한_비용과시간보다_작을수없다(
    conn: sqlite3.Connection,
):
    _begin(conn, cost=10.0, elapsed=2.0)

    with pytest.raises(lifecycle.StateConflictError):
        lifecycle.finalize_once(
            conn,
            _record(cost=9.0, elapsed=2.0),
            expected_state=lifecycle.STATE_PENDING,
        )
    with pytest.raises(lifecycle.StateConflictError):
        lifecycle.finalize_once(
            conn,
            _record(cost=10.0, elapsed=1.0),
            expected_state=lifecycle.STATE_PENDING,
        )


def test_만료된_pending만_조회하고_상태를_마음대로_바꾸지않는다(
    conn: sqlite3.Connection,
):
    _begin(
        conn,
        run_id="expired",
        at="2026-08-17T09:00:00",
        expires_at="2026-08-17T09:30:00",
    )
    _begin(conn, run_id="alive")
    _begin(
        conn,
        run_id="running",
        at="2026-08-17T08:00:00",
        expires_at="2026-08-17T08:30:00",
    )
    lifecycle.mark_running(
        conn, "running", event_at="2026-08-17T08:01:00"
    )

    expired = lifecycle.list_expired_pending(
        conn, now="2026-08-17T10:00:00"
    )

    assert [entry.run_id for entry in expired] == ["expired"]
    assert lifecycle.get_entry(conn, "expired").state == lifecycle.STATE_PENDING


def test_재시작뒤_pending과running은_복원되고_final은_후보에서빠진다(
    tmp_path: Path,
):
    path = tmp_path / "observability.db"
    first = sqlite3.connect(path)
    lifecycle.ensure_schema(first)
    _begin(first, run_id="pending")
    _begin(first, run_id="running")
    lifecycle.mark_running(
        first, "running", event_at="2026-08-17T10:01:00"
    )
    lifecycle.finalize_once(first, _record(run_id="already-final"))
    first.close()

    restarted = sqlite3.connect(path)
    lifecycle.ensure_schema(restarted)
    candidates = lifecycle.list_restart_candidates(restarted)
    restarted.close()

    assert [(entry.run_id, entry.state) for entry in candidates] == [
        ("pending", lifecycle.STATE_PENDING),
        ("running", lifecycle.STATE_RUNNING),
    ]


def test_두연결이_동시에소비해도_한쪽만_running을_얻는다(tmp_path: Path):
    path = tmp_path / "consume.db"
    setup = sqlite3.connect(path)
    lifecycle.ensure_schema(setup)
    _begin(setup, run_id="race")
    setup.close()

    def consume() -> bool:
        worker = sqlite3.connect(path, timeout=5)
        try:
            return lifecycle.mark_running(
                worker, "race", event_at="2026-08-17T10:01:00"
            )
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: consume(), range(2)))

    check = sqlite3.connect(path)
    try:
        assert sorted(results) == [False, True]
        assert lifecycle.get_entry(check, "race").state == lifecycle.STATE_RUNNING
        assert [
            event.to_state for event in lifecycle.list_audit(check, "race")
        ].count(lifecycle.STATE_RUNNING) == 1
    finally:
        check.close()


def test_두연결이_같은최종값을_동시에써도_final은_한개다(tmp_path: Path):
    path = tmp_path / "finalize.db"
    setup = sqlite3.connect(path)
    lifecycle.ensure_schema(setup)
    _begin(setup, run_id="race-final")
    setup.close()
    final = _record(run_id="race-final")

    def finish() -> bool:
        worker = sqlite3.connect(path, timeout=5)
        try:
            return lifecycle.finalize_once(
                worker,
                final,
                expected_state=lifecycle.STATE_PENDING,
            )
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: finish(), range(2)))

    check = sqlite3.connect(path)
    try:
        assert sorted(results) == [False, True]
        assert check.execute(
            f"SELECT COUNT(*) FROM {lifecycle.TABLE_RUN_LIFECYCLE} "
            "WHERE run_id = ? AND state = ?",
            ("race-final", lifecycle.STATE_FINAL),
        ).fetchone()[0] == 1
        assert [
            event.to_state for event in lifecycle.list_audit(check, "race-final")
        ].count(lifecycle.STATE_FINAL) == 1
    finally:
        check.close()


def test_감사이력은_덧붙일수만있고_수정삭제할수없다(
    conn: sqlite3.Connection,
):
    _begin(conn)
    event_id = lifecycle.list_audit(conn, "run-1")[0].event_id

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"UPDATE {lifecycle.TABLE_RUN_AUDIT} SET to_state = ? WHERE event_id = ?",
            (lifecycle.STATE_FINAL, event_id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"DELETE FROM {lifecycle.TABLE_RUN_AUDIT} WHERE event_id = ?",
            (event_id,),
        )

    assert len(lifecycle.list_audit(conn, "run-1")) == 1
