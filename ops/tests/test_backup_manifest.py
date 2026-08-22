"""앱 백업 writer와 ops verifier의 manifest wire 계약 호환 시험."""

from __future__ import annotations

from dataclasses import asdict

from app.src.features.backup import manifest as app_manifest
from ops import backup_manifest as ops_manifest


def test_app_writer_record_is_exactly_parseable_by_ops_gate() -> None:
    app_record = app_manifest.SignedBackupManifestRecord(
        version=app_manifest.MANIFEST_VERSION,
        sequence=1,
        scope="storage-db",
        backup_id="a" * 64,
        storage_provider="s3",
        storage_bucket="private-backups",
        object_key="company-analysis/storage-backup.sqlite3",
        checksum_key="company-analysis/storage-backup.sqlite3.sha256",
        database_name="storage-backup.sqlite3",
        database_sha256="b" * 64,
        database_size_bytes=4096,
        checksum_sha256="c" * 64,
        created_at="2026-08-23T01:00:00Z",
        retention_until="2026-09-27T01:00:00Z",
        data_boundary_id="backup-data-boundary",
        data_authority_id="backup-data-writer",
        manifest_boundary_id="sink-attestation-sha256:" + "d" * 64,
        manifest_writer_principal="arn:aws:iam::123456789012:role/manifest-writer",
        previous_record_sha256="",
        manifest_key_identity="spki-sha256:" + "e" * 64,
        signature_algorithm="ed25519",
        signature="f" * 128,
    )

    parsed = ops_manifest.BackupManifestRecord.from_mapping(asdict(app_record))

    assert parsed.serialized_bytes() == app_record.serialized_bytes()
    assert parsed.record_sha256() == app_record.record_sha256()
    assert set(parsed.__dataclass_fields__) == set(app_record.__dataclass_fields__)
