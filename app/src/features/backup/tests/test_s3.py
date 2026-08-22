"""S3 호환 외부 SQLite 백업의 업로드·재검증·보관 정책 시험."""

from __future__ import annotations

import hashlib
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.features.backup import s3
from src.features.backup import manifest as backup_manifest


NOW = datetime(2026, 8, 21, 18, 15, tzinfo=timezone.utc)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.bad_head = False
        self.corrupt_download = False

    def put_object(self, *, Bucket, Key, Body, ContentLength, **kwargs):
        content = Body.read()
        assert len(content) == ContentLength
        self.objects[Key] = {
            "Body": content,
            "Metadata": kwargs["Metadata"],
            "ContentLength": ContentLength,
            "LastModified": NOW,
            "ServerSideEncryption": kwargs["ServerSideEncryption"],
        }

    def head_object(self, *, Bucket, Key):
        item = self.objects[Key]
        return {
            "Metadata": item["Metadata"],
            "ContentLength": item["ContentLength"] + (1 if self.bad_head else 0),
        }

    def get_object(self, *, Bucket, Key):
        content = self.objects[Key]["Body"]
        if self.corrupt_download and Key.endswith(".sqlite3"):
            content = b"damaged"
        return {"Body": io.BytesIO(content)}

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys, **kwargs):
        return {
            "Contents": [
                {"Key": key, "LastModified": item["LastModified"]}
                for key, item in self.objects.items()
                if key.startswith(Prefix)
            ],
            "IsTruncated": False,
        }

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)


class FakeManifestAppender(backup_manifest.BackupManifestAppender):
    def __init__(self) -> None:
        self.requests: list[backup_manifest.BackupManifestRequest] = []
        self.fail = False
        self.tamper_object = False
        self._boundary = backup_manifest.ManifestBoundary(
            boundary_id="manifest-control-plane",
            authority_id="manifest-security-owner",
            retention_days=35,
            append_only=True,
            signed=True,
            conditional_append=True,
            production_ready=True,
        )

    @property
    def boundary(self) -> backup_manifest.ManifestBoundary:
        return self._boundary

    def append_and_verify(
        self, request: backup_manifest.BackupManifestRequest
    ) -> backup_manifest.ManifestAppendReceipt:
        self.requests.append(request)
        if self.fail:
            raise backup_manifest.ManifestAppendError("시험용 append 실패")
        sequence = len(self.requests)
        record = backup_manifest.SignedBackupManifestRecord(
            version=backup_manifest.MANIFEST_VERSION,
            sequence=sequence,
            scope=request.scope,
            backup_id=request.backup_id,
            storage_provider=request.storage_provider,
            storage_bucket=request.storage_bucket,
            object_key=("wrong/object.sqlite3" if self.tamper_object else request.object_key),
            checksum_key=request.checksum_key,
            database_name=request.database_name,
            database_sha256=request.database_sha256,
            database_size_bytes=request.database_size_bytes,
            checksum_sha256=request.checksum_sha256,
            created_at=request.created_at.astimezone(timezone.utc).replace(
                microsecond=0
            ).isoformat().replace("+00:00", "Z"),
            retention_until=(
                request.created_at.astimezone(timezone.utc).replace(microsecond=0)
                + timedelta(days=self.boundary.retention_days)
            ).isoformat().replace("+00:00", "Z"),
            data_boundary_id=request.data_boundary_id,
            data_authority_id=request.data_authority_id,
            manifest_boundary_id=self.boundary.boundary_id,
            manifest_authority_id=self.boundary.authority_id,
            previous_record_sha256="",
            key_id="fake-key-v1",
            signature="f" * 64,
        )
        return backup_manifest.ManifestAppendReceipt(
            record=record,
            record_sha256=record.record_sha256(),
            verified_head_sequence=sequence,
            readback_verified=True,
        )


def _config(**changes) -> s3.S3BackupConfig:
    values = {
        "bucket": "private-backups",
        "prefix": "company-analysis/",
        "region": "ap-northeast-2",
        "endpoint_url": "https://objects.example.com",
        "server_side_encryption": "AES256",
        "kms_key_id": "",
        "addressing_style": "auto",
        "retention_days": 35,
        "max_backups": 35,
        "access_key_id": "access",
        "secret_access_key": "secret",
        "session_token": "",
        "data_boundary_id": "backup-data-bucket",
        "data_authority_id": "backup-data-writer",
        "manifest_minimum_retention_days": 35,
    }
    values.update(changes)
    return s3.S3BackupConfig(**values)


def _database(path: Path) -> str:
    raw_key = "ab" * 16
    key_hash = hashlib.sha256(raw_key.encode("ascii")).hexdigest()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE share_links ("
            "key_hash TEXT PRIMARY KEY, company TEXT NOT NULL, job TEXT NOT NULL, "
            "report_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL, opened_count INTEGER NOT NULL DEFAULT 0, "
            "first_opened_at TEXT NOT NULL DEFAULT '', "
            "last_opened_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO share_links (key_hash, company, job, created_at) "
            "VALUES (?, '회사', '직무', '2026-08-21T00:00:00+09:00')",
            (key_hash,),
        )
    return raw_key


def test_업로드한_두_파일을_다시_받아_검증하고_원문열쇠를_남기지_않는다(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "storage.db"
    raw_key = _database(source)
    client = FakeS3()
    monkeypatch.setattr(s3, "_new_s3_client", lambda _config: client)

    appender = FakeManifestAppender()
    result = s3.run_backup(
        config=_config(),
        source_path=source,
        now=NOW,
        manifest_appender=appender,
    )

    assert set(client.objects) == {result.object_key, result.checksum_key}
    assert result.sha256 == hashlib.sha256(
        client.objects[result.object_key]["Body"]
    ).hexdigest()
    assert raw_key.encode("ascii") not in client.objects[result.object_key]["Body"]
    assert all(
        item["ServerSideEncryption"] == "AES256"
        for item in client.objects.values()
    )
    assert result.manifest_sequence == 1
    assert result.manifest_backup_id == appender.requests[0].backup_id
    assert appender.requests[0].object_key == result.object_key
    assert appender.requests[0].checksum_key == result.checksum_key


def test_보관기간이나_개수상한을_넘은_관리대상_한쌍만_지운다(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    client = FakeS3()
    old_base = "company-analysis/storage-backup-20260701T000000000000Z.sqlite3"
    for key in (old_base, old_base + ".sha256"):
        client.objects[key] = {
            "Body": b"old",
            "Metadata": {},
            "ContentLength": 3,
            "LastModified": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "ServerSideEncryption": "AES256",
        }
    client.objects["company-analysis/keep-me.txt"] = {
        "Body": b"unmanaged",
        "Metadata": {},
        "ContentLength": 9,
        "LastModified": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "ServerSideEncryption": "AES256",
    }
    monkeypatch.setattr(s3, "_new_s3_client", lambda _config: client)

    result = s3.run_backup(
        config=_config(max_backups=1),
        source_path=source,
        now=NOW,
        manifest_appender=FakeManifestAppender(),
    )

    assert result.deleted_objects == 2
    assert old_base not in client.objects
    assert old_base + ".sha256" not in client.objects
    assert "company-analysis/keep-me.txt" in client.objects


@pytest.mark.parametrize("failure", ["head", "download"])
def test_검증이_실패하면_이번에_올린_불완전객체를_지운다(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    client = FakeS3()
    client.bad_head = failure == "head"
    client.corrupt_download = failure == "download"
    monkeypatch.setattr(s3, "_new_s3_client", lambda _config: client)

    with pytest.raises(s3.ExternalBackupError):
        s3.run_backup(
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=FakeManifestAppender(),
        )

    assert not [key for key in client.objects if key.endswith((".sqlite3", ".sha256"))]


def test_환경설정은_HTTP_endpoint와_짧은_호출비밀을_거부한다(monkeypatch) -> None:
    monkeypatch.setenv(s3.ENV_BUCKET, "bucket")
    monkeypatch.setenv(s3.ENV_ACCESS_KEY_ID, "access")
    monkeypatch.setenv(s3.ENV_SECRET_ACCESS_KEY, "secret")
    monkeypatch.setenv(s3.ENV_ENDPOINT_URL, "http://objects.example.com")

    with pytest.raises(s3.BackupConfigurationError, match="HTTPS"):
        s3.config_from_env()

    monkeypatch.setenv(s3.ENV_TRIGGER_SECRET, "short")
    with pytest.raises(s3.BackupConfigurationError, match="32바이트"):
        s3.trigger_secret_from_env()


def test_같은_프로세스의_백업_중복실행을_즉시_거부한다(tmp_path: Path) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    assert s3._RUN_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(s3.BackupAlreadyRunning, match="이미 실행 중"):
            s3.run_backup(
                config=_config(),
                source_path=source,
                now=NOW,
                manifest_appender=FakeManifestAppender(),
            )
    finally:
        s3._RUN_LOCK.release()


def test_실패한_백업은_잠금을_풀어_다음_실행을_허용한다(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    first_client = FakeS3()
    first_client.bad_head = True
    second_client = FakeS3()
    clients = iter((first_client, second_client))
    monkeypatch.setattr(s3, "_new_s3_client", lambda _config: next(clients))

    appender = FakeManifestAppender()
    with pytest.raises(s3.ExternalBackupError):
        s3.run_backup(
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=appender,
        )

    result = s3.run_backup(
        config=_config(),
        source_path=source,
        now=NOW,
        manifest_appender=appender,
    )

    assert result.object_key in second_client.objects
    assert result.checksum_key in second_client.objects


def test_manifest_appender_누락은_S3_client_생성전에_fail_closed한다(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    client_created = False

    def forbidden_client(_config):
        nonlocal client_created
        client_created = True
        raise AssertionError("manifest 설정 전에 S3를 호출하면 안 됩니다")

    monkeypatch.setattr(s3, "_new_s3_client", forbidden_client)

    with pytest.raises(s3.BackupConfigurationError, match="manifest"):
        s3.run_backup(config=_config(), source_path=source, now=NOW)

    assert client_created is False


def test_manifest_append_실패는_성공을_반환하지_않고_원격쌍을_보존한다(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    client = FakeS3()
    appender = FakeManifestAppender()
    appender.fail = True
    monkeypatch.setattr(s3, "_new_s3_client", lambda _config: client)

    with pytest.raises(s3.ExternalBackupError, match="manifest"):
        s3.run_backup(
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=appender,
        )

    # append 결과가 미확정일 수 있어 manifest가 가리킬 객체를 지우지 않는다.
    assert len(client.objects) == 2
    assert any(key.endswith(".sqlite3") for key in client.objects)
    assert any(key.endswith(".sqlite3.sha256") for key in client.objects)


def test_manifest_receipt가_다른_object를_결속하면_성공을_거부한다(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    client = FakeS3()
    appender = FakeManifestAppender()
    appender.tamper_object = True
    monkeypatch.setattr(s3, "_new_s3_client", lambda _config: client)

    with pytest.raises(s3.ExternalBackupError, match="manifest"):
        s3.run_backup(
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=appender,
        )

    assert len(client.objects) == 2
