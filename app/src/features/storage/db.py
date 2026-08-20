"""SQLite 연결 열기 · 표 만들기(마이그레이션).

★ 새 의존성을 쓰지 않는다 — 파이썬 표준 `sqlite3`만 쓴다 (팀장 지시).

★ 연결을 오래 들고 있지 않는다. 요청마다 짧게 열고 닫는다 — 웹 요청 코드가
  파이프라인을 `asyncio.to_thread`로 별도 스레드에서 돌리므로(port.py 참고),
  연결 하나를 여러 스레드가 나눠 쓰면 `sqlite3.ProgrammingError`가 난다.
  짧게 열고 닫으면 스레드 안전성을 별도 잠금 코드 없이 얻는다 (자세한 근거는
  최종 보고 §위험 요소 — 동시 접속).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterator, Optional

from src.core import paths
from src.features.storage import constants


def default_db_path() -> Path:
    """`db_path`를 안 넘겼을 때 쓰는 DB 파일 경로.

    ★ 환경변수로 바꿀 수 있다. 두 곳에서 필요하다 —
      ① **시험**이 진짜 DB를 건드리면 안 된다 (돌릴 때마다 기록이 더러워진다)
      ② 배포할 때 데이터를 다른 위치에 두고 싶을 수 있다
    """
    override = os.environ.get(constants.ENV_DB_PATH, "").strip()
    if override:
        return Path(override)
    return paths.APP_ROOT / constants.DEFAULT_DB_RELATIVE_PATH


#: 표를 만드는 SQL. 전부 `IF NOT EXISTS`라 여러 번 불러도 안전하다(멱등).
_SCHEMA_STATEMENTS: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {constants.TABLE_REPORTS} (
        report_id    TEXT PRIMARY KEY,
        corp_id      TEXT NOT NULL,
        job          TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        created_at   TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {constants.TABLE_LAYER1_CACHE} (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        corp_id             TEXT NOT NULL,
        job_key             TEXT NOT NULL,
        posting_fingerprint TEXT NOT NULL,
        report_id           TEXT NOT NULL REFERENCES {constants.TABLE_REPORTS}(report_id),
        fiscal_year         INTEGER,
        created_at          TEXT NOT NULL,
        UNIQUE(corp_id, job_key, posting_fingerprint)
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_layer1_lookup
        ON {constants.TABLE_LAYER1_CACHE}(corp_id, job_key)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {constants.TABLE_LAYER2_CACHE} (
        corp_id            TEXT PRIMARY KEY,
        fragments_json     TEXT NOT NULL,
        filing_json        TEXT,
        cell_judgments_json TEXT,
        fiscal_year        INTEGER,
        collected_at       TEXT NOT NULL,
        updated_at         TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {constants.TABLE_ALIAS_CACHE} (
        alias_key  TEXT PRIMARY KEY,
        corp_id    TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {constants.TABLE_SESSIONS} (
        token      TEXT PRIMARY KEY,
        email      TEXT NOT NULL,
        subject    TEXT NOT NULL,
        is_admin   INTEGER NOT NULL,
        expires_at REAL NOT NULL
    )
    """,
)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """표가 없으면 만들고, 있으면 그대로 둔다.

    ★ 열쇠 링크 표는 그 feature가 자기 모양을 갖고 있다 (`sharelink/store.py`).
      여기서 다시 적으면 «같은 정의가 두 곳»이 되어 한쪽만 고쳐진다 (P-83과 같은 함정).
    """
    from src.features.sharelink.allowlist import CREATE_SQL as ALLOWED_SQL  # noqa: PLC0415
    from src.features.sharelink.store import CREATE_SQL as SHARE_LINKS_SQL  # noqa: PLC0415
    from src.features.export_notion.store import CREATE_SQL as NOTION_EXPORTS_SQL  # noqa: PLC0415

    for statement in (
        *_SCHEMA_STATEMENTS,
        SHARE_LINKS_SQL,
        ALLOWED_SQL,
        NOTION_EXPORTS_SQL,
    ):
        conn.execute(statement)

    # OAuth ``sub``는 이메일과 달리 계정에서 바뀌지 않는 사람 식별자다. 예전
    # DB에는 이 열이 없으므로 표를 지우지 않고 nullable 열로만 보탠다. 값이
    # 없는 예전 세션은 sessions.load_session()이 폐기해 재로그인을 요구한다.
    session_columns = {
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({constants.TABLE_SESSIONS})"
        ).fetchall()
    }
    if "subject" not in session_columns:
        conn.execute(
            f"ALTER TABLE {constants.TABLE_SESSIONS} ADD COLUMN subject TEXT"
        )


@contextmanager
def connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """DB 연결을 열고 표를 갖춘 뒤 넘겨준다. `with` 블록이 끝나면 커밋하고 닫는다.

    Args:
        db_path: DB 파일 경로. 생략하면 `default_db_path()`.
            ★ 부모 폴더가 없으면 만든다 — 첫 실행에서도 바로 동작해야 한다.

    Yields:
        `sqlite3.Row`를 쓰는 연결(컬럼 이름으로 접근 가능).

    Raises:
        예외가 나면 롤백하고 그대로 다시 던진다 — 절반만 쓰인 상태가 남지 않는다.
    """
    resolved = db_path if db_path is not None else default_db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(resolved), timeout=constants.DB_BUSY_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    try:
        # WAL — 읽기 여러 개가 쓰기 하나를 막지 않는다(동시 접속 완화).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
