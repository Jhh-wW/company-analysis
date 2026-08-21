"""LINK 접속·생성 이력 저장소의 보존 및 비밀 비노출 계약."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from src.features.sharelink import store as share_store


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    share_store.ensure_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def _insert_link(
    conn: sqlite3.Connection,
    *,
    key: str = "link-secret-for-history-tests",
    company: str = "카카오",
    job: str = "백엔드 개발",
    report_id: str = "",
) -> None:
    assert share_store.insert_new(
        conn,
        key=key,
        company=company,
        job=job,
        report_id=report_id,
        note="지원 링크",
        now_iso="2026-08-21T09:00:00+09:00",
    )


def test_hashed_schema_migration_adds_history_without_losing_initial_report() -> None:
    """기존 해시 스키마를 확장해도 연결된 시작 보고서와 요약값은 그대로다."""

    raw_key = "legacy-hashed-link-secret"
    key_hash = share_store.key_hash_of(raw_key)
    migrated = sqlite3.connect(":memory:")
    try:
        migrated.execute(
            """
            CREATE TABLE share_links (
                key_hash        TEXT PRIMARY KEY,
                company         TEXT NOT NULL,
                job             TEXT NOT NULL,
                report_id       TEXT NOT NULL DEFAULT '',
                note            TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                opened_count    INTEGER NOT NULL DEFAULT 0,
                first_opened_at TEXT NOT NULL DEFAULT '',
                last_opened_at  TEXT NOT NULL DEFAULT ''
            )
            """
        )
        migrated.execute(
            """
            INSERT INTO share_links (
                key_hash, company, job, report_id, note, created_at,
                opened_count, first_opened_at, last_opened_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_hash,
                "기존 회사",
                "기존 직무",
                "initial-report-must-survive",
                "기존 메모",
                "2026-08-01T10:00:00+09:00",
                2,
                "2026-08-02T10:00:00+09:00",
                "2026-08-03T10:00:00+09:00",
            ),
        )

        share_store.ensure_schema(migrated)
        share_store.ensure_schema(migrated)

        link = share_store.load_by_hash(migrated, key_hash)
        assert link is not None
        assert link.report_id == "initial-report-must-survive"
        assert link.opened_count == 2
        assert link.first_opened_at == "2026-08-02T10:00:00+09:00"
        assert link.last_opened_at == "2026-08-03T10:00:00+09:00"
        assert link.revoked_at == ""

        link_columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(share_links)")
        }
        tables = {
            row[0]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "revoked_at" in link_columns
        assert share_store.TABLE_OPEN_EVENTS in tables
        assert share_store.TABLE_RUN_HISTORY in tables
        assert migrated.execute(
            f"SELECT COUNT(*) FROM {share_store.TABLE_OPEN_EVENTS}"
        ).fetchone()[0] == 0
        assert migrated.execute(
            f"SELECT COUNT(*) FROM {share_store.TABLE_RUN_HISTORY}"
        ).fetchone()[0] == 0
        assert raw_key not in "\n".join(migrated.iterdump())
    finally:
        migrated.close()


def test_raw_key_never_enters_database_or_new_history(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "RAW-Link-Secret-Never-Persist"
    key_hash = share_store.key_hash_of(raw_key)
    _insert_link(conn, key=raw_key)

    assert share_store.mark_opened(
        conn, raw_key, "2026-08-21T09:01:00+09:00"
    )
    assert share_store.start_run(
        conn,
        key=raw_key,
        run_id="run-secret-audit",
        started_at="2026-08-21T09:02:00+09:00",
        input_company="네이버",
        confirmed_company="네이버(주)",
        company_id="corp-naver",
    )

    event = share_store.list_open_events_by_hash(conn, key_hash)[0]
    run = share_store.load_run(conn, "run-secret-audit")
    assert event.link_key_hash == key_hash
    assert run is not None and run.link_key_hash == key_hash

    dump = "\n".join(conn.iterdump())
    assert raw_key not in dump
    assert raw_key.lower() not in dump.lower()
    assert key_hash in dump
    for table in (
        share_store.TABLE_SHARE_LINKS,
        share_store.TABLE_OPEN_EVENTS,
        share_store.TABLE_RUN_HISTORY,
    ):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert "key" not in columns


def test_each_known_open_is_preserved_with_consistent_summary(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "open-events-link"
    key_hash = share_store.key_hash_of(raw_key)
    _insert_link(conn, key=raw_key)
    opened_at = (
        "2026-08-21T10:00:00+09:00",
        "2026-08-21T10:02:00+09:00",
        "2026-08-21T10:05:00+09:00",
    )

    for timestamp in opened_at:
        assert share_store.mark_opened(conn, raw_key, timestamp)
    assert not share_store.mark_opened(
        conn, "unknown-link", "2026-08-21T10:10:00+09:00"
    )

    events = share_store.list_open_events_by_hash(conn, key_hash)
    assert [event.opened_at for event in events] == list(opened_at)
    assert [event.id for event in events] == sorted(event.id for event in events)
    assert all(event.link_key_hash == key_hash for event in events)

    link = share_store.load(conn, raw_key)
    assert link is not None
    assert link.opened_count == len(opened_at)
    assert link.first_opened_at == opened_at[0]
    assert link.last_opened_at == opened_at[-1]


def test_soft_revoke_preserves_link_events_and_runs_but_blocks_new_run(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "soft-revoke-link"
    key_hash = share_store.key_hash_of(raw_key)
    _insert_link(conn, key=raw_key, report_id="initial-report")
    assert share_store.mark_opened(
        conn, raw_key, "2026-08-21T11:00:00+09:00"
    )
    assert share_store.start_run(
        conn,
        key=raw_key,
        run_id="run-before-revoke",
        started_at="2026-08-21T11:01:00+09:00",
        input_company="네이버",
        confirmed_company="네이버(주)",
        company_id="corp-naver",
    )
    assert share_store.finish_run(
        conn,
        run_id="run-before-revoke",
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at="2026-08-21T11:02:00+09:00",
        report_id="generated-before-revoke",
    )

    assert share_store.delete(
        conn,
        raw_key,
        revoked_at="2026-08-21T11:03:00+09:00",
    )
    assert not share_store.delete(
        conn,
        raw_key,
        revoked_at="2026-08-21T11:04:00+09:00",
    )

    link = share_store.load_by_hash(conn, key_hash)
    assert link is not None
    assert link.is_revoked
    assert link.revoked_at == "2026-08-21T11:03:00+09:00"
    assert link.report_id == "initial-report"
    assert len(share_store.list_open_events_by_hash(conn, key_hash)) == 1
    runs = share_store.list_runs_by_hash(conn, key_hash)
    assert [run.run_id for run in runs] == ["run-before-revoke"]
    assert runs[0].report_id == "generated-before-revoke"

    assert not share_store.start_run(
        conn,
        key=raw_key,
        run_id="run-after-revoke",
        started_at="2026-08-21T11:05:00+09:00",
        input_company="다른 회사",
        confirmed_company="다른 회사(주)",
        company_id="corp-other",
    )
    assert share_store.load_run(conn, "run-after-revoke") is None


def test_one_link_keeps_two_company_runs_without_overwriting_initial_report(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "multi-company-link"
    key_hash = share_store.key_hash_of(raw_key)
    _insert_link(conn, key=raw_key, report_id="initial-kakao-report")

    runs = (
        (
            "run-naver",
            "2026-08-21T12:00:00+09:00",
            "네이버",
            "네이버(주)",
            "corp-naver",
            "generated-naver-report",
        ),
        (
            "run-yg",
            "2026-08-21T12:01:00+09:00",
            "YG",
            "와이지엔터테인먼트",
            "corp-yg",
            "generated-yg-report",
        ),
    )
    for run_id, started_at, input_name, company, company_id, report_id in runs:
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=run_id,
            started_at=started_at,
            input_company=input_name,
            confirmed_company=company,
            company_id=company_id,
        )
        assert share_store.finish_run(
            conn,
            run_id=run_id,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at=started_at,
            report_id=report_id,
        )

    stored = {run.run_id: run for run in share_store.list_runs_by_hash(conn, key_hash)}
    assert set(stored) == {"run-naver", "run-yg"}
    assert stored["run-naver"].confirmed_company == "네이버(주)"
    assert stored["run-naver"].company_id == "corp-naver"
    assert stored["run-yg"].confirmed_company == "와이지엔터테인먼트"
    assert stored["run-yg"].company_id == "corp-yg"

    link = share_store.load(conn, raw_key)
    assert link is not None
    assert link.report_id == "initial-kakao-report"
    assert share_store.is_linked_report(conn, "initial-kakao-report")
    assert share_store.is_linked_report(conn, "generated-naver-report")
    assert share_store.is_linked_report(conn, "generated-yg-report")
    assert not share_store.is_linked_report(conn, "unrelated-report")


def test_awaiting_release_becomes_completed_only_with_bound_hashes(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "release-completion-link"
    _insert_link(conn, key=raw_key)
    assert share_store.start_run(
        conn,
        key=raw_key,
        run_id="run-awaiting-release",
        started_at="2026-08-21T13:00:00+09:00",
        input_company="네이버",
        confirmed_company="네이버(주)",
        company_id="corp-naver",
    )
    assert share_store.finish_run(
        conn,
        run_id="run-awaiting-release",
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at="2026-08-21T13:01:00+09:00",
        report_id="release-report-id",
        internal_ai_cost_krw=321.5,
        customer_charge_krw=0,
    )

    awaiting = share_store.load_run(conn, "run-awaiting-release")
    assert awaiting is not None
    assert share_store.load_run_by_report_id(conn, "release-report-id") == awaiting
    assert awaiting.status == share_store.RUN_STATUS_AWAITING_RELEASE
    assert awaiting.pdf_sha256 == ""
    assert awaiting.release_sha256 == ""

    pdf_sha256 = "a" * 64
    release_sha256 = "b" * 64
    assert share_store.mark_released(
        conn,
        report_id="release-report-id",
        pdf_sha256=pdf_sha256,
        release_sha256=release_sha256,
        released_at="2026-08-21T13:02:00+09:00",
        customer_charge_krw=990,
    )

    completed = share_store.load_run(conn, "run-awaiting-release")
    assert completed is not None
    assert completed.status == share_store.RUN_STATUS_COMPLETED
    assert completed.report_id == "release-report-id"
    assert completed.pdf_sha256 == pdf_sha256
    assert completed.release_sha256 == release_sha256
    assert completed.internal_ai_cost_krw == pytest.approx(321.5)
    assert completed.customer_charge_krw == pytest.approx(990)
    assert completed.finished_at
    assert share_store.mark_released(
        conn,
        report_id="release-report-id",
        pdf_sha256=pdf_sha256,
        release_sha256=release_sha256,
        released_at="2026-08-21T13:02:30+09:00",
        customer_charge_krw=990,
    )
    assert not share_store.mark_released(
        conn,
        report_id="release-report-id",
        pdf_sha256="c" * 64,
        release_sha256="d" * 64,
        released_at="2026-08-21T13:03:00+09:00",
    )


def test_stopped_run_keeps_stage_reason_completion_time_and_costs(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "stopped-run-link"
    _insert_link(conn, key=raw_key)
    assert share_store.start_run(
        conn,
        key=raw_key,
        run_id="run-stopped",
        started_at="2026-08-21T14:00:00+09:00",
        input_company="검색 입력",
        confirmed_company="확정 회사",
        company_id="corp-stopped",
    )
    assert share_store.finish_run(
        conn,
        run_id="run-stopped",
        status=share_store.RUN_STATUS_STOPPED,
        finished_at="2026-08-21T14:02:00+09:00",
        stop_step="pipeline",
        stop_reason="검증 자료 부족",
        internal_ai_cost_krw=87.25,
        customer_charge_krw=100,
    )

    stopped = share_store.load_run(conn, "run-stopped")
    assert stopped is not None
    assert stopped.status == share_store.RUN_STATUS_STOPPED
    assert stopped.stop_step == "pipeline"
    assert stopped.stop_reason == "검증 자료 부족"
    assert stopped.finished_at == "2026-08-21T14:02:00+09:00"
    assert stopped.internal_ai_cost_krw == pytest.approx(87.25)
    assert stopped.customer_charge_krw == pytest.approx(100)
    assert stopped.report_id == ""
    assert stopped.pdf_sha256 == ""
    assert stopped.release_sha256 == ""


def test_invalid_hash_admin_lookups_fail_closed_without_mutation(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "invalid-hash-guard-link"
    _insert_link(conn, key=raw_key, report_id="initial-report")
    invalid_values = (
        "",
        "not-a-hash",
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "' OR 1=1 --",
    )

    for invalid in invalid_values:
        assert not share_store.is_key_hash(invalid)
        assert share_store.load_by_hash(conn, invalid) is None
        assert share_store.list_open_events_by_hash(conn, invalid) == []
        assert share_store.list_runs_by_hash(conn, invalid) == []
        assert not share_store.set_report_by_hash(conn, invalid, "overwritten")
        assert not share_store.delete_by_hash(
            conn,
            invalid,
            revoked_at="2026-08-21T15:00:00+09:00",
        )

    unknown_valid_hash = "f" * 64
    assert share_store.is_key_hash(unknown_valid_hash)
    assert share_store.load_by_hash(conn, unknown_valid_hash) is None
    assert share_store.list_open_events_by_hash(conn, unknown_valid_hash) == []
    assert share_store.list_runs_by_hash(conn, unknown_valid_hash) == []
    assert not share_store.set_report_by_hash(
        conn, unknown_valid_hash, "overwritten"
    )
    assert not share_store.delete_by_hash(
        conn,
        unknown_valid_hash,
        revoked_at="2026-08-21T15:01:00+09:00",
    )

    untouched = share_store.load(conn, raw_key)
    assert untouched is not None
    assert untouched.report_id == "initial-report"
    assert not untouched.is_revoked


def test_run_history_rows_cannot_be_deleted_accidentally(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "preserved-run-history-link"
    _insert_link(conn, key=raw_key)
    assert share_store.start_run(
        conn,
        key=raw_key,
        run_id="run-must-survive",
        started_at="2026-08-21T16:00:00+09:00",
        input_company="네이버",
        confirmed_company="네이버(주)",
        company_id="corp-naver",
    )

    with pytest.raises(sqlite3.IntegrityError, match="run history is preserved"):
        conn.execute(
            f"DELETE FROM {share_store.TABLE_RUN_HISTORY} WHERE run_id = ?",
            ("run-must-survive",),
        )

    preserved = share_store.load_run(conn, "run-must-survive")
    assert preserved is not None
    assert preserved.status == share_store.RUN_STATUS_RUNNING


def test_restart_interrupts_only_running_rows_and_preserves_known_costs(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "restart-recovery-link"
    _insert_link(conn, key=raw_key)
    for run_id in ("running-existing-cost", "running-ledger-cost", "awaiting"):
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=run_id,
            started_at="2026-08-21T17:00:00+09:00",
            input_company=run_id,
            confirmed_company=f"{run_id}(주)",
            company_id=f"corp-{run_id}",
        )
    conn.execute(
        f"""
        UPDATE {share_store.TABLE_RUN_HISTORY}
           SET internal_ai_cost_krw = 70
         WHERE run_id = 'running-existing-cost'
        """
    )
    assert share_store.finish_run(
        conn,
        run_id="awaiting",
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at="2026-08-21T17:01:00+09:00",
        report_id="awaiting",
        internal_ai_cost_krw=9,
    )

    changed = share_store.interrupt_running_runs(
        conn,
        interrupted_at="2026-08-21T17:02:00+09:00",
        stop_step="05_생성",
        stop_reason="server_restart",
        known_internal_cost_krw_by_run={
            "running-existing-cost": 42,
            "running-ledger-cost": 55,
        },
    )

    assert changed == 2
    existing = share_store.load_run(conn, "running-existing-cost")
    from_ledger = share_store.load_run(conn, "running-ledger-cost")
    awaiting = share_store.load_run(conn, "awaiting")
    assert existing is not None and from_ledger is not None and awaiting is not None
    for recovered in (existing, from_ledger):
        assert recovered.status == share_store.RUN_STATUS_INTERRUPTED
        assert recovered.stop_step == "05_생성"
        assert recovered.stop_reason == "server_restart"
        assert recovered.finished_at == "2026-08-21T17:02:00+09:00"
    assert existing.internal_ai_cost_krw == pytest.approx(70)
    assert from_ledger.internal_ai_cost_krw == pytest.approx(55)
    assert awaiting.status == share_store.RUN_STATUS_AWAITING_RELEASE
    assert awaiting.internal_ai_cost_krw == pytest.approx(9)
    assert share_store.interrupt_running_runs(
        conn,
        interrupted_at="2026-08-21T17:03:00+09:00",
        stop_step="05_생성",
        stop_reason="server_restart",
        known_internal_cost_krw_by_run={},
    ) == 0


def test_release_and_gate_stop_never_transition_a_different_matching_run_id(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "cross-run-isolation-link"
    _insert_link(conn, key=raw_key)

    # 공격적인 충돌 모양: 한 행의 run_id가 다른 행의 report_id와 같다.
    for run_id in (
        "release-report-id",
        "actual-release-run",
        "stopped-report-id",
        "actual-stopped-run",
    ):
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=run_id,
            started_at="2026-08-21T18:00:00+09:00",
            input_company=run_id,
            confirmed_company=f"{run_id}(주)",
            company_id=f"corp-{run_id}",
        )
    assert share_store.finish_run(
        conn,
        run_id="actual-release-run",
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at="2026-08-21T18:01:00+09:00",
        report_id="release-report-id",
    )
    assert share_store.finish_run(
        conn,
        run_id="actual-stopped-run",
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at="2026-08-21T18:01:00+09:00",
        report_id="stopped-report-id",
    )

    assert share_store.mark_released(
        conn,
        report_id="release-report-id",
        pdf_sha256="a" * 64,
        release_sha256="b" * 64,
        released_at="2026-08-21T18:02:00+09:00",
        customer_charge_krw=990,
    )
    assert share_store.mark_release_stopped(
        conn,
        report_id="stopped-report-id",
        stopped_at="2026-08-21T18:02:00+09:00",
        stop_step="automatic_release_gate",
        stop_reason="automatic_release_gate_stopped",
    )

    assert share_store.load_run(conn, "release-report-id").status == (
        share_store.RUN_STATUS_RUNNING
    )
    assert share_store.load_run(conn, "stopped-report-id").status == (
        share_store.RUN_STATUS_RUNNING
    )
    assert share_store.load_run(conn, "actual-release-run").status == (
        share_store.RUN_STATUS_COMPLETED
    )
    assert share_store.load_run(conn, "actual-stopped-run").status == (
        share_store.RUN_STATUS_STOPPED
    )


def test_duplicate_nonempty_report_binding_is_rejected_but_blank_is_allowed(
    conn: sqlite3.Connection,
) -> None:
    raw_key = "unique-report-binding-link"
    _insert_link(conn, key=raw_key)
    for run_id in ("first-binding", "second-binding"):
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=run_id,
            started_at="2026-08-21T19:00:00+09:00",
            input_company=run_id,
            confirmed_company=f"{run_id}(주)",
            company_id=f"corp-{run_id}",
        )
    assert share_store.finish_run(
        conn,
        run_id="first-binding",
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at="2026-08-21T19:01:00+09:00",
        report_id="one-report-only",
    )
    with pytest.raises(sqlite3.IntegrityError):
        share_store.finish_run(
            conn,
            run_id="second-binding",
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at="2026-08-21T19:02:00+09:00",
            report_id="one-report-only",
        )
    assert share_store.load_run(conn, "second-binding").report_id == ""


def test_duplicate_report_migration_fails_without_deleting_existing_rows() -> None:
    migrated = sqlite3.connect(":memory:")
    try:
        migrated.execute(share_store.CREATE_SQL)
        migrated.execute(share_store.CREATE_RUN_HISTORY_SQL)
        _insert_link(migrated, key="duplicate-migration-link")
        for run_id in ("legacy-duplicate-a", "legacy-duplicate-b"):
            migrated.execute(
                f"""
                INSERT INTO {share_store.TABLE_RUN_HISTORY} (
                    run_id, link_key_hash, started_at, input_company,
                    confirmed_company, company_id, status, report_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    share_store.key_hash_of("duplicate-migration-link"),
                    "2026-08-21T20:00:00+09:00",
                    run_id,
                    run_id,
                    f"corp-{run_id}",
                    share_store.RUN_STATUS_AWAITING_RELEASE,
                    "legacy-duplicate-report",
                ),
            )

        with pytest.raises(RuntimeError, match="중복 report_id"):
            share_store.ensure_schema(migrated)

        rows = migrated.execute(
            f"SELECT run_id, report_id FROM {share_store.TABLE_RUN_HISTORY} "
            "ORDER BY run_id"
        ).fetchall()
        assert rows == [
            ("legacy-duplicate-a", "legacy-duplicate-report"),
            ("legacy-duplicate-b", "legacy-duplicate-report"),
        ]
    finally:
        migrated.close()


@pytest.mark.parametrize(
    "bad_index_sql",
    (
        f"CREATE INDEX {share_store.INDEX_RUN_REPORT_ID} "
        f"ON {share_store.TABLE_RUN_HISTORY}(run_id)",
        f"CREATE UNIQUE INDEX {share_store.INDEX_RUN_REPORT_ID} "
        f"ON {share_store.TABLE_RUN_HISTORY}(report_id) WHERE report_id = ''",
    ),
    ids=("non_unique_wrong_column", "wrong_partial_predicate"),
)
def test_same_named_invalid_report_index_fails_closed(bad_index_sql: str) -> None:
    migrated = sqlite3.connect(":memory:")
    try:
        migrated.execute(share_store.CREATE_SQL)
        migrated.execute(share_store.CREATE_RUN_HISTORY_SQL)
        migrated.execute(bad_index_sql)

        with pytest.raises(RuntimeError, match="report_id 1:1 index"):
            share_store.ensure_schema(migrated)

        # 실패한 검증이 이름을 선점한 기존 index를 바꾸거나 지우지 않는다.
        stored = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            (share_store.INDEX_RUN_REPORT_ID,),
        ).fetchone()
        assert stored is not None
        assert "CREATE" in stored[0]
    finally:
        migrated.close()
