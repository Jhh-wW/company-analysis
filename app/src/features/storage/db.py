"""SQLite 연결 열기 · 표 만들기(마이그레이션).

★ 새 의존성을 쓰지 않는다 — 파이썬 표준 `sqlite3`만 쓴다.

★ 연결을 오래 들고 있지 않는다. 요청마다 짧게 열고 닫는다 — 웹 요청 코드가
  파이프라인을 `asyncio.to_thread`로 별도 스레드에서 돌리므로(port.py 참고),
  연결 하나를 여러 스레드가 나눠 쓰면 `sqlite3.ProgrammingError`가 난다.
  짧게 열고 닫으면 스레드 안전성을 별도 잠금 코드 없이 얻는다.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterator, Optional

from src.core import paths
from src.core.persistent_schema import ensure_persistent_schema
from src.features.storage import constants
from src.shared.bounded_file_lock import exclusive_file_lock


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


_CREATE_SESSIONS_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {constants.TABLE_SESSIONS} (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    subject    TEXT NOT NULL,
    is_admin   INTEGER NOT NULL,
    expires_at REAL NOT NULL
)
"""
_LEGACY_SESSIONS_TABLE: Final[str] = "sessions_legacy_raw_token"
_SESSION_MIGRATION_SAVEPOINT: Final[str] = "migrate_sessions_token_hash"
_DATABASE_IDENTITY_TABLE: Final[str] = "storage_database_identity"

# 요청마다 모든 feature의 DDL과 migration을 다시 실행하면, 첫 동시 요청끼리
# journal/schema 쓰기 잠금을 다투고 정상 DB에서도 연결 실패가 난다. DB 안의
# 불변 identity와 SQLite schema cookie를 함께 기억해 같은 파일은 한 프로세스에서
# 한 번만 bootstrap한다. 파일 교체·schema 변화는 다음 연결이 다시 감지한다.
_BOOTSTRAP_GUARD = threading.RLock()
_BOOTSTRAPPED_DATABASES: dict[Path, tuple[str, int]] = {}

_SchemaColumn = tuple[str, str, int, object, int, int]
_SchemaSignature = tuple[_SchemaColumn, ...]


def sqlite_wal_reset_fixed(
    version: tuple[int, int, int] = sqlite3.sqlite_version_info,
) -> bool:
    """현재 SQLite가 공식 WAL-reset 결함 수정판인지 판정한다."""

    if version >= constants.SQLITE_WAL_FIXED_VERSION:
        return True
    return any(
        lower <= version < upper
        for lower, upper in constants.SQLITE_WAL_FIXED_BACKPORT_RANGES
    )


def preferred_journal_mode(
    version: tuple[int, int, int] = sqlite3.sqlite_version_info,
) -> str:
    """패치된 SQLite만 WAL을 쓰고 그 외에는 rollback journal을 고른다."""

    if sqlite_wal_reset_fixed(version):
        return constants.SQLITE_JOURNAL_MODE_WAL
    return constants.SQLITE_JOURNAL_MODE_FALLBACK


def _configure_journal_mode(conn: sqlite3.Connection) -> str:
    """안전한 저널 모드를 적용하고 전환 실패는 조용히 넘기지 않는다."""

    requested = preferred_journal_mode()
    row = conn.execute(f"PRAGMA journal_mode={requested}").fetchone()
    actual = "" if row is None else str(row[0]).upper()
    if actual != requested:
        raise RuntimeError(
            "SQLite 안전 저널 모드 전환에 실패했습니다 "
            f"(요청={requested}, 실제={actual or '응답 없음'})"
        )
    return actual


def _configure_synchronous_durability(conn: sqlite3.Connection) -> int:
    """모든 쓰기 연결에 전원 중단까지 포함한 명시적 commit 내구성을 건다."""

    conn.execute(f"PRAGMA synchronous={constants.SQLITE_SYNCHRONOUS_MODE}")
    row = conn.execute("PRAGMA synchronous").fetchone()
    actual = -1 if row is None else int(row[0])
    if actual != constants.SQLITE_SYNCHRONOUS_LEVEL:
        raise RuntimeError(
            "SQLite commit 내구성 설정에 실패했습니다 "
            f"(요청={constants.SQLITE_SYNCHRONOUS_MODE}, 실제={actual})"
        )
    return actual

# ``PRAGMA table_xinfo``의 (name, type, notnull, default, pk, hidden) 정본.
# subject가 없는 최초판과 그 열을 뒤에 붙인 판까지는 실제 과거 스키마다.
_RAW_SESSION_SCHEMAS: Final[tuple[_SchemaSignature, ...]] = (
    (
        ("token", "TEXT", 0, None, 1, 0),
        ("email", "TEXT", 1, None, 0, 0),
        ("subject", "TEXT", 1, None, 0, 0),
        ("is_admin", "INTEGER", 1, None, 0, 0),
        ("expires_at", "REAL", 1, None, 0, 0),
    ),
    (
        ("token", "TEXT", 0, None, 1, 0),
        ("email", "TEXT", 1, None, 0, 0),
        ("is_admin", "INTEGER", 1, None, 0, 0),
        ("expires_at", "REAL", 1, None, 0, 0),
    ),
    (
        ("token", "TEXT", 0, None, 1, 0),
        ("email", "TEXT", 1, None, 0, 0),
        ("is_admin", "INTEGER", 1, None, 0, 0),
        ("expires_at", "REAL", 1, None, 0, 0),
        ("subject", "TEXT", 0, None, 0, 0),
    ),
)
_HASHED_SESSION_SCHEMAS: Final[tuple[_SchemaSignature, ...]] = (
    (
        ("token_hash", "TEXT", 0, None, 1, 0),
        ("email", "TEXT", 1, None, 0, 0),
        ("subject", "TEXT", 1, None, 0, 0),
        ("is_admin", "INTEGER", 1, None, 0, 0),
        ("expires_at", "REAL", 1, None, 0, 0),
    ),
    (
        ("token_hash", "TEXT", 0, None, 1, 0),
        ("email", "TEXT", 1, None, 0, 0),
        ("is_admin", "INTEGER", 1, None, 0, 0),
        ("expires_at", "REAL", 1, None, 0, 0),
    ),
    (
        ("token_hash", "TEXT", 0, None, 1, 0),
        ("email", "TEXT", 1, None, 0, 0),
        ("is_admin", "INTEGER", 1, None, 0, 0),
        ("expires_at", "REAL", 1, None, 0, 0),
        ("subject", "TEXT", 0, None, 0, 0),
    ),
)


#: 표를 만드는 SQL. 전부 `IF NOT EXISTS`라 여러 번 불러도 안전하다(멱등).
_SCHEMA_STATEMENTS: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {_DATABASE_IDENTITY_TABLE} (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        identity     TEXT NOT NULL UNIQUE
    )
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS storage_database_identity_no_update
    BEFORE UPDATE ON {_DATABASE_IDENTITY_TABLE}
    BEGIN SELECT RAISE(ABORT, 'storage database identity is immutable'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS storage_database_identity_no_delete
    BEFORE DELETE ON {_DATABASE_IDENTITY_TABLE}
    BEGIN SELECT RAISE(ABORT, 'storage database identity is immutable'); END
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {constants.TABLE_REPORTS} (
        report_id    TEXT PRIMARY KEY,
        corp_id      TEXT NOT NULL,
        job          TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        engine_epoch_digest TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {constants.TABLE_REPORT_PUBLIC_PROJECTIONS} (
        report_id       TEXT PRIMARY KEY
                        REFERENCES {constants.TABLE_REPORTS}(report_id),
        projection_json TEXT NOT NULL,
        content_sha256  TEXT NOT NULL,
        display_sha256  TEXT NOT NULL,
        created_at      TEXT NOT NULL
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
        engine_epoch_digest TEXT NOT NULL DEFAULT '',
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
)


def _bootstrap_lock_path(db_path: Path) -> Path:
    return db_path.with_name(f".{db_path.name}.schema.lock")


@contextmanager
def _cross_process_bootstrap_lock(db_path: Path) -> Iterator[None]:
    """같은 DB의 journal 전환과 schema migration을 프로세스 사이에서도 한 줄로 세운다."""

    # backup/recovery와 같은 공용 잠금 경계를 쓴다. ``Path.open('a+b')``를
    # 여기서 다시 구현하면 symlink/reparse point를 따라가 잠금 대상이 아닌 파일을
    # 열 수 있고, Windows의 진짜 I/O 오류까지 "잠금 중"으로 오인한다.
    with exclusive_file_lock(
        _bootstrap_lock_path(db_path),
        timeout_seconds=constants.DB_SCHEMA_LOCK_TIMEOUT_SEC,
    ):
        yield


def _read_database_identity(
    conn: sqlite3.Connection,
) -> Optional[tuple[str, int]]:
    """bootstrap 표가 완결된 DB만 identity와 schema cookie를 돌려준다."""

    try:
        rows = conn.execute(
            f"SELECT singleton_id, identity FROM {_DATABASE_IDENTITY_TABLE}"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise
    if len(rows) != 1 or int(rows[0][0]) != 1:
        raise RuntimeError("SQLite 저장소 identity 표가 완결된 한 행이 아닙니다")
    identity = str(rows[0][1])
    if len(identity) != 64 or any(ch not in "0123456789abcdef" for ch in identity):
        raise RuntimeError("SQLite 저장소 identity 값이 올바르지 않습니다")
    schema_row = conn.execute("PRAGMA schema_version").fetchone()
    if schema_row is None:
        raise RuntimeError("SQLite schema version을 읽을 수 없습니다")
    return identity, int(schema_row[0])


def _cached_identity(db_path: Path) -> Optional[tuple[str, int]]:
    with _BOOTSTRAP_GUARD:
        return _BOOTSTRAPPED_DATABASES.get(db_path)


def _current_journal_mode(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA journal_mode").fetchone()
    return "" if row is None else str(row[0]).upper()


def _connection_is_bootstrapped(
    conn: sqlite3.Connection, db_path: Path
) -> bool:
    cached = _cached_identity(db_path)
    current = _read_database_identity(conn)
    if cached is not None and current is None:
        # 이 프로세스가 이미 같은 경로의 완결 DB를 썼는데 identity가 통째로
        # 사라졌다면 파일 삭제·빈 파일 교체·불완전 복구다. 여기서 자동 bootstrap하면
        # 데이터 소실을 "새 설치"로 가장한 채 서비스가 정상으로 열린다.
        # identity를 가진 완전한 DB로 원자 교체한 경우는 아래 비교가 False가 되어
        # 정상 migration/재검증 경로로 들어간다.
        raise RuntimeError(
            "실행 중 SQLite 저장소 identity가 사라졌습니다; "
            "빈 DB를 자동 생성하지 않고 복구를 기다립니다"
        )
    return bool(
        cached is not None
        and current == cached
        and _current_journal_mode(conn) == preferred_journal_mode()
    )


def _ensure_connection_bootstrapped(
    conn: sqlite3.Connection, db_path: Path
) -> None:
    """현재 프로세스·현재 DB 파일 조합에 전체 schema를 정확히 한 번 적용한다."""

    if _connection_is_bootstrapped(conn, db_path):
        return

    # RLock은 같은 프로세스의 첫 요청들을 직렬화한다. 파일 잠금은 배포 교체 때
    # 잠깐 겹친 두 프로세스가 동시에 journal/DDL을 바꾸는 일을 막는다.
    with _BOOTSTRAP_GUARD:
        if _connection_is_bootstrapped(conn, db_path):
            return
        with _cross_process_bootstrap_lock(db_path):
            if _connection_is_bootstrapped(conn, db_path):
                return
            _configure_journal_mode(conn)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                _ensure_schema(conn)
                conn.execute(
                    f"INSERT OR IGNORE INTO {_DATABASE_IDENTITY_TABLE} "
                    "(singleton_id, identity) VALUES (1, ?)",
                    (secrets.token_hex(32),),
                )
                identity = _read_database_identity(conn)
                if identity is None:
                    raise RuntimeError("SQLite 저장소 identity를 만들지 못했습니다")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            # commit 뒤 schema cookie를 다시 읽어 성공한 파일만 cache한다.
            committed_identity = _read_database_identity(conn)
            if committed_identity is None:
                raise RuntimeError("SQLite schema 준비 결과를 확인할 수 없습니다")
            _BOOTSTRAPPED_DATABASES[db_path] = committed_identity


def _schema_object_type(conn: sqlite3.Connection, name: str) -> Optional[str]:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name=?",
        (name,),
    ).fetchone()
    return None if row is None else str(row[0])


def _table_signature(conn: sqlite3.Connection, name: str) -> _SchemaSignature:
    escaped = name.replace('"', '""')
    return tuple(
        (
            str(row[1]),
            str(row[2]).strip().upper(),
            int(row[3]),
            row[4],
            int(row[5]),
            int(row[6]),
        )
        for row in conn.execute(f'PRAGMA table_xinfo("{escaped}")').fetchall()
    )


def _session_table_state(conn: sqlite3.Connection, name: str) -> str:
    object_type = _schema_object_type(conn, name)
    if object_type is None:
        return "missing"
    if object_type != "table":
        return "unexpected"
    signature = _table_signature(conn, name)
    if signature in _HASHED_SESSION_SCHEMAS:
        return "hashed"
    if signature in _RAW_SESSION_SCHEMAS:
        return "raw"
    return "unexpected"


def _session_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        column[0]
        for column in _table_signature(conn, constants.TABLE_SESSIONS)
    }


def _migrate_sessions_to_token_hash(conn: sqlite3.Connection) -> None:
    """원문 토큰 세션만 한 번 폐기하고 해시 기본키 표로 원자 전환한다."""
    savepoint = _SESSION_MIGRATION_SAVEPOINT
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        current = _session_table_state(conn, constants.TABLE_SESSIONS)
        reserved = _session_table_state(conn, _LEGACY_SESSIONS_TABLE)

        if current == "raw" and reserved == "missing":
            # 결정 1-A: 옛 세션 행만 무효화한다. SAVEPOINT 안에서 이름 변경,
            # 빈 해시 표 생성, 원문 표 삭제를 모두 끝내야 중간 상태가 영속되지 않는다.
            conn.execute(
                f"ALTER TABLE {constants.TABLE_SESSIONS} "
                f"RENAME TO {_LEGACY_SESSIONS_TABLE}"
            )
            conn.execute(_CREATE_SESSIONS_SQL)
            conn.execute(f"DROP TABLE {_LEGACY_SESSIONS_TABLE}")
        elif current == "hashed" and reserved == "raw":
            # 원자화 전 코드가 ALTER/CREATE 뒤 중단된 상태를 복구한다. 새 해시
            # 세션은 보존하고, 결정 1-A 대상인 원문 legacy 행만 제거한다.
            conn.execute(f"DROP TABLE {_LEGACY_SESSIONS_TABLE}")
        elif current == "missing" and reserved == "raw":
            # ALTER 직후 중단된 DB를 직접 열어도 같은 결정으로 복구한다.
            conn.execute(_CREATE_SESSIONS_SQL)
            conn.execute(f"DROP TABLE {_LEGACY_SESSIONS_TABLE}")
        elif current == "missing" and reserved == "missing":
            conn.execute(_CREATE_SESSIONS_SQL)
        elif current != "hashed" or reserved != "missing":
            raise RuntimeError(
                "지원할 수 없는 sessions 마이그레이션 상태입니다 "
                f"(sessions={current}, legacy={reserved})"
            )

        if (
            _session_table_state(conn, constants.TABLE_SESSIONS) != "hashed"
            or _session_table_state(conn, _LEGACY_SESSIONS_TABLE) != "missing"
        ):
            raise RuntimeError("sessions 해시 스키마 전환을 완결하지 못했습니다")
    except BaseException:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        finally:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """표가 없으면 만들고, 있으면 그대로 둔다.

    ★ 열쇠 링크 표는 그 feature가 자기 모양을 갖고 있다 (`sharelink/store.py`).
      여기서 다시 적으면 «같은 정의가 두 곳»이 되어 한쪽만 고쳐진다 — 같은 정의가 두 곳이 되는 함정이다.
    """
    from src.features.sharelink import allowlist as share_allow  # noqa: PLC0415
    from src.features.sharelink import store as share_store  # noqa: PLC0415
    from src.features.storage import job_interruptions  # noqa: PLC0415
    from src.features.export_notion.store import CREATE_SQL as NOTION_EXPORTS_SQL  # noqa: PLC0415
    from src.features.admin_dashboard import store as dashboard_store  # noqa: PLC0415

    for statement in (
        *_SCHEMA_STATEMENTS,
        NOTION_EXPORTS_SQL,
        job_interruptions.CREATE_SQL,
    ):
        conn.execute(statement)

    share_allow.ensure_schema(conn)
    share_store.ensure_schema(conn)
    dashboard_store.ensure_schema(conn)
    ensure_persistent_schema(conn)
    _migrate_sessions_to_token_hash(conn)

    # OAuth ``sub``는 이메일과 달리 계정에서 바뀌지 않는 사람 식별자다. 이미
    # token_hash를 쓰지만 subject가 없는 구 버전은 nullable 열로만 보탠다.
    # 값이 없는 예전 세션은 sessions.load_session()이 폐기해 재로그인을 요구한다.
    session_columns = _session_columns(conn)
    if "subject" not in session_columns:
        conn.execute(
            f"ALTER TABLE {constants.TABLE_SESSIONS} ADD COLUMN subject TEXT"
        )

    # 과거 공개 보고서는 그대로 읽되, 새 캐시 권위에는 process epoch가 반드시
    # 들어간다. DEFAULT ''은 옛 행을 명시적인 비권위 상태로 보존한다.
    for table_name in (
        constants.TABLE_REPORTS,
        constants.TABLE_LAYER1_CACHE,
    ):
        columns = {
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        }
        if "engine_epoch_digest" not in columns:
            conn.execute(
                f'ALTER TABLE "{table_name}" '
                "ADD COLUMN engine_epoch_digest TEXT NOT NULL DEFAULT ''"
            )


def _open_bootstrapped_connection(
    db_path: Optional[Path],
) -> sqlite3.Connection:
    """같은 설정·schema 계약으로 쓰기 연결 하나를 준비한다."""

    configured = db_path if db_path is not None else default_db_path()
    resolved = Path(configured).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(resolved), timeout=constants.DB_BUSY_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    try:
        # synchronous는 DB 파일 속성이 아니라 연결 속성이다. 최초 migration뿐
        # 아니라 cache hit인 모든 새 연결에서 transaction 전에 다시 강제한다.
        _configure_synchronous_durability(conn)
        # journal 전환과 전체 migration은 DB 파일마다 첫 연결에서 원자적으로
        # 끝낸다. 이후 요청은 identity+schema cookie를 읽기만 하므로 동시 첫
        # 요청이 DDL 쓰기 잠금을 다투지 않는다.
        _ensure_connection_bootstrapped(conn, resolved)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except BaseException:
        conn.close()
        raise


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
    conn = _open_bootstrapped_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect_explicit_commit(
    db_path: Optional[Path] = None,
) -> Iterator[sqlite3.Connection]:
    """호출부가 최종 fence 바로 뒤 commit을 직접 소유하는 연결이다.

    정상 ``with`` 탈출 때 transaction이 남아 있으면 조용히 한 번 더 commit하지
    않는다. 이는 명시 commit 뒤 후속 쓰기가 생겨 신원 fence 밖에서 영속되는
    실수를 rollback하고 시험에서 즉시 드러낸다.
    """

    conn = _open_bootstrapped_connection(db_path)
    try:
        yield conn
        if conn.in_transaction:
            conn.rollback()
            raise RuntimeError(
                "명시 commit 연결에 fence 밖의 미확정 transaction이 남았습니다"
            )
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect_readonly_existing(
    db_path: Optional[Path] = None,
) -> Iterator[Optional[sqlite3.Connection]]:
    """이미 존재하는 DB만 읽고 파일·폴더·schema는 만들지 않는다.

    공개 GET처럼 조회 자체가 상태를 만들면 안 되는 경계에서 쓴다. 파일이
    없으면 정상 miss인 ``None``을 넘긴다. 존재하는 파일은 SQLite ``mode=ro``와
    ``query_only``를 함께 적용해, 아래 조회 함수가 실수로 쓰기를 시도해도 DB가
    거부하게 한다. 일반 ``connect``와 달리 journal 전환·schema bootstrap·commit을
    전혀 수행하지 않는다.
    """

    configured = db_path if db_path is not None else default_db_path()
    resolved = Path(configured).resolve()
    if not resolved.is_file():
        yield None
        return
    uri = resolved.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=constants.DB_BUSY_TIMEOUT_SEC,
        )
    except sqlite3.OperationalError:
        # 존재 확인 직후 파일이 사라진 경쟁은 처음부터 없었던 GET과 같다.
        if not resolved.is_file():
            yield None
            return
        raise
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()
