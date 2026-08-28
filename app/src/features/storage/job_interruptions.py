"""종료 제한시간을 넘긴 작업의 재시작 안내용 최소 영속 상태."""

from __future__ import annotations

import sqlite3


TABLE_NAME = "job_interruptions"
CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    job_id         TEXT PRIMARY KEY,
    interrupted_at TEXT NOT NULL,
    reason         TEXT NOT NULL
)
"""


def mark(
    conn: sqlite3.Connection, *, job_id: str, interrupted_at: str, reason: str
) -> None:
    conn.execute(
        f"""
        INSERT INTO {TABLE_NAME} (job_id, interrupted_at, reason)
        VALUES (?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            interrupted_at=excluded.interrupted_at,
            reason=excluded.reason
        """,
        (job_id, interrupted_at, reason),
    )


def persist(*, job_id: str, interrupted_at: str, reason: str) -> None:
    """종료 제한시간 안에 이 한 표만 만들고 중단 표식을 커밋한다.

    일반 ``storage_db.connect()``는 신선한 DB에서 제품 전체 schema를 준비한다.
    process 종료 직전 그 작업을 하면 짧은 취소 유예를 schema bootstrap이 모두
    소비한다. 이 최소 경계는 같은 DB에 이 feature 표만 멱등 생성하며, 다음 정상
    기동의 전체 bootstrap은 그대로 남겨 둔다.
    """

    # db가 이 모듈의 CREATE_SQL을 가져가므로 module import 시점 순환을 피한다.
    from src.features.storage import constants, db as storage_db  # noqa: PLC0415

    path = storage_db.default_db_path().resolve()
    # 종료 경계가 사라진 운영 DB를 새 파일로 되살리면 다음 재시작이 데이터
    # 소실을 최초 설치로 오인한다. main startup이 이미 준비한 기존 파일만 연다.
    uri = path.as_uri() + "?mode=rw"
    conn = sqlite3.connect(
        uri,
        uri=True,
        timeout=constants.DB_BUSY_TIMEOUT_SEC,
    )
    try:
        # 정상 연결을 우회하는 종료 전 최소 경계도 같은 commit 내구성을 가져야 한다.
        storage_db._configure_synchronous_durability(conn)  # noqa: SLF001
        current_identity = storage_db._read_database_identity(conn)  # noqa: SLF001
        cached_identity = storage_db._cached_identity(path)  # noqa: SLF001
        if current_identity is None or current_identity != cached_identity:
            raise RuntimeError(
                "종료 중단 표식을 쓸 SQLite 저장소 identity가 현재 실행과 다릅니다"
            )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(CREATE_SQL)
        mark(
            conn,
            job_id=job_id,
            interrupted_at=interrupted_at,
            reason=reason,
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def exists(conn: sqlite3.Connection, job_id: str) -> bool:
    return conn.execute(
        f"SELECT 1 FROM {TABLE_NAME} WHERE job_id = ?", (job_id,)
    ).fetchone() is not None


def delete(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(f"DELETE FROM {TABLE_NAME} WHERE job_id = ?", (job_id,))
