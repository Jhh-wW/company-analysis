"""최초 승인 PDF bytes를 다시 그리지 않고 불변으로 보관한다."""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Iterator, Protocol

from src.features.report_delivery import store as lifecycle_store
from src.features.report_delivery.canonical import (
    canonical_digest,
    datetime_from_utc_text,
    require_aware,
    require_sha256_hex,
    sha256_hex,
    utc_text,
)
from src.features.report_delivery.models import Delivery, PDF_CHANNEL
from src.shared.bounded_file_lock import (
    BoundedFileLockError,
    BoundedFileLockTimeout,
    exclusive_file_lock,
)


TABLE_ARTIFACTS: Final[str] = "report_delivery_artifacts"
TABLE_DELIVERY_ARTIFACTS: Final[str] = "report_delivery_delivery_artifacts"
TABLE_BLOB_INTENTS: Final[str] = "artifact_blob_intents"
TABLE_BLOB_INTENT_EVENTS: Final[str] = "artifact_blob_intent_events"

# 배포 교체 중 이전 프로세스의 쓰기가 아직 끝나지 않았을 수 있다.
# 시작 정리는 오래된 intent만 다루고, 새 intent는 살아 있는 writer로 보존한다.
BLOB_INTENT_RECONCILE_GRACE: Final[dt.timedelta] = dt.timedelta(hours=24)
ARTIFACT_ROOT_LOCK_TIMEOUT_SECONDS: Final[float] = 10.0
_BLOB_TEMP_PREFIX: Final[str] = ".artifact-"
_BLOB_TEMP_SUFFIX: Final[str] = ".tmp"
_WINDOWS_LEGACY_MAX_PATH_CHARS: Final[int] = 259

_INTENT_CREATED: Final[str] = "created"
_INTENT_BLOB_STORED: Final[str] = "blob_stored"
_INTENT_BOUND: Final[str] = "bound"
_INTENT_RECLAIMED_DELETED: Final[str] = "reclaimed_deleted"
_INTENT_RECLAIMED_ABSENT: Final[str] = "reclaimed_absent"
_INTENT_TERMINAL_EVENTS: Final[frozenset[str]] = frozenset(
    {_INTENT_BOUND, _INTENT_RECLAIMED_DELETED, _INTENT_RECLAIMED_ABSENT}
)


# ``configured_artifact_backend()``는 요청마다 새 객체를 만든다. 객체별 lock이면
# 같은 디렉터리의 두 writer가 각각 옛 사용량을 보고 한도를 함께 넘어설 수 있다.
# 현재 배포 계약(worker=1) 안에서는 resolved root 하나가 lock 하나를 공유한다.
_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[Path, threading.RLock] = {}


def _lock_for_root(root: Path) -> threading.RLock:
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.get(root)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[root] = lock
        return lock


@contextmanager
def _bounded_thread_root_lock(
    lock: threading.RLock,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    effective_timeout = (
        ARTIFACT_ROOT_LOCK_TIMEOUT_SECONDS
        if timeout_seconds is None
        else timeout_seconds
    )
    if not lock.acquire(timeout=effective_timeout):
        raise ArtifactRootBusy(
            "artifact 저장소를 다른 thread가 오래 점유해 안전하게 처리할 수 없습니다"
        )
    try:
        yield
    finally:
        lock.release()


@contextmanager
def _cross_process_root_lock(
    root: Path,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """동일 blob root를 쓰는 다른 프로세스와 용량·삭제 판정을 직렬화한다.

    파일 잠금은 프로세스 메모리 lock의 바깥 경계다. 운영 Windows와
    POSIX 개발 환경을 파이썬 표준 라이브러리로만 다룬다.
    """

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".artifact-root.lock"
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
        raise ArtifactRootBusy(
            "artifact 저장소를 다른 process가 오래 점유해 안전하게 처리할 수 없습니다"
        ) from exc
    except BoundedFileLockError as exc:
        raise ArtifactError("artifact 저장소 잠금 파일을 신뢰할 수 없습니다") from exc


class ArtifactError(RuntimeError):
    """불변 출고물을 저장하거나 읽을 수 없다."""


class ArtifactCapacityExceeded(ArtifactError):
    """설정한 blob 저장 한도를 넘어서 자동 삭제 없이 저장을 거절했다."""


class ArtifactImmutableConflict(ArtifactError):
    """기존 blob 또는 metadata를 다른 값으로 덮어쓰려 했다."""


class ArtifactRootBusy(ArtifactError):
    """thread/process가 저장소 lock을 제한 시간 안에 놓지 않았다."""


def _exceeds_windows_legacy_path(
    path: Path,
    *,
    platform_name: str | None = None,
) -> bool:
    """long-path 설정에 기대지 않는 Windows filesystem backend 경계다."""

    effective_platform = os.name if platform_name is None else platform_name
    return (
        effective_platform == "nt"
        and len(str(path)) > _WINDOWS_LEGACY_MAX_PATH_CHARS
    )


def _fsync_directory(
    path: Path,
    *,
    platform_name: str | None = None,
) -> bool:
    """POSIX directory entry를 디스크에 봉인하고 그 밖의 OS는 정직하게 표시한다.

    파일 본문만 ``fsync``해도 새 파일명·삭제가 전원 중단 뒤 남는다는 보장은 없다.
    실제 배포인 Render/Linux에서는 부모 directory도 반드시 동기화한다. Windows의
    Python ``os.open``은 directory handle을 열 수 없으므로, 그 환경에서는 거짓
    내구성을 주장하지 않고 ``False``를 돌려준다. Windows 운영은 동일 보장을 주는
    별도 backend가 생기기 전까지 process-crash 안전성까지만 가진다.
    """

    effective_platform = os.name if platform_name is None else platform_name
    if effective_platform != "posix":
        return False
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ArtifactError(
            "artifact 파일명 변경을 디스크에 안전하게 확정하지 못했습니다"
        ) from exc
    return True


def _fsync_directory_chain(directory: Path, *, root: Path) -> None:
    """새 ``sha256/xx`` 경로의 모든 directory entry를 root까지 봉인한다."""

    current = directory
    while True:
        _fsync_directory(current)
        if current == root:
            return
        parent = current.parent
        try:
            current.relative_to(root)
        except ValueError as exc:  # pragma: no cover - _target의 선행 방어
            raise ArtifactError("artifact 경로가 저장소 root 밖입니다") from exc
        if parent == current:
            raise ArtifactError("artifact directory fsync 경계가 올바르지 않습니다")
        current = parent


class OrphanDeleteResult(str, Enum):
    DELETED = "deleted"
    ABSENT = "absent"
    MISMATCH = "mismatch"


class ArtifactOriginalState(str, Enum):
    STORED = "stored"
    LEGACY_ORIGINAL_UNKNOWN = "legacy_original_unknown"


class ArtifactInspectionStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    CORRUPT = "corrupt"
    LEGACY_ORIGINAL_UNKNOWN = "legacy_original_unknown"


@dataclass(frozen=True)
class BlobPointer:
    key: str
    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("artifact blob 위치가 필요합니다")
        require_sha256_hex(self.sha256, label="artifact blob")
        if self.byte_length <= 0:
            raise ValueError("artifact blob 길이는 0보다 커야 합니다")


@dataclass(frozen=True)
class BlobUsage:
    used_bytes: int
    capacity_bytes: int | None

    @property
    def remaining_bytes(self) -> int | None:
        if self.capacity_bytes is None:
            return None
        return max(0, self.capacity_bytes - self.used_bytes)


@dataclass(frozen=True)
class ArtifactBlobIntent:
    intent_id: str
    storage_identity: str
    pointer: BlobPointer
    created_at: dt.datetime

    def __post_init__(self) -> None:
        if not str(self.intent_id).strip() or not str(self.storage_identity).strip():
            raise ValueError("artifact blob intent 신원과 저장소가 필요합니다")
        require_aware(self.created_at, label="artifact blob intent 생성")


@dataclass(frozen=True)
class BlobIntentReconcileReport:
    examined: int = 0
    deleted: int = 0
    absent: int = 0
    bound_existing: int = 0
    kept_active: int = 0
    kept_mismatch: int = 0


class ArtifactBlobBackend(Protocol):
    """파일시스템·object storage를 교체할 수 있는 최소 불변 blob 계약.

    일반 ``delete``는 없다. 단, 별도 커밋된 intent와 DB 재조회가 삭제를
    허용한 정확한 hash·길이의 blob만 목적별 메서드로 회수한다.
    """

    def put_immutable(self, *, sha256: str, data: bytes) -> BlobPointer: ...

    def read(self, pointer: BlobPointer) -> bytes | None: ...

    def usage(self) -> BlobUsage: ...

    @property
    def storage_identity(self) -> str: ...

    def expected_pointer(self, *, sha256: str, byte_length: int) -> BlobPointer: ...

    def delete_orphan_if_exact(self, pointer: BlobPointer) -> OrphanDeleteResult: ...

    def delete_retired_if_exact(self, pointer: BlobPointer) -> OrphanDeleteResult: ...


class FilesystemArtifactBlobBackend:
    """한 디렉터리에 내용주소 blob을 write-once로 보관한다.

    Render 1GB disk 같은 작은 저장소에서는 ``capacity_bytes``를 실제 artifact
    예산으로 설정한다. 넘으면 과거 파일을 지우지 않고 명시적으로 실패한다.
    """

    def __init__(self, root: Path, *, capacity_bytes: int | None = None) -> None:
        resolved = Path(root).resolve()
        if capacity_bytes is not None and capacity_bytes <= 0:
            raise ValueError("artifact 저장 한도는 0보다 커야 합니다")
        # 배포 대상인 Linux에는 이 제한이 없지만, 로컬 Windows가 long-path를
        # 허용하지 않는 경우 최종 blob보다 긴 임시명/잠금명이 뒤늦게 원문 경로를
        # 담은 FileNotFoundError로 샜다. 내용주소의 최종 경로는 길이가 고정이므로
        # backend를 만든 경계에서 지원 여부를 명확히 닫는다.
        sample_target = resolved / "sha256" / "ff" / ("f" * 64 + ".blob")
        if _exceeds_windows_legacy_path(sample_target):
            raise ArtifactError(
                "artifact 저장 경로가 Windows 지원 길이를 넘습니다"
            )
        # backend 생성은 조회 경계에서도 일어난다. 디렉터리는 최초 쓰기 때만
        # 만들고, 존재하지 않는 public_id의 GET이 디스크를 바꾸지 않게 한다.
        self._root = resolved
        self._capacity_bytes = capacity_bytes
        self._lock = _lock_for_root(resolved)

    @property
    def storage_identity(self) -> str:
        # Windows의 드라이브/대소문자 별칭을 같은 root로 인식한다.
        return "filesystem:" + os.path.normcase(str(self._root))

    def expected_pointer(self, *, sha256: str, byte_length: int) -> BlobPointer:
        digest = str(sha256).strip().lower()
        require_sha256_hex(digest, label="artifact blob")
        if byte_length <= 0:
            raise ArtifactError("artifact blob 길이는 0보다 커야 합니다")
        return BlobPointer(
            key=f"sha256/{digest[:2]}/{digest}.blob",
            sha256=digest,
            byte_length=byte_length,
        )

    def _target(self, key: str) -> Path:
        relative = Path(str(key))
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ArtifactError("artifact blob 경로가 상대 내용주소가 아닙니다")
        target = self._root / relative
        # 마지막 파일 자체를 resolve하면 symlink였다는 사실이 사라져 삭제
        # 검사에서 실제 대상을 지우게 된다. 부모만 정규화하고, 경로 중간의
        # symlink도 모두 거부한 뒤 원래 target을 돌려준다.
        current = self._root
        for part in relative.parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ArtifactError("artifact blob 부모 경로가 symlink입니다")
        resolved_parent = target.parent.resolve()
        try:
            resolved_parent.relative_to(self._root)
        except ValueError as exc:
            raise ArtifactError("artifact blob 경로가 저장소 밖을 가리킵니다") from exc
        return target

    def _usage_unlocked(self) -> BlobUsage:
        used = sum(
            path.stat().st_size
            for path in self._root.rglob("*.blob")
            if path.is_file()
        )
        return BlobUsage(used_bytes=used, capacity_bytes=self._capacity_bytes)

    def usage(self) -> BlobUsage:
        with _bounded_thread_root_lock(self._lock):
            # 읽기 전용 조회가 없는 root를 생성하지 않는 기존 계약.
            if not self._root.exists():
                return BlobUsage(used_bytes=0, capacity_bytes=self._capacity_bytes)
            with _cross_process_root_lock(self._root):
                return self._usage_unlocked()

    def put_immutable(self, *, sha256: str, data: bytes) -> BlobPointer:
        digest = str(sha256).strip().lower()
        if len(digest) != 64 or sha256_hex(data) != digest:
            raise ArtifactError("artifact bytes와 SHA-256이 맞지 않습니다")
        pointer = self.expected_pointer(sha256=digest, byte_length=len(data))
        target = self._target(pointer.key)
        with _bounded_thread_root_lock(self._lock):
            with _cross_process_root_lock(self._root):
                if target.exists():
                    if target.is_symlink() or not target.is_file():
                        raise ArtifactImmutableConflict(
                            "기존 artifact blob 경로가 일반 파일이 아닙니다"
                        )
                    existing = target.read_bytes()
                    if sha256_hex(existing) != digest or len(existing) != len(data):
                        raise ArtifactImmutableConflict(
                            "같은 내용주소의 기존 artifact blob이 손상됐습니다"
                        )
                    # 이전 시도가 hard-link 뒤 중단됐을 수 있다. 같은 bytes를
                    # 발견한 재시도도 directory entry를 봉인한 뒤에만 성공한다.
                    _fsync_directory_chain(target.parent, root=self._root)
                    return pointer
                usage = self._usage_unlocked()
                if (
                    usage.capacity_bytes is not None
                    and usage.used_bytes + len(data) > usage.capacity_bytes
                ):
                    raise ArtifactCapacityExceeded(
                        "artifact 저장 한도가 부족합니다; 자동으로 과거 원본을 지우지 않았습니다"
                    )
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise ArtifactError(
                        "artifact blob 부모 디렉터리를 만들 수 없습니다"
                    ) from exc
                if target.parent.is_symlink() or not target.parent.is_dir():
                    raise ArtifactError(
                        "artifact blob 부모가 symlink 없는 디렉터리가 아닙니다"
                    )
                # 새 shard directory 자체가 root/sha256 아래에 생겼다는 사실부터
                # 봉인한다. 이 단계가 빠지면 전원 중단 뒤 부모만 사라질 수 있고,
                # mkstemp가 실패한 경우도 원인 없는 FileNotFoundError로 샌다.
                _fsync_directory_chain(target.parent, root=self._root)
                try:
                    descriptor, temporary_name = tempfile.mkstemp(
                        # digest는 최종 내용주소와 bytes 검증에 이미 있다. 임시명에
                        # 64자를 반복하면 Windows에서 최종 blob은 들어가는데 임시
                        # 파일만 MAX_PATH를 넘어 출고가 실패한다.
                        prefix=_BLOB_TEMP_PREFIX,
                        suffix=_BLOB_TEMP_SUFFIX,
                        dir=target.parent,
                    )
                except OSError as exc:
                    raise ArtifactError(
                        "artifact blob 임시 파일을 안전한 부모에 만들 수 없습니다"
                    ) from exc
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        # hard link는 기존 target을 덮어쓰지 않는다. 동시에 같은
                        # digest를 쓰면 한쪽만 이기고 bytes 동일성을 다시 본다.
                        os.link(temporary, target)
                    except FileExistsError:
                        if target.is_symlink() or not target.is_file():
                            raise ArtifactImmutableConflict(
                                "동시에 생긴 artifact blob 경로가 일반 파일이 아닙니다"
                            )
                        existing = target.read_bytes()
                        if sha256_hex(existing) != digest or len(existing) != len(data):
                            raise ArtifactImmutableConflict(
                                "동시에 저장된 artifact blob의 내용이 다릅니다"
                            )
                    except OSError as exc:
                        # Windows MAX_PATH, 권한, 디스크/파일시스템 장애의 원래
                        # 예외에는 절대 경로가 들어갈 수 있다. 외부에는 고정된
                        # 저장 계약 오류만 내보내고 metadata commit은 허용하지 않는다.
                        raise ArtifactError(
                            "artifact 최종 내용주소 파일을 만들 수 없습니다"
                        ) from exc
                finally:
                    temporary.unlink(missing_ok=True)
                # 이 호출이 성공한 뒤 metadata transaction이 commit될 수 있다.
                # 따라서 파일 본문·새 파일명·새 부모 directory와 임시 hard-link
                # 제거까지 먼저 전원 중단에 견디게 확정해야 한다.
                _fsync_directory_chain(target.parent, root=self._root)
        return pointer

    def read(self, pointer: BlobPointer) -> bytes | None:
        target = self._target(pointer.key)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            # inspect_artifact가 checksum 불일치인 CORRUPT로 닫도록 빈 bytes를
            # 돌려준다. 올바른 내용의 symlink도 승인 원본으로 인정하지 않는다.
            return b""
        if not target.exists():
            return None
        return target.read_bytes()

    def _delete_if_exact(self, pointer: BlobPointer) -> OrphanDeleteResult:
        expected = self.expected_pointer(
            sha256=pointer.sha256,
            byte_length=pointer.byte_length,
        )
        if expected != pointer:
            return OrphanDeleteResult.MISMATCH
        target = self._target(pointer.key)
        with _bounded_thread_root_lock(self._lock):
            with _cross_process_root_lock(self._root):
                if not target.exists():
                    return OrphanDeleteResult.ABSENT
                if target.is_symlink() or not target.is_file():
                    return OrphanDeleteResult.MISMATCH
                data = target.read_bytes()
                if (
                    len(data) != pointer.byte_length
                    or sha256_hex(data) != pointer.sha256
                ):
                    return OrphanDeleteResult.MISMATCH
                target.unlink()
                # DB에는 이 반환 뒤 terminal 사건이 commit된다. 삭제 directory
                # entry를 먼저 봉인하지 않으면 전원 중단 뒤 파일만 되살아난다.
                _fsync_directory(target.parent)
                return OrphanDeleteResult.DELETED

    def delete_orphan_if_exact(self, pointer: BlobPointer) -> OrphanDeleteResult:
        """미결속 쓰기 intent의 고아 blob만 내용 검증 후 지운다."""

        return self._delete_if_exact(pointer)

    def delete_retired_if_exact(self, pointer: BlobPointer) -> OrphanDeleteResult:
        """정리 intent가 DB 참조 소멸을 입증한 blob만 정확히 회수한다.

        호출자는 ``BEGIN IMMEDIATE`` 안에서 참조를 삭제 직전 다시 읽어야
        한다. 이 메서드는 같은 root의 프로세스·프로세스 간 lock과 실제
        hash·길이·symlink 검사를 맡는다.
        """

        return self._delete_if_exact(pointer)


@dataclass(frozen=True)
class ArtifactVersion:
    renderer_version: str
    font_bundle_version: str
    checker_version: str
    format_version: str = "pdf"

    def __post_init__(self) -> None:
        values = (
            self.renderer_version,
            self.font_bundle_version,
            self.checker_version,
            self.format_version,
        )
        if any(not str(value).strip() for value in values):
            raise ValueError("renderer·font·checker·format 버전을 모두 기록해야 합니다")

    @classmethod
    def legacy_unknown(cls) -> "ArtifactVersion":
        return cls(
            renderer_version="legacy-unknown",
            font_bundle_version="legacy-unknown",
            checker_version="legacy-unknown",
            format_version="pdf",
        )


def _stored_artifact_id(
    *,
    content_snapshot_id: str,
    channel: str,
    bytes_sha256: str,
    version: ArtifactVersion,
) -> str:
    return "artifact_" + canonical_digest(
        {
            "content_snapshot_id": content_snapshot_id,
            "channel": channel,
            "bytes_sha256": bytes_sha256,
            "renderer_version": version.renderer_version,
            "font_bundle_version": version.font_bundle_version,
            "checker_version": version.checker_version,
            "format_version": version.format_version,
        }
    )


def _legacy_artifact_id(
    *, content_snapshot_id: str, channel: str, legacy_reference: str
) -> str:
    return "artifact_" + canonical_digest(
        {
            "content_snapshot_id": content_snapshot_id,
            "channel": channel,
            "original_state": ArtifactOriginalState.LEGACY_ORIGINAL_UNKNOWN.value,
            "legacy_reference": legacy_reference,
        }
    )


@dataclass(frozen=True)
class ArtifactRetention:
    policy_id: str
    retain_until: dt.datetime | None
    legal_hold: bool = False

    def __post_init__(self) -> None:
        if not str(self.policy_id).strip():
            raise ValueError("artifact 보존 정책 ID가 필요합니다")
        if self.retain_until is not None:
            require_aware(self.retain_until, label="artifact 보존")

    def review_is_due(self, *, now: dt.datetime) -> bool:
        current = require_aware(now, label="artifact 보존 검토")
        if self.legal_hold or self.retain_until is None:
            return False
        return current >= require_aware(self.retain_until, label="artifact 보존")


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_id: str
    content_snapshot_id: str
    channel: str
    original_state: ArtifactOriginalState
    blob_pointer: BlobPointer | None
    version: ArtifactVersion
    created_at: dt.datetime
    retention: ArtifactRetention
    legacy_reference: str = ""

    def __post_init__(self) -> None:
        if not self.content_snapshot_id.strip() or not self.channel.strip():
            raise ValueError("artifact의 내용 원본 ID와 channel이 필요합니다")
        require_aware(self.created_at, label="artifact 생성")
        if self.original_state is ArtifactOriginalState.STORED:
            if self.blob_pointer is None:
                raise ValueError("저장된 artifact에 blob 위치가 없습니다")
            if self.legacy_reference:
                raise ValueError("저장된 artifact에 과거 reference를 섞을 수 없습니다")
            expected = _stored_artifact_id(
                content_snapshot_id=self.content_snapshot_id,
                channel=self.channel,
                bytes_sha256=self.blob_pointer.sha256,
                version=self.version,
            )
        elif self.original_state is ArtifactOriginalState.LEGACY_ORIGINAL_UNKNOWN:
            if self.blob_pointer is not None:
                raise ValueError("최초 원본을 모르는 artifact에 blob을 연결할 수 없습니다")
            if not self.legacy_reference.strip():
                raise ValueError("과거 artifact reference가 필요합니다")
            expected = _legacy_artifact_id(
                content_snapshot_id=self.content_snapshot_id,
                channel=self.channel,
                legacy_reference=self.legacy_reference,
            )
        else:  # pragma: no cover - Enum 타입 방어선
            raise ValueError("artifact 원본 상태를 알 수 없습니다")
        if self.artifact_id != expected:
            raise ValueError("저장된 artifact ID가 원본·버전 신원과 맞지 않습니다")


@dataclass(frozen=True)
class ArtifactInspection:
    metadata: ArtifactMetadata
    status: ArtifactInspectionStatus
    pdf_bytes: bytes | None = None


_ARTIFACT_SCHEMA: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_ARTIFACTS} (
        artifact_id          TEXT PRIMARY KEY,
        content_snapshot_id  TEXT NOT NULL
                             REFERENCES {lifecycle_store.TABLE_CONTENT_SNAPSHOTS}(content_id),
        channel              TEXT NOT NULL,
        original_state       TEXT NOT NULL,
        blob_key             TEXT NOT NULL,
        bytes_sha256         TEXT NOT NULL,
        byte_length          INTEGER NOT NULL,
        renderer_version     TEXT NOT NULL,
        font_bundle_version  TEXT NOT NULL,
        checker_version      TEXT NOT NULL,
        format_version       TEXT NOT NULL,
        created_at           TEXT NOT NULL,
        retention_policy_id  TEXT NOT NULL,
        retain_until         TEXT NOT NULL,
        legal_hold           INTEGER NOT NULL CHECK(legal_hold IN (0, 1)),
        legacy_reference     TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_DELIVERY_ARTIFACTS} (
        delivery_id  TEXT NOT NULL
                     REFERENCES {lifecycle_store.TABLE_DELIVERIES}(delivery_id),
        channel      TEXT NOT NULL,
        artifact_id  TEXT NOT NULL REFERENCES {TABLE_ARTIFACTS}(artifact_id),
        PRIMARY KEY (delivery_id, channel)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_BLOB_INTENTS} (
        intent_id          TEXT PRIMARY KEY,
        storage_identity   TEXT NOT NULL,
        blob_key           TEXT NOT NULL,
        bytes_sha256       TEXT NOT NULL,
        byte_length        INTEGER NOT NULL CHECK(byte_length > 0),
        created_at         TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_BLOB_INTENT_EVENTS} (
        event_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        intent_id          TEXT NOT NULL REFERENCES {TABLE_BLOB_INTENTS}(intent_id),
        event_type         TEXT NOT NULL CHECK(event_type IN (
            '{_INTENT_CREATED}', '{_INTENT_BLOB_STORED}', '{_INTENT_BOUND}',
            '{_INTENT_RECLAIMED_DELETED}', '{_INTENT_RECLAIMED_ABSENT}'
        )),
        artifact_id        TEXT NOT NULL,
        recorded_at        TEXT NOT NULL
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_artifact_blob_intent_events_latest
    ON {TABLE_BLOB_INTENT_EVENTS}(intent_id, event_id DESC)
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS artifact_blob_intent_events_valid_transition
    BEFORE INSERT ON {TABLE_BLOB_INTENT_EVENTS}
    WHEN NOT (
        (
            NEW.event_type = '{_INTENT_CREATED}'
            AND NEW.artifact_id = ''
            AND NOT EXISTS (
                SELECT 1 FROM {TABLE_BLOB_INTENT_EVENTS}
                WHERE intent_id = NEW.intent_id
            )
        )
        OR
        (
            NEW.event_type = '{_INTENT_BLOB_STORED}'
            AND NEW.artifact_id = ''
            AND (
                SELECT event_type FROM {TABLE_BLOB_INTENT_EVENTS}
                WHERE intent_id = NEW.intent_id
                ORDER BY event_id DESC LIMIT 1
            ) = '{_INTENT_CREATED}'
        )
        OR
        (
            NEW.event_type = '{_INTENT_BOUND}'
            AND NEW.artifact_id <> ''
            AND (
                SELECT event_type FROM {TABLE_BLOB_INTENT_EVENTS}
                WHERE intent_id = NEW.intent_id
                ORDER BY event_id DESC LIMIT 1
            ) IN ('{_INTENT_CREATED}', '{_INTENT_BLOB_STORED}')
            AND EXISTS (
                SELECT 1
                FROM {TABLE_BLOB_INTENTS} AS intents
                JOIN {TABLE_ARTIFACTS} AS artifacts
                  ON artifacts.artifact_id = NEW.artifact_id
                 AND artifacts.blob_key = intents.blob_key
                 AND artifacts.bytes_sha256 = intents.bytes_sha256
                 AND artifacts.byte_length = intents.byte_length
                WHERE intents.intent_id = NEW.intent_id
            )
        )
        OR
        (
            NEW.event_type IN (
                '{_INTENT_RECLAIMED_DELETED}', '{_INTENT_RECLAIMED_ABSENT}'
            )
            AND NEW.artifact_id = ''
            AND (
                SELECT event_type FROM {TABLE_BLOB_INTENT_EVENTS}
                WHERE intent_id = NEW.intent_id
                ORDER BY event_id DESC LIMIT 1
            ) IN ('{_INTENT_CREATED}', '{_INTENT_BLOB_STORED}')
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'invalid artifact blob intent transition');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS artifact_blob_intents_no_update
    BEFORE UPDATE ON {TABLE_BLOB_INTENTS}
    BEGIN
        SELECT RAISE(ABORT, 'artifact blob intent is immutable');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS artifact_blob_intents_no_delete
    BEFORE DELETE ON {TABLE_BLOB_INTENTS}
    BEGIN
        SELECT RAISE(ABORT, 'artifact blob intent is immutable');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS artifact_blob_intent_events_no_update
    BEFORE UPDATE ON {TABLE_BLOB_INTENT_EVENTS}
    BEGIN
        SELECT RAISE(ABORT, 'artifact blob intent events are append-only');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS artifact_blob_intent_events_no_delete
    BEFORE DELETE ON {TABLE_BLOB_INTENT_EVENTS}
    BEGIN
        SELECT RAISE(ABORT, 'artifact blob intent events are append-only');
    END
    """,
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """영속 schema registry가 이 모듈 소유 표만 준비하는 진입점."""

    for statement in _ARTIFACT_SCHEMA:
        conn.execute(statement)


def ensure_artifact_schema(conn: sqlite3.Connection) -> None:
    """단독 adapter에서 선행 내용 표까지 함께 준비한다."""

    lifecycle_store.ensure_schema(conn)
    ensure_schema(conn)


def _intent_from_row(row: sqlite3.Row | tuple[object, ...]) -> ArtifactBlobIntent:
    try:
        return ArtifactBlobIntent(
            intent_id=str(row[0]),
            storage_identity=str(row[1]),
            pointer=BlobPointer(
                key=str(row[2]),
                sha256=str(row[3]),
                byte_length=int(row[4]),
            ),
            created_at=datetime_from_utc_text(row[5], label="artifact blob intent 생성"),
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactError("artifact blob intent가 손상됐습니다") from exc


def load_blob_write_intent(
    conn: sqlite3.Connection,
    intent_id: str,
) -> ArtifactBlobIntent | None:
    ensure_artifact_schema(conn)
    row = conn.execute(
        f"""
        SELECT intent_id, storage_identity, blob_key, bytes_sha256,
               byte_length, created_at
        FROM {TABLE_BLOB_INTENTS} WHERE intent_id = ?
        """,
        (str(intent_id).strip(),),
    ).fetchone()
    return None if row is None else _intent_from_row(row)


def _latest_intent_event(
    conn: sqlite3.Connection,
    intent_id: str,
) -> tuple[str, str] | None:
    row = conn.execute(
        f"""
        SELECT event_type, artifact_id
        FROM {TABLE_BLOB_INTENT_EVENTS}
        WHERE intent_id = ? ORDER BY event_id DESC LIMIT 1
        """,
        (str(intent_id).strip(),),
    ).fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


def _append_intent_event(
    conn: sqlite3.Connection,
    *,
    intent_id: str,
    event_type: str,
    artifact_id: str = "",
    recorded_at: dt.datetime,
) -> None:
    conn.execute(
        f"""
        INSERT INTO {TABLE_BLOB_INTENT_EVENTS}
            (intent_id, event_type, artifact_id, recorded_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            str(intent_id).strip(),
            event_type,
            str(artifact_id).strip(),
            utc_text(recorded_at, label="artifact blob intent 이벤트"),
        ),
    )


def create_blob_write_intent(
    conn: sqlite3.Connection,
    backend: ArtifactBlobBackend,
    *,
    pdf_bytes: bytes,
    created_at: dt.datetime,
) -> ArtifactBlobIntent:
    """blob보다 먼저 별도 커밋할 불변 쓰기 intent를 만든다.

    이 함수 자체가 임의로 caller transaction을 commit하지는 않는다.
    운영 adapter는 전용 ``storage_db.connect()`` 블록에서 이 함수를
    호출하고 블록을 닫은 뒤에만 blob·본 transaction을 시작한다.
    """

    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        raise ArtifactError("승인 PDF bytes가 비어 있습니다")
    ensure_artifact_schema(conn)
    created = require_aware(created_at, label="artifact blob intent 생성")
    digest = sha256_hex(pdf_bytes)
    pointer = backend.expected_pointer(sha256=digest, byte_length=len(pdf_bytes))
    intent = ArtifactBlobIntent(
        intent_id="blob_intent_" + uuid.uuid4().hex,
        storage_identity=backend.storage_identity,
        pointer=pointer,
        created_at=created,
    )
    conn.execute(
        f"""
        INSERT INTO {TABLE_BLOB_INTENTS} (
            intent_id, storage_identity, blob_key, bytes_sha256,
            byte_length, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            intent.intent_id,
            intent.storage_identity,
            pointer.key,
            pointer.sha256,
            pointer.byte_length,
            utc_text(created, label="artifact blob intent 생성"),
        ),
    )
    _append_intent_event(
        conn,
        intent_id=intent.intent_id,
        event_type=_INTENT_CREATED,
        recorded_at=created,
    )
    return intent


def _validated_blob_intent(
    conn: sqlite3.Connection,
    backend: ArtifactBlobBackend,
    *,
    intent: ArtifactBlobIntent,
    digest: str,
    byte_length: int,
) -> tuple[ArtifactBlobIntent, tuple[str, str]]:
    persisted = load_blob_write_intent(conn, intent.intent_id)
    if persisted is None or persisted != intent:
        raise ArtifactError("DB에 먼저 확정된 artifact blob intent가 아닙니다")
    expected = backend.expected_pointer(sha256=digest, byte_length=byte_length)
    if (
        persisted.storage_identity != backend.storage_identity
        or persisted.pointer != expected
    ):
        raise ArtifactError("artifact blob intent의 root·경로·hash·길이가 다릅니다")
    latest = _latest_intent_event(conn, persisted.intent_id)
    if latest is None:
        raise ArtifactError("artifact blob intent의 최초 이벤트가 없습니다")
    return persisted, latest


def _record_blob_stored(
    conn: sqlite3.Connection,
    *,
    intent: ArtifactBlobIntent,
    recorded_at: dt.datetime,
) -> None:
    latest = _latest_intent_event(conn, intent.intent_id)
    if latest is None or latest[0] not in {_INTENT_CREATED, _INTENT_BLOB_STORED}:
        raise ArtifactError("종료된 artifact blob intent로 파일을 저장할 수 없습니다")
    if latest[0] == _INTENT_BLOB_STORED:
        return
    _append_intent_event(
        conn,
        intent_id=intent.intent_id,
        event_type=_INTENT_BLOB_STORED,
        recorded_at=recorded_at,
    )


def _bind_blob_intent(
    conn: sqlite3.Connection,
    *,
    intent: ArtifactBlobIntent,
    metadata: ArtifactMetadata,
    recorded_at: dt.datetime,
) -> None:
    pointer = metadata.blob_pointer
    if pointer is None or pointer != intent.pointer:
        raise ArtifactError("artifact metadata와 blob intent의 pointer가 다릅니다")
    latest = _latest_intent_event(conn, intent.intent_id)
    if latest == (_INTENT_BOUND, metadata.artifact_id):
        return
    if latest is None or latest[0] not in {_INTENT_CREATED, _INTENT_BLOB_STORED}:
        raise ArtifactError("종료된 artifact blob intent를 다른 artifact에 결속할 수 없습니다")
    _append_intent_event(
        conn,
        intent_id=intent.intent_id,
        event_type=_INTENT_BOUND,
        artifact_id=metadata.artifact_id,
        recorded_at=recorded_at,
    )


def _metadata_row(metadata: ArtifactMetadata) -> tuple[object, ...]:
    pointer = metadata.blob_pointer
    return (
        metadata.artifact_id,
        metadata.content_snapshot_id,
        metadata.channel,
        metadata.original_state.value,
        pointer.key if pointer else "",
        pointer.sha256 if pointer else "",
        pointer.byte_length if pointer else 0,
        metadata.version.renderer_version,
        metadata.version.font_bundle_version,
        metadata.version.checker_version,
        metadata.version.format_version,
        utc_text(metadata.created_at, label="artifact 생성"),
        metadata.retention.policy_id,
        (
            utc_text(metadata.retention.retain_until, label="artifact 보존")
            if metadata.retention.retain_until is not None
            else ""
        ),
        1 if metadata.retention.legal_hold else 0,
        metadata.legacy_reference,
    )


def _save_metadata(conn: sqlite3.Connection, metadata: ArtifactMetadata) -> None:
    ensure_artifact_schema(conn)
    payload = _metadata_row(metadata)
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_ARTIFACTS} (
            artifact_id, content_snapshot_id, channel, original_state,
            blob_key, bytes_sha256, byte_length, renderer_version,
            font_bundle_version, checker_version, format_version, created_at,
            retention_policy_id, retain_until, legal_hold, legacy_reference
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"SELECT * FROM {TABLE_ARTIFACTS} WHERE artifact_id = ?",
        (metadata.artifact_id,),
    ).fetchone()
    if existing is None or tuple(existing) != payload:
        raise ArtifactImmutableConflict("같은 artifact ID를 다른 값으로 덮어쓸 수 없습니다")


def _raw_artifact_references_for_pointer(
    conn: sqlite3.Connection,
    pointer: BlobPointer,
) -> tuple[tuple[str, str, str, int], ...]:
    # blob_key가 같은데 hash/길이가 다르면 DB 손상으로 보고 삭제를
    # 금지한다. 정상 일치만 자동으로 bound 상태로 종료할 수 있다.
    rows = conn.execute(
        f"""
        SELECT artifact_id, blob_key, bytes_sha256, byte_length
        FROM {TABLE_ARTIFACTS} WHERE blob_key = ?
        ORDER BY artifact_id
        """,
        (pointer.key,),
    ).fetchall()
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]), int(row[3])) for row in rows
    )


def _all_blob_intents(conn: sqlite3.Connection) -> tuple[ArtifactBlobIntent, ...]:
    rows = conn.execute(
        f"""
        SELECT intent_id, storage_identity, blob_key, bytes_sha256,
               byte_length, created_at
        FROM {TABLE_BLOB_INTENTS} ORDER BY created_at, intent_id
        """
    ).fetchall()
    return tuple(_intent_from_row(row) for row in rows)


def _pointer_is_protected_by_other_intent(
    conn: sqlite3.Connection,
    *,
    candidate: ArtifactBlobIntent,
    cutoff: dt.datetime,
) -> bool:
    for other in _all_blob_intents(conn):
        if (
            other.intent_id == candidate.intent_id
            or other.storage_identity != candidate.storage_identity
            or other.pointer != candidate.pointer
        ):
            continue
        latest = _latest_intent_event(conn, other.intent_id)
        if latest is None:
            # 이벤트가 없는 손상 intent도 자동 삭제의 근거가 될 수 없다.
            return True
        if latest[0] == _INTENT_BOUND:
            return True
        if latest[0] not in _INTENT_TERMINAL_EVENTS and other.created_at > cutoff:
            return True
    return False


def reconcile_blob_write_intents(
    conn: sqlite3.Connection,
    backend: ArtifactBlobBackend,
    *,
    now: dt.datetime,
    grace: dt.timedelta = BLOB_INTENT_RECONCILE_GRACE,
) -> BlobIntentReconcileReport:
    """오래된 미결속 intent의 고아 blob만 시작 시점에 보수적으로 정리한다.

    caller는 전용 DB 연결에서 호출해야 한다. 함수가 ``BEGIN
    IMMEDIATE``를 먼저 얻으므로, 아래의 참조·활성 intent 재조회와
    파일 삭제 사이에 다른 정상 writer가 metadata를 결속할 수 없다.
    이미 다른 transaction에 속한 연결은 caller가 직렬화를 보장해야 한다.
    """

    ensure_artifact_schema(conn)
    current = require_aware(now, label="artifact blob intent 정리")
    if grace <= dt.timedelta(0):
        raise ValueError("artifact blob intent 유예 기간은 0보다 커야 합니다")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    cutoff = current - grace
    examined = deleted = absent = bound_existing = kept_active = kept_mismatch = 0

    # 후보 목록은 스냅샷일 뿐이다. 삭제 직전에 모든 불변식을
    # 동일 IMMEDIATE transaction 안에서 다시 읽는다.
    candidates = tuple(
        intent
        for intent in _all_blob_intents(conn)
        if intent.storage_identity == backend.storage_identity
        and intent.created_at <= cutoff
        and (
            (latest := _latest_intent_event(conn, intent.intent_id)) is not None
            and latest[0] not in _INTENT_TERMINAL_EVENTS
        )
    )
    for intent in candidates:
        examined += 1
        latest = _latest_intent_event(conn, intent.intent_id)
        if latest is None or latest[0] in _INTENT_TERMINAL_EVENTS:
            kept_active += 1
            continue
        references = _raw_artifact_references_for_pointer(conn, intent.pointer)
        exact_reference = next(
            (
                artifact_id
                for artifact_id, key, digest, length in references
                if (key, digest, length)
                == (
                    intent.pointer.key,
                    intent.pointer.sha256,
                    intent.pointer.byte_length,
                )
            ),
            "",
        )
        if exact_reference:
            _append_intent_event(
                conn,
                intent_id=intent.intent_id,
                event_type=_INTENT_BOUND,
                artifact_id=exact_reference,
                recorded_at=current,
            )
            bound_existing += 1
            continue
        if references:
            kept_mismatch += 1
            continue
        if _pointer_is_protected_by_other_intent(
            conn,
            candidate=intent,
            cutoff=cutoff,
        ):
            kept_active += 1
            continue

        # 삭제 직전 재조회. BEGIN IMMEDIATE가 다른 DB writer를 막고,
        # backend root file lock이 다른 프로세스의 put/delete를 막는다.
        latest = _latest_intent_event(conn, intent.intent_id)
        references = _raw_artifact_references_for_pointer(conn, intent.pointer)
        protected = _pointer_is_protected_by_other_intent(
            conn,
            candidate=intent,
            cutoff=cutoff,
        )
        if (
            latest is None
            or latest[0] in _INTENT_TERMINAL_EVENTS
            or references
            or protected
        ):
            kept_active += 1
            continue
        result = backend.delete_orphan_if_exact(intent.pointer)
        if result is OrphanDeleteResult.MISMATCH:
            kept_mismatch += 1
            continue
        event_type = (
            _INTENT_RECLAIMED_DELETED
            if result is OrphanDeleteResult.DELETED
            else _INTENT_RECLAIMED_ABSENT
        )
        _append_intent_event(
            conn,
            intent_id=intent.intent_id,
            event_type=event_type,
            recorded_at=current,
        )
        if result is OrphanDeleteResult.DELETED:
            deleted += 1
        else:
            absent += 1
    return BlobIntentReconcileReport(
        examined=examined,
        deleted=deleted,
        absent=absent,
        bound_existing=bound_existing,
        kept_active=kept_active,
        kept_mismatch=kept_mismatch,
    )


def store_approved_pdf(
    conn: sqlite3.Connection,
    backend: ArtifactBlobBackend,
    *,
    blob_intent: ArtifactBlobIntent | None,
    content_snapshot_id: str,
    pdf_bytes: bytes,
    version: ArtifactVersion,
    created_at: dt.datetime,
    retention: ArtifactRetention,
) -> ArtifactMetadata:
    """최초 승인 bytes를 내용주소 blob과 불변 metadata로 함께 결속한다."""

    content_id = str(content_snapshot_id).strip()
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        raise ArtifactError("승인 PDF bytes가 비어 있습니다")
    if lifecycle_store.load_content_snapshot(conn, content_id) is None:
        raise ArtifactError("내용 원본을 먼저 저장해야 PDF artifact를 만들 수 있습니다")
    created = require_aware(created_at, label="artifact 생성")
    digest = sha256_hex(pdf_bytes)
    artifact_id = _stored_artifact_id(
        content_snapshot_id=content_id,
        channel=PDF_CHANNEL,
        bytes_sha256=digest,
        version=version,
    )
    existing = load_artifact_metadata(conn, artifact_id)
    if existing is not None:
        # DB까지 확정된 artifact는 재시도에서 현재 renderer로 고치거나 blob을
        # 다시 쓰지 않는다. missing/corrupt는 inspect 결과로 운영에 드러낸다.
        if blob_intent is not None:
            persisted_intent, _latest = _validated_blob_intent(
                conn,
                backend,
                intent=blob_intent,
                digest=digest,
                byte_length=len(pdf_bytes),
            )
            _bind_blob_intent(
                conn,
                intent=persisted_intent,
                metadata=existing,
                recorded_at=created,
            )
        return existing
    if blob_intent is None:
        raise ArtifactError(
            "새 artifact blob을 쓰기 전에 별도 커밋된 intent가 필요합니다"
        )
    persisted_intent, _latest = _validated_blob_intent(
        conn,
        backend,
        intent=blob_intent,
        digest=digest,
        byte_length=len(pdf_bytes),
    )
    pointer = backend.put_immutable(sha256=digest, data=pdf_bytes)
    if pointer != persisted_intent.pointer:
        raise ArtifactError("backend이 blob intent와 다른 pointer를 반환했습니다")
    _record_blob_stored(
        conn,
        intent=persisted_intent,
        recorded_at=created,
    )
    metadata = ArtifactMetadata(
        artifact_id=artifact_id,
        content_snapshot_id=content_id,
        channel=PDF_CHANNEL,
        original_state=ArtifactOriginalState.STORED,
        blob_pointer=pointer,
        version=version,
        created_at=created,
        retention=retention,
    )
    # blob과 SQLite는 하나의 transaction이 될 수 없다. 대신 먼저
    # 커밋된 intent가 있고, metadata·BOUND event는 하나의 DB 거래로 묶인다.
    # 이후 큰 transaction이 rollback되면 intent만 남아 유예 후 안전 정리된다.
    _save_metadata(conn, metadata)
    _bind_blob_intent(
        conn,
        intent=persisted_intent,
        metadata=metadata,
        recorded_at=created,
    )
    return metadata


def register_legacy_original_unknown(
    conn: sqlite3.Connection,
    *,
    content_snapshot_id: str,
    legacy_reference: str,
    recorded_at: dt.datetime,
    retention: ArtifactRetention,
) -> ArtifactMetadata:
    """최초 bytes를 복구 못 한 과거 PDF를 재생성본으로 가장하지 않는다."""

    content_id = str(content_snapshot_id).strip()
    reference = str(legacy_reference).strip()
    if not reference:
        raise ArtifactError("과거 출고 기록을 가리키는 reference가 필요합니다")
    if lifecycle_store.load_content_snapshot(conn, content_id) is None:
        raise ArtifactError("과거 artifact도 연결할 내용 원본이 필요합니다")
    recorded = require_aware(recorded_at, label="과거 artifact 기록")
    version = ArtifactVersion.legacy_unknown()
    artifact_id = _legacy_artifact_id(
        content_snapshot_id=content_id,
        channel=PDF_CHANNEL,
        legacy_reference=reference,
    )
    existing = load_artifact_metadata(conn, artifact_id)
    if existing is not None:
        return existing
    metadata = ArtifactMetadata(
        artifact_id=artifact_id,
        content_snapshot_id=content_id,
        channel=PDF_CHANNEL,
        original_state=ArtifactOriginalState.LEGACY_ORIGINAL_UNKNOWN,
        blob_pointer=None,
        version=version,
        created_at=recorded,
        retention=retention,
        legacy_reference=reference,
    )
    _save_metadata(conn, metadata)
    return metadata


def load_artifact_metadata(
    conn: sqlite3.Connection, artifact_id: str
) -> ArtifactMetadata | None:
    ensure_artifact_schema(conn)
    row = conn.execute(
        f"SELECT * FROM {TABLE_ARTIFACTS} WHERE artifact_id = ?",
        (str(artifact_id).strip(),),
    ).fetchone()
    if row is None:
        return None
    try:
        original_state = ArtifactOriginalState(str(row[3]))
    except ValueError as exc:
        raise ArtifactError("artifact 원본 상태가 손상됐습니다") from exc
    try:
        pointer = (
            BlobPointer(key=str(row[4]), sha256=str(row[5]), byte_length=int(row[6]))
            if original_state is ArtifactOriginalState.STORED
            else None
        )
        retain_until = (
            datetime_from_utc_text(row[13], label="artifact 보존")
            if row[13]
            else None
        )
        metadata = ArtifactMetadata(
            artifact_id=str(row[0]),
            content_snapshot_id=str(row[1]),
            channel=str(row[2]),
            original_state=original_state,
            blob_pointer=pointer,
            version=ArtifactVersion(
                renderer_version=str(row[7]),
                font_bundle_version=str(row[8]),
                checker_version=str(row[9]),
                format_version=str(row[10]),
            ),
            created_at=datetime_from_utc_text(row[11], label="artifact 생성"),
            retention=ArtifactRetention(
                policy_id=str(row[12]),
                retain_until=retain_until,
                legal_hold=bool(row[14]),
            ),
            legacy_reference=str(row[15]),
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactError("artifact metadata가 손상됐습니다") from exc
    if lifecycle_store.load_content_snapshot(conn, metadata.content_snapshot_id) is None:
        raise ArtifactError("artifact가 없는 내용 원본을 가리킵니다")
    return metadata


def inspect_artifact(
    conn: sqlite3.Connection,
    backend: ArtifactBlobBackend,
    artifact_id: str,
) -> ArtifactInspection | None:
    """읽기만 하며 missing/corrupt를 현재 renderer로 고치지 않는다."""

    metadata = load_artifact_metadata(conn, artifact_id)
    if metadata is None:
        return None
    if metadata.original_state is ArtifactOriginalState.LEGACY_ORIGINAL_UNKNOWN:
        return ArtifactInspection(
            metadata=metadata,
            status=ArtifactInspectionStatus.LEGACY_ORIGINAL_UNKNOWN,
        )
    pointer = metadata.blob_pointer
    if pointer is None:
        return ArtifactInspection(metadata=metadata, status=ArtifactInspectionStatus.CORRUPT)
    data = backend.read(pointer)
    if data is None:
        return ArtifactInspection(metadata=metadata, status=ArtifactInspectionStatus.MISSING)
    if len(data) != pointer.byte_length or sha256_hex(data) != pointer.sha256:
        return ArtifactInspection(metadata=metadata, status=ArtifactInspectionStatus.CORRUPT)
    return ArtifactInspection(
        metadata=metadata,
        status=ArtifactInspectionStatus.AVAILABLE,
        pdf_bytes=data,
    )


def bind_artifact_to_delivery(
    conn: sqlite3.Connection,
    *,
    delivery_id: str,
    artifact_id: str,
) -> None:
    """한 delivery의 channel artifact를 나중에 바꿔치기하지 못하게 한다."""

    ensure_artifact_schema(conn)
    delivery = lifecycle_store.load_delivery(conn, delivery_id)
    metadata = load_artifact_metadata(conn, artifact_id)
    if delivery is None or metadata is None:
        raise ArtifactError("delivery와 artifact가 모두 저장돼 있어야 연결할 수 있습니다")
    if delivery.content_snapshot_id != metadata.content_snapshot_id:
        raise ArtifactError("delivery와 artifact가 서로 다른 내용 원본을 가리킵니다")
    payload = (delivery.delivery_id, metadata.channel, metadata.artifact_id)
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_DELIVERY_ARTIFACTS}
            (delivery_id, channel, artifact_id) VALUES (?, ?, ?)
        """,
        payload,
    )
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"""
        SELECT delivery_id, channel, artifact_id
        FROM {TABLE_DELIVERY_ARTIFACTS}
        WHERE delivery_id = ? AND channel = ?
        """,
        (delivery.delivery_id, metadata.channel),
    ).fetchone()
    if existing is None or tuple(existing) != payload:
        raise ArtifactImmutableConflict("발급된 delivery의 PDF artifact를 바꿀 수 없습니다")


def artifact_for_delivery(
    conn: sqlite3.Connection,
    *,
    delivery_id: str,
    channel: str = PDF_CHANNEL,
) -> ArtifactMetadata | None:
    """GET adapter가 다시 렌더하지 않고 delivery에 고정된 artifact를 찾는다."""

    ensure_artifact_schema(conn)
    row = conn.execute(
        f"""
        SELECT artifact_id FROM {TABLE_DELIVERY_ARTIFACTS}
        WHERE delivery_id = ? AND channel = ?
        """,
        (str(delivery_id).strip(), str(channel).strip()),
    ).fetchone()
    if row is None:
        return None
    return load_artifact_metadata(conn, str(row[0]))


def deliveries_for_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
) -> tuple[Delivery, ...]:
    """한 최초 승인 artifact를 실제로 발급받은 Delivery들을 돌려준다.

    single-flight waiter는 같은 비용 통장의 owner Delivery가 이 목록에
    있을 때만 그 artifact를 재사용한다. content ID만 우연히 같은 다른
    통장의 PDF를 가져오는 우회를 막기 위한 조회 경계다.
    """

    ensure_artifact_schema(conn)
    metadata = load_artifact_metadata(conn, artifact_id)
    if metadata is None:
        return ()
    rows = conn.execute(
        f"""
        SELECT delivery_id
        FROM {TABLE_DELIVERY_ARTIFACTS}
        WHERE artifact_id = ? AND channel = ?
        ORDER BY delivery_id
        """,
        (metadata.artifact_id, metadata.channel),
    ).fetchall()
    deliveries: list[Delivery] = []
    for row in rows:
        delivery = lifecycle_store.load_delivery(conn, str(row[0]))
        if delivery is None:
            raise ArtifactError("artifact 결속이 없는 Delivery를 가리킵니다")
        if delivery.content_snapshot_id != metadata.content_snapshot_id:
            raise ArtifactError("artifact 결속과 Delivery의 내용 원본이 다릅니다")
        deliveries.append(delivery)
    return tuple(deliveries)


def retention_review_due(
    conn: sqlite3.Connection, *, now: dt.datetime
) -> tuple[ArtifactMetadata, ...]:
    """보존 검토 대상만 알려주며 bytes나 metadata를 삭제하지 않는다.

    artifact 자체의 보존일이 지났어도 그를 사용하는 새 delivery가
    살아 있으면 검토 대상으로 내보내지 않는다. 이 하한이 없으면
    59일 된 캐시 내용을 새 60일 링크로 준 뒤 PDF만 먼저 없어질 수 있다.
    """

    ensure_artifact_schema(conn)
    current = require_aware(now, label="artifact 보존 검토")
    rows = conn.execute(f"SELECT artifact_id FROM {TABLE_ARTIFACTS}").fetchall()
    due: list[ArtifactMetadata] = []
    for row in rows:
        metadata = load_artifact_metadata(conn, str(row[0]))
        if metadata is None or not metadata.retention.review_is_due(now=current):
            continue
        delivery_rows = conn.execute(
            f"""
            SELECT deliveries.expires_at
            FROM {TABLE_DELIVERY_ARTIFACTS} AS bindings
            JOIN {lifecycle_store.TABLE_DELIVERIES} AS deliveries
              ON deliveries.delivery_id = bindings.delivery_id
            WHERE bindings.artifact_id = ?
            """,
            (metadata.artifact_id,),
        ).fetchall()
        latest_delivery_expiry = max(
            (
                datetime_from_utc_text(item[0], label="보고서 만료")
                for item in delivery_rows
            ),
            default=None,
        )
        if latest_delivery_expiry is None or current >= latest_delivery_expiry:
            due.append(metadata)
    return tuple(sorted(due, key=lambda item: item.artifact_id))
