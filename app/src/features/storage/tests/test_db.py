"""연결 열기·표 만들기 시험."""

from __future__ import annotations

import hashlib
import multiprocessing
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.features.budget import spend_store
from src.features.export_pdf import release_store
from src.features.export_notion import store as notion_store
from src.features.sharelink import store as share_store
from src.features.storage import constants, db, job_interruptions
from src.features.storage import sessions
from src.shared.bounded_file_lock import BoundedFileLockError


def _bootstrap_in_spawned_process(
    db_path: str,
    bootstrap_started,
    results,
    *,
    slow: bool,
) -> None:
    """cross-process schema lock 회귀를 위한 spawn-safe worker."""

    from src.features.storage import db as child_db  # noqa: PLC0415

    child_db.constants.DB_BUSY_TIMEOUT_SEC = 0.05
    child_db.constants.DB_SCHEMA_LOCK_TIMEOUT_SEC = 2.0
    original = child_db._ensure_schema  # noqa: SLF001
    if slow:
        def slowed(conn: sqlite3.Connection) -> None:
            bootstrap_started.set()
            time.sleep(0.25)
            original(conn)

        child_db._ensure_schema = slowed  # type: ignore[assignment]  # noqa: SLF001
    try:
        with child_db.connect(Path(db_path)) as conn:
            conn.execute("SELECT COUNT(*) FROM storage_database_identity").fetchone()
    except BaseException as exc:  # noqa: BLE001 - 자식 결과를 부모 assertion으로 전달
        results.put(("error", type(exc).__name__))
    else:
        results.put(("ok", ""))


@pytest.mark.parametrize(
    ("version", "expected"),
    (
        ((3, 44, 5), False),
        ((3, 44, 6), True),
        ((3, 45, 0), False),
        ((3, 50, 6), False),
        ((3, 50, 7), True),
        ((3, 51, 0), False),
        ((3, 51, 2), False),
        ((3, 51, 3), True),
        ((3, 52, 0), True),
    ),
)
def test_sqlite_wal_reset_fix_boundaries(
    version: tuple[int, int, int], expected: bool
) -> None:
    """공식 수정판과 역이식판에서만 WAL을 허용한다."""

    assert db.sqlite_wal_reset_fixed(version) is expected


def test_connect_uses_journal_mode_safe_for_runtime(tmp_path: Path) -> None:
    """실제 런타임이 결함 범위면 rollback journal로 안전하게 내린다."""

    with db.connect(tmp_path / "journal-mode.db") as conn:
        actual = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()

    assert actual == db.preferred_journal_mode()


def test_모든쓰기연결은_SQLite_EXTRA_commit내구성을_명시한다(
    tmp_path: Path,
) -> None:
    target = tmp_path / "synchronous-extra.db"

    with db.connect(target) as first:
        first_level = int(first.execute("PRAGMA synchronous").fetchone()[0])
    # synchronous는 connection-local이라 schema cache가 맞은 두 번째 연결도
    # 똑같이 명시해야 한다.
    with db.connect(target) as second:
        second_level = int(second.execute("PRAGMA synchronous").fetchone()[0])

    assert constants.SQLITE_SYNCHRONOUS_MODE == "EXTRA"
    assert (first_level, second_level) == (
        constants.SQLITE_SYNCHRONOUS_LEVEL,
        constants.SQLITE_SYNCHRONOUS_LEVEL,
    )


def test_종료직전_최소중단표식도_EXTRA내구성으로_commit한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "shutdown-marker.db"
    monkeypatch.setenv(constants.ENV_DB_PATH, str(target))
    # persist는 정상 startup 뒤 종료에서만 호출된다. 최초 DB 생성 권한은 main
    # bootstrap 하나에만 두고, 종료 우회 경계에는 주지 않는다.
    with db.connect(target):
        pass
    levels: list[int] = []
    original = db._configure_synchronous_durability  # noqa: SLF001

    def record(conn: sqlite3.Connection) -> int:
        level = original(conn)
        levels.append(level)
        return level

    monkeypatch.setattr(db, "_configure_synchronous_durability", record)
    job_interruptions.persist(
        job_id="shutdown-extra",
        interrupted_at="2026-08-28T00:00:00+09:00",
        reason="shutdown_timeout",
    )

    assert levels == [constants.SQLITE_SYNCHRONOUS_LEVEL]
    with db.connect(target) as conn:
        assert job_interruptions.exists(conn, "shutdown-extra")


def test_종료중단표식은_사라진DB와부모를_새로만들지않는다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "missing-parent" / "storage.db"
    monkeypatch.setenv(constants.ENV_DB_PATH, str(target))

    with pytest.raises(sqlite3.OperationalError):
        job_interruptions.persist(
            job_id="must-not-create-db",
            interrupted_at="2026-08-28T00:00:00+09:00",
            reason="shutdown_timeout",
        )

    assert not target.exists()
    assert not target.parent.exists()


def test_connect_creates_missing_parent_dir_and_file(tmp_path: Path) -> None:
    """DB 파일이 없어도, 폴더가 없어도 처음 연결하면 만들어진다."""
    target = tmp_path / "nested" / "storage.db"
    assert not target.exists()

    with db.connect(target) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert target.exists()
    assert {
        constants.TABLE_REPORTS,
        constants.TABLE_LAYER1_CACHE,
        constants.TABLE_LAYER2_CACHE,
        constants.TABLE_ALIAS_CACHE,
        constants.TABLE_SESSIONS,
        notion_store.TABLE_NOTION_EXPORTS,
    } <= tables

    with db.connect(target) as conn:
        session_columns = {
            row["name"]: row["pk"]
            for row in conn.execute(
                f"PRAGMA table_info({constants.TABLE_SESSIONS})"
            )
        }
    assert session_columns["token_hash"] == 1
    assert "token" not in session_columns


def test_readonly_existing은_없는DB와부모폴더를_만들지않는다(
    tmp_path: Path,
) -> None:
    target = tmp_path / "never-created" / "storage.db"

    with db.connect_readonly_existing(target) as conn:
        assert conn is None

    assert not target.exists()
    assert not target.parent.exists()


def test_readonly_existing은_기존DB를_읽되_쓰기는거부한다(
    tmp_path: Path,
) -> None:
    target = tmp_path / "storage.db"
    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {constants.TABLE_ALIAS_CACHE} "
            "(alias_key, corp_id, created_at) VALUES ('a', 'CORP1', '2026-08-28')"
        )
    before_digest = hashlib.sha256(target.read_bytes()).hexdigest()

    with db.connect_readonly_existing(target) as conn:
        assert conn is not None
        row = conn.execute(
            f"SELECT corp_id FROM {constants.TABLE_ALIAS_CACHE} "
            "WHERE alias_key = 'a'"
        ).fetchone()
        assert row is not None and row["corp_id"] == "CORP1"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                f"DELETE FROM {constants.TABLE_ALIAS_CACHE} WHERE alias_key = 'a'"
            )
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before_digest


def test_connect_reopen_keeps_data(tmp_path: Path) -> None:
    """서버를 껐다 켜는 것을 흉내 — 연결을 닫고 다시 열어도 표는 그대로다."""
    target = tmp_path / "storage.db"

    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {constants.TABLE_ALIAS_CACHE} (alias_key, corp_id, created_at) "
            "VALUES ('a', 'CORP1', '2026-08-15T00:00:00')"
        )

    with db.connect(target) as conn:
        row = conn.execute(
            f"SELECT corp_id FROM {constants.TABLE_ALIAS_CACHE} WHERE alias_key = 'a'"
        ).fetchone()

    assert row is not None
    assert row["corp_id"] == "CORP1"


def test_connect_twice_is_idempotent(tmp_path: Path) -> None:
    """표를 두 번 만들어도(멱등) 에러가 나지 않는다."""
    target = tmp_path / "storage.db"
    with db.connect(target):
        pass
    with db.connect(target):
        pass  # 두 번째도 예외 없이 지나가야 한다


def test_DB_identity는_실행중_수정하거나_지울수없다(tmp_path: Path) -> None:
    target = tmp_path / "immutable-identity.db"
    with db.connect(target) as conn:
        before = db._read_database_identity(conn)  # noqa: SLF001
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            conn.execute(
                "UPDATE storage_database_identity SET identity=? WHERE singleton_id=1",
                ("f" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            conn.execute(
                "DELETE FROM storage_database_identity WHERE singleton_id=1"
            )
        after = db._read_database_identity(conn)  # noqa: SLF001

    assert before is not None and after == before


def test_schema잠금경로가_일반파일이아니면_따라가지않고_실패한다(
    tmp_path: Path,
) -> None:
    target = tmp_path / "unsafe-lock.db"
    lock_path = db._bootstrap_lock_path(target)  # noqa: SLF001 - 잠금 경계 회귀
    lock_path.mkdir()

    with pytest.raises(BoundedFileLockError, match="일반 파일"):
        with db.connect(target):
            pass

    # 연결 파일은 SQLite가 먼저 만들 수 있지만 schema를 절반 적용하면 안 된다.
    with sqlite3.connect(target) as raw:
        assert raw.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0] == 0


def test_동시_최초연결은_schema를_한번만_원자준비한다(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """시험 순서가 숨기던 fresh DB 경쟁: 모든 요청이 살고 DDL은 한 번뿐이다."""

    target = tmp_path / "concurrent-first-connect.db"
    worker_count = 12
    barrier = threading.Barrier(worker_count)
    count_guard = threading.Lock()
    ensure_calls = 0
    original = db._ensure_schema  # noqa: SLF001 - 최초 준비 횟수 장애 회귀

    def slow_counted_ensure(conn: sqlite3.Connection) -> None:
        nonlocal ensure_calls
        with count_guard:
            ensure_calls += 1
        # 옛 구현에서 모든 스레드가 DDL에 들어가는 경쟁 창을 확실히 연다.
        time.sleep(0.05)
        original(conn)

    monkeypatch.setattr(db, "_ensure_schema", slow_counted_ensure)

    def connect_and_write(index: int) -> str:
        barrier.wait(timeout=5.0)
        with db.connect(target) as conn:
            conn.execute(
                f"INSERT INTO {constants.TABLE_ALIAS_CACHE} "
                "(alias_key, corp_id, created_at) VALUES (?, ?, ?)",
                (f"alias-{index}", f"corp-{index}", "2026-08-28T00:00:00Z"),
            )
            return str(
                conn.execute("PRAGMA journal_mode").fetchone()[0]
            ).upper()

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        modes = tuple(pool.map(connect_and_write, range(worker_count)))

    assert ensure_calls == 1
    assert set(modes) == {db.preferred_journal_mode()}
    with db.connect(target) as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {constants.TABLE_ALIAS_CACHE}"
        ).fetchone()[0] == worker_count
    assert ensure_calls == 1, "평범한 재연결은 전체 migration을 되풀이하면 안 된다"


def test_서로다른_프로세스의_최초준비도_파일잠금으로_직렬화한다(
    tmp_path: Path,
) -> None:
    """worker 교체가 겹쳐도 짧은 SQLite timeout 때문에 한쪽이 죽지 않는다."""

    target = tmp_path / "cross-process-first-connect.db"
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    results = context.Queue()
    first = context.Process(
        target=_bootstrap_in_spawned_process,
        args=(str(target), started, results),
        kwargs={"slow": True},
    )
    second = context.Process(
        target=_bootstrap_in_spawned_process,
        args=(str(target), started, results),
        kwargs={"slow": False},
    )
    first.start()
    # Windows spawn은 이 큰 제품 모듈을 자식에서 처음 import한다. 병렬 전체
    # suite 중에는 import만 5초를 넘을 수 있으므로, 제품의 2초 schema-lock
    # 검증과 무관한 process 기동 대기는 넉넉히 분리한다.
    if not started.wait(timeout=30.0):
        first.terminate()
        first.join(timeout=5.0)
        pytest.fail("첫 프로세스가 schema 준비 경계에 진입하지 못했습니다")
    second.start()
    first.join(timeout=30.0)
    second.join(timeout=30.0)
    if first.is_alive():
        first.terminate()
        first.join(timeout=2.0)
    if second.is_alive():
        second.terminate()
        second.join(timeout=2.0)

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert sorted((results.get(timeout=2.0), results.get(timeout=2.0))) == [
        ("ok", ""),
        ("ok", ""),
    ]


def test_schema준비_실패는_cache하지_않고_다음연결이_복구한다(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "bootstrap-failure-retry.db"
    original = db._ensure_schema  # noqa: SLF001
    calls = 0

    def fail_once(conn: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("주입한 migration 실패")
        original(conn)

    monkeypatch.setattr(db, "_ensure_schema", fail_once)
    with pytest.raises(RuntimeError, match="주입한 migration 실패"):
        with db.connect(target):
            pass

    with db.connect(target) as recovered:
        assert recovered.execute(
            f"SELECT COUNT(*) FROM {constants.TABLE_SESSIONS}"
        ).fetchone()[0] == 0
    assert calls == 2


def test_실행중_DB파일교체는_옛_cache를_믿지_않고_다시_준비한다(
    tmp_path: Path,
) -> None:
    target = tmp_path / "replace-detected.db"
    archived = tmp_path / "old-storage.db"
    replacement_source = tmp_path / "prepared-replacement.db"
    with db.connect(target) as first:
        first_identity = db._read_database_identity(first)  # noqa: SLF001
        first.execute(
            f"INSERT INTO {constants.TABLE_ALIAS_CACHE} "
            "(alias_key, corp_id, created_at) VALUES ('old', 'old', 'old')"
        )
    # 정상 복구물은 자체 identity와 완결 schema를 가진 DB다. 빈 경로를 새 설치로
    # 착각하는 경우는 아래 별도 시험에서 차단한다.
    with db.connect(replacement_source) as prepared:
        prepared_identity = db._read_database_identity(prepared)  # noqa: SLF001

    target.replace(archived)
    replacement_source.replace(target)
    with db.connect(target) as replacement:
        replacement_identity = db._read_database_identity(replacement)  # noqa: SLF001
        assert replacement.execute(
            f"SELECT COUNT(*) FROM {constants.TABLE_ALIAS_CACHE}"
        ).fetchone()[0] == 0

    assert first_identity is not None
    assert prepared_identity is not None
    assert replacement_identity is not None
    assert replacement_identity[0] == prepared_identity[0]
    assert replacement_identity[0] != first_identity[0]


def test_실행중_DB소실은_빈서비스로_재생성하지않고_완전복구만_허용한다(
    tmp_path: Path,
) -> None:
    target = tmp_path / "lost-while-running.db"
    intact_backup = tmp_path / "intact-backup.db"
    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {constants.TABLE_ALIAS_CACHE} "
            "(alias_key, corp_id, created_at) VALUES ('keep', 'corp-keep', 'now')"
        )

    # 운영 중 파일 삭제·빈 파일 교체를 흉내 낸다. 옛 구현은 여기서 전체 schema를
    # 새로 만들고 0건짜리 정상 서비스로 열었다.
    target.replace(intact_backup)
    with pytest.raises(RuntimeError, match="identity가 사라졌습니다"):
        with db.connect(target):
            pass

    # 실패 과정에서 sqlite3가 만든 빈 파일은 승인된 복구물이 아니다. identity를
    # 가진 완전 DB를 원자 교체하면 기존 cache를 버리고 정본을 재검증해 정상 복구한다.
    target.unlink()
    intact_backup.replace(target)
    with db.connect(target) as restored:
        row = restored.execute(
            f"SELECT corp_id FROM {constants.TABLE_ALIAS_CACHE} "
            "WHERE alias_key='keep'"
        ).fetchone()
    assert row is not None and row[0] == "corp-keep"


def test_실행중_schema변경은_cookie로_감지해_정본을_재확인한다(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "schema-cookie-change.db"
    with db.connect(target):
        pass

    original = db._ensure_schema  # noqa: SLF001
    calls = 0

    def counted(conn: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        original(conn)

    monkeypatch.setattr(db, "_ensure_schema", counted)
    with sqlite3.connect(target) as external:
        external.execute("CREATE TABLE external_schema_change (id INTEGER)")

    with db.connect(target):
        pass
    with db.connect(target):
        pass
    assert calls == 1


def test_실행중_저널모드변경도_감지해_안전모드로_복구한다(
    tmp_path: Path,
) -> None:
    target = tmp_path / "journal-mode-change.db"
    with db.connect(target):
        pass

    with sqlite3.connect(target) as external:
        changed = str(
            external.execute("PRAGMA journal_mode=MEMORY").fetchone()[0]
        ).upper()
    assert changed == "MEMORY"

    with db.connect(target) as recovered:
        assert db._current_journal_mode(recovered) == db.preferred_journal_mode()  # noqa: SLF001


def test_legacy_session_migration_preserves_other_data_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """결정 1-A는 옛 세션만 지우고 보고서·링크·예산·승인을 보존한다."""
    target = tmp_path / "legacy-with-product-data.db"
    legacy = sqlite3.connect(target)
    try:
        legacy.execute(
            "CREATE TABLE sessions (token TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "subject TEXT NOT NULL, is_admin INTEGER NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        legacy.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-raw-cookie",
                "admin@example.com",
                "google:admin-1",
                1,
                2_000_000_000.0,
            ),
        )
        legacy.execute(
            "CREATE TABLE reports ("
            "report_id TEXT PRIMARY KEY, corp_id TEXT NOT NULL, job TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, generated_at TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        legacy.execute(
            "INSERT INTO reports VALUES "
            "('report-keep', 'corp-keep', 'job-keep', '{}', 'generated', 'created')"
        )
        legacy.execute(
            "CREATE TABLE share_links ("
            "key TEXT PRIMARY KEY, company TEXT NOT NULL, job TEXT NOT NULL, "
            "report_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL, opened_count INTEGER NOT NULL DEFAULT 0, "
            "first_opened_at TEXT NOT NULL DEFAULT '', "
            "last_opened_at TEXT NOT NULL DEFAULT '')"
        )
        legacy.execute(
            "INSERT INTO share_links "
            "(key, company, job, report_id, note, created_at) "
            "VALUES ('link-keep', '회사', '직무', 'report-keep', '보존', 'created')"
        )
        legacy.execute(spend_store.CREATE_SQL)
        legacy.execute(
            "INSERT INTO budget_spend_events "
            "(run_id, phase, day, bucket_id, cost_krw, created_at) "
            "VALUES ('run-keep', 'pipeline', '2026-08-20', 'bucket-hash', 123, 'created')"
        )
        legacy.execute(release_store.CREATE_SQL)
        legacy.execute(
            "INSERT INTO pdf_release_records "
            "(report_id, pdf_sha256, approval_json, approval_created_at) "
            "VALUES (?, ?, '{}', 'created')",
            ("report-keep", "a" * 64),
        )
        legacy.commit()
    finally:
        legacy.close()

    replacement = sessions.SessionRecord(
        "new-raw-cookie",
        "admin@example.com",
        "google:admin-1",
        True,
        2_000_000_000.0,
    )
    with db.connect(target) as migrated:
        columns = {
            row["name"]: row["pk"]
            for row in migrated.execute("PRAGMA table_info(sessions)")
        }
        assert columns["token_hash"] == 1
        assert "token" not in columns
        assert migrated.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert migrated.execute(
            "SELECT payload_json FROM reports WHERE report_id='report-keep'"
        ).fetchone()[0] == "{}"
        assert migrated.execute(
            "SELECT note FROM share_links WHERE key_hash=?",
            (share_store.key_hash_of("link-keep"),),
        ).fetchone()[0] == "보존"
        assert "key" not in {
            row[1] for row in migrated.execute("PRAGMA table_info(share_links)")
        }
        assert migrated.execute(
            "SELECT cost_krw FROM budget_spend_events WHERE run_id='run-keep'"
        ).fetchone()[0] == 123
        assert migrated.execute(
            "SELECT approval_json FROM pdf_release_records "
            "WHERE report_id='report-keep'"
        ).fetchone()[0] == "{}"
        sessions.save_session(migrated, replacement)

    # 새 스키마가 마이그레이션 완료 표식이다. 재연결해도 새 세션을 다시 지우지 않는다.
    with db.connect(target) as reopened:
        assert sessions.load_session(
            reopened, replacement.token, now=1.0
        ) == replacement
        assert reopened.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
        assert reopened.execute("SELECT COUNT(*) FROM share_links").fetchone()[0] == 1
        assert reopened.execute(
            "SELECT COUNT(*) FROM budget_spend_events"
        ).fetchone()[0] == 1
        assert reopened.execute(
            "SELECT COUNT(*) FROM pdf_release_records"
        ).fetchone()[0] == 1


def test_session_migration_create_failure_rolls_back_then_restart_recovers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """ALTER 뒤 CREATE가 실패해도 원문 표가 예약 이름으로 노출되지 않는다."""
    target = tmp_path / "migration-create-failure.db"
    raw_token = "rollback-raw-cookie"
    initial = sqlite3.connect(target)
    try:
        initial.execute(
            "CREATE TABLE sessions (token TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "subject TEXT NOT NULL, is_admin INTEGER NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        initial.execute(
            "INSERT INTO sessions VALUES (?, 'admin@example.com', "
            "'google:admin-1', 1, 2000000000.0)",
            (raw_token,),
        )
        initial.execute("CREATE TABLE keep_data (value TEXT NOT NULL)")
        initial.execute("INSERT INTO keep_data VALUES ('preserve-me')")
        initial.commit()
    finally:
        initial.close()

    valid_create = db._CREATE_SESSIONS_SQL  # noqa: SLF001 - DDL 장애 주입 경계
    monkeypatch.setattr(db, "_CREATE_SESSIONS_SQL", "CREATE TABLE invalid (")
    with pytest.raises(sqlite3.OperationalError):
        with db.connect(target):
            pass

    rolled_back = sqlite3.connect(target)
    try:
        tables = {
            row[0]
            for row in rolled_back.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            row[1] for row in rolled_back.execute("PRAGMA table_info(sessions)")
        }
        assert "sessions" in tables
        assert db._LEGACY_SESSIONS_TABLE not in tables  # noqa: SLF001
        assert "token" in columns and "token_hash" not in columns
        assert (
            rolled_back.execute("SELECT token FROM sessions").fetchone()[0]
            == raw_token
        )
        assert (
            rolled_back.execute("SELECT value FROM keep_data").fetchone()[0]
            == "preserve-me"
        )
    finally:
        rolled_back.close()

    monkeypatch.setattr(db, "_CREATE_SESSIONS_SQL", valid_create)
    with db.connect(target) as recovered:
        assert not recovered.in_transaction, "migration SAVEPOINT는 요청 전에 끝나야 한다"
        tables = {
            row["name"]
            for row in recovered.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert db._LEGACY_SESSIONS_TABLE not in tables  # noqa: SLF001
        assert recovered.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert (
            recovered.execute("SELECT value FROM keep_data").fetchone()[0]
            == "preserve-me"
        )

    with sqlite3.connect(target) as verified:
        assert raw_token not in "\n".join(verified.iterdump())


def test_interrupted_hashed_and_exact_raw_legacy_recovers_without_losing_new_data(
    tmp_path: Path,
) -> None:
    """원자화 전 중단 상태는 새 해시 세션을 살리고 원문 legacy만 폐기한다."""
    target = tmp_path / "interrupted-migration.db"
    old_raw_token = "interrupted-old-raw-cookie"
    new_raw_token = "new-session-must-survive"
    token_hash = hashlib.sha256(new_raw_token.encode("utf-8")).hexdigest()
    interrupted = sqlite3.connect(target)
    try:
        interrupted.execute(db._CREATE_SESSIONS_SQL)  # noqa: SLF001
        interrupted.execute(
            "INSERT INTO sessions VALUES (?, 'new@example.com', "
            "'google:new-1', 1, 2000000000.0)",
            (token_hash,),
        )
        interrupted.execute(
            f"CREATE TABLE {db._LEGACY_SESSIONS_TABLE} ("  # noqa: SLF001
            "token TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "subject TEXT NOT NULL, is_admin INTEGER NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        interrupted.execute(
            f"INSERT INTO {db._LEGACY_SESSIONS_TABLE} VALUES "  # noqa: SLF001
            "(?, 'old@example.com', 'google:old-1', 1, 2000000000.0)",
            (old_raw_token,),
        )
        interrupted.execute("CREATE TABLE keep_data (value TEXT NOT NULL)")
        interrupted.execute("INSERT INTO keep_data VALUES ('preserve-me')")
        interrupted.commit()
    finally:
        interrupted.close()

    expected = sessions.SessionRecord(
        new_raw_token,
        "new@example.com",
        "google:new-1",
        True,
        2_000_000_000.0,
    )
    with db.connect(target) as recovered:
        assert not recovered.in_transaction, "복구 SAVEPOINT는 요청 전에 끝나야 한다"
        assert sessions.load_session(recovered, new_raw_token, now=1.0) == expected
        tables = {
            row["name"]
            for row in recovered.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert db._LEGACY_SESSIONS_TABLE not in tables  # noqa: SLF001
        assert (
            recovered.execute("SELECT value FROM keep_data").fetchone()[0]
            == "preserve-me"
        )

    with db.connect(target) as reopened:
        assert sessions.load_session(reopened, new_raw_token, now=1.0) == expected
        assert reopened.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    with sqlite3.connect(target) as verified:
        dump = "\n".join(verified.iterdump())
        assert old_raw_token not in dump
        assert new_raw_token not in dump


def test_unexpected_reserved_legacy_schema_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "unexpected-legacy-schema.db"
    prepared = sqlite3.connect(target)
    try:
        prepared.execute(db._CREATE_SESSIONS_SQL)  # noqa: SLF001
        prepared.execute(
            f"CREATE TABLE {db._LEGACY_SESSIONS_TABLE} ("  # noqa: SLF001
            "token TEXT PRIMARY KEY, unexpected TEXT NOT NULL)"
        )
        prepared.execute(
            f"INSERT INTO {db._LEGACY_SESSIONS_TABLE} VALUES "  # noqa: SLF001
            "('do-not-guess', 'preserve')"
        )
        prepared.commit()
    finally:
        prepared.close()

    with pytest.raises(RuntimeError, match="legacy=unexpected"):
        with db.connect(target):
            pass

    with sqlite3.connect(target) as verified:
        assert verified.execute(
            f"SELECT token FROM {db._LEGACY_SESSIONS_TABLE}"  # noqa: SLF001
        ).fetchone()[0] == "do-not-guess"


def test_raw_sessions_plus_reserved_legacy_combo_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "unexpected-two-raw-tables.db"
    prepared = sqlite3.connect(target)
    raw_schema = (
        "(token TEXT PRIMARY KEY, email TEXT NOT NULL, subject TEXT NOT NULL, "
        "is_admin INTEGER NOT NULL, expires_at REAL NOT NULL)"
    )
    try:
        prepared.execute(f"CREATE TABLE sessions {raw_schema}")
        prepared.execute(
            f"CREATE TABLE {db._LEGACY_SESSIONS_TABLE} {raw_schema}"  # noqa: SLF001
        )
        prepared.commit()
    finally:
        prepared.close()

    with pytest.raises(RuntimeError, match="sessions=raw, legacy=raw"):
        with db.connect(target):
            pass

    with sqlite3.connect(target) as verified:
        tables = {
            row[0]
            for row in verified.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"sessions", db._LEGACY_SESSIONS_TABLE} <= tables  # noqa: SLF001


def test_default_db_path_is_under_app_data_dir(monkeypatch) -> None:
    # ★ 환경변수를 «걷어내고» 진짜 기본값을 본다.
    #   (시험 전체가 임시 DB를 쓰도록 conftest.py가 이 변수를 걸어 둔다)
    monkeypatch.delenv(constants.ENV_DB_PATH, raising=False)
    path = db.default_db_path()
    assert path.name == "storage.db"
    assert path.parent.name == "data"
