"""백업 진본성을 데이터 저장 경계 밖의 서명 원장에 결박한다.

운영 어댑터는 :class:`ManifestSink`를 구현해야 한다. 구현체는 DB/sidecar와
다른 권한 주체·보존 경계에 있고, 조건부 append를 원자적으로 제공해야 한다.
이 모듈은 외부 자격증명을 조회하거나 네트워크에 접속하지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Mapping


MANIFEST_VERSION: Final[int] = 1
MIN_SIGNING_KEY_BYTES: Final[int] = 32
MAX_MANIFEST_BYTES: Final[int] = 64 * 1024 * 1024
MAX_RECORD_BYTES: Final[int] = 16 * 1024
MAX_CLOCK_SKEW_SEC: Final[int] = 300
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
)
FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^[^/\\\r\n]{1,255}$")

_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


class ManifestError(RuntimeError):
    """독립 manifest 계약을 만족하지 못했다."""


class ManifestConflictError(ManifestError):
    """조건부 append 중 원장 head가 바뀌었다."""


def _identifier(value: object, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise ManifestError(f"{label} 식별자가 올바르지 않습니다.")
    return normalized


def _sha256(value: object, *, label: str, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if allow_empty and not normalized:
        return ""
    if not SHA256_RE.fullmatch(normalized):
        raise ManifestError(f"{label} SHA-256이 올바르지 않습니다.")
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
        raise ManifestError(f"{label} object key가 올바르지 않습니다.")
    return normalized


def _utc_datetime(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{label} 시각이 올바르지 않습니다.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ManifestError(f"{label} 시각은 UTC여야 합니다.")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime, *, label: str) -> str:
    if value.tzinfo is None:
        raise ManifestError(f"{label} 시각은 timezone-aware여야 합니다.")
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


@dataclass(frozen=True)
class ManifestBoundary:
    """운영 주입 어댑터가 증명해야 하는 권한·보존 속성."""

    boundary_id: str
    authority_id: str
    retention_days: int
    append_only: bool
    signed: bool
    conditional_append: bool
    production_ready: bool

    def validate(self, *, minimum_retention_days: int, allow_test_sink: bool) -> None:
        _identifier(self.boundary_id, label="manifest 경계")
        _identifier(self.authority_id, label="manifest 권한")
        if isinstance(self.retention_days, bool) or self.retention_days < minimum_retention_days:
            raise ManifestError("manifest 보존 기간이 운영 최소값보다 짧습니다.")
        if not self.append_only or not self.signed or not self.conditional_append:
            raise ManifestError("manifest sink는 서명·append-only·조건부 append여야 합니다.")
        if not self.production_ready and not allow_test_sink:
            raise ManifestError("로컬 시험 manifest sink는 운영 gate에 쓸 수 없습니다.")


@dataclass(frozen=True)
class BackupManifestRecord:
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
    signature: str

    def payload(self) -> dict[str, object]:
        values = asdict(self)
        values.pop("signature")
        return values

    def payload_bytes(self) -> bytes:
        return _canonical_json(self.payload())

    def serialized_bytes(self) -> bytes:
        return _canonical_json(asdict(self))

    def record_sha256(self) -> str:
        return hashlib.sha256(self.serialized_bytes()).hexdigest()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "BackupManifestRecord":
        expected = set(cls.__dataclass_fields__)
        if set(payload) != expected:
            raise ManifestError("manifest 레코드 필드가 정확하지 않습니다.")
        try:
            record = cls(**payload)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ManifestError("manifest 레코드 형식이 올바르지 않습니다.") from exc
        record.validate_shape()
        return record

    def validate_shape(self) -> None:
        if isinstance(self.version, bool) or self.version != MANIFEST_VERSION:
            raise ManifestError("지원하지 않는 manifest 버전입니다.")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ManifestError("manifest sequence가 올바르지 않습니다.")
        _identifier(self.scope, label="백업 scope")
        _identifier(self.backup_id, label="백업")
        _identifier(self.storage_provider, label="백업 저장 공급자")
        _identifier(self.storage_bucket, label="백업 bucket")
        _object_key(self.object_key, label="manifest 백업")
        _object_key(self.checksum_key, label="manifest checksum")
        if self.object_key == self.checksum_key:
            raise ManifestError("백업 DB와 checksum object key가 같을 수 없습니다.")
        if not isinstance(self.database_name, str) or not FILENAME_RE.fullmatch(self.database_name):
            raise ManifestError("manifest DB 파일명이 올바르지 않습니다.")
        _sha256(self.database_sha256, label="DB")
        if (
            isinstance(self.database_size_bytes, bool)
            or not isinstance(self.database_size_bytes, int)
            or self.database_size_bytes <= 0
        ):
            raise ManifestError("manifest DB 크기가 올바르지 않습니다.")
        _sha256(self.checksum_sha256, label="checksum 객체")
        created = _utc_datetime(self.created_at, label="백업 생성")
        retained = _utc_datetime(self.retention_until, label="백업 보존")
        if retained <= created:
            raise ManifestError("manifest 보존 종료가 생성 시각보다 늦어야 합니다.")
        _identifier(self.data_boundary_id, label="데이터 경계")
        _identifier(self.data_authority_id, label="데이터 권한")
        _identifier(self.manifest_boundary_id, label="manifest 경계")
        _identifier(self.manifest_authority_id, label="manifest 권한")
        _sha256(self.previous_record_sha256, label="이전 레코드", allow_empty=True)
        _identifier(self.key_id, label="manifest 서명 키")
        _sha256(self.signature, label="manifest 서명")


class ManifestSigner(ABC):
    """비밀을 출력하지 않는 서명기/검증기 주입 계약."""

    @property
    @abstractmethod
    def key_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def sign(self, payload: bytes) -> str:
        raise NotImplementedError

    @abstractmethod
    def verify(self, payload: bytes, signature: str) -> bool:
        raise NotImplementedError


class HMACManifestSigner(ManifestSigner):
    """운영 secret manager가 주입한 키를 사용하는 HMAC-SHA256 구현."""

    def __init__(self, *, key_id: str, key: bytes) -> None:
        self._key_id = _identifier(key_id, label="manifest 서명 키")
        if not isinstance(key, bytes) or len(key) < MIN_SIGNING_KEY_BYTES:
            raise ManifestError("manifest 서명 키는 32바이트 이상이어야 합니다.")
        self._key = bytes(key)

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str) -> bool:
        try:
            normalized = _sha256(signature, label="manifest 서명")
        except ManifestError:
            return False
        return hmac.compare_digest(self.sign(payload), normalized)

    def __repr__(self) -> str:
        return f"HMACManifestSigner(key_id={self.key_id!r}, key=[비공개])"


class ManifestSink(ABC):
    """운영 구현체가 따라야 하는 닫힌 append-only sink 인터페이스.

    ``append``는 저장소가 제공하는 원자적 compare-and-append여야 한다. 기존 객체를
    갱신·삭제하는 메서드는 계약에 존재하지 않는다.
    """

    @property
    @abstractmethod
    def boundary(self) -> ManifestBoundary:
        raise NotImplementedError

    @abstractmethod
    def read_records(self) -> tuple[BackupManifestRecord, ...]:
        raise NotImplementedError

    @abstractmethod
    def append(
        self,
        record: BackupManifestRecord,
        *,
        expected_head_sha256: str,
    ) -> None:
        raise NotImplementedError

    def local_storage_path(self) -> Path | None:
        """로컬 시험 sink만 경계 중첩 검사에 사용할 경로를 돌려준다."""
        return None


def _local_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.Lock())


class LocalAppendOnlyManifestSink(ManifestSink):
    """격리 단위시험용 JSONL sink. 운영용으로 승격할 수 없다."""

    def __init__(self, path: Path, *, boundary: ManifestBoundary) -> None:
        if boundary.production_ready:
            raise ManifestError("로컬 manifest sink는 production_ready일 수 없습니다.")
        self._path = path.expanduser()
        self._boundary = boundary

    @property
    def boundary(self) -> ManifestBoundary:
        return self._boundary

    def local_storage_path(self) -> Path | None:
        return self._path.resolve()

    def _read_bytes(self) -> bytes:
        if self._path.is_symlink():
            raise ManifestError("로컬 manifest는 심볼릭 링크일 수 없습니다.")
        if not self._path.exists():
            return b""
        if not self._path.is_file():
            raise ManifestError("로컬 manifest 경로가 일반 파일이 아닙니다.")
        try:
            size = self._path.stat().st_size
            if size > MAX_MANIFEST_BYTES:
                raise ManifestError("로컬 manifest가 허용 크기를 넘었습니다.")
            return self._path.read_bytes()
        except OSError as exc:
            raise ManifestError("로컬 manifest를 읽지 못했습니다.") from exc

    def read_records(self) -> tuple[BackupManifestRecord, ...]:
        raw = self._read_bytes()
        if not raw:
            return ()
        if not raw.endswith(b"\n"):
            raise ManifestError("로컬 manifest 마지막 레코드가 완결되지 않았습니다.")
        records: list[BackupManifestRecord] = []
        for line in raw.splitlines():
            if not line or len(line) > MAX_RECORD_BYTES:
                raise ManifestError("로컬 manifest 레코드 크기가 올바르지 않습니다.")
            try:
                payload = json.loads(line.decode("ascii"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ManifestError("로컬 manifest JSON을 읽지 못했습니다.") from exc
            if not isinstance(payload, dict):
                raise ManifestError("로컬 manifest 레코드는 객체여야 합니다.")
            records.append(BackupManifestRecord.from_mapping(payload))
        return tuple(records)

    def append(
        self,
        record: BackupManifestRecord,
        *,
        expected_head_sha256: str,
    ) -> None:
        expected = _sha256(
            expected_head_sha256, label="기대 manifest head", allow_empty=True
        )
        parent = self._path.parent.resolve(strict=True)
        if not parent.is_dir() or self._path.is_symlink():
            raise ManifestError("로컬 manifest 상위 경계가 올바르지 않습니다.")
        serialized = record.serialized_bytes() + b"\n"
        if len(serialized) > MAX_RECORD_BYTES:
            raise ManifestError("manifest 레코드가 허용 크기를 넘었습니다.")

        with _local_lock(self._path):
            records = self.read_records()
            actual_head = records[-1].record_sha256() if records else ""
            if not hmac.compare_digest(actual_head, expected):
                raise ManifestConflictError("manifest head가 바뀌어 append를 거부했습니다.")
            try:
                descriptor = os.open(
                    self._path,
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                    0o600,
                )
                with os.fdopen(descriptor, "ab", closefd=True) as stream:
                    stream.write(serialized)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise ManifestError("로컬 manifest append에 실패했습니다.") from exc


def validate_manifest_chain(
    records: tuple[BackupManifestRecord, ...],
    *,
    boundary: ManifestBoundary,
    signer: ManifestSigner,
) -> None:
    previous: BackupManifestRecord | None = None
    backup_ids: set[str] = set()
    previous_created: datetime | None = None
    for index, record in enumerate(records, start=1):
        record.validate_shape()
        if record.sequence != index:
            raise ManifestError("manifest sequence가 연속적이지 않습니다.")
        if record.backup_id in backup_ids:
            raise ManifestError("manifest backup_id가 재사용됐습니다.")
        backup_ids.add(record.backup_id)
        if (
            record.manifest_boundary_id != boundary.boundary_id
            or record.manifest_authority_id != boundary.authority_id
        ):
            raise ManifestError("manifest 레코드의 저장 경계 결속이 다릅니다.")
        if record.key_id != signer.key_id or not signer.verify(
            record.payload_bytes(), record.signature
        ):
            raise ManifestError("manifest 서명 검증에 실패했습니다.")
        expected_previous = previous.record_sha256() if previous is not None else ""
        if not hmac.compare_digest(record.previous_record_sha256, expected_previous):
            raise ManifestError("manifest hash chain이 끊어졌습니다.")
        created = _utc_datetime(record.created_at, label="백업 생성")
        retained = _utc_datetime(record.retention_until, label="백업 보존")
        if previous_created is not None and created <= previous_created:
            raise ManifestError("manifest 생성 시각이 단조 증가하지 않습니다.")
        if retained - created < timedelta(days=boundary.retention_days):
            raise ManifestError("manifest 레코드 보존 기한이 sink 계약보다 짧습니다.")
        previous_created = created
        previous = record


@dataclass(frozen=True)
class ManifestExpectation:
    backup_id: str
    scope: str
    storage_provider: str
    storage_bucket: str
    object_key: str
    checksum_key: str
    data_boundary_id: str
    data_authority_id: str
    minimum_sequence: int
    now: datetime | None = None
    max_age_seconds: int | None = None

    def validate(self) -> None:
        _identifier(self.backup_id, label="백업")
        _identifier(self.scope, label="백업 scope")
        _identifier(self.storage_provider, label="백업 저장 공급자")
        _identifier(self.storage_bucket, label="백업 bucket")
        _object_key(self.object_key, label="백업")
        _object_key(self.checksum_key, label="checksum")
        _identifier(self.data_boundary_id, label="데이터 경계")
        _identifier(self.data_authority_id, label="데이터 권한")
        if (
            isinstance(self.minimum_sequence, bool)
            or not isinstance(self.minimum_sequence, int)
            or self.minimum_sequence < 1
        ):
            raise ManifestError("신뢰 checkpoint sequence가 필요합니다.")
        if self.max_age_seconds is not None and (
            isinstance(self.max_age_seconds, bool) or self.max_age_seconds <= 0
        ):
            raise ManifestError("manifest 최대 나이는 양수여야 합니다.")


class IndependentManifestGate:
    """독립 sink·서명·head·checkpoint를 모두 만족해야 통과시키는 gate."""

    def __init__(
        self,
        *,
        sink: ManifestSink,
        signer: ManifestSigner,
        minimum_retention_days: int,
        allow_test_sink: bool = False,
    ) -> None:
        if not isinstance(sink, ManifestSink) or not isinstance(signer, ManifestSigner):
            raise ManifestError("닫힌 manifest sink와 signer 구현을 주입해야 합니다.")
        if minimum_retention_days < 1:
            raise ManifestError("manifest 최소 보존 기간은 1일 이상이어야 합니다.")
        sink.boundary.validate(
            minimum_retention_days=minimum_retention_days,
            allow_test_sink=allow_test_sink,
        )
        self._sink = sink
        self._signer = signer
        self._minimum_retention_days = minimum_retention_days
        self._allow_test_sink = allow_test_sink

    @property
    def sink(self) -> ManifestSink:
        return self._sink

    def verify(
        self,
        *,
        expectation: ManifestExpectation,
        database_name: str,
        database_sha256: str,
        database_size_bytes: int,
        checksum_sha256: str,
        data_root: Path,
    ) -> BackupManifestRecord:
        expectation.validate()
        boundary = self._sink.boundary
        boundary.validate(
            minimum_retention_days=self._minimum_retention_days,
            allow_test_sink=self._allow_test_sink,
        )
        if (
            boundary.boundary_id == expectation.data_boundary_id
            or boundary.authority_id == expectation.data_authority_id
        ):
            raise ManifestError("manifest와 DB의 권한·보존 경계가 독립적이지 않습니다.")
        local_path = self._sink.local_storage_path()
        resolved_data_root = data_root.expanduser().resolve(strict=True)
        if local_path is not None and local_path.resolve().is_relative_to(resolved_data_root):
            raise ManifestError("로컬 manifest 파일을 DB 데이터 경계 안에 둘 수 없습니다.")

        records = self._sink.read_records()
        if not records:
            raise ManifestError("독립 서명 manifest 레코드가 없습니다.")
        validate_manifest_chain(records, boundary=boundary, signer=self._signer)
        matching = [record for record in records if record.backup_id == expectation.backup_id]
        if len(matching) != 1:
            raise ManifestError("요청한 backup_id의 독립 manifest가 정확히 하나가 아닙니다.")
        record = matching[0]
        scope_head = max(
            (item for item in records if item.scope == expectation.scope),
            key=lambda item: item.sequence,
            default=None,
        )
        if scope_head is None or scope_head.sequence != record.sequence:
            raise ManifestError("오래된 manifest 레코드 replay를 거부했습니다.")
        if record.sequence < expectation.minimum_sequence:
            raise ManifestError("manifest가 신뢰 checkpoint보다 오래됐습니다.")
        if record.scope != expectation.scope:
            raise ManifestError("manifest 백업 scope가 다릅니다.")
        if (
            record.storage_provider != expectation.storage_provider
            or record.storage_bucket != expectation.storage_bucket
            or record.object_key != expectation.object_key
            or record.checksum_key != expectation.checksum_key
        ):
            raise ManifestError("manifest의 원격 백업 객체 결속이 다릅니다.")
        if (
            record.object_key.rsplit("/", 1)[-1] != record.database_name
            or record.checksum_key != record.object_key + ".sha256"
        ):
            raise ManifestError("manifest 객체 key와 DB/sidecar 이름 결속이 다릅니다.")
        expected_checksum_digest = _sha256(checksum_sha256, label="checksum 객체")
        if not hmac.compare_digest(record.checksum_sha256, expected_checksum_digest):
            raise ManifestError("manifest의 checksum 객체 지문이 다릅니다.")
        if (
            record.data_boundary_id != expectation.data_boundary_id
            or record.data_authority_id != expectation.data_authority_id
        ):
            raise ManifestError("manifest의 원본 데이터 경계 결속이 다릅니다.")
        if record.database_name != database_name:
            raise ManifestError("manifest의 DB 객체 이름이 다릅니다.")
        actual_digest = _sha256(database_sha256, label="실제 DB")
        if not hmac.compare_digest(record.database_sha256, actual_digest):
            raise ManifestError("manifest의 DB SHA-256이 실제 DB와 다릅니다.")
        if record.database_size_bytes != database_size_bytes:
            raise ManifestError("manifest의 DB 크기가 실제 DB와 다릅니다.")

        now = (expectation.now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        created = _utc_datetime(record.created_at, label="백업 생성")
        retained = _utc_datetime(record.retention_until, label="백업 보존")
        if created > now + timedelta(seconds=MAX_CLOCK_SKEW_SEC):
            raise ManifestError("manifest 생성 시각이 현재보다 지나치게 미래입니다.")
        if now > retained:
            raise ManifestError("manifest 보존 기한이 끝났습니다.")
        if expectation.max_age_seconds is not None and (
            now - created > timedelta(seconds=expectation.max_age_seconds)
        ):
            raise ManifestError("manifest가 허용한 복구 백업 나이보다 오래됐습니다.")
        return record


class ManifestLedger:
    """백업 생성자가 성공 해시를 독립 sink에 append하는 쓰기 계약."""

    def __init__(
        self,
        *,
        sink: ManifestSink,
        signer: ManifestSigner,
        minimum_retention_days: int,
        allow_test_sink: bool = False,
    ) -> None:
        sink.boundary.validate(
            minimum_retention_days=minimum_retention_days,
            allow_test_sink=allow_test_sink,
        )
        self._sink = sink
        self._signer = signer

    def append_backup(
        self,
        *,
        scope: str,
        backup_id: str,
        storage_provider: str,
        storage_bucket: str,
        object_key: str,
        checksum_key: str,
        database_name: str,
        database_sha256: str,
        database_size_bytes: int,
        checksum_sha256: str,
        created_at: datetime,
        data_boundary_id: str,
        data_authority_id: str,
    ) -> BackupManifestRecord:
        boundary = self._sink.boundary
        if (
            boundary.boundary_id == data_boundary_id
            or boundary.authority_id == data_authority_id
        ):
            raise ManifestError("manifest와 DB의 권한·보존 경계가 독립적이지 않습니다.")
        records = self._sink.read_records()
        validate_manifest_chain(records, boundary=boundary, signer=self._signer)
        if any(item.backup_id == backup_id for item in records):
            raise ManifestError("backup_id replay append를 거부했습니다.")
        created_text = _utc_text(created_at, label="백업 생성")
        created = _utc_datetime(created_text, label="백업 생성")
        if records and created <= _utc_datetime(records[-1].created_at, label="백업 생성"):
            raise ManifestError("새 manifest 생성 시각은 현재 head보다 늦어야 합니다.")
        previous_hash = records[-1].record_sha256() if records else ""
        unsigned = BackupManifestRecord(
            version=MANIFEST_VERSION,
            sequence=len(records) + 1,
            scope=_identifier(scope, label="백업 scope"),
            backup_id=_identifier(backup_id, label="백업"),
            storage_provider=_identifier(storage_provider, label="백업 저장 공급자"),
            storage_bucket=_identifier(storage_bucket, label="백업 bucket"),
            object_key=_object_key(object_key, label="백업"),
            checksum_key=_object_key(checksum_key, label="checksum"),
            database_name=database_name,
            database_sha256=_sha256(database_sha256, label="DB"),
            database_size_bytes=database_size_bytes,
            checksum_sha256=_sha256(checksum_sha256, label="checksum 객체"),
            created_at=created_text,
            retention_until=_utc_text(
                created + timedelta(days=boundary.retention_days), label="백업 보존"
            ),
            data_boundary_id=_identifier(data_boundary_id, label="데이터 경계"),
            data_authority_id=_identifier(data_authority_id, label="데이터 권한"),
            manifest_boundary_id=boundary.boundary_id,
            manifest_authority_id=boundary.authority_id,
            previous_record_sha256=previous_hash,
            key_id=self._signer.key_id,
            signature="0" * 64,
        )
        unsigned.validate_shape()
        signed = replace(
            unsigned,
            signature=self._signer.sign(unsigned.payload_bytes()),
        )
        self._sink.append(signed, expected_head_sha256=previous_hash)
        persisted = self._sink.read_records()
        validate_manifest_chain(persisted, boundary=boundary, signer=self._signer)
        if not persisted or persisted[-1] != signed:
            raise ManifestError("manifest append 결과를 다시 확인하지 못했습니다.")
        return signed


__all__ = [
    "BackupManifestRecord",
    "HMACManifestSigner",
    "IndependentManifestGate",
    "LocalAppendOnlyManifestSink",
    "ManifestBoundary",
    "ManifestConflictError",
    "ManifestError",
    "ManifestExpectation",
    "ManifestLedger",
    "ManifestSigner",
    "ManifestSink",
    "validate_manifest_chain",
]
