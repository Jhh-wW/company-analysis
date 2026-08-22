"""백업 manifest의 비밀 분리·운영 attestation·checkpoint 신뢰 계약.

이 모듈은 네트워크나 외부 자격증명에 접근하지 않는다. 저장소에는 로컬 시험용
구현과 운영 어댑터가 따라야 할 추상 계약만 둔다. 운영용 attestation은 외부
검증기가 검증한 불투명 타입으로만 표현하며 자유 불리언을 신뢰 근거로 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Final, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


LOCAL_TEST_HMAC_ALGORITHM: Final[str] = "local-test-hmac-sha256"
ED25519_ALGORITHM: Final[str] = "ed25519"
KMS_ASYMMETRIC_ALGORITHM: Final[str] = "kms-asymmetric"
CHECKPOINT_VERSION: Final[int] = 1
MIN_LOCAL_TEST_HMAC_BYTES: Final[int] = 32
MAX_ATTESTATION_BYTES: Final[int] = 64 * 1024
MAX_CHECKPOINT_SIGNATURE_HEX: Final[int] = 4096
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
HEX_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]+$")
IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$"
)
ARN_RE: Final[re.Pattern[str]] = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):[a-z0-9-]+:[a-z0-9-]*:[0-9]{0,12}:[A-Za-z0-9+=,.@_:/-]+$"
)
KMS_KEY_ARN_RE: Final[re.Pattern[str]] = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):kms:[a-z0-9-]+:[0-9]{12}:key/[0-9a-fA-F-]{16,64}$"
)


class ManifestError(RuntimeError):
    """백업 manifest 신뢰 계약을 만족하지 못했다."""


def identifier(value: object, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise ManifestError(f"{label} 식별자가 올바르지 않습니다.")
    return normalized


def sha256_text(value: object, *, label: str, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if allow_empty and not normalized:
        return ""
    if not SHA256_RE.fullmatch(normalized):
        raise ManifestError(f"{label} SHA-256이 올바르지 않습니다.")
    return normalized


def utc_datetime(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{label} 시각이 올바르지 않습니다.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ManifestError(f"{label} 시각은 UTC여야 합니다.")
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime, *, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ManifestError(f"{label} 시각은 timezone-aware여야 합니다.")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def validate_signature_text(signature: object, *, algorithm: str) -> str:
    normalized = str(signature or "").strip().lower()
    if not HEX_RE.fullmatch(normalized) or len(normalized) % 2:
        raise ManifestError("서명 인코딩은 짝수 길이의 소문자 hex여야 합니다.")
    if algorithm == LOCAL_TEST_HMAC_ALGORITHM and len(normalized) != 64:
        raise ManifestError("로컬 시험 HMAC 서명 길이가 올바르지 않습니다.")
    if algorithm == ED25519_ALGORITHM and len(normalized) != 128:
        raise ManifestError("Ed25519 서명 길이가 올바르지 않습니다.")
    if algorithm == KMS_ASYMMETRIC_ALGORITHM and not (
        128 <= len(normalized) <= MAX_CHECKPOINT_SIGNATURE_HEX
    ):
        raise ManifestError("KMS/HSM 비대칭 서명 길이가 올바르지 않습니다.")
    if algorithm not in {
        LOCAL_TEST_HMAC_ALGORITHM,
        ED25519_ALGORITHM,
        KMS_ASYMMETRIC_ALGORITHM,
    }:
        raise ManifestError("허용되지 않은 서명 알고리즘입니다.")
    return normalized


def spki_sha256_identity(public_key: Ed25519PublicKey) -> str:
    if not isinstance(public_key, Ed25519PublicKey):
        raise ManifestError("Ed25519 공개 키가 필요합니다.")
    spki = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return "spki-sha256:" + hashlib.sha256(spki).hexdigest()


class ManifestSigner(ABC):
    """writer 전용 sign-only 계약. 검증 메서드와 공개 gate 기능이 없다."""

    @property
    @abstractmethod
    def key_identity(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def algorithm(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def sign(self, payload: bytes) -> str:
        raise NotImplementedError


class ManifestVerifier(ABC):
    """restore/public gate 전용 verify-only 계약. 서명 메서드와 비밀이 없다."""

    @property
    @abstractmethod
    def key_identity(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def algorithm(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def verify(self, payload: bytes, signature: str) -> bool:
        raise NotImplementedError


class LocalTestHMACManifestSigner(ManifestSigner):
    """로컬 단위시험 전용 HMAC signer. 운영 계약에는 주입할 수 없다."""

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) < MIN_LOCAL_TEST_HMAC_BYTES:
            raise ManifestError("로컬 시험 HMAC 키는 32바이트 이상이어야 합니다.")
        self._key = bytes(key)
        self._key_identity = "local-test-hmac-sha256:" + hashlib.sha256(key).hexdigest()

    @property
    def key_identity(self) -> str:
        return self._key_identity

    @property
    def algorithm(self) -> str:
        return LOCAL_TEST_HMAC_ALGORITHM

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def __repr__(self) -> str:
        return "LocalTestHMACManifestSigner(key=[비공개])"


class LocalTestHMACManifestVerifier(ManifestVerifier):
    """로컬 단위시험 전용 HMAC verifier. 운영 gate는 타입으로 거부한다."""

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) < MIN_LOCAL_TEST_HMAC_BYTES:
            raise ManifestError("로컬 시험 HMAC 키는 32바이트 이상이어야 합니다.")
        self._key = bytes(key)
        self._key_identity = "local-test-hmac-sha256:" + hashlib.sha256(key).hexdigest()

    @property
    def key_identity(self) -> str:
        return self._key_identity

    @property
    def algorithm(self) -> str:
        return LOCAL_TEST_HMAC_ALGORITHM

    def verify(self, payload: bytes, signature: str) -> bool:
        try:
            normalized = validate_signature_text(signature, algorithm=self.algorithm)
        except ManifestError:
            return False
        expected = hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, normalized)

    def __repr__(self) -> str:
        return "LocalTestHMACManifestVerifier(key=[비공개])"


class LocalTestEd25519ManifestSigner(ManifestSigner):
    """로컬 시험용 Ed25519 private signer. 운영 private-key adapter는 제공하지 않는다."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ManifestError("Ed25519 private key 객체가 필요합니다.")
        self._private_key = private_key
        self._public_spki = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._key_identity = spki_sha256_identity(private_key.public_key())

    @classmethod
    def generate(cls) -> "LocalTestEd25519ManifestSigner":
        return cls(Ed25519PrivateKey.generate())

    @property
    def key_identity(self) -> str:
        return self._key_identity

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    @property
    def public_key_spki(self) -> bytes:
        return bytes(self._public_spki)

    def sign(self, payload: bytes) -> str:
        return self._private_key.sign(payload).hex()

    def __repr__(self) -> str:
        return f"LocalTestEd25519ManifestSigner(key_identity={self.key_identity!r}, key=[비공개])"


class PinnedEd25519ManifestVerifier(ManifestVerifier):
    """고정된 SPKI 공개 키만 보유하는 운영 호환 verify-only 구현."""

    def __init__(self, public_key_spki: bytes) -> None:
        try:
            public_key = serialization.load_der_public_key(bytes(public_key_spki))
        except (TypeError, ValueError) as exc:
            raise ManifestError("고정 Ed25519 SPKI 공개 키가 올바르지 않습니다.") from exc
        if not isinstance(public_key, Ed25519PublicKey):
            raise ManifestError("고정 공개 키는 Ed25519여야 합니다.")
        self._public_key = public_key
        self._key_identity = spki_sha256_identity(public_key)

    @property
    def key_identity(self) -> str:
        return self._key_identity

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    def verify(self, payload: bytes, signature: str) -> bool:
        try:
            encoded = bytes.fromhex(
                validate_signature_text(signature, algorithm=self.algorithm)
            )
            self._public_key.verify(encoded, payload)
        except (ManifestError, InvalidSignature, ValueError):
            return False
        return True


@dataclass(frozen=True)
class KmsAsymmetricKeyMetadata:
    """외부 KMS/HSM이 검증해 공급해야 하는 고정 키 metadata."""

    key_arn: str
    key_version: str
    signing_algorithm: str
    public_key_spki_sha256: str
    signer_principal_arn: str
    verifier_principal_arn: str
    sign_api: str
    verify_api: str

    def validate(self) -> None:
        if not KMS_KEY_ARN_RE.fullmatch(str(self.key_arn or "")):
            raise ManifestError("검증된 KMS key ARN이 필요합니다.")
        identifier(self.key_version, label="KMS 키 버전")
        if self.signing_algorithm not in {
            "ECDSA_SHA_256",
            "RSASSA_PSS_SHA_256",
            "EDDSA",
        }:
            raise ManifestError("허용되지 않은 KMS/HSM 비대칭 서명 알고리즘입니다.")
        sha256_text(self.public_key_spki_sha256, label="KMS 공개 키 SPKI")
        if not ARN_RE.fullmatch(str(self.signer_principal_arn or "")):
            raise ManifestError("검증된 KMS Sign IAM principal ARN이 필요합니다.")
        if not ARN_RE.fullmatch(str(self.verifier_principal_arn or "")):
            raise ManifestError("검증된 KMS Verify IAM principal ARN이 필요합니다.")
        if self.signer_principal_arn == self.verifier_principal_arn:
            raise ManifestError("KMS Sign과 Verify IAM principal은 분리돼야 합니다.")
        if self.sign_api != "kms:Sign" or self.verify_api != "kms:Verify":
            raise ManifestError("KMS Sign/Verify API 권한 경계가 분리돼야 합니다.")

    @property
    def key_identity(self) -> str:
        self.validate()
        digest = hashlib.sha256(canonical_json(asdict(self))).hexdigest()
        return "kms-key-metadata-sha256:" + digest


class KmsAsymmetricManifestSigner(ManifestSigner, ABC):
    """분리된 writer IAM/API가 구현할 외부 KMS/HSM sign-only 계약."""

    def __init__(self, metadata: KmsAsymmetricKeyMetadata) -> None:
        metadata.validate()
        self._metadata = metadata

    @property
    def key_identity(self) -> str:
        return self._metadata.key_identity

    @property
    def algorithm(self) -> str:
        return KMS_ASYMMETRIC_ALGORITHM

    @property
    def metadata(self) -> KmsAsymmetricKeyMetadata:
        return self._metadata


class KmsAsymmetricManifestVerifier(ManifestVerifier, ABC):
    """분리된 restore/public IAM/API가 구현할 외부 KMS/HSM verify-only 계약."""

    def __init__(self, metadata: KmsAsymmetricKeyMetadata) -> None:
        metadata.validate()
        self._metadata = metadata

    @property
    def key_identity(self) -> str:
        return self._metadata.key_identity

    @property
    def algorithm(self) -> str:
        return KMS_ASYMMETRIC_ALGORITHM

    @property
    def metadata(self) -> KmsAsymmetricKeyMetadata:
        return self._metadata


@dataclass(frozen=True)
class OperationalSinkAttestationClaims:
    """외부 verifier가 서명 evidence에서 복호·검증한 WORM sink 주장."""

    storage_provider: str
    sink_resource_arn: str
    writer_principal_arn: str
    reader_principal_arn: str
    retention_days: int
    object_lock_mode: str
    conditional_append_protocol: str
    issued_at: str
    expires_at: str

    def validate(self, *, minimum_retention_days: int, now: datetime) -> None:
        identifier(self.storage_provider, label="manifest 저장 공급자")
        for label, value in (
            ("manifest sink ARN", self.sink_resource_arn),
            ("manifest writer principal", self.writer_principal_arn),
            ("manifest reader principal", self.reader_principal_arn),
        ):
            if not ARN_RE.fullmatch(str(value or "")):
                raise ManifestError(f"검증된 {label}이 필요합니다.")
        if self.writer_principal_arn == self.reader_principal_arn:
            raise ManifestError("manifest writer와 reader principal은 분리돼야 합니다.")
        if isinstance(self.retention_days, bool) or self.retention_days < minimum_retention_days:
            raise ManifestError("WORM/Object Lock 보존 기간이 운영 최소값보다 짧습니다.")
        if self.object_lock_mode != "COMPLIANCE":
            raise ManifestError("운영 sink는 Object Lock COMPLIANCE/WORM 증명이 필요합니다.")
        if self.conditional_append_protocol != "if-none-match-and-head-cas":
            raise ManifestError("원자적 conditional append 증명이 필요합니다.")
        issued = utc_datetime(self.issued_at, label="sink attestation 발급")
        expires = utc_datetime(self.expires_at, label="sink attestation 만료")
        if expires <= issued or not (issued <= now <= expires):
            raise ManifestError("sink attestation이 유효한 시간 범위가 아닙니다.")


_SINK_ATTESTATION_TOKEN = object()


@dataclass(frozen=True)
class VerifiedOperationalSinkAttestation:
    claims: OperationalSinkAttestationClaims
    sink_identity: str
    _token: object = field(repr=False, compare=False)

    def validate(self, *, minimum_retention_days: int, now: datetime) -> None:
        if self._token is not _SINK_ATTESTATION_TOKEN:
            raise ManifestError("외부 verifier가 검증한 sink attestation이 아닙니다.")
        self.claims.validate(minimum_retention_days=minimum_retention_days, now=now)
        expected = "sink-attestation-sha256:" + hashlib.sha256(
            canonical_json(asdict(self.claims))
        ).hexdigest()
        if not hmac.compare_digest(self.sink_identity, expected):
            raise ManifestError("sink attestation 정체성이 claims와 결속되지 않았습니다.")


class OperationalSinkAttestationVerifier(ABC):
    """외부 trust root가 구현하는 signed attestation 검증기. 저장소 구현은 없다."""

    def verify(
        self,
        evidence: bytes,
        *,
        minimum_retention_days: int,
        now: datetime | None = None,
    ) -> VerifiedOperationalSinkAttestation:
        if not isinstance(evidence, bytes) or not 1 <= len(evidence) <= MAX_ATTESTATION_BYTES:
            raise ManifestError("sink attestation evidence 크기가 올바르지 않습니다.")
        claims = self._verify_and_decode(evidence)
        effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        claims.validate(
            minimum_retention_days=minimum_retention_days,
            now=effective_now,
        )
        identity = "sink-attestation-sha256:" + hashlib.sha256(
            canonical_json(asdict(claims))
        ).hexdigest()
        return VerifiedOperationalSinkAttestation(
            claims=claims,
            sink_identity=identity,
            _token=_SINK_ATTESTATION_TOKEN,
        )

    @abstractmethod
    def _verify_and_decode(self, evidence: bytes) -> OperationalSinkAttestationClaims:
        """서명과 발급자 체인을 검증한 뒤 claims만 반환한다."""
        raise NotImplementedError


class CheckpointSigner(ABC):
    """checkpoint 발급 전용 sign-only 계약."""

    @property
    @abstractmethod
    def key_identity(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def algorithm(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def sign(self, payload: bytes) -> str:
        raise NotImplementedError


class CheckpointVerifier(ABC):
    """checkpoint trust root의 verify-only·anti-rollback 계약."""

    @property
    @abstractmethod
    def key_identity(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def algorithm(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def verify_latest(self, checkpoint: "SignedManifestCheckpoint") -> None:
        raise NotImplementedError


class LocalTestEd25519CheckpointSigner(CheckpointSigner):
    """manifest signer와 별도 키를 쓰는 로컬 checkpoint 발급기."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        if not isinstance(private_key, Ed25519PrivateKey):
            raise ManifestError("checkpoint Ed25519 private key 객체가 필요합니다.")
        self._private_key = private_key
        self._public_spki = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._key_identity = spki_sha256_identity(private_key.public_key())

    @classmethod
    def generate(cls) -> "LocalTestEd25519CheckpointSigner":
        return cls(Ed25519PrivateKey.generate())

    @property
    def key_identity(self) -> str:
        return self._key_identity

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    @property
    def public_key_spki(self) -> bytes:
        return bytes(self._public_spki)

    def sign(self, payload: bytes) -> str:
        return self._private_key.sign(payload).hex()

    def __repr__(self) -> str:
        return f"LocalTestEd25519CheckpointSigner(key_identity={self.key_identity!r}, key=[비공개])"


@dataclass(frozen=True)
class SignedManifestCheckpoint:
    version: int
    scope: str
    sink_identity: str
    checkpoint_provider_identity: str
    manifest_key_identity: str
    sequence: int
    head_record_sha256: str
    issued_at: str
    checkpoint_key_identity: str
    signature_algorithm: str
    signature: str = field(repr=False)

    def payload(self) -> dict[str, object]:
        values = asdict(self)
        values.pop("signature")
        return values

    def payload_bytes(self) -> bytes:
        return canonical_json(self.payload())

    def validate_shape(self) -> None:
        if isinstance(self.version, bool) or self.version != CHECKPOINT_VERSION:
            raise ManifestError("지원하지 않는 checkpoint 버전입니다.")
        identifier(self.scope, label="checkpoint scope")
        identifier(self.sink_identity, label="checkpoint sink")
        identifier(self.checkpoint_provider_identity, label="checkpoint provider")
        identifier(self.manifest_key_identity, label="checkpoint manifest 키")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ManifestError("checkpoint sequence가 올바르지 않습니다.")
        sha256_text(self.head_record_sha256, label="checkpoint head 레코드")
        utc_datetime(self.issued_at, label="checkpoint 발급")
        identifier(self.checkpoint_key_identity, label="checkpoint 서명 키")
        if self.signature_algorithm != ED25519_ALGORITHM:
            raise ManifestError("checkpoint는 고정 Ed25519 공개 키로 검증해야 합니다.")
        validate_signature_text(self.signature, algorithm=self.signature_algorithm)


def sign_manifest_checkpoint(
    *,
    signer: CheckpointSigner,
    scope: str,
    sink_identity: str,
    checkpoint_provider_identity: str,
    manifest_key_identity: str,
    sequence: int,
    head_record_sha256: str,
    issued_at: datetime,
) -> SignedManifestCheckpoint:
    """writer와 분리된 checkpoint 발급 경계에서 호출하는 순수 서명 함수."""

    if not isinstance(signer, CheckpointSigner):
        raise ManifestError("checkpoint sign-only 구현이 필요합니다.")
    if signer.algorithm != ED25519_ALGORITHM:
        raise ManifestError("checkpoint signer는 Ed25519 비대칭 키여야 합니다.")
    unsigned = SignedManifestCheckpoint(
        version=CHECKPOINT_VERSION,
        scope=identifier(scope, label="checkpoint scope"),
        sink_identity=identifier(sink_identity, label="checkpoint sink"),
        checkpoint_provider_identity=identifier(
            checkpoint_provider_identity,
            label="checkpoint provider",
        ),
        manifest_key_identity=identifier(
            manifest_key_identity, label="checkpoint manifest 키"
        ),
        sequence=sequence,
        head_record_sha256=sha256_text(
            head_record_sha256, label="checkpoint head 레코드"
        ),
        issued_at=utc_text(issued_at, label="checkpoint 발급"),
        checkpoint_key_identity=identifier(
            signer.key_identity, label="checkpoint 서명 키"
        ),
        signature_algorithm=signer.algorithm,
        signature="0" * 128,
    )
    unsigned.validate_shape()
    signed = replace(unsigned, signature=signer.sign(unsigned.payload_bytes()))
    signed.validate_shape()
    return signed


class PinnedEd25519CheckpointVerifier(CheckpointVerifier):
    """고정 공개 키와 관측한 최신 head를 함께 잠그는 checkpoint 검증기."""

    def __init__(self, public_key_spki: bytes) -> None:
        try:
            public_key = serialization.load_der_public_key(bytes(public_key_spki))
        except (TypeError, ValueError) as exc:
            raise ManifestError("고정 checkpoint SPKI 공개 키가 올바르지 않습니다.") from exc
        if not isinstance(public_key, Ed25519PublicKey):
            raise ManifestError("checkpoint 공개 키는 Ed25519여야 합니다.")
        self._public_key = public_key
        self._key_identity = spki_sha256_identity(public_key)
        self._highest: dict[tuple[str, str, str, str], tuple[int, str]] = {}
        self._lock = threading.Lock()

    @property
    def key_identity(self) -> str:
        return self._key_identity

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    def verify_latest(self, checkpoint: SignedManifestCheckpoint) -> None:
        checkpoint.validate_shape()
        if checkpoint.checkpoint_key_identity != self.key_identity:
            raise ManifestError("checkpoint가 고정 공개 키에 결속되지 않았습니다.")
        try:
            signature = bytes.fromhex(
                validate_signature_text(
                    checkpoint.signature,
                    algorithm=checkpoint.signature_algorithm,
                )
            )
            self._public_key.verify(signature, checkpoint.payload_bytes())
        except (InvalidSignature, ValueError, ManifestError) as exc:
            raise ManifestError("checkpoint 서명 검증에 실패했습니다.") from exc

        binding = (
            checkpoint.scope,
            checkpoint.sink_identity,
            checkpoint.checkpoint_provider_identity,
            checkpoint.manifest_key_identity,
        )
        with self._lock:
            highest = self._highest.get(binding)
            if highest is not None and checkpoint.sequence < highest[0]:
                raise ManifestError("오래된 checkpoint rollback/replay를 거부했습니다.")
            if (
                highest is not None
                and checkpoint.sequence == highest[0]
                and not hmac.compare_digest(checkpoint.head_record_sha256, highest[1])
            ):
                raise ManifestError("같은 checkpoint sequence의 fork를 거부했습니다.")
            self._highest[binding] = (
                checkpoint.sequence,
                checkpoint.head_record_sha256,
            )


class TrustedCheckpointProvider(ABC):
    """manifest sink와 별도 권한·저장 경계에서 latest checkpoint만 읽는 계약."""

    @property
    @abstractmethod
    def provider_identity(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def latest_checkpoint(
        self,
        *,
        scope: str,
        sink_identity: str,
        manifest_key_identity: str,
    ) -> SignedManifestCheckpoint:
        raise NotImplementedError


class LocalTestTrustedCheckpointProvider(TrustedCheckpointProvider):
    """메모리 단위시험 전용 독립 checkpoint store. 운영 승격할 수 없다."""

    def __init__(self, *, provider_identity: str = "local-test-checkpoint-store") -> None:
        self._provider_identity = "local-test:" + identifier(
            provider_identity, label="시험 checkpoint provider"
        )
        self._checkpoints: list[SignedManifestCheckpoint] = []
        self._lock = threading.Lock()

    @property
    def provider_identity(self) -> str:
        return self._provider_identity

    def publish(self, checkpoint: SignedManifestCheckpoint) -> None:
        checkpoint.validate_shape()
        if checkpoint.checkpoint_provider_identity != self.provider_identity:
            raise ManifestError("checkpoint가 이 provider 정체성에 결속되지 않았습니다.")
        binding = (
            checkpoint.scope,
            checkpoint.sink_identity,
            checkpoint.manifest_key_identity,
        )
        with self._lock:
            previous = [
                item
                for item in self._checkpoints
                if (
                    item.scope,
                    item.sink_identity,
                    item.manifest_key_identity,
                )
                == binding
                and item.checkpoint_provider_identity == self.provider_identity
            ]
            if previous and checkpoint.sequence <= previous[-1].sequence:
                raise ManifestError("checkpoint store는 rollback/replay publish를 거부합니다.")
            self._checkpoints.append(checkpoint)

    def latest_checkpoint(
        self,
        *,
        scope: str,
        sink_identity: str,
        manifest_key_identity: str,
    ) -> SignedManifestCheckpoint:
        binding = (
            identifier(scope, label="checkpoint scope"),
            identifier(sink_identity, label="checkpoint sink"),
            identifier(manifest_key_identity, label="checkpoint manifest 키"),
        )
        with self._lock:
            matching = [
                item
                for item in self._checkpoints
                if (
                    item.scope,
                    item.sink_identity,
                    item.manifest_key_identity,
                )
                == binding
            ]
        if not matching:
            raise ManifestError("독립 최신 checkpoint가 없습니다.")
        return matching[-1]


@dataclass(frozen=True)
class OperationalCheckpointProviderClaims:
    provider_resource_arn: str
    writer_principal_arn: str
    reader_principal_arn: str
    sink_identity: str
    checkpoint_key_identity: str
    monotonic_read_protocol: str
    issued_at: str
    expires_at: str

    def validate(self, *, now: datetime) -> None:
        if not ARN_RE.fullmatch(str(self.provider_resource_arn or "")):
            raise ManifestError("검증된 checkpoint provider ARN이 필요합니다.")
        if not ARN_RE.fullmatch(str(self.reader_principal_arn or "")):
            raise ManifestError("검증된 checkpoint reader principal이 필요합니다.")
        if not ARN_RE.fullmatch(str(self.writer_principal_arn or "")):
            raise ManifestError("검증된 checkpoint writer principal이 필요합니다.")
        if self.writer_principal_arn == self.reader_principal_arn:
            raise ManifestError("checkpoint writer와 reader principal은 분리돼야 합니다.")
        identifier(self.sink_identity, label="checkpoint sink")
        identifier(self.checkpoint_key_identity, label="checkpoint 서명 키")
        if self.monotonic_read_protocol != "strong-latest-with-generation-cas":
            raise ManifestError("checkpoint 최신성·단조성 증명이 필요합니다.")
        issued = utc_datetime(self.issued_at, label="checkpoint provider attestation 발급")
        expires = utc_datetime(self.expires_at, label="checkpoint provider attestation 만료")
        if expires <= issued or not (issued <= now <= expires):
            raise ManifestError("checkpoint provider attestation이 유효하지 않습니다.")


_CHECKPOINT_PROVIDER_TOKEN = object()


@dataclass(frozen=True)
class VerifiedOperationalCheckpointProviderAttestation:
    claims: OperationalCheckpointProviderClaims
    provider_identity: str
    _token: object = field(repr=False, compare=False)

    def validate(self, *, now: datetime) -> None:
        if self._token is not _CHECKPOINT_PROVIDER_TOKEN:
            raise ManifestError("외부 verifier가 검증한 checkpoint provider가 아닙니다.")
        self.claims.validate(now=now)
        expected = "checkpoint-provider-attestation-sha256:" + hashlib.sha256(
            canonical_json(asdict(self.claims))
        ).hexdigest()
        if not hmac.compare_digest(self.provider_identity, expected):
            raise ManifestError("checkpoint provider 정체성이 claims와 결속되지 않았습니다.")


class OperationalCheckpointProviderAttestationVerifier(ABC):
    """별도 최신성 저장소의 attestation을 검증하는 외부 trust root 계약."""

    def verify(
        self,
        evidence: bytes,
        *,
        now: datetime | None = None,
    ) -> VerifiedOperationalCheckpointProviderAttestation:
        if not isinstance(evidence, bytes) or not 1 <= len(evidence) <= MAX_ATTESTATION_BYTES:
            raise ManifestError("checkpoint provider evidence 크기가 올바르지 않습니다.")
        claims = self._verify_and_decode(evidence)
        effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        claims.validate(now=effective_now)
        identity = "checkpoint-provider-attestation-sha256:" + hashlib.sha256(
            canonical_json(asdict(claims))
        ).hexdigest()
        return VerifiedOperationalCheckpointProviderAttestation(
            claims=claims,
            provider_identity=identity,
            _token=_CHECKPOINT_PROVIDER_TOKEN,
        )

    @abstractmethod
    def _verify_and_decode(self, evidence: bytes) -> OperationalCheckpointProviderClaims:
        raise NotImplementedError


class OperationalTrustedCheckpointProvider(TrustedCheckpointProvider, ABC):
    """외부 attestation을 통과한 운영 checkpoint provider 추상 계약."""

    def __init__(
        self,
        attestation: VerifiedOperationalCheckpointProviderAttestation,
    ) -> None:
        if not isinstance(
            attestation, VerifiedOperationalCheckpointProviderAttestation
        ):
            raise ManifestError("검증된 운영 checkpoint provider attestation이 필요합니다.")
        self._attestation = attestation

    @property
    def attestation(self) -> VerifiedOperationalCheckpointProviderAttestation:
        return self._attestation

    @property
    def provider_identity(self) -> str:
        return self._attestation.provider_identity


__all__ = [
    "CHECKPOINT_VERSION",
    "CheckpointSigner",
    "CheckpointVerifier",
    "ED25519_ALGORITHM",
    "KMS_ASYMMETRIC_ALGORITHM",
    "KmsAsymmetricKeyMetadata",
    "KmsAsymmetricManifestSigner",
    "KmsAsymmetricManifestVerifier",
    "LOCAL_TEST_HMAC_ALGORITHM",
    "LocalTestEd25519CheckpointSigner",
    "LocalTestEd25519ManifestSigner",
    "LocalTestHMACManifestSigner",
    "LocalTestHMACManifestVerifier",
    "LocalTestTrustedCheckpointProvider",
    "ManifestError",
    "ManifestSigner",
    "ManifestVerifier",
    "OperationalCheckpointProviderAttestationVerifier",
    "OperationalCheckpointProviderClaims",
    "OperationalSinkAttestationClaims",
    "OperationalSinkAttestationVerifier",
    "OperationalTrustedCheckpointProvider",
    "PinnedEd25519CheckpointVerifier",
    "PinnedEd25519ManifestVerifier",
    "SignedManifestCheckpoint",
    "TrustedCheckpointProvider",
    "VerifiedOperationalCheckpointProviderAttestation",
    "VerifiedOperationalSinkAttestation",
    "canonical_json",
    "identifier",
    "sha256_text",
    "sign_manifest_checkpoint",
    "spki_sha256_identity",
    "utc_datetime",
    "utc_text",
    "validate_signature_text",
]
