"""S3 호환 외부 SQLite 백업의 업로드·재검증·보관 정책 시험."""

from __future__ import annotations

import hashlib
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.features.backup import s3


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

    result = s3.run_backup(config=_config(), source_path=source, now=NOW)

    assert set(client.objects) == {result.object_key, result.checksum_key}
    assert result.sha256 == hashlib.sha256(
        client.objects[result.object_key]["Body"]
    ).hexdigest()
    assert raw_key.encode("ascii") not in client.objects[result.object_key]["Body"]
    assert all(
        item["ServerSideEncryption"] == "AES256"
        for item in client.objects.values()
    )


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
        config=_config(max_backups=1), source_path=source, now=NOW
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
        s3.run_backup(config=_config(), source_path=source, now=NOW)

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
