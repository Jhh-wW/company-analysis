"""비대칭 manifest와 독립 최신 checkpoint의 공격 경계 시험."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import backup_manifest as manifest


NOW = datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
CHECKSUM_DIGEST = "c" * 64
HMAC_KEY = b"local-test-only-hmac-key-32-bytes-minimum"


def _boundary() -> manifest.LocalTestManifestBoundary:
    return manifest.LocalTestManifestBoundary(
        boundary_label="independent-ledger",
        writer_label="independent-security",
        retention_days=35,
    )


def _ed_components(path: Path):
    signer = manifest.LocalTestEd25519ManifestSigner.generate()
    verifier = manifest.PinnedEd25519ManifestVerifier(signer.public_key_spki)
    sink = manifest.LocalTestAppendOnlyManifestSink(path, boundary=_boundary())
    ledger = manifest.ManifestLedger(
        sink=sink,
        signer=signer,
        minimum_retention_days=35,
    )
    checkpoint_signer = manifest.LocalTestEd25519CheckpointSigner.generate()
    checkpoint_verifier = manifest.PinnedEd25519CheckpointVerifier(
        checkpoint_signer.public_key_spki
    )
    checkpoint_provider = manifest.LocalTestTrustedCheckpointProvider()
    return (
        signer,
        verifier,
        sink,
        ledger,
        checkpoint_signer,
        checkpoint_verifier,
        checkpoint_provider,
    )


def _append(
    ledger: manifest.ManifestLedger,
    *,
    backup_id: str,
    database_name: str,
    digest: str,
    created_at: datetime,
) -> manifest.BackupManifestRecord:
    return ledger.append_backup(
        scope="storage-db",
        backup_id=backup_id,
        storage_provider="s3",
        storage_bucket="private-backups",
        object_key=f"company-analysis/{database_name}",
        checksum_key=f"company-analysis/{database_name}.sha256",
        database_name=database_name,
        database_sha256=digest,
        database_size_bytes=4096,
        checksum_sha256=CHECKSUM_DIGEST,
        created_at=created_at,
        data_boundary_id="private-backup-bucket",
        data_authority_id="backup-writer",
    )


def _checkpoint(
    *,
    signer: manifest.LocalTestEd25519CheckpointSigner,
    sink: manifest.ManifestSink,
    verifier: manifest.ManifestVerifier,
    provider: manifest.LocalTestTrustedCheckpointProvider,
    record: manifest.BackupManifestRecord,
    head_sha256: str | None = None,
) -> manifest.SignedManifestCheckpoint:
    return manifest.sign_manifest_checkpoint(
        signer=signer,
        scope=record.scope,
        sink_identity=sink.sink_identity,
        checkpoint_provider_identity=provider.provider_identity,
        manifest_key_identity=verifier.key_identity,
        sequence=record.sequence,
        head_record_sha256=head_sha256 or record.record_sha256(),
        issued_at=record_created(record) + timedelta(seconds=1),
    )


def record_created(record: manifest.BackupManifestRecord) -> datetime:
    return datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))


def _gate(
    *,
    sink: manifest.LocalTestAppendOnlyManifestSink,
    verifier: manifest.ManifestVerifier,
    checkpoint_provider: manifest.LocalTestTrustedCheckpointProvider,
    checkpoint_verifier: manifest.CheckpointVerifier,
) -> manifest.LocalTestIndependentManifestGate:
    return manifest.LocalTestIndependentManifestGate(
        sink=sink,
        manifest_verifier=verifier,
        checkpoint_provider=checkpoint_provider,
        checkpoint_verifier=checkpoint_verifier,
        minimum_retention_days=35,
    )


def _expectation(
    backup_id: str,
    database_name: str = "storage-backup.sqlite3",
) -> manifest.ManifestExpectation:
    return manifest.ManifestExpectation(
        backup_id=backup_id,
        scope="storage-db",
        storage_provider="s3",
        storage_bucket="private-backups",
        object_key=f"company-analysis/{database_name}",
        checksum_key=f"company-analysis/{database_name}.sha256",
        data_boundary_id="private-backup-bucket",
        data_authority_id="backup-writer",
        now=NOW + timedelta(minutes=5),
    )


def _verify(
    gate: manifest.IndependentManifestGate,
    *,
    tmp_path: Path,
    backup_id: str,
    database_name: str,
    digest: str,
) -> manifest.BackupManifestRecord:
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    return gate.verify(
        expectation=_expectation(backup_id, database_name),
        database_name=database_name,
        database_sha256=digest,
        database_size_bytes=4096,
        checksum_sha256=CHECKSUM_DIGEST,
        data_root=data_root,
    )


def test_정상_Ed25519와_독립_checkpoint는_verify를_통과한다(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    (
        signer,
        verifier,
        sink,
        ledger,
        checkpoint_signer,
        checkpoint_verifier,
        provider,
    ) = _ed_components(manifest_path)
    record = _append(
        ledger,
        backup_id="backup-current",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )
    provider.publish(
        _checkpoint(
            signer=checkpoint_signer,
            sink=sink,
            verifier=verifier,
            provider=provider,
            record=record,
        )
    )

    verified = _verify(
        _gate(
            sink=sink,
            verifier=verifier,
            checkpoint_provider=provider,
            checkpoint_verifier=checkpoint_verifier,
        ),
        tmp_path=tmp_path,
        backup_id="backup-current",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_A,
    )

    assert verified == record
    assert verified.signature_algorithm == manifest.ED25519_ALGORITHM
    assert verified.manifest_key_identity == verifier.key_identity
    assert not hasattr(signer, "verify")
    assert not hasattr(verifier, "sign")


@pytest.mark.parametrize("replacement_kind", ("hmac", "ed25519"))
def test_DB_sidecar_manifest_전체를_새키로_교체해도_pinned_verifier가_거부한다(
    tmp_path: Path,
    replacement_kind: str,
) -> None:
    target_path = tmp_path / "independent" / "manifest.jsonl"
    target_path.parent.mkdir()
    (
        _original_signer,
        pinned_verifier,
        sink,
        ledger,
        checkpoint_signer,
        checkpoint_verifier,
        provider,
    ) = _ed_components(target_path)
    original = _append(
        ledger,
        backup_id="original",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )
    provider.publish(
        _checkpoint(
            signer=checkpoint_signer,
            sink=sink,
            verifier=pinned_verifier,
            provider=provider,
            record=original,
        )
    )

    attacker_path = tmp_path / "attacker" / "manifest.jsonl"
    attacker_path.parent.mkdir()
    attacker_sink = manifest.LocalTestAppendOnlyManifestSink(
        attacker_path,
        boundary=_boundary(),
    )
    if replacement_kind == "hmac":
        attacker_signer: manifest.ManifestSigner = manifest.LocalTestHMACManifestSigner(
            HMAC_KEY
        )
    else:
        attacker_signer = manifest.LocalTestEd25519ManifestSigner.generate()
    attacker_ledger = manifest.ManifestLedger(
        sink=attacker_sink,
        signer=attacker_signer,
        minimum_retention_days=35,
    )
    _append(
        attacker_ledger,
        backup_id="original",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_B,
        created_at=NOW,
    )
    target_path.write_bytes(attacker_path.read_bytes())

    gate = _gate(
        sink=sink,
        verifier=pinned_verifier,
        checkpoint_provider=provider,
        checkpoint_verifier=checkpoint_verifier,
    )
    with pytest.raises(manifest.ManifestError, match="키 정체성|알고리즘|서명"):
        _verify(
            gate,
            tmp_path=tmp_path,
            backup_id="original",
            database_name="storage-backup.sqlite3",
            digest=DIGEST_B,
        )


def test_manifest_signer_탈취만으로_checkpoint를_위조할수없다(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    (
        manifest_signer,
        verifier,
        sink,
        ledger,
        checkpoint_signer,
        checkpoint_verifier,
        provider,
    ) = _ed_components(manifest_path)
    record = _append(
        ledger,
        backup_id="signer-compromised",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )
    valid = _checkpoint(
        signer=checkpoint_signer,
        sink=sink,
        verifier=verifier,
        provider=provider,
        record=record,
    )
    forged = replace(
        valid,
        signature=manifest_signer.sign(valid.payload_bytes()),
    )
    provider.publish(forged)

    with pytest.raises(manifest.ManifestError, match="checkpoint 서명"):
        _verify(
            _gate(
                sink=sink,
                verifier=verifier,
                checkpoint_provider=provider,
                checkpoint_verifier=checkpoint_verifier,
            ),
            tmp_path=tmp_path,
            backup_id="signer-compromised",
            database_name="storage-backup.sqlite3",
            digest=DIGEST_A,
        )
    with pytest.raises(manifest.ManifestError, match="checkpoint sign-only"):
        manifest.sign_manifest_checkpoint(  # type: ignore[arg-type]
            signer=manifest_signer,
            scope=record.scope,
            sink_identity=sink.sink_identity,
            checkpoint_provider_identity=provider.provider_identity,
            manifest_key_identity=verifier.key_identity,
            sequence=record.sequence,
            head_record_sha256=record.record_sha256(),
            issued_at=NOW + timedelta(seconds=1),
        )


def test_checkpoint_부재_변조_wrong_head를_모두_fail_closed한다(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    (
        _signer,
        verifier,
        sink,
        ledger,
        checkpoint_signer,
        checkpoint_verifier,
        provider,
    ) = _ed_components(manifest_path)
    record = _append(
        ledger,
        backup_id="checkpoint-bound",
        database_name="storage-backup.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )
    gate = _gate(
        sink=sink,
        verifier=verifier,
        checkpoint_provider=provider,
        checkpoint_verifier=checkpoint_verifier,
    )
    with pytest.raises(manifest.ManifestError, match="checkpoint"):
        _verify(
            gate,
            tmp_path=tmp_path,
            backup_id="checkpoint-bound",
            database_name="storage-backup.sqlite3",
            digest=DIGEST_A,
        )

    tampered_provider = manifest.LocalTestTrustedCheckpointProvider(
        provider_identity="tampered"
    )
    valid = _checkpoint(
        signer=checkpoint_signer,
        sink=sink,
        verifier=verifier,
        provider=tampered_provider,
        record=record,
    )
    tampered_provider.publish(replace(valid, signature="0" * 128))
    with pytest.raises(manifest.ManifestError, match="checkpoint 서명"):
        _verify(
            _gate(
                sink=sink,
                verifier=verifier,
                checkpoint_provider=tampered_provider,
                checkpoint_verifier=manifest.PinnedEd25519CheckpointVerifier(
                    checkpoint_signer.public_key_spki
                ),
            ),
            tmp_path=tmp_path,
            backup_id="checkpoint-bound",
            database_name="storage-backup.sqlite3",
            digest=DIGEST_A,
        )

    wrong_head_provider = manifest.LocalTestTrustedCheckpointProvider(
        provider_identity="wrong-head"
    )
    wrong_head_provider.publish(
        _checkpoint(
            signer=checkpoint_signer,
            sink=sink,
            verifier=verifier,
            provider=wrong_head_provider,
            record=record,
            head_sha256="f" * 64,
        )
    )
    with pytest.raises(manifest.ManifestError, match="정확히 같지"):
        _verify(
            _gate(
                sink=sink,
                verifier=verifier,
                checkpoint_provider=wrong_head_provider,
                checkpoint_verifier=manifest.PinnedEd25519CheckpointVerifier(
                    checkpoint_signer.public_key_spki
                ),
            ),
            tmp_path=tmp_path,
            backup_id="checkpoint-bound",
            database_name="storage-backup.sqlite3",
            digest=DIGEST_A,
        )


def test_old_checkpoint_replay와_잘린_manifest_prefix를_거부한다(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    (
        _signer,
        verifier,
        sink,
        ledger,
        checkpoint_signer,
        checkpoint_verifier,
        provider,
    ) = _ed_components(manifest_path)
    first = _append(
        ledger,
        backup_id="backup-001",
        database_name="storage-backup-001.sqlite3",
        digest=DIGEST_A,
        created_at=NOW,
    )
    second = _append(
        ledger,
        backup_id="backup-002",
        database_name="storage-backup-002.sqlite3",
        digest=DIGEST_B,
        created_at=NOW + timedelta(seconds=2),
    )
    first_checkpoint = _checkpoint(
        signer=checkpoint_signer,
        sink=sink,
        verifier=verifier,
        provider=provider,
        record=first,
    )
    second_checkpoint = _checkpoint(
        signer=checkpoint_signer,
        sink=sink,
        verifier=verifier,
        provider=provider,
        record=second,
    )
    provider.publish(first_checkpoint)
    provider.publish(second_checkpoint)
    gate = _gate(
        sink=sink,
        verifier=verifier,
        checkpoint_provider=provider,
        checkpoint_verifier=checkpoint_verifier,
    )
    assert _verify(
        gate,
        tmp_path=tmp_path,
        backup_id="backup-002",
        database_name="storage-backup-002.sqlite3",
        digest=DIGEST_B,
    ).sequence == 2

    provider._checkpoints = [first_checkpoint]  # noqa: SLF001 — provider replay 공격
    with pytest.raises(manifest.ManifestError, match="rollback|replay"):
        _verify(
            gate,
            tmp_path=tmp_path,
            backup_id="backup-001",
            database_name="storage-backup-001.sqlite3",
            digest=DIGEST_A,
        )

    manifest_path.write_bytes(manifest_path.read_bytes().splitlines(keepends=True)[0])
    provider._checkpoints = [second_checkpoint]  # noqa: SLF001 — sink prefix truncation 공격
    with pytest.raises(manifest.ManifestError, match="정확히 같지|checkpoint"):
        _verify(
            _gate(
                sink=sink,
                verifier=verifier,
                checkpoint_provider=provider,
                checkpoint_verifier=manifest.PinnedEd25519CheckpointVerifier(
                    checkpoint_signer.public_key_spki
                ),
            ),
            tmp_path=tmp_path,
            backup_id="backup-001",
            database_name="storage-backup-001.sqlite3",
            digest=DIGEST_A,
        )


def test_동시_append는_중복sequence나_fork를_게시하지_않는다(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    signer = manifest.LocalTestEd25519ManifestSigner.generate()
    sink = manifest.LocalTestAppendOnlyManifestSink(manifest_path, boundary=_boundary())
    workers = 8
    barrier = threading.Barrier(workers)

    def attempt(index: int) -> bool:
        ledger = manifest.ManifestLedger(
            sink=manifest.LocalTestAppendOnlyManifestSink(
                manifest_path,
                boundary=_boundary(),
            ),
            signer=signer,
            minimum_retention_days=35,
        )
        barrier.wait()
        try:
            _append(
                ledger,
                backup_id=f"parallel-{index:02d}",
                database_name=f"storage-backup-parallel-{index:02d}.sqlite3",
                digest=f"{index + 1:064x}",
                created_at=NOW + timedelta(seconds=index + 1),
            )
        except manifest.ManifestError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(attempt, range(workers)))

    records = sink.read_records()
    verifier = manifest.PinnedEd25519ManifestVerifier(signer.public_key_spki)
    manifest.validate_manifest_chain(records, sink=sink, verifier=verifier)
    assert 1 <= sum(outcomes) == len(records) <= workers
    assert [record.sequence for record in records] == list(range(1, len(records) + 1))


class _SinkEvidenceVerifier(manifest.OperationalSinkAttestationVerifier):
    def __init__(self, claims: manifest.OperationalSinkAttestationClaims) -> None:
        self._claims = claims

    def _verify_and_decode(self, _evidence: bytes):
        return self._claims


class _OperationalSink(manifest.OperationalManifestSink):
    def read_records(self):
        return ()

    def append(self, record, *, expected_head_sha256):
        raise AssertionError("constructor attack 시험에서는 append하지 않습니다")


class _CheckpointEvidenceVerifier(
    manifest.OperationalCheckpointProviderAttestationVerifier
):
    def __init__(self, claims: manifest.OperationalCheckpointProviderClaims) -> None:
        self._claims = claims

    def _verify_and_decode(self, _evidence: bytes):
        return self._claims


class _OperationalCheckpointProvider(manifest.OperationalTrustedCheckpointProvider):
    def latest_checkpoint(self, **_kwargs):
        raise AssertionError("constructor attack 시험에서는 읽지 않습니다")


def _operational_sink() -> _OperationalSink:
    claims = manifest.OperationalSinkAttestationClaims(
        storage_provider="s3-object-lock",
        sink_resource_arn="arn:aws:s3:::manifest-ledger",
        writer_principal_arn="arn:aws:iam::123456789012:role/manifest-writer",
        reader_principal_arn="arn:aws:iam::123456789012:role/manifest-reader",
        retention_days=35,
        object_lock_mode="COMPLIANCE",
        conditional_append_protocol="if-none-match-and-head-cas",
        issued_at=(NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
        expires_at=(NOW + timedelta(days=365)).isoformat().replace("+00:00", "Z"),
    )
    attestation = _SinkEvidenceVerifier(claims).verify(
        b"external-signed-sink-attestation",
        minimum_retention_days=35,
        now=NOW,
    )
    return _OperationalSink(attestation)


def _operational_provider(
    *,
    sink: _OperationalSink,
    checkpoint_key_identity: str,
    resource_arn: str,
    writer_principal: str,
    reader_principal: str,
) -> _OperationalCheckpointProvider:
    claims = manifest.OperationalCheckpointProviderClaims(
        provider_resource_arn=resource_arn,
        writer_principal_arn=writer_principal,
        reader_principal_arn=reader_principal,
        sink_identity=sink.sink_identity,
        checkpoint_key_identity=checkpoint_key_identity,
        monotonic_read_protocol="strong-latest-with-generation-cas",
        issued_at=(NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        expires_at=(NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
    )
    attestation = _CheckpointEvidenceVerifier(claims).verify(
        b"external-signed-checkpoint-provider-attestation",
        now=NOW,
    )
    return _OperationalCheckpointProvider(attestation)


def test_운영_gate는_HMAC_verifier를_거부한다() -> None:
    sink = _operational_sink()
    manifest_signer = manifest.LocalTestEd25519ManifestSigner.generate()
    pinned_manifest = manifest.PinnedEd25519ManifestVerifier(
        manifest_signer.public_key_spki
    )
    checkpoint_signer = manifest.LocalTestEd25519CheckpointSigner.generate()
    checkpoint_verifier = manifest.PinnedEd25519CheckpointVerifier(
        checkpoint_signer.public_key_spki
    )
    valid_provider = _operational_provider(
        sink=sink,
        checkpoint_key_identity=checkpoint_verifier.key_identity,
        resource_arn="arn:aws:s3:::checkpoint-ledger",
        writer_principal="arn:aws:iam::123456789012:role/checkpoint-writer",
        reader_principal="arn:aws:iam::123456789012:role/checkpoint-reader",
    )
    with pytest.raises(manifest.ManifestError, match="HMAC|고정 Ed25519"):
        manifest.OperationalIndependentManifestGate(
            sink=sink,
            manifest_verifier=manifest.LocalTestHMACManifestVerifier(HMAC_KEY),
            checkpoint_provider=valid_provider,
            checkpoint_verifier=checkpoint_verifier,
            minimum_retention_days=35,
            now=NOW,
        )


def test_운영_writer_gate는_로컬Ed_임의verify_동일경계를_거부한다() -> None:
    sink = _operational_sink()
    local_signer = manifest.LocalTestEd25519ManifestSigner.generate()
    with pytest.raises(manifest.ManifestError, match="외부 KMS/HSM signer"):
        manifest.ManifestLedger(
            sink=sink,
            signer=local_signer,
            minimum_retention_days=35,
        )

    class ForgedManifestVerifier(manifest.ManifestVerifier):
        @property
        def key_identity(self) -> str:
            return "spki-sha256:" + "1" * 64

        @property
        def algorithm(self) -> str:
            return manifest.ED25519_ALGORITHM

        def verify(self, _payload: bytes, _signature: str) -> bool:
            return True

    class ForgedCheckpointVerifier(manifest.CheckpointVerifier):
        @property
        def key_identity(self) -> str:
            return "spki-sha256:" + "2" * 64

        @property
        def algorithm(self) -> str:
            return manifest.ED25519_ALGORITHM

        def verify_latest(self, _checkpoint) -> None:
            return None

    checkpoint_signer = manifest.LocalTestEd25519CheckpointSigner.generate()
    pinned_checkpoint = manifest.PinnedEd25519CheckpointVerifier(
        checkpoint_signer.public_key_spki
    )
    provider = _operational_provider(
        sink=sink,
        checkpoint_key_identity=pinned_checkpoint.key_identity,
        resource_arn="arn:aws:s3:::checkpoint-ledger",
        writer_principal="arn:aws:iam::123456789012:role/checkpoint-writer",
        reader_principal="arn:aws:iam::123456789012:role/checkpoint-reader",
    )
    with pytest.raises(manifest.ManifestError, match="고정 Ed25519"):
        manifest.OperationalIndependentManifestGate(
            sink=sink,
            manifest_verifier=ForgedManifestVerifier(),
            checkpoint_provider=provider,
            checkpoint_verifier=pinned_checkpoint,
            minimum_retention_days=35,
            now=NOW,
        )

    pinned_manifest = manifest.PinnedEd25519ManifestVerifier(
        local_signer.public_key_spki
    )
    with pytest.raises(manifest.ManifestError, match="고정 비대칭 checkpoint"):
        manifest.OperationalIndependentManifestGate(
            sink=sink,
            manifest_verifier=pinned_manifest,
            checkpoint_provider=provider,
            checkpoint_verifier=ForgedCheckpointVerifier(),
            minimum_retention_days=35,
            now=NOW,
        )

    same_resource = _operational_provider(
        sink=sink,
        checkpoint_key_identity=pinned_checkpoint.key_identity,
        resource_arn=sink.attestation.claims.sink_resource_arn,
        writer_principal="arn:aws:iam::123456789012:role/checkpoint-writer",
        reader_principal="arn:aws:iam::123456789012:role/checkpoint-reader",
    )
    with pytest.raises(manifest.ManifestError, match="별도 자원"):
        manifest.OperationalIndependentManifestGate(
            sink=sink,
            manifest_verifier=pinned_manifest,
            checkpoint_provider=same_resource,
            checkpoint_verifier=pinned_checkpoint,
            minimum_retention_days=35,
            now=NOW,
        )

    same_principal = _operational_provider(
        sink=sink,
        checkpoint_key_identity=pinned_checkpoint.key_identity,
        resource_arn="arn:aws:s3:::checkpoint-ledger",
        writer_principal=sink.writer_principal,
        reader_principal="arn:aws:iam::123456789012:role/checkpoint-reader",
    )
    with pytest.raises(manifest.ManifestError, match="principal"):
        manifest.OperationalIndependentManifestGate(
            sink=sink,
            manifest_verifier=pinned_manifest,
            checkpoint_provider=same_principal,
            checkpoint_verifier=pinned_checkpoint,
            minimum_retention_days=35,
            now=NOW,
        )


def test_gate_생성자는_signer나_비밀을_받지않는다(tmp_path: Path) -> None:
    manifest_path = tmp_path / "independent" / "manifest.jsonl"
    manifest_path.parent.mkdir()
    signer, _verifier, sink, _ledger, _cs, checkpoint_verifier, provider = (
        _ed_components(manifest_path)
    )
    with pytest.raises(TypeError):
        manifest.LocalTestIndependentManifestGate(  # type: ignore[call-arg]
            sink=sink,
            signer=signer,
            checkpoint_provider=provider,
            checkpoint_verifier=checkpoint_verifier,
            minimum_retention_days=35,
        )


def test_키와_오류표현에_비밀원문이_노출되지않는다() -> None:
    secret = b"do-not-print-independent-secret-32-bytes"
    signer = manifest.LocalTestHMACManifestSigner(secret)
    verifier = manifest.LocalTestHMACManifestVerifier(secret)

    assert secret.decode("ascii") not in repr(signer)
    assert secret.decode("ascii") not in repr(verifier)
    assert secret.decode("ascii") not in signer.key_identity
    assert secret.decode("ascii") not in verifier.key_identity


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        (
            {
                "verifier_principal_arn": (
                    "arn:aws:iam::123456789012:role/manifest-writer"
                )
            },
            "principal",
        ),
        ({"verify_api": "kms:Sign"}, "API"),
        ({"key_arn": "arbitrary-key-name"}, "KMS key ARN"),
    ),
)
def test_KMS_metadata는_검증된_키와_분리IAM_API에서만_identity를_파생한다(
    changes: dict[str, str],
    message: str,
) -> None:
    metadata = manifest.KmsAsymmetricKeyMetadata(
        key_arn=(
            "arn:aws:kms:ap-northeast-2:123456789012:"
            "key/12345678-1234-1234-1234-123456789abc"
        ),
        key_version="version-1",
        signing_algorithm="ECDSA_SHA_256",
        public_key_spki_sha256="a" * 64,
        signer_principal_arn="arn:aws:iam::123456789012:role/manifest-writer",
        verifier_principal_arn="arn:aws:iam::123456789012:role/manifest-verifier",
        sign_api="kms:Sign",
        verify_api="kms:Verify",
    )
    forged = replace(metadata, **changes)

    with pytest.raises(manifest.ManifestError, match=message):
        forged.validate()

    metadata.validate()
    assert metadata.key_identity.startswith("kms-key-metadata-sha256:")
    assert len(metadata.key_identity.removeprefix("kms-key-metadata-sha256:")) == 64
