"""독립 백업 manifest 원장과 fail-closed verify/restore gate.

운영 구성에는 외부 attestation verifier가 승인한 WORM sink와 별도 최신
checkpoint provider가 모두 필요하다. 이 모듈이 제공하는 파일 sink와 private-key
signer는 이름과 타입이 명시된 로컬 시험 전용 구현이다.
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

try:
    from .backup_trust import (
        ED25519_ALGORITHM,
        KMS_ASYMMETRIC_ALGORITHM,
        LOCAL_TEST_HMAC_ALGORITHM,
        CheckpointVerifier,
        KmsAsymmetricKeyMetadata,
        KmsAsymmetricManifestSigner,
        KmsAsymmetricManifestVerifier,
        LocalTestEd25519CheckpointSigner,
        LocalTestEd25519ManifestSigner,
        LocalTestHMACManifestSigner,
        LocalTestHMACManifestVerifier,
        LocalTestTrustedCheckpointProvider,
        ManifestError,
        ManifestSigner,
        ManifestVerifier,
        OperationalCheckpointProviderAttestationVerifier,
        OperationalCheckpointProviderClaims,
        OperationalSinkAttestationClaims,
        OperationalSinkAttestationVerifier,
        OperationalTrustedCheckpointProvider,
        PinnedEd25519CheckpointVerifier,
        PinnedEd25519ManifestVerifier,
        SignedManifestCheckpoint,
        TrustedCheckpointProvider,
        VerifiedOperationalCheckpointProviderAttestation,
        VerifiedOperationalSinkAttestation,
        canonical_json,
        identifier,
        sha256_text,
        sign_manifest_checkpoint,
        utc_datetime,
        utc_text,
        validate_signature_text,
    )
except ImportError:  # 직접 스크립트 import 호환
    from backup_trust import (  # type: ignore[no-redef]
        ED25519_ALGORITHM,
        KMS_ASYMMETRIC_ALGORITHM,
        LOCAL_TEST_HMAC_ALGORITHM,
        CheckpointVerifier,
        KmsAsymmetricKeyMetadata,
        KmsAsymmetricManifestSigner,
        KmsAsymmetricManifestVerifier,
        LocalTestEd25519CheckpointSigner,
        LocalTestEd25519ManifestSigner,
        LocalTestHMACManifestSigner,
        LocalTestHMACManifestVerifier,
        LocalTestTrustedCheckpointProvider,
        ManifestError,
        ManifestSigner,
        ManifestVerifier,
        OperationalCheckpointProviderAttestationVerifier,
        OperationalCheckpointProviderClaims,
        OperationalSinkAttestationClaims,
        OperationalSinkAttestationVerifier,
        OperationalTrustedCheckpointProvider,
        PinnedEd25519CheckpointVerifier,
        PinnedEd25519ManifestVerifier,
        SignedManifestCheckpoint,
        TrustedCheckpointProvider,
        VerifiedOperationalCheckpointProviderAttestation,
        VerifiedOperationalSinkAttestation,
        canonical_json,
        identifier,
        sha256_text,
        sign_manifest_checkpoint,
        utc_datetime,
        utc_text,
        validate_signature_text,
    )


MANIFEST_VERSION: Final[int] = 2
MAX_MANIFEST_BYTES: Final[int] = 64 * 1024 * 1024
MAX_RECORD_BYTES: Final[int] = 16 * 1024
MAX_CLOCK_SKEW_SEC: Final[int] = 300
FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^[^/\\\r\n]{1,255}$")
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


class ManifestConflictError(ManifestError):
    """원자적 conditional append 중 원장 head가 바뀌었다."""


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


@dataclass(frozen=True)
class BackupManifestRecord:
    """앱 writer와 ops gate가 공유하는 v2 서명 레코드."""

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
    manifest_writer_principal: str
    previous_record_sha256: str
    manifest_key_identity: str
    signature_algorithm: str
    signature: str

    def payload(self) -> dict[str, object]:
        values = asdict(self)
        values.pop("signature")
        return values

    def payload_bytes(self) -> bytes:
        return canonical_json(self.payload())

    def serialized_bytes(self) -> bytes:
        return canonical_json(asdict(self))

    def record_sha256(self) -> str:
        return hashlib.sha256(self.serialized_bytes()).hexdigest()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "BackupManifestRecord":
        if set(payload) != set(cls.__dataclass_fields__):
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
        identifier(self.scope, label="백업 scope")
        identifier(self.backup_id, label="백업")
        identifier(self.storage_provider, label="백업 저장 공급자")
        identifier(self.storage_bucket, label="백업 bucket")
        _object_key(self.object_key, label="manifest 백업")
        _object_key(self.checksum_key, label="manifest checksum")
        if self.object_key == self.checksum_key:
            raise ManifestError("백업 DB와 checksum object key가 같을 수 없습니다.")
        if not isinstance(self.database_name, str) or not FILENAME_RE.fullmatch(self.database_name):
            raise ManifestError("manifest DB 파일명이 올바르지 않습니다.")
        sha256_text(self.database_sha256, label="DB")
        if (
            isinstance(self.database_size_bytes, bool)
            or not isinstance(self.database_size_bytes, int)
            or self.database_size_bytes <= 0
        ):
            raise ManifestError("manifest DB 크기가 올바르지 않습니다.")
        sha256_text(self.checksum_sha256, label="checksum 객체")
        created = utc_datetime(self.created_at, label="백업 생성")
        retained = utc_datetime(self.retention_until, label="백업 보존")
        if retained <= created:
            raise ManifestError("manifest 보존 종료가 생성 시각보다 늦어야 합니다.")
        identifier(self.data_boundary_id, label="데이터 경계")
        identifier(self.data_authority_id, label="데이터 권한")
        identifier(self.manifest_boundary_id, label="manifest 경계")
        identifier(self.manifest_writer_principal, label="manifest writer principal")
        sha256_text(self.previous_record_sha256, label="이전 레코드", allow_empty=True)
        identifier(self.manifest_key_identity, label="manifest 서명 키")
        validate_signature_text(self.signature, algorithm=self.signature_algorithm)


@dataclass(frozen=True)
class LocalTestManifestBoundary:
    """운영 attestation과 혼동할 수 없는 로컬 시험 전용 경계."""

    boundary_label: str
    writer_label: str
    retention_days: int

    def validate(self, *, minimum_retention_days: int) -> None:
        identifier(self.boundary_label, label="시험 manifest 경계")
        identifier(self.writer_label, label="시험 manifest writer")
        if isinstance(self.retention_days, bool) or self.retention_days < minimum_retention_days:
            raise ManifestError("시험 manifest 보존 기간이 최소값보다 짧습니다.")


class ManifestSink(ABC):
    """삭제·갱신 API가 없는 append-only sink 공통 읽기 계약."""

    @property
    @abstractmethod
    def sink_identity(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def boundary_id(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def writer_principal(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def retention_days(self) -> int:
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
        return None


class OperationalManifestSink(ManifestSink, ABC):
    """검증된 Object Lock/WORM attestation 없이는 만들 수 없는 운영 추상 sink."""

    def __init__(self, attestation: VerifiedOperationalSinkAttestation) -> None:
        if not isinstance(attestation, VerifiedOperationalSinkAttestation):
            raise ManifestError("검증된 운영 sink attestation이 필요합니다.")
        self._attestation = attestation

    @property
    def attestation(self) -> VerifiedOperationalSinkAttestation:
        return self._attestation

    @property
    def sink_identity(self) -> str:
        return self._attestation.sink_identity

    @property
    def boundary_id(self) -> str:
        return self._attestation.sink_identity

    @property
    def writer_principal(self) -> str:
        return self._attestation.claims.writer_principal_arn

    @property
    def retention_days(self) -> int:
        return self._attestation.claims.retention_days


def _local_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.Lock())


class LocalTestAppendOnlyManifestSink(ManifestSink):
    """격리 단위시험 JSONL sink. 타입상 운영 gate에 주입할 수 없다."""

    def __init__(
        self,
        path: Path,
        *,
        boundary: LocalTestManifestBoundary,
    ) -> None:
        if not isinstance(boundary, LocalTestManifestBoundary):
            raise ManifestError("명시적 로컬 시험 manifest 경계가 필요합니다.")
        boundary.validate(minimum_retention_days=1)
        self._path = path.expanduser()
        self._boundary = boundary
        resolved = os.path.normcase(str(self._path.resolve()))
        self._sink_identity = "local-test-sink-sha256:" + hashlib.sha256(
            resolved.encode("utf-8")
        ).hexdigest()

    @property
    def sink_identity(self) -> str:
        return self._sink_identity

    @property
    def boundary_id(self) -> str:
        return "local-test-boundary:" + self._boundary.boundary_label

    @property
    def writer_principal(self) -> str:
        return "local-test-writer:" + self._boundary.writer_label

    @property
    def retention_days(self) -> int:
        return self._boundary.retention_days

    def local_storage_path(self) -> Path | None:
        return self._path.resolve()

    def _read_bytes(self) -> bytes:
        if self._path.is_symlink():
            raise ManifestError("로컬 시험 manifest는 심볼릭 링크일 수 없습니다.")
        if not self._path.exists():
            return b""
        if not self._path.is_file():
            raise ManifestError("로컬 시험 manifest 경로가 일반 파일이 아닙니다.")
        try:
            if self._path.stat().st_size > MAX_MANIFEST_BYTES:
                raise ManifestError("로컬 시험 manifest가 허용 크기를 넘었습니다.")
            return self._path.read_bytes()
        except OSError as exc:
            raise ManifestError("로컬 시험 manifest를 읽지 못했습니다.") from exc

    def read_records(self) -> tuple[BackupManifestRecord, ...]:
        raw = self._read_bytes()
        if not raw:
            return ()
        if not raw.endswith(b"\n"):
            raise ManifestError("로컬 시험 manifest 마지막 레코드가 완결되지 않았습니다.")
        records: list[BackupManifestRecord] = []
        for line in raw.splitlines():
            if not line or len(line) > MAX_RECORD_BYTES:
                raise ManifestError("로컬 시험 manifest 레코드 크기가 올바르지 않습니다.")
            try:
                payload = json.loads(line.decode("ascii"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ManifestError("로컬 시험 manifest JSON을 읽지 못했습니다.") from exc
            if not isinstance(payload, dict):
                raise ManifestError("로컬 시험 manifest 레코드는 객체여야 합니다.")
            records.append(BackupManifestRecord.from_mapping(payload))
        return tuple(records)

    def append(
        self,
        record: BackupManifestRecord,
        *,
        expected_head_sha256: str,
    ) -> None:
        expected = sha256_text(
            expected_head_sha256,
            label="기대 manifest head",
            allow_empty=True,
        )
        parent = self._path.parent.resolve(strict=True)
        if not parent.is_dir() or self._path.is_symlink():
            raise ManifestError("로컬 시험 manifest 상위 경계가 올바르지 않습니다.")
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
                raise ManifestError("로컬 시험 manifest append에 실패했습니다.") from exc


def _validate_manifest_structure(
    records: tuple[BackupManifestRecord, ...],
    *,
    sink: ManifestSink,
    expected_key_identity: str | None = None,
    expected_algorithm: str | None = None,
) -> None:
    previous: BackupManifestRecord | None = None
    previous_created: datetime | None = None
    backup_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        record.validate_shape()
        if record.sequence != index:
            raise ManifestError("manifest sequence가 연속적이지 않습니다.")
        if record.backup_id in backup_ids:
            raise ManifestError("manifest backup_id가 재사용됐습니다.")
        backup_ids.add(record.backup_id)
        if (
            record.manifest_boundary_id != sink.boundary_id
            or record.manifest_writer_principal != sink.writer_principal
        ):
            raise ManifestError("manifest 레코드의 검증된 저장 경계 결속이 다릅니다.")
        if expected_key_identity is not None and record.manifest_key_identity != expected_key_identity:
            raise ManifestError("manifest 레코드의 고정 서명 키 정체성이 다릅니다.")
        if expected_algorithm is not None and record.signature_algorithm != expected_algorithm:
            raise ManifestError("manifest 레코드의 서명 알고리즘이 다릅니다.")
        expected_previous = previous.record_sha256() if previous is not None else ""
        if not hmac.compare_digest(record.previous_record_sha256, expected_previous):
            raise ManifestError("manifest hash chain이 끊어졌습니다.")
        created = utc_datetime(record.created_at, label="백업 생성")
        retained = utc_datetime(record.retention_until, label="백업 보존")
        if previous_created is not None and created <= previous_created:
            raise ManifestError("manifest 생성 시각이 단조 증가하지 않습니다.")
        if retained - created < timedelta(days=sink.retention_days):
            raise ManifestError("manifest 레코드 보존 기한이 sink 계약보다 짧습니다.")
        previous_created = created
        previous = record


def validate_manifest_chain(
    records: tuple[BackupManifestRecord, ...],
    *,
    sink: ManifestSink,
    verifier: ManifestVerifier,
) -> None:
    """verify-only 공개 키로 전체 원장 체인과 경계 결속을 검증한다."""

    if not isinstance(verifier, ManifestVerifier):
        raise ManifestError("manifest verify-only 구현이 필요합니다.")
    _validate_manifest_structure(
        records,
        sink=sink,
        expected_key_identity=verifier.key_identity,
        expected_algorithm=verifier.algorithm,
    )
    for record in records:
        if not verifier.verify(record.payload_bytes(), record.signature):
            raise ManifestError("manifest 서명 검증에 실패했습니다.")


class ManifestLedger:
    """writer sign-only 키로 원자 append하고 동일 bytes read-back만 확인한다."""

    def __init__(
        self,
        *,
        sink: ManifestSink,
        signer: ManifestSigner,
        minimum_retention_days: int,
    ) -> None:
        if not isinstance(sink, ManifestSink) or not isinstance(signer, ManifestSigner):
            raise ManifestError("닫힌 manifest sink와 sign-only 구현이 필요합니다.")
        if isinstance(sink, OperationalManifestSink):
            sink.attestation.validate(
                minimum_retention_days=minimum_retention_days,
                now=datetime.now(timezone.utc),
            )
            if not isinstance(signer, KmsAsymmetricManifestSigner):
                raise ManifestError(
                    "운영 writer는 분리 IAM/API가 결속된 외부 KMS/HSM signer만 쓸 수 있습니다."
                )
            if signer.metadata.signer_principal_arn != sink.writer_principal:
                raise ManifestError("KMS Sign principal이 attested sink writer와 다릅니다.")
            if signer.metadata.verifier_principal_arn == sink.writer_principal:
                raise ManifestError("KMS Verify principal은 sink writer와 분리돼야 합니다.")
        elif not isinstance(sink, LocalTestAppendOnlyManifestSink):
            raise ManifestError("attestation 없는 manifest sink를 거부했습니다.")
        if isinstance(minimum_retention_days, bool) or minimum_retention_days < 1:
            raise ManifestError("manifest 최소 보존 기간은 1일 이상이어야 합니다.")
        if sink.retention_days < minimum_retention_days:
            raise ManifestError("manifest sink 보존 기간이 최소값보다 짧습니다.")
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
        sink = self._sink
        if sink.boundary_id == data_boundary_id or sink.writer_principal == data_authority_id:
            raise ManifestError("manifest와 DB의 쓰기·보존 경계가 독립적이지 않습니다.")
        records = sink.read_records()
        _validate_manifest_structure(
            records,
            sink=sink,
            expected_key_identity=self._signer.key_identity,
            expected_algorithm=self._signer.algorithm,
        )
        if any(item.backup_id == backup_id for item in records):
            raise ManifestError("backup_id replay append를 거부했습니다.")
        created_text = utc_text(created_at, label="백업 생성")
        created = utc_datetime(created_text, label="백업 생성")
        if records and created <= utc_datetime(records[-1].created_at, label="백업 생성"):
            raise ManifestError("새 manifest 생성 시각은 현재 head보다 늦어야 합니다.")
        previous_hash = records[-1].record_sha256() if records else ""
        placeholder_length = 64 if self._signer.algorithm == LOCAL_TEST_HMAC_ALGORITHM else 128
        unsigned = BackupManifestRecord(
            version=MANIFEST_VERSION,
            sequence=len(records) + 1,
            scope=identifier(scope, label="백업 scope"),
            backup_id=identifier(backup_id, label="백업"),
            storage_provider=identifier(storage_provider, label="백업 저장 공급자"),
            storage_bucket=identifier(storage_bucket, label="백업 bucket"),
            object_key=_object_key(object_key, label="백업"),
            checksum_key=_object_key(checksum_key, label="checksum"),
            database_name=database_name,
            database_sha256=sha256_text(database_sha256, label="DB"),
            database_size_bytes=database_size_bytes,
            checksum_sha256=sha256_text(checksum_sha256, label="checksum 객체"),
            created_at=created_text,
            retention_until=utc_text(
                created + timedelta(days=sink.retention_days),
                label="백업 보존",
            ),
            data_boundary_id=identifier(data_boundary_id, label="데이터 경계"),
            data_authority_id=identifier(data_authority_id, label="데이터 권한"),
            manifest_boundary_id=sink.boundary_id,
            manifest_writer_principal=sink.writer_principal,
            previous_record_sha256=previous_hash,
            manifest_key_identity=identifier(
                self._signer.key_identity,
                label="manifest 서명 키",
            ),
            signature_algorithm=self._signer.algorithm,
            signature="0" * placeholder_length,
        )
        unsigned.validate_shape()
        signed = replace(unsigned, signature=self._signer.sign(unsigned.payload_bytes()))
        signed.validate_shape()
        sink.append(signed, expected_head_sha256=previous_hash)
        persisted = sink.read_records()
        _validate_manifest_structure(
            persisted,
            sink=sink,
            expected_key_identity=self._signer.key_identity,
            expected_algorithm=self._signer.algorithm,
        )
        if not persisted or persisted[-1] != signed:
            raise ManifestError("manifest append bytes를 다시 확인하지 못했습니다.")
        return signed


@dataclass(frozen=True)
class ManifestExpectation:
    """복구 대상 객체 결속. 최신성은 raw 정수가 아니라 서명 checkpoint가 증명한다."""

    backup_id: str
    scope: str
    storage_provider: str
    storage_bucket: str
    object_key: str
    checksum_key: str
    data_boundary_id: str
    data_authority_id: str
    now: datetime | None = None
    max_age_seconds: int | None = None

    def validate(self) -> None:
        identifier(self.backup_id, label="백업")
        identifier(self.scope, label="백업 scope")
        identifier(self.storage_provider, label="백업 저장 공급자")
        identifier(self.storage_bucket, label="백업 bucket")
        _object_key(self.object_key, label="백업")
        _object_key(self.checksum_key, label="checksum")
        identifier(self.data_boundary_id, label="데이터 경계")
        identifier(self.data_authority_id, label="데이터 권한")
        if self.max_age_seconds is not None and (
            isinstance(self.max_age_seconds, bool) or self.max_age_seconds <= 0
        ):
            raise ManifestError("manifest 최대 나이는 양수여야 합니다.")


class IndependentManifestGate(ABC):
    """release readiness가 받는 verify-only gate 공통 계약."""

    @abstractmethod
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
        raise NotImplementedError


def _verify_gate(
    *,
    sink: ManifestSink,
    manifest_verifier: ManifestVerifier,
    checkpoint_provider: TrustedCheckpointProvider,
    checkpoint_verifier: CheckpointVerifier,
    minimum_retention_days: int,
    expectation: ManifestExpectation,
    database_name: str,
    database_sha256: str,
    database_size_bytes: int,
    checksum_sha256: str,
    data_root: Path,
) -> BackupManifestRecord:
    expectation.validate()
    if sink.retention_days < minimum_retention_days:
        raise ManifestError("manifest sink 보존 기간이 운영 최소값보다 짧습니다.")
    if sink.boundary_id == expectation.data_boundary_id or sink.writer_principal == expectation.data_authority_id:
        raise ManifestError("manifest와 DB의 쓰기·보존 경계가 독립적이지 않습니다.")
    if manifest_verifier.key_identity == checkpoint_verifier.key_identity:
        raise ManifestError("manifest와 checkpoint 서명 키는 분리돼야 합니다.")
    local_path = sink.local_storage_path()
    resolved_data_root = data_root.expanduser().resolve(strict=True)
    if local_path is not None and local_path.resolve().is_relative_to(resolved_data_root):
        raise ManifestError("로컬 시험 manifest 파일을 DB 데이터 경계 안에 둘 수 없습니다.")

    records = sink.read_records()
    if not records:
        raise ManifestError("독립 서명 manifest 레코드가 없습니다.")
    validate_manifest_chain(records, sink=sink, verifier=manifest_verifier)
    scope_records = [item for item in records if item.scope == expectation.scope]
    if not scope_records:
        raise ManifestError("요청 scope의 manifest head가 없습니다.")
    scope_head = scope_records[-1]

    checkpoint = checkpoint_provider.latest_checkpoint(
        scope=expectation.scope,
        sink_identity=sink.sink_identity,
        manifest_key_identity=manifest_verifier.key_identity,
    )
    if not isinstance(checkpoint, SignedManifestCheckpoint):
        raise ManifestError("서명된 독립 최신 checkpoint가 필요합니다.")
    if (
        checkpoint.scope != expectation.scope
        or checkpoint.sink_identity != sink.sink_identity
        or checkpoint.checkpoint_provider_identity
        != checkpoint_provider.provider_identity
        or checkpoint.manifest_key_identity != manifest_verifier.key_identity
    ):
        raise ManifestError("checkpoint scope/sink/manifest 키 결속이 다릅니다.")
    checkpoint_verifier.verify_latest(checkpoint)
    if (
        checkpoint.sequence != scope_head.sequence
        or not hmac.compare_digest(
            checkpoint.head_record_sha256,
            scope_head.record_sha256(),
        )
    ):
        raise ManifestError("checkpoint가 실제 최신 manifest head와 정확히 같지 않습니다.")

    matching = [record for record in records if record.backup_id == expectation.backup_id]
    if len(matching) != 1:
        raise ManifestError("요청한 backup_id의 독립 manifest가 정확히 하나가 아닙니다.")
    record = matching[0]
    if record != scope_head:
        raise ManifestError("최신 checkpoint보다 오래된 manifest replay를 거부했습니다.")
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
    expected_checksum_digest = sha256_text(checksum_sha256, label="checksum 객체")
    if not hmac.compare_digest(record.checksum_sha256, expected_checksum_digest):
        raise ManifestError("manifest의 checksum 객체 지문이 다릅니다.")
    if (
        record.data_boundary_id != expectation.data_boundary_id
        or record.data_authority_id != expectation.data_authority_id
    ):
        raise ManifestError("manifest의 원본 데이터 경계 결속이 다릅니다.")
    if record.database_name != database_name:
        raise ManifestError("manifest의 DB 객체 이름이 다릅니다.")
    actual_digest = sha256_text(database_sha256, label="실제 DB")
    if not hmac.compare_digest(record.database_sha256, actual_digest):
        raise ManifestError("manifest의 DB SHA-256이 실제 DB와 다릅니다.")
    if record.database_size_bytes != database_size_bytes:
        raise ManifestError("manifest의 DB 크기가 실제 DB와 다릅니다.")

    now = (expectation.now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    created = utc_datetime(record.created_at, label="백업 생성")
    retained = utc_datetime(record.retention_until, label="백업 보존")
    checkpoint_issued = utc_datetime(checkpoint.issued_at, label="checkpoint 발급")
    if created > now + timedelta(seconds=MAX_CLOCK_SKEW_SEC):
        raise ManifestError("manifest 생성 시각이 현재보다 지나치게 미래입니다.")
    if checkpoint_issued > now + timedelta(seconds=MAX_CLOCK_SKEW_SEC):
        raise ManifestError("checkpoint 발급 시각이 현재보다 지나치게 미래입니다.")
    if checkpoint_issued < created:
        raise ManifestError("checkpoint가 manifest head보다 먼저 발급됐습니다.")
    if now > retained:
        raise ManifestError("manifest 보존 기한이 끝났습니다.")
    if expectation.max_age_seconds is not None and (
        now - created > timedelta(seconds=expectation.max_age_seconds)
    ):
        raise ManifestError("manifest가 허용한 복구 백업 나이보다 오래됐습니다.")
    return record


class LocalTestIndependentManifestGate(IndependentManifestGate):
    """로컬 공격 시험용 gate. 운영 타입으로 승격할 수 없다."""

    def __init__(
        self,
        *,
        sink: LocalTestAppendOnlyManifestSink,
        manifest_verifier: ManifestVerifier,
        checkpoint_provider: LocalTestTrustedCheckpointProvider,
        checkpoint_verifier: CheckpointVerifier,
        minimum_retention_days: int,
    ) -> None:
        if not isinstance(sink, LocalTestAppendOnlyManifestSink):
            raise ManifestError("로컬 시험 gate에는 명시적 시험 sink가 필요합니다.")
        if not isinstance(manifest_verifier, ManifestVerifier):
            raise ManifestError("gate에는 verify-only manifest 구현이 필요합니다.")
        if not isinstance(checkpoint_provider, LocalTestTrustedCheckpointProvider):
            raise ManifestError("로컬 시험 독립 checkpoint provider가 필요합니다.")
        if not isinstance(checkpoint_verifier, CheckpointVerifier):
            raise ManifestError("verify-only checkpoint 구현이 필요합니다.")
        if minimum_retention_days < 1:
            raise ManifestError("manifest 최소 보존 기간은 1일 이상이어야 합니다.")
        self._sink = sink
        self._manifest_verifier = manifest_verifier
        self._checkpoint_provider = checkpoint_provider
        self._checkpoint_verifier = checkpoint_verifier
        self._minimum_retention_days = minimum_retention_days

    @property
    def sink(self) -> LocalTestAppendOnlyManifestSink:
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
        return _verify_gate(
            sink=self._sink,
            manifest_verifier=self._manifest_verifier,
            checkpoint_provider=self._checkpoint_provider,
            checkpoint_verifier=self._checkpoint_verifier,
            minimum_retention_days=self._minimum_retention_days,
            expectation=expectation,
            database_name=database_name,
            database_sha256=database_sha256,
            database_size_bytes=database_size_bytes,
            checksum_sha256=checksum_sha256,
            data_root=data_root,
        )


class OperationalIndependentManifestGate(IndependentManifestGate):
    """비대칭 공개 키·attested WORM sink·attested latest provider 전용 운영 gate."""

    def __init__(
        self,
        *,
        sink: OperationalManifestSink,
        manifest_verifier: ManifestVerifier,
        checkpoint_provider: OperationalTrustedCheckpointProvider,
        checkpoint_verifier: CheckpointVerifier,
        minimum_retention_days: int,
        now: datetime | None = None,
    ) -> None:
        if not isinstance(sink, OperationalManifestSink):
            raise ManifestError("운영 gate에는 attestation 검증된 WORM sink가 필요합니다.")
        if not isinstance(checkpoint_provider, OperationalTrustedCheckpointProvider):
            raise ManifestError("운영 gate에는 attestation 검증된 최신 checkpoint provider가 필요합니다.")
        pinned_manifest = type(manifest_verifier) is PinnedEd25519ManifestVerifier
        kms_manifest = isinstance(manifest_verifier, KmsAsymmetricManifestVerifier)
        if not (pinned_manifest or kms_manifest):
            raise ManifestError(
                "운영 gate는 고정 Ed25519 공개 키 또는 검증된 KMS/HSM verifier만 허용합니다."
            )
        if type(checkpoint_verifier) is not PinnedEd25519CheckpointVerifier:
            raise ManifestError("운영 gate에는 고정 비대칭 checkpoint verifier가 필요합니다.")
        if isinstance(minimum_retention_days, bool) or minimum_retention_days < 1:
            raise ManifestError("manifest 최소 보존 기간은 1일 이상이어야 합니다.")
        effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        sink.attestation.validate(
            minimum_retention_days=minimum_retention_days,
            now=effective_now,
        )
        checkpoint_provider.attestation.validate(now=effective_now)
        provider_claims = checkpoint_provider.attestation.claims
        sink_claims = sink.attestation.claims
        if provider_claims.sink_identity != sink.sink_identity:
            raise ManifestError("checkpoint provider가 운영 sink 정체성에 결속되지 않았습니다.")
        if provider_claims.provider_resource_arn == sink_claims.sink_resource_arn:
            raise ManifestError("checkpoint provider와 manifest sink는 별도 자원이어야 합니다.")
        if provider_claims.checkpoint_key_identity != checkpoint_verifier.key_identity:
            raise ManifestError("checkpoint provider와 고정 checkpoint 키가 다릅니다.")
        checkpoint_principals = {
            provider_claims.writer_principal_arn,
            provider_claims.reader_principal_arn,
        }
        sink_principals = {
            sink_claims.writer_principal_arn,
            sink_claims.reader_principal_arn,
        }
        if not checkpoint_principals.isdisjoint(sink_principals):
            raise ManifestError("checkpoint와 manifest sink IAM principal은 모두 분리돼야 합니다.")
        if kms_manifest:
            assert isinstance(manifest_verifier, KmsAsymmetricManifestVerifier)
            if manifest_verifier.metadata.signer_principal_arn != sink.writer_principal:
                raise ManifestError("KMS Sign principal이 attested sink writer와 다릅니다.")
            if manifest_verifier.metadata.verifier_principal_arn == sink.writer_principal:
                raise ManifestError("restore KMS Verify principal은 sink writer와 분리돼야 합니다.")
        self._sink = sink
        self._manifest_verifier = manifest_verifier
        self._checkpoint_provider = checkpoint_provider
        self._checkpoint_verifier = checkpoint_verifier
        self._minimum_retention_days = minimum_retention_days

    @property
    def sink(self) -> OperationalManifestSink:
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
        return _verify_gate(
            sink=self._sink,
            manifest_verifier=self._manifest_verifier,
            checkpoint_provider=self._checkpoint_provider,
            checkpoint_verifier=self._checkpoint_verifier,
            minimum_retention_days=self._minimum_retention_days,
            expectation=expectation,
            database_name=database_name,
            database_sha256=database_sha256,
            database_size_bytes=database_size_bytes,
            checksum_sha256=checksum_sha256,
            data_root=data_root,
        )


__all__ = [
    "BackupManifestRecord",
    "CheckpointVerifier",
    "ED25519_ALGORITHM",
    "IndependentManifestGate",
    "KMS_ASYMMETRIC_ALGORITHM",
    "KmsAsymmetricKeyMetadata",
    "KmsAsymmetricManifestSigner",
    "KmsAsymmetricManifestVerifier",
    "LOCAL_TEST_HMAC_ALGORITHM",
    "LocalTestAppendOnlyManifestSink",
    "LocalTestEd25519CheckpointSigner",
    "LocalTestEd25519ManifestSigner",
    "LocalTestHMACManifestSigner",
    "LocalTestHMACManifestVerifier",
    "LocalTestIndependentManifestGate",
    "LocalTestManifestBoundary",
    "LocalTestTrustedCheckpointProvider",
    "MANIFEST_VERSION",
    "ManifestConflictError",
    "ManifestError",
    "ManifestExpectation",
    "ManifestLedger",
    "ManifestSigner",
    "ManifestSink",
    "ManifestVerifier",
    "OperationalCheckpointProviderAttestationVerifier",
    "OperationalCheckpointProviderClaims",
    "OperationalIndependentManifestGate",
    "OperationalManifestSink",
    "OperationalSinkAttestationClaims",
    "OperationalSinkAttestationVerifier",
    "OperationalTrustedCheckpointProvider",
    "PinnedEd25519CheckpointVerifier",
    "PinnedEd25519ManifestVerifier",
    "SignedManifestCheckpoint",
    "TrustedCheckpointProvider",
    "VerifiedOperationalCheckpointProviderAttestation",
    "VerifiedOperationalSinkAttestation",
    "sign_manifest_checkpoint",
    "validate_manifest_chain",
]
