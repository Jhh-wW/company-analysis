"""S3 호환 외부 SQLite 백업의 업로드·재검증·보관 정책 시험."""

from __future__ import annotations

import hashlib
import io
import sqlite3
from dataclasses import replace
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


class FakeContractVerifier(backup_manifest.OperationalManifestContractVerifier):
    def _verify_and_decode(self, _evidence: bytes):
        return backup_manifest.OperationalManifestContractClaims(
            sink_identity="sink-attestation-sha256:" + "a" * 64,
            writer_principal_arn=(
                "arn:aws:iam::123456789012:role/manifest-writer"
            ),
            reader_principal_arn=(
                "arn:aws:iam::123456789012:role/manifest-reader"
            ),
            retention_days=35,
            object_lock_mode="COMPLIANCE",
            conditional_append_protocol="if-none-match-and-head-cas",
            manifest_key_identity="spki-sha256:" + "b" * 64,
            signature_algorithm=backup_manifest.ED25519_ALGORITHM,
            checkpoint_provider_identity=(
                "checkpoint-provider-attestation-sha256:" + "c" * 64
            ),
            checkpoint_key_identity="spki-sha256:" + "d" * 64,
            issued_at=(NOW - timedelta(minutes=1)).isoformat().replace(
                "+00:00", "Z"
            ),
            expires_at=(NOW + timedelta(days=1)).isoformat().replace(
                "+00:00", "Z"
            ),
        )


class FakeManifestAppender(backup_manifest.BackupManifestAppender):
    def __init__(self) -> None:
        self.requests: list[backup_manifest.BackupManifestRequest] = []
        self.fail = False
        self.tamper_object = False
        self._contract = FakeContractVerifier().verify(
            b"test-only-external-attestation",
            minimum_retention_days=35,
            now=NOW,
        )

    @property
    def contract(self) -> backup_manifest.VerifiedOperationalManifestContract:
        return self._contract

    def append_and_readback(
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
                + timedelta(days=self.contract.claims.retention_days)
            ).isoformat().replace("+00:00", "Z"),
            data_boundary_id=request.data_boundary_id,
            data_authority_id=request.data_authority_id,
            manifest_boundary_id=self.contract.claims.sink_identity,
            manifest_writer_principal=self.contract.claims.writer_principal_arn,
            previous_record_sha256="",
            manifest_key_identity=self.contract.claims.manifest_key_identity,
            signature_algorithm=self.contract.claims.signature_algorithm,
            signature="f" * 128,
        )
        return backup_manifest.ManifestAppendReceipt(
            record=record,
            readback_record_sha256=record.record_sha256(),
            readback_head_sequence=sequence,
        )


def _run_backup_mechanics(**kwargs) -> s3.ExternalBackupResult:
    """고정 차단된 public 경로와 분리해 업로드 mechanics만 회귀 시험한다."""

    return s3._run_backup_mechanics_test_only(  # noqa: SLF001
        _test_only_token=s3._TEST_ONLY_MECHANICS_TOKEN,  # noqa: SLF001
        **kwargs,
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
    result = _run_backup_mechanics(
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

    result = _run_backup_mechanics(
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
        _run_backup_mechanics(
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
            _run_backup_mechanics(
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
        _run_backup_mechanics(
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=appender,
        )

    result = _run_backup_mechanics(
        config=_config(),
        source_path=source,
        now=NOW,
        manifest_appender=appender,
    )

    assert result.object_key in second_client.objects
    assert result.checksum_key in second_client.objects


@pytest.mark.parametrize(
    "explicit_forged_appender",
    (False, True),
    ids=("default-no-trust-adapter", "explicit-forged-hex-signature"),
)
def test_public_run_backup은_신뢰adapter가_완결될때까지_pre_s3_fail_closed한다(
    tmp_path: Path,
    monkeypatch,
    explicit_forged_appender: bool,
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    source_before = source.read_bytes()
    client_created = False
    prune_called = False

    def forbidden_client(_config):
        nonlocal client_created
        client_created = True
        raise AssertionError("production trust blocker 뒤에 S3를 호출하면 안 됩니다")

    def forbidden_prune(*_args, **_kwargs):
        nonlocal prune_called
        prune_called = True
        raise AssertionError("production trust blocker 뒤에 prune하면 안 됩니다")

    monkeypatch.setattr(s3, "_new_s3_client", forbidden_client)
    monkeypatch.setattr(s3, "_prune_backups", forbidden_prune)
    appender = FakeManifestAppender() if explicit_forged_appender else None

    with pytest.raises(
        s3.BackupConfigurationError,
        match="고정 공개키 commit 검증.*checkpoint adapter",
    ):
        s3.run_backup(
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=appender,
        )

    assert client_created is False
    assert prune_called is False
    assert source.read_bytes() == source_before
    if appender is not None:
        # Fake appender는 임의 ``f * 128`` 서명을 만들지만 호출 자체가 차단된다.
        assert appender.requests == []


def test_test_only_mechanics는_내부capability_없이는_pre_s3_fail_closed한다(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "storage.db"
    _database(source)
    client_created = False

    def forbidden_client(_config):
        nonlocal client_created
        client_created = True
        raise AssertionError("시험 capability 검증 전에 S3를 호출하면 안 됩니다")

    monkeypatch.setattr(s3, "_new_s3_client", forbidden_client)

    with pytest.raises(s3.BackupConfigurationError, match="시험 전용"):
        s3._run_backup_mechanics_test_only(  # noqa: SLF001
            _test_only_token=object(),
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=FakeManifestAppender(),
        )

    assert client_created is False


def test_test_only_mechanics는_production_caller에서_참조하지_않는다() -> None:
    app_src = Path(s3.__file__).resolve().parents[2]
    forbidden_name = "_run_backup_mechanics_test_only"
    references: list[str] = []
    for path in app_src.rglob("*.py"):
        if path.resolve() == Path(s3.__file__).resolve() or "tests" in path.parts:
            continue
        if forbidden_name in path.read_text(encoding="utf-8"):
            references.append(str(path.relative_to(app_src)))

    assert references == []


def test_test_only_mechanics_manifest_appender_누락은_S3전에_fail_closed한다(
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
        _run_backup_mechanics(config=_config(), source_path=source, now=NOW)

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
        _run_backup_mechanics(
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
        _run_backup_mechanics(
            config=_config(),
            source_path=source,
            now=NOW,
            manifest_appender=appender,
        )

    assert len(client.objects) == 2


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("sink_identity", "sink-attestation-sha256:not-hex"),
        ("manifest_key_identity", "spki-sha256:" + "A" * 64),
        (
            "checkpoint_provider_identity",
            "checkpoint-provider-attestation-sha256:" + "g" * 64,
        ),
        ("checkpoint_key_identity", "spki-sha256:" + "0" * 63),
    ),
)
def test_manifest_attestation_파생identity는_정확한_lowercase_sha256만_허용한다(
    field_name: str,
    forged_value: str,
) -> None:
    claims = FakeContractVerifier()._verify_and_decode(b"test")  # noqa: SLF001

    with pytest.raises(backup_manifest.ManifestConfigurationError, match="형식"):
        replace(claims, **{field_name: forged_value}).validate(
            minimum_retention_days=35,
            now=NOW,
        )


def test_manifest_contract는_외부verifier_token없이_직접_위조할수없다() -> None:
    claims = FakeContractVerifier()._verify_and_decode(b"test")  # noqa: SLF001
    forged = backup_manifest.VerifiedOperationalManifestContract(
        claims=claims,
        evidence_sha256="f" * 64,
        _token=object(),
    )

    with pytest.raises(backup_manifest.ManifestConfigurationError, match="외부 verifier"):
        forged.validate(minimum_retention_days=35, now=NOW)
    assert hasattr(backup_manifest.BackupManifestAppender, "append_and_readback")
    assert not hasattr(backup_manifest.BackupManifestAppender, "append_and_verify")
