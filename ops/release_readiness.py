#!/usr/bin/env python3
"""배포 전 SQLite 백업·복구·운영 상태를 읽기 전용으로 검증한다.

이 도구는 운영 DB를 수정하지 않는다. 복구 검증은 매번 새 임시 디렉터리에
복사한 DB에서 수행하며, 종료 시 복사본을 자동으로 제거한다.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable
from urllib.parse import quote

if __package__:
    from .backup_manifest import (
        IndependentManifestGate,
        ManifestError,
        ManifestExpectation,
    )
else:  # 직접 스크립트 실행 호환
    from backup_manifest import (  # type: ignore[no-redef]
        IndependentManifestGate,
        ManifestError,
        ManifestExpectation,
    )


HASH_CHUNK_BYTES: Final[int] = 1024 * 1024
CHECKSUM_MAX_BYTES: Final[int] = 4096
SQLITE_TIMEOUT_SEC: Final[float] = 10.0
DEFAULT_MIN_FREE_BYTES: Final[int] = 256 * 1024 * 1024
DEFAULT_MAX_DISK_USED_PERCENT: Final[float] = 85.0
DEFAULT_MAX_DATABASE_BYTES: Final[int] = 768 * 1024 * 1024
DEFAULT_MAX_WAL_BYTES: Final[int] = 128 * 1024 * 1024
DEFAULT_MAX_LINK_OPEN_EVENTS: Final[int] = 100_000
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
SIDECAR_RE: Final[re.Pattern[str]] = re.compile(
    r"^([0-9A-Fa-f]{64})  ([^/\\\r\n]+)$"
)

REQUIRED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "reports",
        "sessions",
        "share_links",
        "share_link_open_events",
        "share_link_run_history",
        "budget_spend_events",
        "budget_spend_inflight",
        "job_interruptions",
        "observability_run_lifecycle",
        "dashboard_service_state",
        "dashboard_member_usage",
        "dashboard_operation_claims",
        "notion_export_operations",
    }
)

ACTIVE_QUERIES: Final[tuple[tuple[str, str], ...]] = (
    ("미정산 비용 예약", "SELECT COUNT(*) FROM budget_spend_inflight"),
    (
        "진행 중 공유 링크 작업",
        "SELECT COUNT(*) FROM share_link_run_history "
        "WHERE status IN ('running', 'awaiting_release')",
    ),
    (
        "진행 중 관측 작업",
        "SELECT COUNT(*) FROM observability_run_lifecycle "
        "WHERE state IN ('pending', 'running')",
    ),
    (
        "예약 상태 회원 작업",
        "SELECT COUNT(*) FROM dashboard_member_usage WHERE state = 'reserved'",
    ),
    (
        "진행 중 운영 작업",
        "SELECT COUNT(*) FROM dashboard_operation_claims WHERE status = 'running'",
    ),
    (
        "진행 중 Notion 내보내기",
        "SELECT COUNT(*) FROM notion_export_operations WHERE state = 'in_progress'",
    ),
)


class ReadinessError(RuntimeError):
    """안전 검증을 계속할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class DatabaseInventory:
    tables: tuple[str, ...]
    row_counts: dict[str, int]


@dataclass(frozen=True)
class AppSchemaInventory:
    table_count: int
    index_count: int
    trigger_count: int


def sha256_file(path: Path) -> str:
    """파일 내용을 출력하지 않고 SHA-256만 계산한다."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReadinessError("파일 해시를 계산하지 못했습니다.") from exc
    return digest.hexdigest()


def _regular_file(path: Path, *, label: str) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise ReadinessError(f"{label}은 심볼릭 링크일 수 없습니다.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReadinessError(f"{label}을 찾을 수 없습니다.") from exc
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ReadinessError(f"{label}은 비어 있지 않은 일반 파일이어야 합니다.")
    return resolved


def _valid_sha256(value: str, *, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        raise ReadinessError(f"{label}은 64자리 SHA-256이어야 합니다.")
    return normalized


def _read_sidecar(path: Path, *, database_name: str) -> str:
    sidecar = _regular_file(path, label="체크섬 파일")
    if sidecar.stat().st_size > CHECKSUM_MAX_BYTES:
        raise ReadinessError("체크섬 파일이 허용 크기를 넘었습니다.")
    try:
        text = sidecar.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ReadinessError("체크섬 파일을 ASCII로 읽을 수 없습니다.") from exc
    lines = text.splitlines()
    if len(lines) != 1:
        raise ReadinessError("체크섬 파일은 정확히 한 줄이어야 합니다.")
    match = SIDECAR_RE.fullmatch(lines[0])
    if match is None or match.group(2) != database_name:
        raise ReadinessError("체크섬 파일의 형식 또는 DB 파일명이 맞지 않습니다.")
    return match.group(1).lower()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri_path = quote(path.resolve().as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{uri_path}?mode=ro", uri=True, timeout=SQLITE_TIMEOUT_SEC
    )
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    escaped = table.replace('"', '""')
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{escaped}")')}


def _assert_sqlite_integrity(connection: sqlite3.Connection) -> set[str]:
    integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise ReadinessError("SQLite 무결성 검사에 실패했습니다.")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ReadinessError("SQLite 외래 키 검사에 실패했습니다.")
    return _table_names(connection)


def _assert_database(connection: sqlite3.Connection) -> set[str]:
    tables = _assert_sqlite_integrity(connection)

    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise ReadinessError("필수 운영 테이블이 없습니다: " + ", ".join(missing))

    share_columns = _columns(connection, "share_links")
    if "key_hash" not in share_columns or "key" in share_columns:
        raise ReadinessError("공유 링크 키가 해시 전용 스키마가 아닙니다.")
    session_columns = _columns(connection, "sessions")
    if "token_hash" not in session_columns or "token" in session_columns:
        raise ReadinessError("세션 토큰이 해시 전용 스키마가 아닙니다.")
    return tables


def _normalized_schema_sql(value: object) -> str:
    return " ".join(str(value or "").split())


def _table_xinfo(
    connection: sqlite3.Connection, table: str
) -> tuple[tuple[object, ...], ...]:
    escaped = table.replace('"', '""')
    return tuple(
        (
            str(row[1]),
            str(row[2]).strip().upper(),
            int(row[3]),
            row[4],
            int(row[5]),
            int(row[6]),
        )
        for row in connection.execute(f'PRAGMA table_xinfo("{escaped}")')
    )


def _index_contract(
    connection: sqlite3.Connection, table: str
) -> dict[str, tuple[object, ...]]:
    escaped = table.replace('"', '""')
    contract: dict[str, tuple[object, ...]] = {}
    for row in connection.execute(f'PRAGMA index_list("{escaped}")'):
        name = str(row[1])
        escaped_name = name.replace('"', '""')
        key_columns = tuple(
            (
                int(item[1]),
                "" if item[2] is None else str(item[2]),
                int(item[3]),
                str(item[4] or ""),
            )
            for item in connection.execute(f'PRAGMA index_xinfo("{escaped_name}")')
            if int(item[5]) == 1
        )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
        contract[name] = (
            int(row[2]),
            str(row[3]),
            int(row[4]),
            key_columns,
            _normalized_schema_sql(None if sql_row is None else sql_row[0]),
        )
    return contract


def _trigger_contract(connection: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    return {
        str(row[0]): (str(row[1]), _normalized_schema_sql(row[2]))
        for row in connection.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master "
            "WHERE type='trigger' ORDER BY name"
        )
    }


def _allowed_prebootstrap_missing_tables(
    connection: sqlite3.Connection,
    *,
    storage_db: object,
    missing_tables: set[str],
) -> set[str]:
    """실제 storage 런타임이 명시적으로 복구하는 중단 상태만 허용한다."""

    sessions_table = "sessions"
    legacy_table = str(
        getattr(storage_db, "_LEGACY_SESSIONS_TABLE", "sessions_legacy_raw_token")
    )
    session_state = getattr(storage_db, "_session_table_state", None)
    if missing_tables != {sessions_table} or not callable(session_state):
        return set()
    try:
        current_state = session_state(connection, sessions_table)
        legacy_state = session_state(connection, legacy_table)
    except (RuntimeError, sqlite3.Error):
        return set()
    if current_state == "missing" and legacy_state == "raw":
        return {sessions_table}
    return set()


def _load_storage_db_module():
    app_root = Path(__file__).resolve().parents[1] / "app"
    expected_module = app_root / "src" / "features" / "storage" / "db.py"
    if not expected_module.is_file():
        raise ReadinessError("앱 storage schema 모듈을 저장소의 고정 경로에서 찾지 못했습니다.")
    app_root_text = str(app_root)
    while app_root_text in sys.path:
        sys.path.remove(app_root_text)
    sys.path.insert(0, app_root_text)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module("src.features.storage.db")
    except Exception as exc:  # noqa: BLE001 — 앱 bootstrap 실패를 한 경계로 닫는다
        raise ReadinessError(
            "앱 storage schema bootstrap을 불러오지 못했습니다. "
            "저장소의 고정 app 경계와 앱 런타임 의존성을 확인하세요."
        ) from exc
    if not callable(getattr(module, "connect", None)):
        raise ReadinessError("앱 storage schema bootstrap 계약이 없습니다.")
    loaded_path = Path(str(getattr(module, "__file__", ""))).resolve()
    if loaded_path != expected_module.resolve():
        raise ReadinessError("앱 storage schema 모듈이 저장소의 고정 경계와 다릅니다.")
    return module


def _copy_database(source: Path, target: Path) -> None:
    try:
        with closing(_readonly_connection(source)) as source_connection:
            with closing(
                sqlite3.connect(str(target), timeout=SQLITE_TIMEOUT_SEC)
            ) as target_connection:
                source_connection.backup(target_connection)
        with target.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, sqlite3.Error) as exc:
        raise ReadinessError("격리 SQLite 복사본을 만들지 못했습니다.") from exc


def _assert_bootstrapped_schema(
    candidate: Path,
    *,
    canonical_parent: Path,
) -> AppSchemaInventory:
    storage_db = _load_storage_db_module()
    canonical = canonical_parent / "canonical-storage.sqlite3"
    if canonical.exists():
        raise ReadinessError("canonical schema 경로가 비어 있지 않습니다.")
    try:
        with storage_db.connect(canonical):
            pass
    except Exception as exc:  # noqa: BLE001 — feature별 migration 실패를 닫는다
        raise ReadinessError(
            "현재 앱의 canonical storage schema를 만들지 못했습니다."
        ) from exc

    try:
        with closing(_readonly_connection(canonical)) as canonical_connection:
            required_tables = _assert_sqlite_integrity(canonical_connection)
            required_table_xinfo = {
                table: _table_xinfo(canonical_connection, table)
                for table in required_tables
            }
            required_indexes = {
                table: _index_contract(canonical_connection, table)
                for table in required_tables
            }
            required_triggers = _trigger_contract(canonical_connection)
        with closing(_readonly_connection(candidate)) as prebootstrap_connection:
            prebootstrap_tables = _assert_sqlite_integrity(prebootstrap_connection)
            missing_before_bootstrap = required_tables - prebootstrap_tables
            allowed_missing = _allowed_prebootstrap_missing_tables(
                prebootstrap_connection,
                storage_db=storage_db,
                missing_tables=missing_before_bootstrap,
            )
    except sqlite3.Error as exc:
        raise ReadinessError("앱 storage schema 계약을 읽지 못했습니다.") from exc

    unapproved_missing = sorted(missing_before_bootstrap - allowed_missing)
    if unapproved_missing:
        raise ReadinessError(
            "bootstrap 전 백업에 canonical 필수 테이블이 없습니다: "
            + ", ".join(unapproved_missing)
        )

    try:
        with storage_db.connect(candidate):
            pass
    except Exception as exc:  # noqa: BLE001 — feature별 migration 실패를 닫는다
        raise ReadinessError(
            "격리 복사본이 앱 storage bootstrap/migration을 통과하지 못했습니다."
        ) from exc

    try:
        with closing(_readonly_connection(candidate)) as actual_connection:
            actual_tables = _assert_sqlite_integrity(actual_connection)
            actual_table_xinfo = {
                table: _table_xinfo(actual_connection, table)
                for table in actual_tables
            }
            actual_indexes = {
                table: _index_contract(actual_connection, table)
                for table in actual_tables
            }
            actual_triggers = _trigger_contract(actual_connection)
    except sqlite3.Error as exc:
        raise ReadinessError("bootstrap 뒤 앱 storage schema 계약을 읽지 못했습니다.") from exc

    missing_tables = sorted(required_tables - actual_tables)
    if missing_tables:
        raise ReadinessError(
            "앱 storage canonical 필수 테이블이 없습니다: " + ", ".join(missing_tables)
        )

    supported_session_signatures = tuple(
        getattr(storage_db, "_HASHED_SESSION_SCHEMAS", ())
    )
    for table in sorted(required_tables):
        actual_signature = actual_table_xinfo[table]
        required_signature = required_table_xinfo[table]
        if table == "sessions" and actual_signature in supported_session_signatures:
            pass
        elif actual_signature != required_signature:
            raise ReadinessError(
                f"앱 storage table_xinfo 계약이 맞지 않습니다: {table}"
            )

        actual_index_names = set(actual_indexes[table])
        required_index_names = set(required_indexes[table])
        if actual_index_names != required_index_names:
            unexpected = sorted(actual_index_names - required_index_names)
            missing = sorted(required_index_names - actual_index_names)
            details = []
            if missing:
                details.append("누락=" + ",".join(missing))
            if unexpected:
                details.append("추가=" + ",".join(unexpected))
            raise ReadinessError(
                f"앱 storage index 집합이 canonical과 다릅니다: {table} "
                + " ".join(details)
            )
        for name, required_index in required_indexes[table].items():
            if actual_indexes[table].get(name) != required_index:
                raise ReadinessError(
                    f"앱 storage 필수 index 계약이 맞지 않습니다: {name}"
                )

    if actual_triggers != required_triggers:
        actual_names = set(actual_triggers)
        required_names = set(required_triggers)
        changed = sorted(
            name
            for name in actual_names & required_names
            if actual_triggers[name] != required_triggers[name]
        )
        details = []
        missing = sorted(required_names - actual_names)
        unexpected = sorted(actual_names - required_names)
        if missing:
            details.append("누락=" + ",".join(missing))
        if unexpected:
            details.append("추가=" + ",".join(unexpected))
        if changed:
            details.append("변조=" + ",".join(changed))
        raise ReadinessError(
            "앱 storage trigger 집합이 canonical과 다릅니다: " + " ".join(details)
        )

    return AppSchemaInventory(
        table_count=len(required_tables),
        index_count=sum(len(value) for value in required_indexes.values()),
        trigger_count=len(required_triggers),
    )


def _assert_app_schema_compatible(
    source: Path,
    *,
    temp_parent: Path | None = None,
    expected_source_sha256: str | None = None,
) -> AppSchemaInventory:
    """원본이 아닌 격리 clone에서 현재 앱 bootstrap과 canonical 계약을 검증한다."""

    source_digest = sha256_file(source)
    if expected_source_sha256 is not None and not hmac.compare_digest(
        source_digest, expected_source_sha256
    ):
        raise ReadinessError("앱 schema 검증 전에 원본 백업 지문이 변경됐습니다.")
    parent = None if temp_parent is None else str(temp_parent.resolve(strict=True))
    temporary_path: Path | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="release-schema-bootstrap-", dir=parent
        ) as directory:
            temporary_path = Path(directory)
            candidate = temporary_path / "restored.sqlite3"
            _copy_database(source, candidate)
            inventory = _assert_bootstrapped_schema(
                candidate,
                canonical_parent=temporary_path,
            )
    finally:
        if sha256_file(source) != source_digest:
            raise ReadinessError("앱 schema 검증 중 원본 백업이 변경됐습니다.")
        if temporary_path is not None and temporary_path.exists():
            raise ReadinessError("앱 schema 검증 임시 디렉터리를 제거하지 못했습니다.")
    return inventory


def verify_backup(
    database_path: Path,
    checksum_path: Path,
    expected_sha256: str,
    *,
    manifest_gate: IndependentManifestGate | None = None,
    manifest_expectation: ManifestExpectation | None = None,
    manifest_data_root: Path | None = None,
) -> dict[str, object]:
    """sidecar·호환 해시와 별도 권한 경계의 서명 manifest를 모두 대조한다."""
    if manifest_gate is None or manifest_expectation is None:
        raise ReadinessError(
            "독립 서명 manifest gate와 신뢰 checkpoint가 없어 검증을 중단합니다."
        )
    database = _regular_file(database_path, label="백업 DB")
    checksum_file = _regular_file(checksum_path, label="체크섬 파일")
    sidecar_digest = _read_sidecar(checksum_file, database_name=database.name)
    checksum_object_digest = sha256_file(checksum_file)
    compatibility_digest = _valid_sha256(expected_sha256, label="호환 체크섬")
    actual_digest = sha256_file(database)
    if not hmac.compare_digest(actual_digest, sidecar_digest):
        raise ReadinessError("백업 DB와 같은 위치의 체크섬이 맞지 않습니다.")
    if not hmac.compare_digest(actual_digest, compatibility_digest):
        raise ReadinessError("백업 DB가 호출자 호환 체크섬과 맞지 않습니다.")
    database_size = database.stat().st_size
    try:
        manifest_record = manifest_gate.verify(
            expectation=manifest_expectation,
            database_name=database.name,
            database_sha256=actual_digest,
            database_size_bytes=database_size,
            checksum_sha256=checksum_object_digest,
            data_root=manifest_data_root or database.parent,
        )
    except ManifestError as exc:
        raise ReadinessError(f"독립 manifest gate가 거부했습니다: {exc}") from exc
    try:
        with closing(_readonly_connection(database)) as connection:
            _assert_sqlite_integrity(connection)
    except sqlite3.Error as exc:
        raise ReadinessError("백업 DB를 읽기 전용으로 검증하지 못했습니다.") from exc
    app_schema = _assert_app_schema_compatible(
        database,
        expected_source_sha256=actual_digest,
    )
    return {
        "status": "통과",
        "sha256": actual_digest,
        "size_bytes": database_size,
        "table_count": app_schema.table_count,
        "index_count": app_schema.index_count,
        "trigger_count": app_schema.trigger_count,
        "manifest_backup_id": manifest_record.backup_id,
        "manifest_sequence": manifest_record.sequence,
        "manifest_key_id": manifest_record.key_id,
    }


def _inventory(path: Path) -> DatabaseInventory:
    try:
        with closing(_readonly_connection(path)) as connection:
            tables = _assert_sqlite_integrity(connection)
            counts: dict[str, int] = {}
            for table in sorted(tables):
                escaped = table.replace('"', '""')
                row = connection.execute(
                    f'SELECT COUNT(*) FROM "{escaped}"'
                ).fetchone()
                counts[table] = int(row[0]) if row is not None else 0
    except sqlite3.Error as exc:
        raise ReadinessError("DB 레코드 수를 검증하지 못했습니다.") from exc
    return DatabaseInventory(tuple(sorted(tables)), counts)


def restore_dry_run(
    database_path: Path,
    checksum_path: Path,
    expected_sha256: str,
    *,
    temp_parent: Path | None = None,
    manifest_gate: IndependentManifestGate | None = None,
    manifest_expectation: ManifestExpectation | None = None,
    manifest_data_root: Path | None = None,
) -> dict[str, object]:
    """임시 복사본으로만 SQLite 복구 가능성과 완전성을 확인한다."""
    verified = verify_backup(
        database_path,
        checksum_path,
        expected_sha256,
        manifest_gate=manifest_gate,
        manifest_expectation=manifest_expectation,
        manifest_data_root=manifest_data_root,
    )
    source = _regular_file(database_path, label="백업 DB")
    parent: str | None = None
    if temp_parent is not None:
        resolved_parent = temp_parent.expanduser().resolve(strict=True)
        if not resolved_parent.is_dir():
            raise ReadinessError("임시 복구 상위 경로는 디렉터리여야 합니다.")
        parent = str(resolved_parent)

    source_inventory = _inventory(source)
    source_digest = sha256_file(source)
    if not hmac.compare_digest(source_digest, str(verified["sha256"])):
        raise ReadinessError("임시 복구 전에 원본 백업 지문이 변경됐습니다.")
    temporary_path: Path | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="release-restore-dry-run-", dir=parent
        ) as directory:
            temporary_path = Path(directory)
            restored = temporary_path / "restored.sqlite3"
            try:
                with closing(_readonly_connection(source)) as source_connection:
                    with closing(
                        sqlite3.connect(str(restored), timeout=SQLITE_TIMEOUT_SEC)
                    ) as target_connection:
                        source_connection.backup(target_connection)
                with restored.open("rb+") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
            except (OSError, sqlite3.Error) as exc:
                raise ReadinessError("임시 복사본 복구에 실패했습니다.") from exc

            restored_inventory = _inventory(restored)
            if source_inventory != restored_inventory:
                raise ReadinessError("임시 복사본의 스키마 또는 레코드 수가 다릅니다.")
            restored_digest = sha256_file(restored)
            app_schema = _assert_bootstrapped_schema(
                restored,
                canonical_parent=temporary_path,
            )
            result = {
                **verified,
                "status": "임시 복구 통과",
                "restored_sha256": restored_digest,
                "row_count": sum(restored_inventory.row_counts.values()),
                "restored_app_table_count": app_schema.table_count,
                "restored_app_index_count": app_schema.index_count,
                "restored_app_trigger_count": app_schema.trigger_count,
            }
    finally:
        if sha256_file(source) != source_digest:
            raise ReadinessError("임시 복구 검증 중 원본 백업이 변경됐습니다.")
        if temporary_path is not None and temporary_path.exists():
            raise ReadinessError("임시 복구 디렉터리를 제거하지 못했습니다.")
    return result


def _single_count(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0]) if row is not None else 0


def _database_under_root(database_path: Path, data_root: Path) -> tuple[Path, Path]:
    database = _regular_file(database_path, label="운영 DB")
    try:
        root = data_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ReadinessError("데이터 루트를 찾을 수 없습니다.") from exc
    if not root.is_dir() or not database.is_relative_to(root):
        raise ReadinessError("운영 DB는 지정한 데이터 루트 안에 있어야 합니다.")
    return database, root


def preflight(
    database_path: Path,
    data_root: Path,
    *,
    require_maintenance: bool,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    max_disk_used_percent: float = DEFAULT_MAX_DISK_USED_PERCENT,
    max_database_bytes: int = DEFAULT_MAX_DATABASE_BYTES,
    max_wal_bytes: int = DEFAULT_MAX_WAL_BYTES,
    max_link_open_events: int = DEFAULT_MAX_LINK_OPEN_EVENTS,
) -> dict[str, object]:
    """운영 DB·디스크·고아 가능 상태를 변경 없이 집계한다."""
    database, root = _database_under_root(database_path, data_root)
    blockers: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    try:
        with closing(_readonly_connection(database)) as connection:
            _assert_database(connection)
            for label, sql in ACTIVE_QUERIES:
                counts[label] = _single_count(connection, sql)
                if counts[label] > 0:
                    blockers.append(f"{label} {counts[label]}건이 남아 있습니다.")
            counts["작업 중단 표식"] = _single_count(
                connection, "SELECT COUNT(*) FROM job_interruptions"
            )
            if counts["작업 중단 표식"] > 0:
                warnings.append("작업 중단 표식의 원인과 재시작 복구 결과를 확인하세요.")
            counts["공유 링크 열람 이벤트"] = _single_count(
                connection, "SELECT COUNT(*) FROM share_link_open_events"
            )
            if counts["공유 링크 열람 이벤트"] > max_link_open_events:
                blockers.append("공유 링크 열람 이벤트가 운영 한도를 넘었습니다.")
            state_row = connection.execute(
                "SELECT status FROM dashboard_service_state WHERE singleton = 1"
            ).fetchone()
            service_state = "없음" if state_row is None else str(state_row[0])
    except sqlite3.Error as exc:
        raise ReadinessError("운영 DB preflight를 실행하지 못했습니다.") from exc

    if require_maintenance and service_state != "maintenance":
        blockers.append("서비스가 maintenance 상태가 아닙니다.")

    disk = shutil.disk_usage(root)
    used_percent = 100.0 * disk.used / disk.total if disk.total else 100.0
    database_bytes = database.stat().st_size
    wal_path = database.with_name(database.name + "-wal")
    shm_path = database.with_name(database.name + "-shm")
    wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
    shm_bytes = shm_path.stat().st_size if shm_path.is_file() else 0
    if disk.free < min_free_bytes:
        blockers.append("데이터 디스크 여유 공간이 하한보다 작습니다.")
    if used_percent > max_disk_used_percent:
        blockers.append("데이터 디스크 사용률이 상한보다 큽니다.")
    if database_bytes > max_database_bytes:
        blockers.append("운영 DB 크기가 사전 합의한 상한보다 큽니다.")
    if wal_bytes > max_wal_bytes:
        blockers.append("WAL 크기가 사전 합의한 상한보다 큽니다.")

    return {
        "status": "차단" if blockers else "통과",
        "service_state": service_state,
        "counts": counts,
        "disk": {
            "total_bytes": disk.total,
            "free_bytes": disk.free,
            "used_percent": round(used_percent, 2),
            "database_bytes": database_bytes,
            "wal_bytes": wal_bytes,
            "shm_bytes": shm_bytes,
        },
        "blockers": blockers,
        "warnings": warnings,
    }


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0 이상의 정수여야 합니다.")
    return number


def _percent(value: str) -> float:
    number = float(value)
    if not 0 < number <= 100:
        raise argparse.ArgumentTypeError("0 초과 100 이하의 수여야 합니다.")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="배포·복구 준비 상태를 안전하게 검증합니다.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("verify-backup", "restore-dry-run"):
        child = subparsers.add_parser(name)
        child.add_argument("--database", type=Path, required=True)
        child.add_argument("--checksum", type=Path, required=True)
        child.add_argument("--expected-sha256", required=True)
        # sink와 signer 자체는 외부 자격증명 조회 없이 main() 호출자가 주입한다.
        child.add_argument("--manifest-backup-id")
        child.add_argument("--manifest-scope", default="storage-db")
        child.add_argument("--manifest-storage-provider", default="s3")
        child.add_argument("--manifest-storage-bucket")
        child.add_argument("--manifest-object-key")
        child.add_argument("--manifest-checksum-key")
        child.add_argument("--data-boundary-id")
        child.add_argument("--data-authority-id")
        child.add_argument("--manifest-min-sequence", type=_positive_int)
        child.add_argument("--manifest-max-age-seconds", type=_positive_int)
        child.add_argument("--manifest-data-root", type=Path)
        if name == "restore-dry-run":
            child.add_argument("--temp-parent", type=Path)

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--database", type=Path, required=True)
    preflight_parser.add_argument("--data-root", type=Path, required=True)
    preflight_parser.add_argument("--require-maintenance", action="store_true")
    preflight_parser.add_argument("--min-free-bytes", type=_positive_int, default=DEFAULT_MIN_FREE_BYTES)
    preflight_parser.add_argument("--max-disk-used-percent", type=_percent, default=DEFAULT_MAX_DISK_USED_PERCENT)
    preflight_parser.add_argument("--max-database-bytes", type=_positive_int, default=DEFAULT_MAX_DATABASE_BYTES)
    preflight_parser.add_argument("--max-wal-bytes", type=_positive_int, default=DEFAULT_MAX_WAL_BYTES)
    preflight_parser.add_argument("--max-link-open-events", type=_positive_int, default=DEFAULT_MAX_LINK_OPEN_EVENTS)
    return parser


def _manifest_expectation_from_args(arguments: argparse.Namespace) -> ManifestExpectation:
    required = {
        "manifest-backup-id": arguments.manifest_backup_id,
        "manifest-storage-bucket": arguments.manifest_storage_bucket,
        "manifest-object-key": arguments.manifest_object_key,
        "manifest-checksum-key": arguments.manifest_checksum_key,
        "data-boundary-id": arguments.data_boundary_id,
        "data-authority-id": arguments.data_authority_id,
        "manifest-min-sequence": arguments.manifest_min_sequence,
    }
    missing = [name for name, value in required.items() if value in (None, "")]
    if missing:
        raise ReadinessError("manifest gate 입력이 없습니다: " + ", ".join(missing))
    return ManifestExpectation(
        backup_id=arguments.manifest_backup_id,
        scope=arguments.manifest_scope,
        storage_provider=arguments.manifest_storage_provider,
        storage_bucket=arguments.manifest_storage_bucket,
        object_key=arguments.manifest_object_key,
        checksum_key=arguments.manifest_checksum_key,
        data_boundary_id=arguments.data_boundary_id,
        data_authority_id=arguments.data_authority_id,
        minimum_sequence=arguments.manifest_min_sequence,
        max_age_seconds=arguments.manifest_max_age_seconds,
    )


def main(
    argv: Iterable[str] | None = None,
    *,
    manifest_gate: IndependentManifestGate | None = None,
) -> int:
    arguments = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if arguments.command == "verify-backup":
            expectation = _manifest_expectation_from_args(arguments)
            result = verify_backup(
                arguments.database,
                arguments.checksum,
                arguments.expected_sha256,
                manifest_gate=manifest_gate,
                manifest_expectation=expectation,
                manifest_data_root=arguments.manifest_data_root,
            )
        elif arguments.command == "restore-dry-run":
            expectation = _manifest_expectation_from_args(arguments)
            result = restore_dry_run(
                arguments.database,
                arguments.checksum,
                arguments.expected_sha256,
                temp_parent=arguments.temp_parent,
                manifest_gate=manifest_gate,
                manifest_expectation=expectation,
                manifest_data_root=arguments.manifest_data_root,
            )
        else:
            result = preflight(
                arguments.database,
                arguments.data_root,
                require_maintenance=arguments.require_maintenance,
                min_free_bytes=arguments.min_free_bytes,
                max_disk_used_percent=arguments.max_disk_used_percent,
                max_database_bytes=arguments.max_database_bytes,
                max_wal_bytes=arguments.max_wal_bytes,
                max_link_open_events=arguments.max_link_open_events,
            )
    except (ReadinessError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "오류", "message": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("status") == "차단" else 0


if __name__ == "__main__":
    sys.exit(main())
