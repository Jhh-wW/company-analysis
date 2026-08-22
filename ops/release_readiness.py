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
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import types
from contextlib import closing, contextmanager
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Final,
    Iterable,
    Iterator,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
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


_PERSISTED_JSON_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "src" / "core" / "persisted_json.py"
)
_persisted_json_spec = importlib.util.spec_from_file_location(
    "_release_persisted_json_contract",
    _PERSISTED_JSON_CONTRACT_PATH,
)
if _persisted_json_spec is None or _persisted_json_spec.loader is None:
    raise RuntimeError("저장 JSON 공통 계약을 고정 app 경계에서 읽지 못했습니다")
_PERSISTED_JSON_CONTRACT = importlib.util.module_from_spec(_persisted_json_spec)
_persisted_json_spec.loader.exec_module(_PERSISTED_JSON_CONTRACT)


HASH_CHUNK_BYTES: Final[int] = 1024 * 1024
CHECKSUM_MAX_BYTES: Final[int] = 4096
SQLITE_TIMEOUT_SEC: Final[float] = 10.0
DEFAULT_MIN_FREE_BYTES: Final[int] = 256 * 1024 * 1024
DEFAULT_MAX_DISK_USED_PERCENT: Final[float] = 85.0
DEFAULT_MAX_DATABASE_BYTES: Final[int] = 768 * 1024 * 1024
DEFAULT_MAX_WAL_BYTES: Final[int] = 128 * 1024 * 1024
DEFAULT_MAX_LINK_OPEN_EVENTS: Final[int] = 100_000
MAX_JSON_FIELD_BYTES: Final[int] = _PERSISTED_JSON_CONTRACT.MAX_FIELD_BYTES
MAX_JSON_TOTAL_BYTES: Final[int] = 256 * 1024 * 1024
MAX_JSON_TOTAL_ROWS: Final[int] = 100_000
JSON_FETCH_BATCH_ROWS: Final[int] = 128
JSON_PAYLOAD_FETCH_ROWS: Final[int] = 1
MAX_JSON_DOCUMENT_NODES: Final[int] = _PERSISTED_JSON_CONTRACT.MAX_DOCUMENT_NODES
MAX_JSON_DOCUMENT_CONTAINER_ITEMS: Final[int] = (
    _PERSISTED_JSON_CONTRACT.MAX_DOCUMENT_CONTAINER_ITEMS
)
MAX_JSON_CONTAINER_ITEMS: Final[int] = _PERSISTED_JSON_CONTRACT.MAX_CONTAINER_ITEMS
MAX_JSON_DOCUMENT_DEPTH: Final[int] = _PERSISTED_JSON_CONTRACT.MAX_DOCUMENT_DEPTH
JSON_STRUCTURE_CHECK_INTERVAL: Final[int] = _PERSISTED_JSON_CONTRACT.CHECK_INTERVAL
PAYLOAD_VALIDATION_DEADLINE_SEC: Final[float] = 30.0
MAX_REPORT_RUNTIME_TYPE_DEPTH: Final[int] = 64
SQLITE_PROGRESS_OPCODES: Final[int] = 1_000
MIN_CLONE_FREE_HEADROOM_BYTES: Final[int] = 64 * 1024 * 1024
SQLITE_COMPANION_SUFFIXES: Final[tuple[str, ...]] = ("-wal", "-shm", "-journal")
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


class _RuntimeTypeContractError(TypeError):
    """payload 값 자체를 노출하지 않는 내부 runtime 타입 불일치."""


@dataclass(frozen=True)
class DatabaseInventory:
    tables: tuple[str, ...]
    row_counts: dict[str, int]


@dataclass(frozen=True)
class AppSchemaInventory:
    table_count: int
    index_count: int
    trigger_count: int


@dataclass(frozen=True)
class TableSemanticContract:
    normalized_create_sql: str
    foreign_keys: tuple[tuple[object, ...], ...]
    without_rowid: int
    strict: int


@dataclass(frozen=True)
class SessionVersionedTableContract:
    version: str
    state: str
    table_xinfo: tuple[tuple[object, ...], ...]
    semantics: TableSemanticContract


@dataclass
class PayloadValidationBudget:
    deadline: float
    rows: int = 0
    bytes: int = 0

    @classmethod
    def start(cls) -> "PayloadValidationBudget":
        return cls(time.monotonic() + PAYLOAD_VALIDATION_DEADLINE_SEC)

    def check_deadline(self) -> None:
        if time.monotonic() >= self.deadline:
            raise ReadinessError("payload 전수 검증 제한시간을 넘었습니다.")

    def add(self, *, rows: int, bytes_count: int) -> None:
        self.rows += rows
        self.bytes += bytes_count
        if self.rows > MAX_JSON_TOTAL_ROWS:
            raise ReadinessError("JSON 전수 검증 행 상한을 넘었습니다.")
        if self.bytes > MAX_JSON_TOTAL_BYTES:
            raise ReadinessError("JSON 전수 검증 총 바이트 상한을 넘었습니다.")
        self.check_deadline()


@dataclass(frozen=True)
class RuntimePayloadConsumers:
    reports: object
    cache: object
    dashboard: object
    lifecycle: object
    publish: object
    pdf_release: object
    admin_audit: object


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


def _assert_no_sqlite_companions(path: Path, *, label: str) -> None:
    """manifest에 결속되지 않은 SQLite sidecar가 하나라도 있으면 닫는다."""

    for suffix in SQLITE_COMPANION_SUFFIXES:
        companion = Path(str(path) + suffix)
        try:
            os.lstat(companion)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ReadinessError(f"{label} SQLite sidecar 상태를 확인하지 못했습니다.") from exc
        raise ReadinessError(
            f"{label} 옆에 manifest가 결속하지 않은 SQLite {suffix} sidecar가 있습니다."
        )


def _stable_database_digest(path: Path, *, label: str) -> str:
    _assert_no_sqlite_companions(path, label=label)
    digest = sha256_file(path)
    _assert_no_sqlite_companions(path, label=label)
    return digest


def _assert_database_size(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReadinessError("백업 DB 크기를 읽지 못했습니다.") from exc
    if size > DEFAULT_MAX_DATABASE_BYTES:
        raise ReadinessError("백업 DB가 복구 검증 허용 크기를 넘었습니다.")
    return size


def _assert_clone_space(parent: Path, *, source_size: int, copies: int) -> None:
    try:
        resolved = parent.expanduser().resolve(strict=True)
        free = shutil.disk_usage(resolved).free
    except OSError as exc:
        raise ReadinessError("격리 복사본 여유 공간을 확인하지 못했습니다.") from exc
    required = source_size * copies + MIN_CLONE_FREE_HEADROOM_BYTES
    if free < required:
        raise ReadinessError("격리 복사본과 canonical DB를 만들 여유 공간이 부족합니다.")


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
    """고정 백업/clone main bytes만 여는 엄격한 읽기 전용 연결."""

    _assert_no_sqlite_companions(path, label="검증 DB")
    uri_path = quote(path.resolve().as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{uri_path}?mode=ro&immutable=1",
        uri=True,
        timeout=SQLITE_TIMEOUT_SEC,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        _assert_no_sqlite_companions(path, label="검증 DB")
    except BaseException:
        connection.close()
        raise
    return connection


def _live_readonly_connection(path: Path) -> sqlite3.Connection:
    """정상 WAL sidecar를 포함한 운영 DB snapshot을 읽기 전용으로 연다.

    백업과 격리 clone은 manifest에 결속된 main bytes만 허용해야 하므로
    ``_readonly_connection``의 sidecar 거부와 immutable 모드를 그대로 쓴다.
    반면 실행 중인 운영 DB의 ``-wal``/``-shm``은 정상 상태일 수 있다. 여기서는
    SQLite가 WAL까지 포함한 일관된 snapshot을 만들도록 일반 ``mode=ro`` 연결과
    명시적 읽기 transaction을 사용한다.
    """

    uri_path = quote(path.resolve().as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{uri_path}?mode=ro",
        uri=True,
        timeout=SQLITE_TIMEOUT_SEC,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN")
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def _bounded_sqlite(
    connection: sqlite3.Connection,
    budget: PayloadValidationBudget,
) -> Iterator[None]:
    """SQLite VM과 Python 소비자 검증이 같은 wall-clock deadline을 쓴다."""

    def interrupted() -> int:
        return int(time.monotonic() >= budget.deadline)

    connection.set_progress_handler(interrupted, SQLITE_PROGRESS_OPCODES)
    try:
        budget.check_deadline()
        yield
        budget.check_deadline()
    except sqlite3.OperationalError as exc:
        if time.monotonic() >= budget.deadline or "interrupt" in str(exc).lower():
            raise ReadinessError("payload SQLite 검증 제한시간을 넘었습니다.") from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


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
    """문자열 literal 내부는 보존하고 SQL의 비의미 공백만 정규화한다."""

    source = str(value or "")
    normalized: list[str] = []
    pending_space = False
    quote_end = ""
    index = 0
    while index < len(source):
        character = source[index]
        if quote_end:
            normalized.append(character)
            if character == quote_end:
                if (
                    quote_end in {"'", '"', "`"}
                    and index + 1 < len(source)
                    and source[index + 1] == quote_end
                ):
                    normalized.append(source[index + 1])
                    index += 1
                else:
                    quote_end = ""
            index += 1
            continue
        if source.startswith("--", index):
            line_end = index + 2
            while line_end < len(source) and source[line_end] not in "\r\n":
                line_end += 1
            index = line_end
            pending_space = True
            continue
        if source.startswith("/*", index):
            block_end = source.find("*/", index + 2)
            if block_end < 0:
                raise ReadinessError("CREATE TABLE SQL 주석이 완결되지 않았습니다.")
            index = block_end + 2
            pending_space = True
            continue
        if character.isspace():
            pending_space = True
            index += 1
            continue
        if character in {"'", '"', "`"}:
            if pending_space and normalized and normalized[-1] not in "(,":
                normalized.append(" ")
            normalized.append(character)
            quote_end = character
            pending_space = False
            index += 1
            continue
        if character == "[":
            if pending_space and normalized and normalized[-1] not in "(,":
                normalized.append(" ")
            normalized.append(character)
            quote_end = "]"
            pending_space = False
            index += 1
            continue
        if character in "(),":
            while normalized and normalized[-1] == " ":
                normalized.pop()
            normalized.append(character)
            pending_space = False
            index += 1
            continue
        if pending_space and normalized and normalized[-1] not in "(,":
            normalized.append(" ")
        normalized.append(character)
        pending_space = False
        index += 1
    return "".join(normalized).strip()


_CREATE_TABLE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+"
    r'(?:"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]+\]|[^\s(]+)',
    re.IGNORECASE,
)


def _normalized_create_table_sql(value: object) -> str:
    normalized = _normalized_schema_sql(value)
    if not _CREATE_TABLE_PREFIX_RE.match(normalized):
        raise ReadinessError("CREATE TABLE SQL 계약을 정규화할 수 없습니다.")
    return _CREATE_TABLE_PREFIX_RE.sub("CREATE TABLE <TABLE>", normalized, count=1)


def _foreign_key_contract(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[tuple[object, ...], ...]:
    escaped = table.replace('"', '""')
    return tuple(
        sorted(
            (
                int(row[0]),
                int(row[1]),
                str(row[2]),
                str(row[3]),
                "" if row[4] is None else str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
            )
            for row in connection.execute(f'PRAGMA foreign_key_list("{escaped}")')
        )
    )


def _table_storage_flags(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[int, int]:
    matches = [
        row
        for row in connection.execute("PRAGMA table_list")
        if str(row[0]) == "main"
        and str(row[1]) == table
        and str(row[2]) == "table"
    ]
    if len(matches) != 1 or len(matches[0]) < 6:
        raise ReadinessError(f"SQLite table_list 계약을 읽지 못했습니다: {table}")
    return int(matches[0][4]), int(matches[0][5])


def _table_semantic_contract(
    connection: sqlite3.Connection,
    table: str,
) -> TableSemanticContract:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None or row[0] is None:
        raise ReadinessError(f"CREATE TABLE SQL 계약이 없습니다: {table}")
    without_rowid, strict = _table_storage_flags(connection, table)
    return TableSemanticContract(
        normalized_create_sql=_normalized_create_table_sql(row[0]),
        foreign_keys=_foreign_key_contract(connection, table),
        without_rowid=without_rowid,
        strict=strict,
    )


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


def _session_create_sql_from_signature(
    signature: tuple[tuple[object, ...], ...],
) -> str:
    columns: list[str] = []
    for column in signature:
        if len(column) != 6:
            raise ReadinessError("sessions version signature 열 계약이 올바르지 않습니다.")
        name, data_type, not_null, default, primary_key, hidden = column
        if int(hidden) != 0:
            raise ReadinessError("sessions version은 hidden/generated 열을 허용하지 않습니다.")
        definition = [str(name), str(data_type).strip().upper()]
        if int(not_null):
            definition.append("NOT NULL")
        if default is not None:
            definition.append("DEFAULT " + str(default))
        if int(primary_key):
            definition.append("PRIMARY KEY")
        columns.append(" ".join(definition))
    return _normalized_create_table_sql(
        "CREATE TABLE sessions (" + ", ".join(columns) + ")"
    )


def _session_versioned_contracts(
    storage_db: object,
    *,
    state: str,
) -> tuple[SessionVersionedTableContract, ...]:
    attribute = {
        "raw": "_RAW_SESSION_SCHEMAS",
        "hashed": "_HASHED_SESSION_SCHEMAS",
    }.get(state)
    if attribute is None:
        raise ReadinessError("지원하지 않는 sessions version 상태입니다.")
    signatures = tuple(getattr(storage_db, attribute, ()))
    if not signatures:
        raise ReadinessError("현재 storage 런타임의 sessions version 계약이 없습니다.")
    contracts: list[SessionVersionedTableContract] = []
    for index, signature in enumerate(signatures, start=1):
        normalized_signature = tuple(tuple(column) for column in signature)
        contracts.append(
            SessionVersionedTableContract(
                version=f"{state}-v{index}",
                state=state,
                table_xinfo=normalized_signature,
                semantics=TableSemanticContract(
                    normalized_create_sql=_session_create_sql_from_signature(
                        normalized_signature
                    ),
                    foreign_keys=(),
                    without_rowid=0,
                    strict=0,
                ),
            )
        )
    return tuple(contracts)


def _assert_session_table_version(
    connection: sqlite3.Connection,
    *,
    table: str,
    state: str,
    storage_db: object,
) -> None:
    actual_xinfo = _table_xinfo(connection, table)
    actual_semantics = _table_semantic_contract(connection, table)
    supported = _session_versioned_contracts(storage_db, state=state)
    if not any(
        actual_xinfo == version.table_xinfo
        and actual_semantics == version.semantics
        for version in supported
    ):
        raise ReadinessError(
            f"sessions {state} versioned table 계약이 맞지 않습니다: {table}"
        )


def _allowed_prebootstrap_missing_tables(
    connection: sqlite3.Connection,
    *,
    storage_db: object,
    missing_tables: set[str],
) -> set[str]:
    """실제 storage 런타임이 명시적으로 복구하는 sessions 중단만 허용한다.

    관리자 감사 표를 비롯한 새 필수 표가 없는 구백업은 삭제 공격과 구분할
    서명된 schema generation이 없으므로 영구 migration 예외 없이 차단한다.
    """

    sessions_table = "sessions"
    legacy_table = str(
        getattr(storage_db, "_LEGACY_SESSIONS_TABLE", "sessions_legacy_raw_token")
    )
    session_state = getattr(storage_db, "_session_table_state", None)
    if not callable(session_state):
        raise ReadinessError("현재 storage 런타임의 sessions 상태 계약이 없습니다.")
    try:
        current_state = session_state(connection, sessions_table)
        legacy_state = session_state(connection, legacy_table)
    except (RuntimeError, sqlite3.Error) as exc:
        raise ReadinessError("bootstrap 전 sessions 상태를 판정하지 못했습니다.") from exc

    allowed_states = {
        ("raw", "missing"),
        ("hashed", "missing"),
        ("hashed", "raw"),
        ("missing", "raw"),
    }
    if (current_state, legacy_state) not in allowed_states:
        raise ReadinessError(
            "bootstrap 전 지원하지 않는 sessions 마이그레이션 상태입니다 "
            f"(sessions={current_state}, legacy={legacy_state})"
        )
    if current_state in {"raw", "hashed"}:
        _assert_session_table_version(
            connection,
            table=sessions_table,
            state=current_state,
            storage_db=storage_db,
        )
    if legacy_state == "raw":
        _assert_session_table_version(
            connection,
            table=legacy_table,
            state="raw",
            storage_db=storage_db,
        )

    if (
        missing_tables == {sessions_table}
        and current_state == "missing"
        and legacy_state == "raw"
    ):
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


def _load_feature_schema_bootstraps() -> tuple[
    tuple[str, Callable[[sqlite3.Connection], None]], ...
]:
    """앱 runtime과 같은 단일 영속 schema registry를 고정 경계에서 읽는다."""

    app_root = Path(__file__).resolve().parents[1] / "app"
    expected_module = app_root / "src" / "core" / "persistent_schema.py"
    try:
        module = importlib.import_module("src.core.persistent_schema")
    except Exception as exc:  # noqa: BLE001 — registry 부재를 한 경계로 닫는다
        raise ReadinessError("앱 영속 schema registry를 불러오지 못했습니다.") from exc
    if Path(str(getattr(module, "__file__", ""))).resolve() != expected_module.resolve():
        raise ReadinessError("앱 영속 schema registry가 저장소의 고정 경계와 다릅니다.")
    loader = getattr(module, "load_persistent_schema_bootstraps", None)
    if not callable(loader):
        raise ReadinessError("앱 영속 schema registry loader 계약이 없습니다.")
    try:
        bootstraps = tuple(loader())
    except Exception as exc:  # noqa: BLE001 — 항목 path/callable 검증 실패를 닫는다
        raise ReadinessError("앱 영속 schema registry 항목 검증에 실패했습니다.") from exc
    if not bootstraps:
        raise ReadinessError("앱 영속 schema registry가 비어 있습니다.")
    return bootstraps


def _apply_feature_schema_bootstraps(
    connection: sqlite3.Connection,
    bootstraps: tuple[tuple[str, Callable[[sqlite3.Connection], None]], ...],
) -> None:
    for label, ensure_schema in bootstraps:
        try:
            ensure_schema(connection)
        except Exception as exc:  # noqa: BLE001 — feature migration 실패를 한 경계로 닫는다
            raise ReadinessError(
                f"{label} feature schema bootstrap/migration에 실패했습니다."
            ) from exc


def _load_runtime_payload_consumers() -> RuntimePayloadConsumers:
    """payload를 실제 읽는 공개 API만 저장소의 고정 app 경계에서 읽는다."""

    app_root = Path(__file__).resolve().parents[1] / "app"
    specifications = {
        "reports": (
            "src.features.storage.reports",
            app_root / "src" / "features" / "storage" / "reports.py",
            ("load", "report_from_json", "report_to_dict"),
        ),
        "cache": (
            "src.features.storage.cache",
            app_root / "src" / "features" / "storage" / "cache.py",
            ("get_layer2",),
        ),
        "dashboard": (
            "src.features.admin_dashboard.store",
            app_root / "src" / "features" / "admin_dashboard" / "store.py",
            ("approved_report_payload",),
        ),
        "lifecycle": (
            "src.features.observability.lifecycle",
            app_root / "src" / "features" / "observability" / "lifecycle.py",
            ("read_final",),
        ),
        "publish": (
            "src.features.report_standard.publish",
            app_root / "src" / "features" / "report_standard" / "publish.py",
            ("validate_publishable",),
        ),
        "pdf_release": (
            "src.features.export_pdf.release_store",
            app_root / "src" / "features" / "export_pdf" / "release_store.py",
            ("validate_persisted_release_records",),
        ),
        "admin_audit": (
            "src.features.observability.admin_audit_store",
            app_root
            / "src"
            / "features"
            / "observability"
            / "admin_audit_store.py",
            ("validate_persisted_events",),
        ),
    }
    loaded: dict[str, object] = {}
    for key, (module_name, expected_path, callables) in specifications.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 — 소비자 부재를 fail-closed한다
            raise ReadinessError(f"{key} payload 소비자를 불러오지 못했습니다.") from exc
        if Path(str(getattr(module, "__file__", ""))).resolve() != expected_path.resolve():
            raise ReadinessError(f"{key} payload 소비자가 고정 app 경계와 다릅니다.")
        if any(not callable(getattr(module, name, None)) for name in callables):
            raise ReadinessError(f"{key} payload 소비자 계약이 없습니다.")
        loaded[key] = module
    return RuntimePayloadConsumers(**loaded)


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_text_columns(
    table_xinfo: dict[str, tuple[tuple[object, ...], ...]],
) -> tuple[tuple[str, str], ...]:
    columns: list[tuple[str, str]] = []
    for table, signature in sorted(table_xinfo.items()):
        for column in signature:
            name = str(column[0])
            data_type = str(column[1]).strip().upper()
            if data_type == "TEXT" and (
                name == "payload_json" or name.endswith("_json")
            ):
                columns.append((table, name))
    return tuple(columns)


def _assert_generic_json_columns(
    connection: sqlite3.Connection,
    *,
    columns: tuple[tuple[str, str], ...],
    budget: PayloadValidationBudget,
) -> None:
    """canonical의 모든 JSON TEXT 열을 표본 추출 없이 SQLite로 전수 검사한다."""

    for table, column in columns:
        budget.check_deadline()
        table_sql = _quoted_identifier(table)
        column_sql = _quoted_identifier(column)
        cursor = connection.execute(
            f"SELECT typeof({column_sql}), length(CAST({column_sql} AS BLOB)) "
            f"FROM {table_sql} "
            f"WHERE {column_sql} IS NOT NULL"
        )
        while rows := cursor.fetchmany(JSON_FETCH_BATCH_ROWS):
            for row in rows:
                value_type, byte_count = str(row[0]), int(row[1])
                if value_type != "text":
                    raise ReadinessError(
                        f"JSON TEXT 형식이 올바르지 않습니다: {table}.{column}"
                    )
                if byte_count > MAX_JSON_FIELD_BYTES:
                    raise ReadinessError(
                        f"JSON 필드가 개별 바이트 상한을 넘었습니다: {table}.{column}"
                    )
                budget.add(rows=1, bytes_count=byte_count)
        valid_cursor = connection.execute(
            f"SELECT json_valid({column_sql}) FROM {table_sql} "
            f"WHERE {column_sql} IS NOT NULL"
        )
        while rows := valid_cursor.fetchmany(JSON_FETCH_BATCH_ROWS):
            for row in rows:
                budget.check_deadline()
                if int(row[0]) != 1:
                    raise ReadinessError(
                        f"JSON TEXT 형식이 올바르지 않습니다: {table}.{column}"
                    )
        payload_cursor = connection.execute(
            f"SELECT {column_sql} FROM {table_sql} "
            f"WHERE {column_sql} IS NOT NULL"
        )
        while rows := payload_cursor.fetchmany(JSON_PAYLOAD_FETCH_ROWS):
            budget.check_deadline()
            _assert_json_document_structure(str(rows[0][0]), budget=budget)


def _assert_json_document_structure(
    payload: str,
    *,
    budget: PayloadValidationBudget,
) -> object:
    """소비자보다 먼저 한 JSON 문서의 깊이·노드·항목 수를 제한한다."""

    try:
        return _PERSISTED_JSON_CONTRACT.validate_persisted_json_text(
            payload,
            deadline_check=budget.check_deadline,
        )
    except (ValueError, RecursionError, MemoryError) as exc:
        raise ReadinessError("JSON 문서를 제한 안에서 해석하지 못했습니다.") from exc


def _assert_runtime_type(
    value: object,
    annotation: object,
    *,
    active_ids: set[int],
    depth: int,
    budget: PayloadValidationBudget,
    type_hints_cache: dict[type, dict[str, object]],
) -> None:
    """dataclass 주석을 실제 값에 재귀 적용하며 모르는 타입은 거부한다."""

    budget.check_deadline()
    if depth > MAX_REPORT_RUNTIME_TYPE_DEPTH:
        raise _RuntimeTypeContractError("runtime 타입 재귀 상한 초과")

    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if annotation is Any:
        raise _RuntimeTypeContractError("Any 주석은 복구 신뢰 근거로 쓸 수 없음")
    if annotation is object:
        if isinstance(value, type) or not is_dataclass(value):
            raise _RuntimeTypeContractError("object 값은 구체 dataclass여야 함")
        _assert_runtime_type(
            value,
            type(value),
            active_ids=active_ids,
            depth=depth + 1,
            budget=budget,
            type_hints_cache=type_hints_cache,
        )
        return
    if origin in (Union, types.UnionType):
        for candidate in arguments:
            try:
                _assert_runtime_type(
                    value,
                    candidate,
                    active_ids=active_ids,
                    depth=depth + 1,
                    budget=budget,
                    type_hints_cache=type_hints_cache,
                )
            except _RuntimeTypeContractError:
                continue
            return
        raise _RuntimeTypeContractError("Union의 어떤 타입과도 일치하지 않음")
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in arguments):
            raise _RuntimeTypeContractError("Literal과 일치하지 않음")
        return

    if origin in (list, dict, tuple):
        if type(value) is not origin:
            raise _RuntimeTypeContractError("container 타입 불일치")
        value_id = id(value)
        if value_id in active_ids:
            raise _RuntimeTypeContractError("순환 container는 허용하지 않음")
        active_ids.add(value_id)
        try:
            if origin is list:
                if len(arguments) != 1:
                    raise _RuntimeTypeContractError("list 원소 주석 누락")
                for item in value:  # type: ignore[union-attr]
                    _assert_runtime_type(
                        item,
                        arguments[0],
                        active_ids=active_ids,
                        depth=depth + 1,
                        budget=budget,
                        type_hints_cache=type_hints_cache,
                    )
            elif origin is dict:
                if len(arguments) != 2:
                    raise _RuntimeTypeContractError("dict 원소 주석 누락")
                for key, item in value.items():  # type: ignore[union-attr]
                    _assert_runtime_type(
                        key,
                        arguments[0],
                        active_ids=active_ids,
                        depth=depth + 1,
                        budget=budget,
                        type_hints_cache=type_hints_cache,
                    )
                    _assert_runtime_type(
                        item,
                        arguments[1],
                        active_ids=active_ids,
                        depth=depth + 1,
                        budget=budget,
                        type_hints_cache=type_hints_cache,
                    )
            elif len(arguments) == 2 and arguments[1] is Ellipsis:
                for item in value:  # type: ignore[union-attr]
                    _assert_runtime_type(
                        item,
                        arguments[0],
                        active_ids=active_ids,
                        depth=depth + 1,
                        budget=budget,
                        type_hints_cache=type_hints_cache,
                    )
            else:
                if len(value) != len(arguments):  # type: ignore[arg-type]
                    raise _RuntimeTypeContractError("고정 tuple 길이 불일치")
                for item, item_type in zip(value, arguments, strict=True):  # type: ignore[arg-type]
                    _assert_runtime_type(
                        item,
                        item_type,
                        active_ids=active_ids,
                        depth=depth + 1,
                        budget=budget,
                        type_hints_cache=type_hints_cache,
                    )
        finally:
            active_ids.remove(value_id)
        return

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not annotation:
            raise _RuntimeTypeContractError("Enum 타입 불일치")
        return
    if annotation in (str, int, float, bool, bytes, type(None)):
        if type(value) is not annotation:
            raise _RuntimeTypeContractError("primitive 타입 불일치")
        return
    if isinstance(annotation, type) and is_dataclass(annotation):
        if type(value) is not annotation:
            raise _RuntimeTypeContractError("dataclass 타입 불일치")
        value_id = id(value)
        if value_id in active_ids:
            raise _RuntimeTypeContractError("순환 dataclass는 허용하지 않음")
        if annotation not in type_hints_cache:
            try:
                type_hints_cache[annotation] = get_type_hints(annotation)
            except Exception as exc:  # noqa: BLE001 — 미해결 주석도 fail-closed
                raise _RuntimeTypeContractError(
                    "dataclass 주석을 해석할 수 없음"
                ) from exc
        type_hints = type_hints_cache[annotation]
        active_ids.add(value_id)
        try:
            for item in fields(annotation):
                field_type = type_hints.get(item.name)
                if field_type is None:
                    raise _RuntimeTypeContractError("dataclass 필드 주석 누락")
                _assert_runtime_type(
                    getattr(value, item.name),
                    field_type,
                    active_ids=active_ids,
                    depth=depth + 1,
                    budget=budget,
                    type_hints_cache=type_hints_cache,
                )
        finally:
            active_ids.remove(value_id)
        return

    raise _RuntimeTypeContractError("지원하지 않는 runtime 타입 주석")


def _assert_report_object(
    report: object,
    *,
    consumers: RuntimePayloadConsumers,
    raw_payload: str,
    budget: PayloadValidationBudget,
    type_hints_cache: dict[type, dict[str, object]],
    require_publishable: bool = False,
) -> None:
    budget.check_deadline()
    report_type = getattr(consumers.reports, "Report", None)
    source_type = getattr(consumers.reports, "Source", None)
    try:
        if not isinstance(report_type, type) or not is_dataclass(report_type):
            raise _RuntimeTypeContractError("고정 Report dataclass 계약 부재")
        if not isinstance(source_type, type) or not is_dataclass(source_type):
            raise _RuntimeTypeContractError("고정 Source dataclass 계약 부재")
        citations = getattr(report, "citations", None)
        if type(citations) is not list or any(
            type(item) is not source_type for item in citations
        ):
            raise _RuntimeTypeContractError("citation은 고정 Source dataclass여야 함")
        _assert_runtime_type(
            report,
            report_type,
            active_ids=set(),
            depth=0,
            budget=budget,
            type_hints_cache=type_hints_cache,
        )
    except _RuntimeTypeContractError as exc:
        raise ReadinessError(
            "보고서 payload runtime 타입이 현재 Report 계약과 다릅니다."
        ) from exc
    if not str(getattr(report, "company", "")).strip():
        raise ReadinessError("보고서 payload의 회사명이 비었습니다.")

    current_version = str(
        getattr(consumers.reports, "CANONICAL_SCHEMA_VERSION", "")
    )
    schema_version = str(getattr(report, "schema_version", ""))
    if schema_version == current_version:
        raw_data = json.loads(raw_payload)
        encoded_data = getattr(consumers.reports, "report_to_dict")(report)
        if not _json_type_sensitive_equal(raw_data, encoded_data):
            raise ReadinessError("canonical 보고서 payload 왕복 schema 계약이 다릅니다.")
    if require_publishable:
        validate = getattr(consumers.publish, "validate_publishable")
        if schema_version != current_version or not bool(validate(report)):
            raise ReadinessError("canonical 보고서 payload가 현재 출고 계약과 다릅니다.")


def _json_type_sensitive_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(  # type: ignore[arg-type]
            _json_type_sensitive_equal(left[key], right[key])  # type: ignore[index]
            for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(  # type: ignore[arg-type]
            _json_type_sensitive_equal(a, b)
            for a, b in zip(left, right, strict=True)  # type: ignore[arg-type]
        )
    return left == right


def _assert_reports_payloads(
    connection: sqlite3.Connection,
    *,
    consumers: RuntimePayloadConsumers,
    budget: PayloadValidationBudget,
    type_hints_cache: dict[type, dict[str, object]],
) -> None:
    cursor = connection.execute(
        "SELECT report_id, job, payload_json, generated_at FROM reports ORDER BY report_id"
    )
    while rows := cursor.fetchmany(JSON_PAYLOAD_FETCH_ROWS):
        for row in rows:
            budget.check_deadline()
            try:
                loaded_report = getattr(consumers.reports, "load")(connection, str(row[0]))
                if loaded_report is None:
                    raise ValueError("missing report")
                raw_report = getattr(consumers.reports, "report_from_json")(str(row[2]))
                _assert_report_object(
                    raw_report,
                    consumers=consumers,
                    raw_payload=str(row[2]),
                    budget=budget,
                    type_hints_cache=type_hints_cache,
                )
                _assert_report_object(
                    loaded_report,
                    consumers=consumers,
                    raw_payload=str(row[2]),
                    budget=budget,
                    type_hints_cache=type_hints_cache,
                )
                product_key = str(
                    getattr(consumers.cache, "_COMPANY_ANALYSIS_PRODUCT_KEY", "")
                )
                if str(row[1]) not in {str(raw_report.job), product_key}:
                    raise ValueError("job binding")
                if str(row[3]) != str(raw_report.generated_at):
                    raise ValueError("generated_at binding")
            except ReadinessError:
                raise
            except Exception as exc:  # noqa: BLE001 — payload/ID를 노출하지 않는다
                raise ReadinessError(
                    "reports.payload_json을 현재 storage.reports.load 계약으로 읽지 못했습니다."
                ) from exc


def _assert_layer2_payloads(
    connection: sqlite3.Connection,
    *,
    consumers: RuntimePayloadConsumers,
    budget: PayloadValidationBudget,
) -> None:
    cursor = connection.execute(
        "SELECT corp_id, fragments_json, filing_json, cell_judgments_json "
        "FROM layer2_cache ORDER BY corp_id"
    )
    while rows := cursor.fetchmany(JSON_PAYLOAD_FETCH_ROWS):
        for row in rows:
            budget.check_deadline()
            try:
                cached = getattr(consumers.cache, "get_layer2")(connection, str(row[0]))
                if cached is None:
                    raise ValueError("missing layer2")
                fragments_data = json.loads(str(row[1]))
                if not isinstance(fragments_data, list):
                    raise TypeError("fragments root")
                fragment_ids: set[int] = set()
                for item in fragments_data:
                    if not isinstance(item, list) or len(item) != 2:
                        raise TypeError("fragment pair")
                    fragment_id, value = item
                    if type(fragment_id) is not int or fragment_id in fragment_ids:
                        raise TypeError("fragment id")
                    fragment_ids.add(fragment_id)
                    if not isinstance(value, dict) or any(
                        not isinstance(key, str) or not isinstance(text, str)
                        for key, text in value.items()
                    ):
                        raise TypeError("fragment value")
                if not isinstance(cached.fragments, dict):
                    raise TypeError("fragments consumer")
                if row[2] is not None:
                    filing = json.loads(str(row[2]))
                    if not isinstance(filing, dict) or not isinstance(cached.filing, dict):
                        raise TypeError("filing")
                if row[3] is not None:
                    judgments = json.loads(str(row[3]))
                    if not isinstance(judgments, dict) or any(
                        not isinstance(key, str) or type(value) is not bool
                        for key, value in judgments.items()
                    ):
                        raise TypeError("cell judgments")
                    if not isinstance(cached.cell_judgments, dict):
                        raise TypeError("cell judgments consumer")
            except Exception as exc:  # noqa: BLE001 — payload/회사 ID를 노출하지 않는다
                raise ReadinessError(
                    "layer2_cache JSON을 현재 storage.cache.get_layer2 계약으로 읽지 못했습니다."
                ) from exc


def _assert_dashboard_payloads(
    connection: sqlite3.Connection,
    *,
    consumers: RuntimePayloadConsumers,
    budget: PayloadValidationBudget,
    type_hints_cache: dict[type, dict[str, object]],
) -> None:
    cursor = connection.execute(
        "SELECT report_id, version, payload_json, payload_sha256 "
        "FROM dashboard_report_versions ORDER BY report_id, version"
    )
    while rows := cursor.fetchmany(JSON_PAYLOAD_FETCH_ROWS):
        for row in rows:
            budget.check_deadline()
            payload = str(row[2])
            expected = str(row[3])
            actual = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if SHA256_RE.fullmatch(expected) is None or not hmac.compare_digest(
                actual, expected
            ):
                raise ReadinessError(
                    "dashboard 보고서 payload와 저장 SHA-256이 맞지 않습니다."
                )
            try:
                report = getattr(consumers.reports, "report_from_json")(payload)
                _assert_report_object(
                    report,
                    consumers=consumers,
                    raw_payload=payload,
                    budget=budget,
                    type_hints_cache=type_hints_cache,
                )
            except ReadinessError:
                raise
            except Exception as exc:  # noqa: BLE001 — snapshot 내용을 노출하지 않는다
                raise ReadinessError(
                    "dashboard 보고서 snapshot을 현재 Report 계약으로 읽지 못했습니다."
                ) from exc

    current_cursor = connection.execute(
        "SELECT s.report_id, v.payload_json FROM dashboard_report_states AS s "
        "LEFT JOIN dashboard_report_versions AS v "
        "ON v.report_id = s.report_id AND v.version = s.version "
        "WHERE s.status = 'normal' AND s.blocked = 0 "
        "ORDER BY s.report_id"
    )
    while rows := current_cursor.fetchmany(JSON_PAYLOAD_FETCH_ROWS):
        for row in rows:
            budget.check_deadline()
            if row[1] is None:
                raise ReadinessError("dashboard 현재 승인 version snapshot이 없습니다.")
            try:
                approved = getattr(consumers.dashboard, "approved_report_payload")(
                    connection, report_id=str(row[0])
                )
            except Exception as exc:  # noqa: BLE001 — 보고서 ID를 노출하지 않는다
                raise ReadinessError(
                    "dashboard 승인 payload를 현재 공개 소비자로 읽지 못했습니다."
                ) from exc
            if not hmac.compare_digest(
                str(approved).encode("utf-8"), str(row[1]).encode("utf-8")
            ):
                raise ReadinessError("dashboard 승인 payload read-back이 snapshot과 다릅니다.")
            try:
                current_report = getattr(consumers.reports, "report_from_json")(str(row[1]))
                _assert_report_object(
                    current_report,
                    consumers=consumers,
                    raw_payload=str(row[1]),
                    budget=budget,
                    type_hints_cache=type_hints_cache,
                    require_publishable=True,
                )
            except ReadinessError:
                raise
            except Exception as exc:  # noqa: BLE001 — 승인 snapshot 내용을 노출하지 않는다
                raise ReadinessError(
                    "dashboard 현재 승인 payload가 현재 출고 계약과 다릅니다."
                ) from exc


def _assert_lifecycle_payloads(
    connection: sqlite3.Connection,
    *,
    consumers: RuntimePayloadConsumers,
    budget: PayloadValidationBudget,
) -> None:
    cursor = connection.execute(
        "SELECT run_id, state, job, confirmed_cost_krw, elapsed_sec, "
        "final_record_json FROM observability_run_lifecycle ORDER BY run_id"
    )
    while rows := cursor.fetchmany(JSON_PAYLOAD_FETCH_ROWS):
        for row in rows:
            budget.check_deadline()
            try:
                record = getattr(consumers.lifecycle, "read_final")(
                    connection, str(row[0])
                )
                if str(row[1]) != "final":
                    if record is not None:
                        raise ValueError("non-final record")
                    continue
                if record is None or row[5] is None:
                    raise ValueError("missing final record")
                if (
                    record.run_id != str(row[0])
                    or record.job != str(row[2])
                    or float(record.cost_krw) < float(row[3])
                    or float(record.elapsed_sec) < float(row[4])
                ):
                    raise ValueError("record binding")
                audit_count_row = connection.execute(
                    "SELECT COUNT(*) FROM observability_run_lifecycle_audit "
                    "WHERE run_id = ? AND to_state = 'final'",
                    (str(row[0]),),
                ).fetchone()
                audit_row = connection.execute(
                    "SELECT record_sha256 FROM observability_run_lifecycle_audit "
                    "WHERE run_id = ? AND to_state = 'final' ORDER BY event_id LIMIT 1",
                    (str(row[0]),),
                ).fetchone()
                expected = hashlib.sha256(str(row[5]).encode("utf-8")).hexdigest()
                if (
                    audit_count_row is None
                    or int(audit_count_row[0]) != 1
                    or audit_row is None
                    or SHA256_RE.fullmatch(str(audit_row[0] or "")) is None
                    or not hmac.compare_digest(expected, str(audit_row[0]))
                ):
                    raise ValueError("audit binding")
            except Exception as exc:  # noqa: BLE001 — run/payload를 노출하지 않는다
                raise ReadinessError(
                    "관측 final JSON을 lifecycle.read_final 및 감사 해시 계약으로 읽지 못했습니다."
                ) from exc


def _assert_pdf_release_payloads(
    connection: sqlite3.Connection,
    *,
    consumers: RuntimePayloadConsumers,
    budget: PayloadValidationBudget,
) -> None:
    try:
        getattr(consumers.pdf_release, "validate_persisted_release_records")(
            connection,
            deadline_check=budget.check_deadline,
        )
    except ReadinessError:
        raise
    except Exception as exc:  # noqa: BLE001 — 승인/식별자 내용을 노출하지 않는다
        raise ReadinessError(
            "PDF 승인·역할·출고 기록을 현재 export_pdf 소비 계약으로 읽지 못했습니다."
        ) from exc


def _assert_admin_audit_payloads(
    connection: sqlite3.Connection,
    *,
    consumers: RuntimePayloadConsumers,
    budget: PayloadValidationBudget,
) -> None:
    try:
        getattr(consumers.admin_audit, "validate_persisted_events")(
            connection,
            deadline_check=budget.check_deadline,
        )
    except ReadinessError:
        raise
    except Exception as exc:  # noqa: BLE001 — 관리자 감사 내용을 노출하지 않는다
        raise ReadinessError(
            "관리자 감사 원장을 현재 observability 소비 계약으로 읽지 못했습니다."
        ) from exc


def _assert_runtime_payload_contracts(
    connection: sqlite3.Connection,
    *,
    canonical_table_xinfo: dict[str, tuple[tuple[object, ...], ...]],
) -> None:
    consumers = _load_runtime_payload_consumers()
    budget = PayloadValidationBudget.start()
    type_hints_cache: dict[type, dict[str, object]] = {}
    try:
        with _bounded_sqlite(connection, budget):
            _assert_generic_json_columns(
                connection,
                columns=_json_text_columns(canonical_table_xinfo),
                budget=budget,
            )
            _assert_reports_payloads(
                connection,
                consumers=consumers,
                budget=budget,
                type_hints_cache=type_hints_cache,
            )
            _assert_layer2_payloads(connection, consumers=consumers, budget=budget)
            _assert_dashboard_payloads(
                connection,
                consumers=consumers,
                budget=budget,
                type_hints_cache=type_hints_cache,
            )
            _assert_lifecycle_payloads(connection, consumers=consumers, budget=budget)
            _assert_pdf_release_payloads(
                connection,
                consumers=consumers,
                budget=budget,
            )
            _assert_admin_audit_payloads(
                connection,
                consumers=consumers,
                budget=budget,
            )
    except ReadinessError:
        raise
    except sqlite3.Error as exc:
        raise ReadinessError("격리 clone의 payload 전수 SQL 검증에 실패했습니다.") from exc


def _copy_database(source: Path, target: Path, *, expected_sha256: str) -> None:
    """sidecar가 없는 manifest 결속 main bytes만 O_EXCL로 복사한다."""

    expected = _valid_sha256(expected_sha256, label="복사 원본 SHA-256")
    if not hmac.compare_digest(
        _stable_database_digest(source, label="복사 원본 DB"), expected
    ):
        raise ReadinessError("격리 복사 직전 원본 DB 지문이 변경됐습니다.")
    if target.exists() or os.path.lexists(target):
        raise ReadinessError("격리 SQLite 복사 대상이 비어 있지 않습니다.")
    try:
        with source.open("rb") as source_stream, target.open("xb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream, HASH_CHUNK_BYTES)
            target_stream.flush()
            os.fsync(target_stream.fileno())
    except OSError as exc:
        raise ReadinessError("격리 SQLite 복사본을 만들지 못했습니다.") from exc
    source_after = _stable_database_digest(source, label="복사 원본 DB")
    target_digest = _stable_database_digest(target, label="격리 복사 DB")
    if not hmac.compare_digest(source_after, expected) or not hmac.compare_digest(
        target_digest, expected
    ):
        raise ReadinessError("격리 SQLite 복사본이 결속된 원본 bytes와 다릅니다.")
    if target.stat().st_size != source.stat().st_size:
        raise ReadinessError("격리 SQLite 복사본 크기가 원본과 다릅니다.")


def _assert_bootstrapped_schema(
    candidate: Path,
    *,
    canonical_parent: Path,
) -> AppSchemaInventory:
    _assert_database_size(candidate)
    _assert_no_sqlite_companions(candidate, label="격리 schema clone")
    storage_db = _load_storage_db_module()
    # storage.db.connect가 이 동일 registry를 실행한다. 여기서는 path/callable
    # identity만 다시 검증하고 중복 bootstrap은 하지 않는다.
    _load_feature_schema_bootstraps()
    canonical = canonical_parent / "canonical-storage.sqlite3"
    if canonical.exists():
        raise ReadinessError("canonical schema 경로가 비어 있지 않습니다.")
    try:
        with storage_db.connect(canonical) as canonical_connection:
            canonical_connection.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 — feature별 migration 실패를 닫는다
        raise ReadinessError(
            "현재 앱의 canonical storage schema를 만들지 못했습니다."
        ) from exc

    try:
        with closing(_readonly_connection(canonical)) as canonical_connection:
            required_tables = _assert_sqlite_integrity(canonical_connection)
            missing_canonical_tables = sorted(REQUIRED_TABLES - required_tables)
            if missing_canonical_tables:
                raise ReadinessError(
                    "현재 앱 canonical bootstrap에 필수 운영 테이블이 없습니다: "
                    + ", ".join(missing_canonical_tables)
                )
            required_table_xinfo = {
                table: _table_xinfo(canonical_connection, table)
                for table in required_tables
            }
            required_table_semantics = {
                table: _table_semantic_contract(canonical_connection, table)
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
            prebootstrap_indexes = {
                table: _index_contract(prebootstrap_connection, table)
                for table in required_tables & prebootstrap_tables
            }
            prebootstrap_table_xinfo = {
                table: _table_xinfo(prebootstrap_connection, table)
                for table in required_tables & prebootstrap_tables
            }
            prebootstrap_table_semantics = {
                table: _table_semantic_contract(prebootstrap_connection, table)
                for table in required_tables & prebootstrap_tables
            }
            prebootstrap_triggers = _trigger_contract(prebootstrap_connection)
            prebootstrap_versioned_session = False
            if "sessions" in prebootstrap_tables:
                session_xinfo = prebootstrap_table_xinfo["sessions"]
                session_semantics = prebootstrap_table_semantics["sessions"]
                session_versions = (
                    *_session_versioned_contracts(storage_db, state="raw"),
                    *_session_versioned_contracts(storage_db, state="hashed"),
                )
                prebootstrap_versioned_session = any(
                    session_xinfo == version.table_xinfo
                    and session_semantics == version.semantics
                    for version in session_versions
                )
    except sqlite3.Error as exc:
        raise ReadinessError("앱 storage schema 계약을 읽지 못했습니다.") from exc

    unapproved_missing = sorted(missing_before_bootstrap - allowed_missing)
    if unapproved_missing:
        raise ReadinessError(
            "bootstrap 전 백업에 canonical 필수 테이블이 없습니다: "
            + ", ".join(unapproved_missing)
        )

    for table in sorted(required_tables & prebootstrap_tables):
        if table == "sessions":
            if not prebootstrap_versioned_session:
                raise ReadinessError(
                    "bootstrap 전 sessions가 명시된 version 계약과 다릅니다."
                )
        elif (
            prebootstrap_table_xinfo[table] != required_table_xinfo[table]
            or prebootstrap_table_semantics[table] != required_table_semantics[table]
        ):
            raise ReadinessError(
                "bootstrap 전 백업의 table/CREATE 의미 계약이 canonical과 다릅니다: "
                + table
            )
        if prebootstrap_indexes[table] != required_indexes[table]:
            if table == "sessions" and prebootstrap_versioned_session:
                continue
            raise ReadinessError(
                "bootstrap 전 백업의 필수 index 계약이 canonical과 다릅니다: "
                + table
            )
    if prebootstrap_triggers != required_triggers:
        actual_names = set(prebootstrap_triggers)
        required_names = set(required_triggers)
        changed = sorted(
            name
            for name in actual_names & required_names
            if prebootstrap_triggers[name] != required_triggers[name]
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
            "bootstrap 전 백업의 trigger 계약이 canonical과 다릅니다: "
            + " ".join(details)
        )

    try:
        with storage_db.connect(candidate) as candidate_connection:
            candidate_connection.execute("SELECT 1")
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
            actual_table_semantics = {
                table: _table_semantic_contract(actual_connection, table)
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
    unexpected_tables = sorted(actual_tables - required_tables)
    if unexpected_tables:
        raise ReadinessError(
            "앱 registry에 없는 추가 영속 테이블이 있습니다: "
            + ", ".join(unexpected_tables)
        )
    supported_session_contracts = _session_versioned_contracts(
        storage_db,
        state="hashed",
    )
    canonical_session_supported = any(
        required_table_xinfo["sessions"] == version.table_xinfo
        and required_table_semantics["sessions"] == version.semantics
        for version in supported_session_contracts
    )
    if not canonical_session_supported:
        raise ReadinessError(
            "현재 앱 canonical sessions가 명시된 hashed version 계약에 없습니다."
        )

    for table in sorted(required_tables):
        actual_signature = actual_table_xinfo[table]
        required_signature = required_table_xinfo[table]
        actual_semantics = actual_table_semantics[table]
        required_semantics = required_table_semantics[table]
        if table == "sessions":
            supported_session = any(
                actual_signature == version.table_xinfo
                and actual_semantics == version.semantics
                for version in supported_session_contracts
            )
            if not supported_session:
                raise ReadinessError(
                    "앱 storage sessions versioned table 계약이 맞지 않습니다."
                )
        elif actual_signature != required_signature:
            raise ReadinessError(
                f"앱 storage table_xinfo 계약이 맞지 않습니다: {table}"
            )
        elif actual_semantics != required_semantics:
            raise ReadinessError(
                "앱 storage CREATE TABLE/foreign key/WR/STRICT 계약이 "
                f"canonical과 다릅니다: {table}"
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

    with closing(_readonly_connection(candidate)) as payload_connection:
        _assert_runtime_payload_contracts(
            payload_connection,
            canonical_table_xinfo=required_table_xinfo,
        )
    _assert_no_sqlite_companions(candidate, label="격리 schema clone")

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

    source_size = _assert_database_size(source)
    source_digest = _stable_database_digest(source, label="schema 검증 원본 DB")
    if expected_source_sha256 is not None and not hmac.compare_digest(
        source_digest, expected_source_sha256
    ):
        raise ReadinessError("앱 schema 검증 전에 원본 백업 지문이 변경됐습니다.")
    temp_root = (
        Path(tempfile.gettempdir()).resolve(strict=True)
        if temp_parent is None
        else temp_parent.resolve(strict=True)
    )
    _assert_clone_space(temp_root, source_size=source_size, copies=2)
    parent = None if temp_parent is None else str(temp_root)
    temporary_path: Path | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="release-schema-bootstrap-", dir=parent
        ) as directory:
            temporary_path = Path(directory)
            candidate = temporary_path / "restored.sqlite3"
            _copy_database(source, candidate, expected_sha256=source_digest)
            inventory = _assert_bootstrapped_schema(
                candidate,
                canonical_parent=temporary_path,
            )
    finally:
        if _stable_database_digest(source, label="schema 검증 원본 DB") != source_digest:
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
    _assert_no_sqlite_companions(database, label="백업 DB")
    database_size = _assert_database_size(database)
    checksum_file = _regular_file(checksum_path, label="체크섬 파일")
    sidecar_digest = _read_sidecar(checksum_file, database_name=database.name)
    checksum_object_digest = sha256_file(checksum_file)
    compatibility_digest = _valid_sha256(expected_sha256, label="호환 체크섬")
    actual_digest = _stable_database_digest(database, label="백업 DB")
    if not hmac.compare_digest(actual_digest, sidecar_digest):
        raise ReadinessError("백업 DB와 같은 위치의 체크섬이 맞지 않습니다.")
    if not hmac.compare_digest(actual_digest, compatibility_digest):
        raise ReadinessError("백업 DB가 호출자 호환 체크섬과 맞지 않습니다.")
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
            with _bounded_sqlite(connection, PayloadValidationBudget.start()):
                _assert_sqlite_integrity(connection)
    except sqlite3.Error as exc:
        raise ReadinessError("백업 DB를 읽기 전용으로 검증하지 못했습니다.") from exc
    app_schema = _assert_app_schema_compatible(
        database,
        expected_source_sha256=actual_digest,
    )
    _assert_no_sqlite_companions(database, label="검증 완료 백업 DB")
    return {
        "status": "통과",
        "sha256": actual_digest,
        "size_bytes": database_size,
        "table_count": app_schema.table_count,
        "index_count": app_schema.index_count,
        "trigger_count": app_schema.trigger_count,
        "manifest_backup_id": manifest_record.backup_id,
        "manifest_sequence": manifest_record.sequence,
        "manifest_key_identity": manifest_record.manifest_key_identity,
    }


def _inventory(path: Path) -> DatabaseInventory:
    try:
        with closing(_readonly_connection(path)) as connection:
            with _bounded_sqlite(connection, PayloadValidationBudget.start()):
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
    _assert_no_sqlite_companions(path, label="inventory DB")
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
    _assert_no_sqlite_companions(source, label="복구 원본 DB")
    source_size = _assert_database_size(source)
    parent: str | None = None
    if temp_parent is not None:
        resolved_parent = temp_parent.expanduser().resolve(strict=True)
        if not resolved_parent.is_dir():
            raise ReadinessError("임시 복구 상위 경로는 디렉터리여야 합니다.")
        parent = str(resolved_parent)

    source_inventory = _inventory(source)
    source_digest = _stable_database_digest(source, label="복구 원본 DB")
    if not hmac.compare_digest(source_digest, str(verified["sha256"])):
        raise ReadinessError("임시 복구 전에 원본 백업 지문이 변경됐습니다.")
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True) if parent is None else Path(parent)
    _assert_clone_space(temp_root, source_size=source_size, copies=2)
    temporary_path: Path | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="release-restore-dry-run-", dir=parent
        ) as directory:
            temporary_path = Path(directory)
            restored = temporary_path / "restored.sqlite3"
            _copy_database(source, restored, expected_sha256=source_digest)
            if not hmac.compare_digest(
                _stable_database_digest(restored, label="복구 clone DB"), source_digest
            ):
                raise ReadinessError("임시 복사본 bytes가 manifest 결속 원본과 다릅니다.")

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
            _assert_no_sqlite_companions(restored, label="복구 검증 완료 clone DB")
    finally:
        if _stable_database_digest(source, label="복구 원본 DB") != source_digest:
            raise ReadinessError("임시 복구 검증 중 원본 백업이 변경됐습니다.")
        if temporary_path is not None and temporary_path.exists():
            raise ReadinessError("임시 복구 디렉터리를 제거하지 못했습니다.")
    _assert_no_sqlite_companions(source, label="복구 검증 완료 원본 DB")
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
        with closing(_live_readonly_connection(database)) as connection:
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
