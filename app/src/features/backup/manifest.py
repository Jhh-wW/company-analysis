"""외부 SQLite 백업을 독립 manifest에 기록하는 운영 appender 계약.

앱은 secret manager나 원격 WORM 저장소에 접속하지 않는다. 운영 조립부는 외부
서명 evidence verifier가 만든 :class:`VerifiedOperationalManifestContract`와
appender를 함께 주입해야 한다. 자유 ``production_ready`` 불리언이나 임의
authority 문자열만으로 운영 성공을 반환할 수 없다.
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


MANIFEST_VERSION: Final[int] = 2
ED25519_ALGORITHM: Final[str] = "ed25519"
KMS_ASYMMETRIC_ALGORITHM: Final[str] = "kms-asymmetric"
MAX_ATTESTATION_BYTES: Final[int] = 64 * 1024
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
HEX_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]+$")
IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$"
)
ARN_RE: Final[re.Pattern[str]] = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):[a-z0-9-]+:[a-z0-9-]*:[0-9]{0,12}:[A-Za-z0-9+=,.@_:/-]+$"
)
_PROVIDER_LOCK = threading.Lock()
_PROVIDER: Callable[[], "BackupManifestAppender"] | None = None
_VERIFIED_CONTRACT_TOKEN = object()


class ManifestConfigurationError(RuntimeError):
    """독립 manifest 운영 주입이 없거나 검증된 경계가 안전하지 않다."""


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


def _signature(value: object, *, algorithm: str) -> str:
    normalized = str(value or "").strip().lower()
    if not HEX_RE.fullmatch(normalized) or len(normalized) % 2:
        raise ManifestAppendError("manifest 서명은 짝수 길이의 소문자 hex여야 합니다")
    if algorithm == ED25519_ALGORITHM and len(normalized) != 128:
        raise ManifestAppendError("Ed25519 manifest 서명 길이가 올바르지 않습니다")
    if algorithm == KMS_ASYMMETRIC_ALGORITHM and not 128 <= len(normalized) <= 4096:
        raise ManifestAppendError("KMS/HSM manifest 서명 길이가 올바르지 않습니다")
    if algorithm not in {ED25519_ALGORITHM, KMS_ASYMMETRIC_ALGORITHM}:
        raise ManifestAppendError("운영 manifest는 비대칭 서명만 허용합니다")
    return normalized


def _derived_identity(value: object, *, prefix: str, label: str) -> str:
    normalized = _identifier(value, label=label)
    suffix = normalized.removeprefix(prefix)
    if not normalized.startswith(prefix) or not SHA256_RE.fullmatch(suffix):
        raise ManifestConfigurationError(
            f"{label} 정체성은 {prefix}<lowercase-64-hex> 형식이어야 합니다"
        )
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
class OperationalManifestContractClaims:
    """외부 verifier가 서명 evidence에서 검증한 운영 appender claims."""

    sink_identity: str
    writer_principal_arn: str
    reader_principal_arn: str
    retention_days: int
    object_lock_mode: str
    conditional_append_protocol: str
    manifest_key_identity: str
    signature_algorithm: str
    checkpoint_provider_identity: str
    checkpoint_key_identity: str
    issued_at: str
    expires_at: str

    def validate(self, *, minimum_retention_days: int, now: datetime) -> None:
        _derived_identity(
            self.sink_identity,
            prefix="sink-attestation-sha256:",
            label="manifest sink",
        )
        for label, value in (
            ("manifest writer principal", self.writer_principal_arn),
            ("manifest reader principal", self.reader_principal_arn),
        ):
            if not ARN_RE.fullmatch(str(value or "")):
                raise ManifestConfigurationError(f"검증된 {label} ARN이 필요합니다")
        if self.writer_principal_arn == self.reader_principal_arn:
            raise ManifestConfigurationError("manifest writer와 reader principal은 분리돼야 합니다")
        if isinstance(self.retention_days, bool) or self.retention_days < minimum_retention_days:
            raise ManifestConfigurationError("manifest WORM 보존 기간이 최소값보다 짧습니다")
        if self.object_lock_mode != "COMPLIANCE":
            raise ManifestConfigurationError("Object Lock COMPLIANCE/WORM 증명이 필요합니다")
        if self.conditional_append_protocol != "if-none-match-and-head-cas":
            raise ManifestConfigurationError("원자적 conditional append 증명이 필요합니다")
        if self.manifest_key_identity.startswith("spki-sha256:"):
            _derived_identity(
                self.manifest_key_identity,
                prefix="spki-sha256:",
                label="manifest 키",
            )
        elif self.manifest_key_identity.startswith("kms-key-metadata-sha256:"):
            _derived_identity(
                self.manifest_key_identity,
                prefix="kms-key-metadata-sha256:",
                label="manifest 키",
            )
        else:
            raise ManifestConfigurationError("manifest 키 정체성은 SPKI/KMS metadata에서 파생돼야 합니다")
        if self.signature_algorithm not in {
            ED25519_ALGORITHM,
            KMS_ASYMMETRIC_ALGORITHM,
        }:
            raise ManifestConfigurationError("운영 manifest는 비대칭 서명만 허용합니다")
        _derived_identity(
            self.checkpoint_provider_identity,
            prefix="checkpoint-provider-attestation-sha256:",
            label="checkpoint provider",
        )
        _derived_identity(
            self.checkpoint_key_identity,
            prefix="spki-sha256:",
            label="checkpoint 키",
        )
        if self.checkpoint_key_identity == self.manifest_key_identity:
            raise ManifestConfigurationError("manifest와 checkpoint 서명 키는 분리돼야 합니다")
        issued = _utc_datetime(self.issued_at, label="manifest 계약 발급")
        expires = _utc_datetime(self.expires_at, label="manifest 계약 만료")
        if expires <= issued or not (issued <= now <= expires):
            raise ManifestConfigurationError("manifest 운영 계약 attestation이 유효하지 않습니다")


@dataclass(frozen=True)
class VerifiedOperationalManifestContract:
    claims: OperationalManifestContractClaims
    evidence_sha256: str
    _token: object = field(repr=False, compare=False)

    def validate(self, *, minimum_retention_days: int, now: datetime) -> None:
        if self._token is not _VERIFIED_CONTRACT_TOKEN:
            raise ManifestConfigurationError("외부 verifier가 검증한 manifest 계약이 아닙니다")
        self.claims.validate(
            minimum_retention_days=minimum_retention_days,
            now=now.astimezone(timezone.utc),
        )
        _sha256(self.evidence_sha256, label="manifest attestation evidence")


class OperationalManifestContractVerifier(ABC):
    """외부 신뢰 루트가 운영 appender evidence를 검증하는 추상 계약."""

    def verify(
        self,
        evidence: bytes,
        *,
        minimum_retention_days: int,
        now: datetime | None = None,
    ) -> VerifiedOperationalManifestContract:
        if not isinstance(evidence, bytes) or not 1 <= len(evidence) <= MAX_ATTESTATION_BYTES:
            raise ManifestConfigurationError("manifest attestation evidence 크기가 올바르지 않습니다")
        claims = self._verify_and_decode(evidence)
        effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        claims.validate(
            minimum_retention_days=minimum_retention_days,
            now=effective_now,
        )
        return VerifiedOperationalManifestContract(
            claims=claims,
            evidence_sha256=hashlib.sha256(evidence).hexdigest(),
            _token=_VERIFIED_CONTRACT_TOKEN,
        )

    @abstractmethod
    def _verify_and_decode(self, evidence: bytes) -> OperationalManifestContractClaims:
        """evidence의 발급자·서명·claims 결속을 외부 trust root로 검증한다."""
        raise NotImplementedError


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
    """ops verifier와 공유하는 버전 2 wire schema."""

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
    signature: str = field(repr=False)

    def serialized_bytes(self) -> bytes:
        return _canonical_json(asdict(self))

    def record_sha256(self) -> str:
        return hashlib.sha256(self.serialized_bytes()).hexdigest()


@dataclass(frozen=True)
class ManifestAppendReceipt:
    record: SignedBackupManifestRecord
    readback_record_sha256: str
    readback_head_sequence: int


class BackupManifestAppender(ABC):
    """검증된 운영 계약을 보유한 독립 manifest append 추상 계약."""

    @property
    @abstractmethod
    def contract(self) -> VerifiedOperationalManifestContract:
        raise NotImplementedError

    @abstractmethod
    def append_and_readback(
        self,
        request: BackupManifestRequest,
    ) -> ManifestAppendReceipt:
        """CAS append 후 동일 record bytes/head read-back receipt를 반환한다."""
        raise NotImplementedError


def install_manifest_appender_provider(
    provider: Callable[[], BackupManifestAppender],
) -> None:
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
                "검증된 독립 manifest appender가 주입되지 않아 백업을 중단합니다"
            )
        appender = provider()
    if not isinstance(appender, BackupManifestAppender):
        raise ManifestConfigurationError("닫힌 BackupManifestAppender 구현이 필요합니다")
    if not isinstance(appender.contract, VerifiedOperationalManifestContract):
        raise ManifestConfigurationError("검증된 운영 manifest 계약이 필요합니다")
    return appender


def validate_append_receipt(
    *,
    request: BackupManifestRequest,
    appender: BackupManifestAppender,
    receipt: ManifestAppendReceipt,
    now: datetime | None = None,
) -> SignedBackupManifestRecord:
    """성공 반환 전에 object binding과 attested contract/read-back을 재검사한다."""

    request.validate()
    contract = appender.contract
    effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    contract.validate(
        minimum_retention_days=request.minimum_retention_days,
        now=effective_now,
    )
    claims = contract.claims
    if claims.sink_identity == request.data_boundary_id or claims.writer_principal_arn == request.data_authority_id:
        raise ManifestConfigurationError("manifest와 DB/sidecar의 쓰기·보존 경계가 독립적이지 않습니다")
    if not isinstance(receipt, ManifestAppendReceipt):
        raise ManifestAppendError("manifest append read-back receipt가 없습니다")
    record = receipt.record
    if record.version != MANIFEST_VERSION:
        raise ManifestAppendError("지원하지 않는 manifest 버전입니다")
    if (
        isinstance(record.sequence, bool)
        or record.sequence < 1
        or receipt.readback_head_sequence != record.sequence
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
        "manifest_boundary_id": claims.sink_identity,
        "manifest_writer_principal": claims.writer_principal_arn,
        "manifest_key_identity": claims.manifest_key_identity,
        "signature_algorithm": claims.signature_algorithm,
    }
    for name, expected in expected_values.items():
        if getattr(record, name) != expected:
            raise ManifestAppendError(f"manifest 레코드의 {name} 결속이 다릅니다")
    created = _utc_datetime(record.created_at, label="백업 생성")
    retained = _utc_datetime(record.retention_until, label="백업 보존")
    if retained - created < timedelta(days=claims.retention_days):
        raise ManifestAppendError("manifest 레코드 보존 기한이 sink 계약보다 짧습니다")
    _sha256(record.previous_record_sha256, label="이전 레코드", allow_empty=True)
    _signature(record.signature, algorithm=record.signature_algorithm)
    actual_record_hash = record.record_sha256()
    expected_record_hash = _sha256(
        receipt.readback_record_sha256,
        label="manifest read-back 레코드",
    )
    if not hmac.compare_digest(actual_record_hash, expected_record_hash):
        raise ManifestAppendError("manifest read-back 레코드 지문이 맞지 않습니다")
    return record


__all__ = [
    "BackupManifestAppender",
    "BackupManifestRequest",
    "ED25519_ALGORITHM",
    "KMS_ASYMMETRIC_ALGORITHM",
    "MANIFEST_VERSION",
    "ManifestAppendError",
    "ManifestAppendReceipt",
    "ManifestConfigurationError",
    "OperationalManifestContractClaims",
    "OperationalManifestContractVerifier",
    "SignedBackupManifestRecord",
    "VerifiedOperationalManifestContract",
    "install_manifest_appender_provider",
    "require_manifest_appender",
    "validate_append_receipt",
]
