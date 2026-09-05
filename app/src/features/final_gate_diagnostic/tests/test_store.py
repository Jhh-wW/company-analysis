from __future__ import annotations

import sqlite3

import pytest

from src.features.final_gate_diagnostic import store


RUN_ID = "0123456789abcdef0123456789abcdef"
RECORDED_AT = "2026-08-22T20:30:00+09:00"
CORP_CODE = "00126380"
CONFIRMED_COMPANY = "가나다전자"
END_STEP = "06_최종게이트"


def _record_once(
    conn: sqlite3.Connection, *, reason_code: str = "publish_blocked"
) -> bool:
    return store.record_once(
        conn,
        run_id=RUN_ID,
        corp_code=CORP_CODE,
        confirmed_company=CONFIRMED_COMPANY,
        end_step=END_STEP,
        reason_code=reason_code,
        recorded_at=RECORDED_AT,
    )


def test_최종게이트_진단은_재연결뒤에도_같은값으로_복원된다(tmp_path) -> None:
    db_path = tmp_path / "storage.db"
    with sqlite3.connect(db_path) as conn:
        assert _record_once(conn)

    with sqlite3.connect(db_path) as conn:
        restored = store.read_for_run(conn, RUN_ID)

    assert restored == store.PersistedFinalGateDiagnostic(
        run_id=RUN_ID,
        schema_version=2,
        corp_code=CORP_CODE,
        confirmed_company=CONFIRMED_COMPANY,
        end_step=END_STEP,
        reason_code="publish_blocked",
        recorded_at=RECORDED_AT,
    )


def test_품질하한_닫힌코드도_원문없이_저장되고_복원된다(tmp_path) -> None:
    """task 022 — publish_blocked_quality_floor도 다른 닫힌 코드와 같이
    원문 없이 SQLite CHECK 제약을 통과해 저장·복원된다."""
    db_path = tmp_path / "storage.db"
    with sqlite3.connect(db_path) as conn:
        assert _record_once(conn, reason_code="publish_blocked_quality_floor")

    with sqlite3.connect(db_path) as conn:
        restored = store.read_for_run(conn, RUN_ID)

    assert restored == store.PersistedFinalGateDiagnostic(
        run_id=RUN_ID,
        schema_version=2,
        corp_code=CORP_CODE,
        confirmed_company=CONFIRMED_COMPANY,
        end_step=END_STEP,
        reason_code="publish_blocked_quality_floor",
        recorded_at=RECORDED_AT,
    )


def test_내부근거계약_닫힌코드도_원문없이_저장되고_복원된다(tmp_path) -> None:
    db_path = tmp_path / "storage.db"
    with sqlite3.connect(db_path) as conn:
        assert _record_once(conn, reason_code="internal_evidence_contract")

    with sqlite3.connect(db_path) as conn:
        restored = store.read_for_run(conn, RUN_ID)

    assert restored == store.PersistedFinalGateDiagnostic(
        run_id=RUN_ID,
        schema_version=2,
        corp_code=CORP_CODE,
        confirmed_company=CONFIRMED_COMPANY,
        end_step=END_STEP,
        reason_code="internal_evidence_contract",
        recorded_at=RECORDED_AT,
    )


def test_읽기는_표를_새로_만들지않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        assert store.read_for_run(conn, RUN_ID) is None
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (store.TABLE_FINAL_GATE_DIAGNOSTICS,),
        ).fetchone()


def test_임의_사유와_시간대없는_시각은_저장하지않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(store.FinalGateDiagnosticStoreError):
            store.record_once(
                conn,
                run_id=RUN_ID,
                corp_code=CORP_CODE,
                confirmed_company=CONFIRMED_COMPANY,
                end_step=END_STEP,
                reason_code="원문이 섞일 수 있는 임의 사유",
                recorded_at=RECORDED_AT,
            )
        with pytest.raises(store.FinalGateDiagnosticStoreError):
            store.record_once(
                conn,
                run_id=RUN_ID,
                corp_code=CORP_CODE,
                confirmed_company=CONFIRMED_COMPANY,
                end_step=END_STEP,
                reason_code="other_gate",
                recorded_at="2026-08-22T20:30:00",
            )


def test_실행번호에_회사명이나_이메일을_넣을수없다() -> None:
    with sqlite3.connect(":memory:") as conn:
        for unsafe_run_id in ("삼성전자", "person@example.com", "run id"):
            with pytest.raises(store.FinalGateDiagnosticStoreError):
                store.record_once(
                    conn,
                    run_id=unsafe_run_id,
                    corp_code=CORP_CODE,
                    confirmed_company=CONFIRMED_COMPANY,
                    end_step=END_STEP,
                    reason_code="other_gate",
                    recorded_at=RECORDED_AT,
                )


@pytest.mark.parametrize(
    ("corp_code", "confirmed_company", "end_step"),
    (
        ("corp-name", CONFIRMED_COMPANY, END_STEP),
        (CORP_CODE, "", END_STEP),
        (CORP_CODE, CONFIRMED_COMPANY, ""),
    ),
)
def test_현재진단은_법인코드_확정회사_종료단계를_모두요구한다(
    corp_code: str, confirmed_company: str, end_step: str
) -> None:
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(store.FinalGateDiagnosticStoreError):
            store.record_once(
                conn,
                run_id=RUN_ID,
                corp_code=corp_code,
                confirmed_company=confirmed_company,
                end_step=end_step,
                reason_code="other_gate",
                recorded_at=RECORDED_AT,
            )


def test_같은값은_멱등이고_다른사유는_덮어쓰지않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        assert _record_once(conn, reason_code="comparison_blocked")
        conn.row_factory = sqlite3.Row
        assert not _record_once(conn, reason_code="comparison_blocked")
        with pytest.raises(store.FinalGateDiagnosticStoreError):
            _record_once(conn, reason_code="publish_blocked")


def test_옛_4열표는_쓰기없이_읽어도_회사칸이_빈_v1행으로_복원된다() -> None:
    with sqlite3.connect(":memory:") as conn:
        conn.execute(
            f"""
            CREATE TABLE {store.TABLE_FINAL_GATE_DIAGNOSTICS} (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                reason_code TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"INSERT INTO {store.TABLE_FINAL_GATE_DIAGNOSTICS} VALUES (?, 1, ?, ?)",
            (RUN_ID, "publish_blocked", RECORDED_AT),
        )

        restored = store.read_for_run(conn, RUN_ID)

    assert restored == store.PersistedFinalGateDiagnostic(
        run_id=RUN_ID,
        schema_version=1,
        corp_code="",
        confirmed_company="",
        end_step="",
        reason_code="publish_blocked",
        recorded_at=RECORDED_AT,
    )


def test_기존사유표를_보존하며_핵심역할결손_닫힌코드를_추가한다() -> None:
    old_allowed = "'comparison_blocked', 'publish_blocked', 'other_gate'"
    with sqlite3.connect(":memory:") as conn:
        conn.execute(
            f"""
            CREATE TABLE {store.TABLE_FINAL_GATE_DIAGNOSTICS} (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                reason_code TEXT NOT NULL CHECK (reason_code IN ({old_allowed})),
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"INSERT INTO {store.TABLE_FINAL_GATE_DIAGNOSTICS} VALUES (?, 1, ?, ?)",
            ("old-run", "publish_blocked", RECORDED_AT),
        )

        assert _record_once(conn, reason_code="publish_missing_revenue")
        rows = conn.execute(
            f"SELECT run_id, schema_version, corp_code, confirmed_company, "
            f"end_step, reason_code FROM {store.TABLE_FINAL_GATE_DIAGNOSTICS} "
            "ORDER BY run_id"
        ).fetchall()
        columns = {
            str(row[1])
            for row in conn.execute(
                f"PRAGMA table_info({store.TABLE_FINAL_GATE_DIAGNOSTICS})"
            )
        }

    assert rows == [
        (
            "0123456789abcdef0123456789abcdef",
            2,
            CORP_CODE,
            CONFIRMED_COMPANY,
            END_STEP,
            "publish_missing_revenue",
        ),
        ("old-run", 1, "", "", "", "publish_blocked"),
    ]
    assert columns == {
        "run_id",
        "schema_version",
        "corp_code",
        "confirmed_company",
        "end_step",
        "reason_code",
        "recorded_at",
    }


def test_직전사유표를_보존하며_내부근거계약_닫힌코드를_추가한다() -> None:
    previous_allowed = ", ".join(
        f"'{reason}'"
        for reason in (
            "comparison_blocked",
            "official_evidence_insufficient",
            "official_evidence_transient",
            "other_gate",
            "publish_blocked",
            "publish_blocked_quality_floor",
            "publish_missing_identity",
            "publish_missing_identity_revenue",
            "publish_missing_revenue",
        )
    )
    with sqlite3.connect(":memory:") as conn:
        conn.execute(
            f"""
            CREATE TABLE {store.TABLE_FINAL_GATE_DIAGNOSTICS} (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                reason_code TEXT NOT NULL CHECK (reason_code IN ({previous_allowed})),
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"INSERT INTO {store.TABLE_FINAL_GATE_DIAGNOSTICS} VALUES (?, 1, ?, ?)",
            ("old-run", "official_evidence_insufficient", RECORDED_AT),
        )

        assert _record_once(conn, reason_code="internal_evidence_contract")
        rows = conn.execute(
            f"SELECT run_id, reason_code FROM {store.TABLE_FINAL_GATE_DIAGNOSTICS} "
            "ORDER BY run_id"
        ).fetchall()

    assert rows == [
        ("0123456789abcdef0123456789abcdef", "internal_evidence_contract"),
        ("old-run", "official_evidence_insufficient"),
    ]
