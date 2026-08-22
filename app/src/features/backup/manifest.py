"""외부 SQLite 백업을 독립 서명 manifest에 기록하는 운영 주입 계약.

이 feature는 특정 secret manager나 원격 저장소에 접속하지 않는다. 운영 조립부가
DB/sidecar와 다른 권한·보존 경계의 :class:`BackupManifestAppender`를 주입해야 하며,
누락되거나 append/read-back 검증이 실패하면 백업 성공을 반환할 수 없다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Final


MANIFEST_VERSION: Final[int] = 1
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
)
_PROVIDER_LOCK = threading.Lock()
_PROVIDER: Callable[[], "BackupManifestAppender"] | None = None


class ManifestConfigurationError(RuntimeError):
    """독립 manifest 운영 주입이 없거나 경계가 안전하지 않다."""


class ManifestAppendError(RuntimeError):
    """manifest append 또는 read-back 검증을 완결하지 못했다."""


def _identifier(value: object, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise ManifestConfigurationError(f"{label} 식별자가 올바르지 않습니다")
    return normalized


def _sha256(value: object, *, label: str, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if allow_empty and not normalized:
        return ""
    if not SHA256_RE.fullmatch(normalized):
        raise ManifestAppendError(f"{label} SHA-256이 올바르지 않습니다")
    return normalized


def _object_key(value: object, *, label: str) -> str:
    normalized = str(value or "").strip()
    segments = normalized.split("/")
    if (
        not normalized
        or len(normalized) > 1024
        or normalized.startswith("/")
        or "\\" in normalized
        or any(not segment or segment in {".", ".."} for segment in segments)
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ManifestConfigurationError(f"{label} object key가 올바르지 않습니다")
    return normalized


def _utc_text(value: datetime, *, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ManifestConfigurationError(f"{label} 시각은 timezone-aware여야 합니다")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _utc_datetime(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ManifestAppendError(f"{label} 시각이 올바르지 않습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ManifestAppendError(f"{label} 시각은 UTC여야 합니다")
    return parsed.astimezone(timezone.utc)


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


@dataclass(frozen=True)
class ManifestBoundary:
    boundary_id: str
    authority_id: str
    retention_days: int
    append_only: bool
    signed: bool
    conditional_append: bool
    production_ready: bool

    def validate(self, *, minimum_retention_days: int) -> None:
        _identifier(self.boundary_id, label="manifest 경계")
        _identifier(self.authority_id, label="manifest 권한")
        if (
            isinstance(self.retention_days, bool)
            or self.retention_days < minimum_retention_days
        ):
            raise ManifestConfigurationError("manifest 보존 기간이 운영 최소값보다 짧습니다")
        if not self.append_only or not self.signed or not self.conditional_append:
            raise ManifestConfigurationError(
                "manifest는 서명·append-only·원자적 조건부 append여야 합니다"
            )
        if not self.production_ready:
            raise ManifestConfigurationError("시험용 manifest sink는 운영 백업에 쓸 수 없습니다")


@dataclass(frozen=True)
class BackupManifestRequest:
    scope: str
    storage_provider: str
    storage_bucket: str
    object_key: str
    checksum_key: str
    database_name: str
    database_sha256: str
    database_size_bytes: int
    checksum_sha256: str
    created_at: datetime
    data_boundary_id: str
    data_authority_id: str
    minimum_retention_days: int

    def validate(self) -> None:
        _identifier(self.scope, label="백업 scope")
        _identifier(self.storage_provider, label="백업 저장 공급자")
        _identifier(self.storage_bucket, label="백업 bucket")
        object_key = _object_key(self.object_key, label="백업")
        checksum_key = _object_key(self.checksum_key, label="checksum")
        if checksum_key != object_key + ".sha256":
            raise ManifestConfigurationError("checksum object key가 DB object key에 결속되지 않았습니다")
        if object_key.rsplit("/", 1)[-1] != self.database_name:
            raise ManifestConfigurationError("DB 파일명과 원격 object key가 다릅니다")
        _sha256(self.database_sha256, label="DB")
        _sha256(self.checksum_sha256, label="checksum 객체")
        if (
            isinstance(self.database_size_bytes, bool)
            or not isinstance(self.database_size_bytes, int)
            or self.database_size_bytes <= 0
        ):
            raise ManifestConfigurationError("백업 DB 크기가 올바르지 않습니다")
        _utc_text(self.created_at, label="백업 생성")
        _identifier(self.data_boundary_id, label="데이터 경계")
        _identifier(self.data_authority_id, label="데이터 권한")
        if self.minimum_retention_days < 1:
            raise ManifestConfigurationError("manifest 최소 보존 기간이 필요합니다")

    @property
    def backup_id(self) -> str:
        self.validate()
        identity = "\0".join(
            (
                self.storage_provider,
                self.storage_bucket,
                self.object_key,
                self.checksum_key,
            )
        ).encode("utf-8")
        return hashlib.sha256(identity).hexdigest()


@dataclass(frozen=True)
class SignedBackupManifestRecord:
    """ops 검증기와 공유하는 버전 1 레코드 wire schema."""

    version: int
    sequence: int
    scope: str
    backup_id: str
    storage_provider: str
    storage_bucket: str
    object_key: str
    checksum_key: str
    database_name: str
    database_sha256: str
    database_size_bytes: int
    checksum_sha256: str
    created_at: str
    retention_until: str
    data_boundary_id: str
    data_authority_id: str
    manifest_boundary_id: str
    manifest_authority_id: str
    previous_record_sha256: str
    key_id: str
    signature: str = field(repr=False)

    def serialized_bytes(self) -> bytes:
        return _canonical_json(asdict(self))

    def record_sha256(self) -> str:
        return hashlib.sha256(self.serialized_bytes()).hexdigest()


@dataclass(frozen=True)
class ManifestAppendReceipt:
    record: SignedBackupManifestRecord
    record_sha256: str
    verified_head_sequence: int
    readback_verified: bool


class BackupManifestAppender(ABC):
    """운영 조립부가 구현·주입하는 독립 manifest append 계약."""

    @property
    @abstractmethod
    def boundary(self) -> ManifestBoundary:
        raise NotImplementedError

    @abstractmethod
    def append_and_verify(
        self, request: BackupManifestRequest
    ) -> ManifestAppendReceipt:
        """서명 후 원자 append하고 전체 chain/head를 다시 검증한 receipt를 돌려준다."""
        raise NotImplementedError


def install_manifest_appender_provider(
    provider: Callable[[], BackupManifestAppender],
) -> None:
    """시작 시 한 번만 외부 manifest 어댑터 provider를 주입한다."""
    if not callable(provider):
        raise ManifestConfigurationError("manifest appender provider가 필요합니다")
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is not None and _PROVIDER is not provider:
            raise ManifestConfigurationError("manifest appender provider는 교체할 수 없습니다")
        _PROVIDER = provider


def require_manifest_appender(
    explicit: BackupManifestAppender | None = None,
) -> BackupManifestAppender:
    if explicit is not None:
        appender = explicit
    else:
        with _PROVIDER_LOCK:
            provider = _PROVIDER
        if provider is None:
            raise ManifestConfigurationError(
                "독립 manifest appender가 주입되지 않아 백업을 중단합니다"
            )
        appender = provider()
    if not isinstance(appender, BackupManifestAppender):
        raise ManifestConfigurationError("닫힌 BackupManifestAppender 구현이 필요합니다")
    return appender


def validate_append_receipt(
    *,
    request: BackupManifestRequest,
    appender: BackupManifestAppender,
    receipt: ManifestAppendReceipt,
) -> SignedBackupManifestRecord:
    """성공 반환 전에 object binding과 독립 경계·read-back receipt를 재검사한다."""
    request.validate()
    boundary = appender.boundary
    boundary.validate(minimum_retention_days=request.minimum_retention_days)
    if (
        boundary.boundary_id == request.data_boundary_id
        or boundary.authority_id == request.data_authority_id
    ):
        raise ManifestConfigurationError(
            "manifest와 DB/sidecar의 권한·보존 경계가 독립적이지 않습니다"
        )
    if not isinstance(receipt, ManifestAppendReceipt) or not receipt.readback_verified:
        raise ManifestAppendError("manifest append read-back 검증 증거가 없습니다")
    record = receipt.record
    if record.version != MANIFEST_VERSION:
        raise ManifestAppendError("지원하지 않는 manifest 버전입니다")
    if (
        isinstance(record.sequence, bool)
        or record.sequence < 1
        or receipt.verified_head_sequence != record.sequence
    ):
        raise ManifestAppendError("manifest head sequence 검증이 맞지 않습니다")
    expected_values = {
        "scope": request.scope,
        "backup_id": request.backup_id,
        "storage_provider": request.storage_provider,
        "storage_bucket": request.storage_bucket,
        "object_key": request.object_key,
        "checksum_key": request.checksum_key,
        "database_name": request.database_name,
        "database_sha256": request.database_sha256,
        "database_size_bytes": request.database_size_bytes,
        "checksum_sha256": request.checksum_sha256,
        "created_at": _utc_text(request.created_at, label="백업 생성"),
        "data_boundary_id": request.data_boundary_id,
        "data_authority_id": request.data_authority_id,
        "manifest_boundary_id": boundary.boundary_id,
        "manifest_authority_id": boundary.authority_id,
    }
    for name, expected in expected_values.items():
        if getattr(record, name) != expected:
            raise ManifestAppendError(f"manifest 레코드의 {name} 결속이 다릅니다")
    created = _utc_datetime(record.created_at, label="백업 생성")
    retained = _utc_datetime(record.retention_until, label="백업 보존")
    if retained - created < timedelta(days=boundary.retention_days):
        raise ManifestAppendError("manifest 레코드 보존 기한이 sink 계약보다 짧습니다")
    _sha256(record.previous_record_sha256, label="이전 레코드", allow_empty=True)
    _identifier(record.key_id, label="manifest 서명 키")
    _sha256(record.signature, label="manifest 서명")
    actual_record_hash = record.record_sha256()
    expected_record_hash = _sha256(receipt.record_sha256, label="manifest 레코드")
    if not hmac.compare_digest(actual_record_hash, expected_record_hash):
        raise ManifestAppendError("manifest read-back 레코드 지문이 맞지 않습니다")
    return record


__all__ = [
    "BackupManifestAppender",
    "BackupManifestRequest",
    "ManifestAppendError",
    "ManifestAppendReceipt",
    "ManifestBoundary",
    "ManifestConfigurationError",
    "SignedBackupManifestRecord",
    "install_manifest_appender_provider",
    "require_manifest_appender",
    "validate_append_receipt",
]
