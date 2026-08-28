"""SQLite snapshot과 불변 보고서 파일을 한 복구 세대로 묶는다.

이 manifest는 로컬 전송 무결성과 복구 연습을 위한 *목록*이다. 공격자가 DB와
같이 다시 쓸 수 있으므로 운영 복구 권한은 아니다. 운영 복구는 별도 권한의 서명
manifest/checkpoint gate를 계속 요구한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Final, Iterator, Mapping, Sequence

from src.shared.bounded_file_lock import (
    BoundedFileLockError,
    BoundedFileLockTimeout,
    exclusive_file_lock,
)


FORMAT_NAME: Final[str] = "company-analysis-recovery-generation"
FORMAT_VERSION: Final[int] = 1
DATABASE_NAME: Final[str] = "storage.sqlite3"
DATABASE_CHECKSUM_NAME: Final[str] = DATABASE_NAME + ".sha256"
MANIFEST_NAME: Final[str] = "recovery-manifest.json"
MANIFEST_CHECKSUM_NAME: Final[str] = MANIFEST_NAME + ".sha256"
# 내용주소 key는 manifest에 그대로 남긴다. 세대 안의 실제 파일 경로는 Windows
# MAX_PATH에서도 깊은 SHA-256 경로가 잘리지 않도록 짧게 유지한다.
ARTIFACT_DIRECTORY: Final[str] = "a"
ARTIFACT_TABLE: Final[str] = "report_delivery_artifacts"
BLOB_INTENT_TABLE: Final[str] = "artifact_blob_intents"
BLOB_INTENT_EVENT_TABLE: Final[str] = "artifact_blob_intent_events"
ROOT_LOCK_NAME: Final[str] = ".artifact-root.lock"
ARTIFACT_ROOT_LOCK_TIMEOUT_SECONDS: Final[float] = 10.0
HASH_CHUNK_BYTES: Final[int] = 1024 * 1024
MAX_MANIFEST_BYTES: Final[int] = 16 * 1024 * 1024
PRIVATE_FILE_MODE: Final[int] = 0o600
PRIVATE_DIR_MODE: Final[int] = 0o700
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
UTC_TEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
REQUIRED_ARTIFACT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "artifact_id",
        "channel",
        "original_state",
        "blob_key",
        "bytes_sha256",
        "byte_length",
    }
)
REQUIRED_INTENT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "intent_id",
        "storage_identity",
        "blob_key",
        "bytes_sha256",
        "byte_length",
    }
)
REQUIRED_EVENT_COLUMNS: Final[frozenset[str]] = frozenset(
    {"event_id", "intent_id", "event_type", "artifact_id"}
)


class RecoveryGenerationError(RuntimeError):
    """완전한 복구 세대를 만들거나 검증할 수 없다."""


@dataclass(frozen=True)
class ArtifactReference:
    blob_key: str
    sha256: str
    byte_length: int
    artifact_ids: tuple[str, ...]

    @property
    def generation_path(self) -> str:
        return f"{ARTIFACT_DIRECTORY}/{self.sha256}.blob"


@dataclass(frozen=True)
class GenerationBuild:
    generation_id: str
    artifact_count: int
    artifact_bytes: int


@dataclass(frozen=True)
class GenerationVerification:
    generation_id: str
    database_path: Path
    database_sha256: str
    artifact_count: int
    artifact_bytes: int
    artifact_storage_identity: str


def artifact_storage_identity(root: Path) -> str:
    """report_delivery filesystem backend과 같은 정규화 규칙을 쓴다."""

    return "filesystem:" + os.path.normcase(str(Path(root).resolve()))


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecoveryGenerationError("복구 세대 생성 시각에 시간대가 없습니다")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_linklike(path: Path, status: os.stat_result | None = None) -> bool:
    inspected = status or path.lstat()
    if stat.S_ISLNK(inspected.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(inspected, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _assert_plain_directory(path: Path, *, label: str) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError as exc:
        raise RecoveryGenerationError(f"{label} 디렉터리가 없습니다") from exc
    if _is_linklike(path, status) or not stat.S_ISDIR(status.st_mode):
        raise RecoveryGenerationError(f"{label} 경로가 symlink 없는 일반 디렉터리가 아닙니다")


def _safe_posix_relative(value: object, *, label: str) -> str:
    normalized = str(value or "")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\\" in normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != normalized
    ):
        raise RecoveryGenerationError(f"{label} 경로가 안전한 상대 POSIX 경로가 아닙니다")
    return normalized


def _expected_blob_key(digest: str) -> str:
    return f"sha256/{digest[:2]}/{digest}.blob"


def _table_columns(conn: sqlite3.Connection, table: str) -> frozenset[str] | None:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()
    if row is None:
        return None
    if str(row[0]) != "table":
        raise RecoveryGenerationError(f"{table}가 일반 SQLite 표가 아닙니다")
    escaped = table.replace('"', '""')
    return frozenset(
        str(column[1])
        for column in conn.execute(f'PRAGMA table_xinfo("{escaped}")').fetchall()
    )


def _readonly_database(path: Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        return sqlite3.connect(uri, uri=True, timeout=10.0)
    except sqlite3.Error as exc:
        raise RecoveryGenerationError("복구 세대 DB snapshot을 열 수 없습니다") from exc


def database_artifact_references(
    database_path: Path,
    *,
    required_storage_identity: str | None = None,
) -> tuple[ArtifactReference, ...]:
    """DB snapshot이 저장됐다고 약속한 모든 blob을 중복 없이 뽑는다.

    ``legacy_original_unknown``은 애초에 최초 bytes가 없다는 정직한 표식이므로
    파일 참조가 아니다. ``stored`` 행은 append-only BOUND intent와 정확히 맞아야
    하며, 복구 세대 생성 때는 현재 artifact root 신원까지 일치해야 한다.
    """

    database = Path(database_path)
    try:
        # sqlite3.Connection의 context manager는 거래만 끝내고 파일 handle은
        # 닫지 않는다. Windows에서도 snapshot rename/삭제가 가능하도록 closing한다.
        with closing(_readonly_database(database)) as conn:
            artifact_columns = _table_columns(conn, ARTIFACT_TABLE)
            if artifact_columns is None:
                return ()
            if not REQUIRED_ARTIFACT_COLUMNS.issubset(artifact_columns):
                raise RecoveryGenerationError("artifact 표가 필요한 열을 갖고 있지 않습니다")
            rows = conn.execute(
                f"""
                SELECT artifact_id, channel, original_state, blob_key,
                       bytes_sha256, byte_length
                FROM {ARTIFACT_TABLE}
                ORDER BY blob_key, artifact_id
                """
            ).fetchall()

            stored_rows: list[tuple[str, str, int, str]] = []
            for raw in rows:
                artifact_id = str(raw[0] or "").strip()
                channel = str(raw[1] or "").strip()
                state = str(raw[2] or "").strip()
                blob_key = str(raw[3] or "")
                digest = str(raw[4] or "").strip()
                try:
                    byte_length = int(raw[5])
                except (TypeError, ValueError) as exc:
                    raise RecoveryGenerationError("artifact byte 길이가 숫자가 아닙니다") from exc
                if not artifact_id or not channel:
                    raise RecoveryGenerationError("artifact ID 또는 channel이 비어 있습니다")
                if state == "legacy_original_unknown":
                    if blob_key or digest or byte_length != 0:
                        raise RecoveryGenerationError(
                            "최초 원본 미확인 artifact가 blob을 가진 것처럼 기록됐습니다"
                        )
                    continue
                if state != "stored":
                    raise RecoveryGenerationError("artifact 원본 상태를 알 수 없습니다")
                if not SHA256_RE.fullmatch(digest):
                    raise RecoveryGenerationError("artifact SHA-256 형식이 올바르지 않습니다")
                safe_key = _safe_posix_relative(blob_key, label="artifact blob")
                if safe_key != _expected_blob_key(digest):
                    raise RecoveryGenerationError(
                        "artifact blob 위치가 내용주소 SHA-256과 맞지 않습니다"
                    )
                if isinstance(raw[5], bool) or byte_length <= 0:
                    raise RecoveryGenerationError("artifact byte 길이가 올바르지 않습니다")
                stored_rows.append((safe_key, digest, byte_length, artifact_id))

            if not stored_rows:
                return ()
            intent_columns = _table_columns(conn, BLOB_INTENT_TABLE)
            event_columns = _table_columns(conn, BLOB_INTENT_EVENT_TABLE)
            if (
                intent_columns is None
                or event_columns is None
                or not REQUIRED_INTENT_COLUMNS.issubset(intent_columns)
                or not REQUIRED_EVENT_COLUMNS.issubset(event_columns)
            ):
                raise RecoveryGenerationError(
                    "저장된 artifact를 증명할 append-only blob intent 표가 없습니다"
                )

            grouped: dict[tuple[str, str, int], list[str]] = {}
            for blob_key, digest, byte_length, artifact_id in stored_rows:
                identities = {
                    str(row[0])
                    for row in conn.execute(
                        f"""
                        SELECT intents.storage_identity
                        FROM {BLOB_INTENT_TABLE} AS intents
                        JOIN {BLOB_INTENT_EVENT_TABLE} AS events
                          ON events.intent_id = intents.intent_id
                        WHERE events.event_type = 'bound'
                          AND events.artifact_id = ?
                          AND intents.blob_key = ?
                          AND intents.bytes_sha256 = ?
                          AND intents.byte_length = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM {BLOB_INTENT_EVENT_TABLE} AS later
                              WHERE later.intent_id = events.intent_id
                                AND later.event_id > events.event_id
                          )
                        """,
                        (artifact_id, blob_key, digest, byte_length),
                    ).fetchall()
                }
                if not identities:
                    raise RecoveryGenerationError(
                        "stored artifact와 정확히 결속된 BOUND blob intent가 없습니다"
                    )
                # metadata에는 root 신원이 없으므로 서로 다른 두 root의 BOUND가
                # 섞이면 어느 파일을 정본으로 복구·삭제해야 하는지 증명할 수 없다.
                # 현재 root가 집합에 하나 들어 있다는 이유만으로 통과시키면 백업은
                # 성공하지만 retention/restore가 같은 DB를 손상으로 거부한다.
                if len(identities) != 1 or "" in identities:
                    raise RecoveryGenerationError(
                        "stored artifact의 원본 저장소 root 신원이 하나가 아닙니다"
                    )
                only_identity = next(iter(identities))
                if (
                    required_storage_identity is not None
                    and required_storage_identity != only_identity
                ):
                    raise RecoveryGenerationError(
                        "DB artifact가 현재 파일 저장소 신원과 결속돼 있지 않습니다"
                    )
                grouped.setdefault((blob_key, digest, byte_length), []).append(artifact_id)

            by_key: dict[str, tuple[str, int]] = {}
            references: list[ArtifactReference] = []
            for (blob_key, digest, byte_length), artifact_ids in sorted(grouped.items()):
                previous = by_key.setdefault(blob_key, (digest, byte_length))
                if previous != (digest, byte_length):
                    raise RecoveryGenerationError(
                        "같은 artifact blob 위치가 서로 다른 bytes를 가리킵니다"
                    )
                references.append(
                    ArtifactReference(
                        blob_key=blob_key,
                        sha256=digest,
                        byte_length=byte_length,
                        artifact_ids=tuple(sorted(set(artifact_ids))),
                    )
                )
            return tuple(references)
    except sqlite3.Error as exc:
        raise RecoveryGenerationError("artifact 참조를 DB snapshot에서 읽지 못했습니다") from exc


@contextmanager
def _artifact_root_lock(
    root: Path,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """report_delivery가 쓰기·GC에 쓰는 동일 lock 파일을 잡는다."""

    _assert_plain_directory(root, label="artifact root")
    lock_path = root / ROOT_LOCK_NAME
    effective_timeout = (
        ARTIFACT_ROOT_LOCK_TIMEOUT_SECONDS
        if timeout_seconds is None
        else timeout_seconds
    )
    try:
        with exclusive_file_lock(
            lock_path,
            timeout_seconds=effective_timeout,
        ):
            yield
    except BoundedFileLockTimeout as exc:
        raise RecoveryGenerationError(
            "artifact 저장소를 다른 process가 오래 점유해 복구 세대를 만들 수 없습니다"
        ) from exc
    except BoundedFileLockError as exc:
        raise RecoveryGenerationError("artifact root lock을 신뢰할 수 없습니다") from exc


def _safe_source_blob(root: Path, blob_key: str) -> Path:
    relative = PurePosixPath(_safe_posix_relative(blob_key, label="artifact blob"))
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            status = current.lstat()
        except FileNotFoundError as exc:
            raise RecoveryGenerationError("참조된 artifact blob 부모가 없습니다") from exc
        if _is_linklike(current, status) or not stat.S_ISDIR(status.st_mode):
            raise RecoveryGenerationError("artifact blob 부모가 일반 디렉터리가 아닙니다")
    return root.joinpath(*relative.parts)


def _open_plain_readonly(path: Path, *, label: str):
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise RecoveryGenerationError(f"{label} 파일이 없습니다") from exc
    if _is_linklike(path, before) or not stat.S_ISREG(before.st_mode):
        raise RecoveryGenerationError(f"{label}가 symlink 없는 일반 파일이 아닙니다")
    if before.st_nlink != 1:
        raise RecoveryGenerationError(f"{label}가 다른 경로와 hard-link돼 있습니다")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RecoveryGenerationError(f"{label}를 안전하게 열 수 없습니다") from exc
    stream = os.fdopen(descriptor, "rb")
    opened = os.fstat(stream.fileno())
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
    ):
        stream.close()
        raise RecoveryGenerationError(f"{label}가 검사 중 바뀌었습니다")
    return stream, before


def _assert_file_stable(path: Path, stream, before: os.stat_result, *, label: str) -> None:
    opened_after = os.fstat(stream.fileno())
    try:
        path_after = path.lstat()
    except FileNotFoundError as exc:
        raise RecoveryGenerationError(f"{label}가 검사 중 사라졌습니다") from exc
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if (
        _is_linklike(path, path_after)
        or not stat.S_ISREG(path_after.st_mode)
        or opened_after.st_nlink != 1
        or path_after.st_nlink != 1
        or (opened_after.st_dev, opened_after.st_ino, opened_after.st_size, opened_after.st_mtime_ns)
        != identity
        or (path_after.st_dev, path_after.st_ino, path_after.st_size, path_after.st_mtime_ns)
        != identity
    ):
        raise RecoveryGenerationError(f"{label}가 검사 중 바뀌었습니다")


def _copy_exact_blob(source: Path, destination: Path, reference: ArtifactReference) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if destination.exists():
        raise RecoveryGenerationError("복구 세대 artifact 경로가 중복됐습니다")
    source_stream, before = _open_plain_readonly(source, label="artifact blob")
    digest = hashlib.sha256()
    total = 0
    descriptor = -1
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            PRIVATE_FILE_MODE,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            while chunk := source_stream.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
                total += len(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        _assert_file_stable(source, source_stream, before, label="artifact blob")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        source_stream.close()
    if total != reference.byte_length or digest.hexdigest() != reference.sha256:
        destination.unlink(missing_ok=True)
        raise RecoveryGenerationError("artifact blob의 hash 또는 길이가 DB와 맞지 않습니다")


def copy_referenced_artifacts(
    *,
    artifact_root: Path,
    generation_root: Path,
    references: Sequence[ArtifactReference],
) -> None:
    if not references:
        return
    source_root = Path(artifact_root)
    target_root = Path(generation_root)
    with _artifact_root_lock(source_root):
        for reference in references:
            source = _safe_source_blob(source_root, reference.blob_key)
            destination = target_root.joinpath(
                *PurePosixPath(reference.generation_path).parts
            )
            _copy_exact_blob(source, destination, reference)


def _write_new_file(path: Path, data: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        PRIVATE_FILE_MODE,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _hash_plain_file(path: Path, *, label: str) -> tuple[str, int]:
    stream, before = _open_plain_readonly(path, label=label)
    digest = hashlib.sha256()
    length = 0
    try:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
            length += len(chunk)
        _assert_file_stable(path, stream, before, label=label)
    finally:
        stream.close()
    return digest.hexdigest(), length


def _generation_identity(payload_without_id: Mapping[str, object]) -> str:
    return _sha256_bytes(_canonical_json(payload_without_id))


def build_manifest(
    *,
    generation_root: Path,
    artifact_root: Path,
    references: Sequence[ArtifactReference],
    created_at: datetime,
) -> GenerationBuild:
    root = Path(generation_root)
    _assert_plain_directory(root, label="복구 세대 staging")
    database = root / DATABASE_NAME
    database_checksum = root / DATABASE_CHECKSUM_NAME
    database_sha256, database_length = _hash_plain_file(database, label="DB snapshot")
    checksum_sha256, checksum_length = _hash_plain_file(
        database_checksum, label="DB checksum"
    )
    expected_checksum = f"{database_sha256}  {DATABASE_NAME}\n".encode("ascii")
    if database_checksum.read_bytes() != expected_checksum:
        raise RecoveryGenerationError("DB checksum이 같은 세대의 DB와 맞지 않습니다")

    artifact_records = [
        {
            "artifact_ids": list(reference.artifact_ids),
            "blob_key": reference.blob_key,
            "byte_length": reference.byte_length,
            "path": reference.generation_path,
            "sha256": reference.sha256,
        }
        for reference in references
    ]
    payload_without_id: dict[str, object] = {
        "artifact_storage_identity": artifact_storage_identity(artifact_root),
        "artifacts": artifact_records,
        "created_at": _utc_text(created_at),
        "database": {
            "byte_length": database_length,
            "checksum_byte_length": checksum_length,
            "checksum_path": DATABASE_CHECKSUM_NAME,
            "checksum_sha256": checksum_sha256,
            "path": DATABASE_NAME,
            "sha256": database_sha256,
        },
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
    }
    generation_id = _generation_identity(payload_without_id)
    payload = dict(payload_without_id)
    payload["generation_id"] = generation_id
    manifest_bytes = _canonical_json(payload) + b"\n"
    manifest_path = root / MANIFEST_NAME
    manifest_checksum_path = root / MANIFEST_CHECKSUM_NAME
    _write_new_file(manifest_path, manifest_bytes)
    manifest_digest = _sha256_bytes(manifest_bytes)
    _write_new_file(
        manifest_checksum_path,
        f"{manifest_digest}  {MANIFEST_NAME}\n".encode("ascii"),
    )
    return GenerationBuild(
        generation_id=generation_id,
        artifact_count=len(references),
        artifact_bytes=sum(reference.byte_length for reference in references),
    )


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryGenerationError("복구 manifest에 중복 JSON 열쇠가 있습니다")
        result[key] = value
    return result


def _require_exact_keys(payload: Mapping[str, object], expected: set[str], *, label: str) -> None:
    if set(payload) != expected:
        raise RecoveryGenerationError(f"{label} 필드 구성이 지원하는 형식과 다릅니다")


def _require_positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RecoveryGenerationError(f"{label} 값이 양의 정수가 아닙니다")
    return value


def _scan_generation_entries(root: Path) -> tuple[set[str], set[str]]:
    _assert_plain_directory(root, label="복구 세대")
    files: set[str] = set()
    directories: set[str] = set()

    def visit(directory: Path, relative: PurePosixPath | None = None) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise RecoveryGenerationError("복구 세대 디렉터리를 읽을 수 없습니다") from exc
        for entry in entries:
            child_relative = (
                PurePosixPath(entry.name)
                if relative is None
                else relative / entry.name
            )
            normalized = _safe_posix_relative(child_relative.as_posix(), label="복구 세대")
            child_path = Path(entry.path)
            try:
                # Windows/Python 3.13의 DirEntry.stat(False)는 st_nlink를 0으로
                # 보고할 수 있다. hard-link 판정은 실제 Path.lstat 결과를 쓴다.
                status = child_path.lstat()
            except OSError as exc:
                raise RecoveryGenerationError("복구 세대 항목 상태를 읽을 수 없습니다") from exc
            if _is_linklike(child_path, status):
                raise RecoveryGenerationError("복구 세대 안에 symlink/reparse point가 있습니다")
            if stat.S_ISDIR(status.st_mode):
                directories.add(normalized)
                visit(child_path, child_relative)
            elif stat.S_ISREG(status.st_mode):
                if status.st_nlink != 1:
                    raise RecoveryGenerationError("복구 세대 파일이 hard-link돼 있습니다")
                files.add(normalized)
            else:
                raise RecoveryGenerationError("복구 세대 안에 일반 파일이 아닌 항목이 있습니다")

    visit(root)
    return files, directories


def _expected_directories(files: set[str]) -> set[str]:
    directories: set[str] = set()
    for filename in files:
        parent = PurePosixPath(filename).parent
        while parent.as_posix() not in {"", "."}:
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _read_manifest(root: Path) -> dict[str, object]:
    manifest_path = root / MANIFEST_NAME
    checksum_path = root / MANIFEST_CHECKSUM_NAME
    manifest_digest, manifest_length = _hash_plain_file(
        manifest_path, label="복구 manifest"
    )
    if manifest_length > MAX_MANIFEST_BYTES:
        raise RecoveryGenerationError("복구 manifest가 허용 크기를 넘었습니다")
    expected_checksum = f"{manifest_digest}  {MANIFEST_NAME}\n".encode("ascii")
    checksum_digest, checksum_length = _hash_plain_file(
        checksum_path, label="복구 manifest checksum"
    )
    del checksum_digest
    if checksum_length != len(expected_checksum) or checksum_path.read_bytes() != expected_checksum:
        raise RecoveryGenerationError("복구 manifest checksum이 맞지 않습니다")
    try:
        raw = manifest_path.read_bytes()
        payload = json.loads(raw, object_pairs_hook=_no_duplicate_object)
    except (UnicodeError, json.JSONDecodeError, OSError) as exc:
        raise RecoveryGenerationError("복구 manifest JSON을 읽을 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise RecoveryGenerationError("복구 manifest 최상위 값이 객체가 아닙니다")
    if _canonical_json(payload) + b"\n" != raw:
        raise RecoveryGenerationError("복구 manifest가 canonical JSON이 아닙니다")
    return payload


def verify_generation(generation_root: Path) -> GenerationVerification:
    """복원 파일을 만들지 않는 fail-closed 복구 dry-run이다."""

    root = Path(generation_root)
    initial_files, initial_directories = _scan_generation_entries(root)
    payload = _read_manifest(root)
    _require_exact_keys(
        payload,
        {
            "artifact_storage_identity",
            "artifacts",
            "created_at",
            "database",
            "format",
            "generation_id",
            "version",
        },
        label="복구 manifest",
    )
    if payload["format"] != FORMAT_NAME or payload["version"] != FORMAT_VERSION:
        raise RecoveryGenerationError("지원하지 않는 복구 manifest 형식입니다")
    generation_id = str(payload["generation_id"] or "")
    if not SHA256_RE.fullmatch(generation_id):
        raise RecoveryGenerationError("복구 세대 ID 형식이 올바르지 않습니다")
    created_at = str(payload["created_at"] or "")
    if not UTC_TEXT_RE.fullmatch(created_at):
        raise RecoveryGenerationError("복구 세대 생성 시각 형식이 올바르지 않습니다")
    storage_identity = str(payload["artifact_storage_identity"] or "")
    if not storage_identity.startswith("filesystem:") or len(storage_identity) <= len("filesystem:"):
        raise RecoveryGenerationError("artifact 저장소 신원이 올바르지 않습니다")

    identity_payload = dict(payload)
    identity_payload.pop("generation_id")
    if _generation_identity(identity_payload) != generation_id:
        raise RecoveryGenerationError("복구 세대 ID가 manifest 내용과 맞지 않습니다")

    database_record = payload["database"]
    if not isinstance(database_record, dict):
        raise RecoveryGenerationError("복구 manifest DB 기록이 객체가 아닙니다")
    _require_exact_keys(
        database_record,
        {
            "byte_length",
            "checksum_byte_length",
            "checksum_path",
            "checksum_sha256",
            "path",
            "sha256",
        },
        label="복구 manifest DB",
    )
    if (
        database_record["path"] != DATABASE_NAME
        or database_record["checksum_path"] != DATABASE_CHECKSUM_NAME
    ):
        raise RecoveryGenerationError("복구 manifest DB 경로가 고정 경계와 다릅니다")
    database_sha256 = str(database_record["sha256"] or "")
    checksum_sha256 = str(database_record["checksum_sha256"] or "")
    if not SHA256_RE.fullmatch(database_sha256) or not SHA256_RE.fullmatch(checksum_sha256):
        raise RecoveryGenerationError("복구 manifest DB/checksum SHA-256이 올바르지 않습니다")
    database_length = _require_positive_int(database_record["byte_length"], label="DB 길이")
    checksum_length = _require_positive_int(
        database_record["checksum_byte_length"], label="DB checksum 길이"
    )

    artifacts_raw = payload["artifacts"]
    if not isinstance(artifacts_raw, list):
        raise RecoveryGenerationError("복구 manifest artifact 목록이 배열이 아닙니다")
    references: list[ArtifactReference] = []
    previous_key = ""
    expected_files = {
        DATABASE_NAME,
        DATABASE_CHECKSUM_NAME,
        MANIFEST_NAME,
        MANIFEST_CHECKSUM_NAME,
    }
    for raw in artifacts_raw:
        if not isinstance(raw, dict):
            raise RecoveryGenerationError("artifact manifest 항목이 객체가 아닙니다")
        _require_exact_keys(
            raw,
            {"artifact_ids", "blob_key", "byte_length", "path", "sha256"},
            label="artifact manifest",
        )
        blob_key = _safe_posix_relative(raw["blob_key"], label="artifact blob")
        digest = str(raw["sha256"] or "")
        if not SHA256_RE.fullmatch(digest) or blob_key != _expected_blob_key(digest):
            raise RecoveryGenerationError("artifact manifest 내용주소가 올바르지 않습니다")
        if blob_key <= previous_key:
            raise RecoveryGenerationError("artifact manifest 순서 또는 중복 key가 올바르지 않습니다")
        previous_key = blob_key
        byte_length = _require_positive_int(raw["byte_length"], label="artifact 길이")
        path = _safe_posix_relative(raw["path"], label="artifact 세대")
        if path != f"{ARTIFACT_DIRECTORY}/{digest}.blob":
            raise RecoveryGenerationError("artifact 세대 경로가 blob key와 맞지 않습니다")
        artifact_ids_raw = raw["artifact_ids"]
        if not isinstance(artifact_ids_raw, list) or not artifact_ids_raw:
            raise RecoveryGenerationError("artifact ID 목록이 비어 있습니다")
        artifact_ids = tuple(str(value or "").strip() for value in artifact_ids_raw)
        if (
            any(not value for value in artifact_ids)
            or tuple(sorted(set(artifact_ids))) != artifact_ids
        ):
            raise RecoveryGenerationError("artifact ID 목록이 정렬된 고유값이 아닙니다")
        reference = ArtifactReference(
            blob_key=blob_key,
            sha256=digest,
            byte_length=byte_length,
            artifact_ids=artifact_ids,
        )
        references.append(reference)
        expected_files.add(reference.generation_path)

    if initial_files != expected_files or initial_directories != _expected_directories(expected_files):
        raise RecoveryGenerationError("복구 세대에 manifest 밖의 누락/추가 파일 또는 폴더가 있습니다")

    actual_db_sha, actual_db_length = _hash_plain_file(
        root / DATABASE_NAME, label="DB snapshot"
    )
    actual_checksum_sha, actual_checksum_length = _hash_plain_file(
        root / DATABASE_CHECKSUM_NAME, label="DB checksum"
    )
    if (actual_db_sha, actual_db_length) != (database_sha256, database_length):
        raise RecoveryGenerationError("DB snapshot의 hash 또는 길이가 manifest와 다릅니다")
    if (actual_checksum_sha, actual_checksum_length) != (
        checksum_sha256,
        checksum_length,
    ):
        raise RecoveryGenerationError("DB checksum 파일의 hash 또는 길이가 manifest와 다릅니다")
    expected_db_checksum = f"{database_sha256}  {DATABASE_NAME}\n".encode("ascii")
    if (root / DATABASE_CHECKSUM_NAME).read_bytes() != expected_db_checksum:
        raise RecoveryGenerationError("DB checksum 파일이 같은 세대 DB에 결속되지 않았습니다")

    database_references = database_artifact_references(
        root / DATABASE_NAME,
        required_storage_identity=storage_identity,
    )
    if tuple(references) != database_references:
        raise RecoveryGenerationError("manifest artifact 목록이 DB snapshot 참조와 다릅니다")
    for reference in references:
        actual_sha, actual_length = _hash_plain_file(
            root.joinpath(*PurePosixPath(reference.generation_path).parts),
            label="복구 세대 artifact",
        )
        if (actual_sha, actual_length) != (reference.sha256, reference.byte_length):
            raise RecoveryGenerationError("복구 세대 artifact hash 또는 길이가 맞지 않습니다")

    final_files, final_directories = _scan_generation_entries(root)
    if (final_files, final_directories) != (initial_files, initial_directories):
        raise RecoveryGenerationError("복구 세대가 검증 중 바뀌었습니다")
    return GenerationVerification(
        generation_id=generation_id,
        database_path=root / DATABASE_NAME,
        database_sha256=database_sha256,
        artifact_count=len(references),
        artifact_bytes=sum(reference.byte_length for reference in references),
        artifact_storage_identity=storage_identity,
    )


__all__ = [
    "ArtifactReference",
    "DATABASE_CHECKSUM_NAME",
    "DATABASE_NAME",
    "GenerationBuild",
    "GenerationVerification",
    "MANIFEST_CHECKSUM_NAME",
    "MANIFEST_NAME",
    "RecoveryGenerationError",
    "artifact_storage_identity",
    "build_manifest",
    "copy_referenced_artifacts",
    "database_artifact_references",
    "verify_generation",
]
