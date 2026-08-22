"""실행 중 SQLite를 S3 호환 저장소에 올리고 다시 검증한다."""

from __future__ import annotations

import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final
from urllib.parse import urlsplit

from src.features.backup import manifest as backup_manifest

ENV_TRIGGER_SECRET: Final[str] = "BACKUP_TRIGGER_SECRET"
ENV_BUCKET: Final[str] = "BACKUP_S3_BUCKET"
ENV_PREFIX: Final[str] = "BACKUP_S3_PREFIX"
ENV_REGION: Final[str] = "BACKUP_S3_REGION"
ENV_ENDPOINT_URL: Final[str] = "BACKUP_S3_ENDPOINT_URL"
ENV_SSE: Final[str] = "BACKUP_S3_SERVER_SIDE_ENCRYPTION"
ENV_KMS_KEY_ID: Final[str] = "BACKUP_S3_KMS_KEY_ID"
ENV_ADDRESSING_STYLE: Final[str] = "BACKUP_S3_ADDRESSING_STYLE"
ENV_RETENTION_DAYS: Final[str] = "BACKUP_RETENTION_DAYS"
ENV_MAX_OBJECTS: Final[str] = "BACKUP_MAX_OBJECTS"
ENV_ACCESS_KEY_ID: Final[str] = "AWS_ACCESS_KEY_ID"
ENV_SECRET_ACCESS_KEY: Final[str] = "AWS_SECRET_ACCESS_KEY"
ENV_SESSION_TOKEN: Final[str] = "AWS_SESSION_TOKEN"
ENV_DATA_BOUNDARY_ID: Final[str] = "BACKUP_DATA_BOUNDARY_ID"
ENV_DATA_AUTHORITY_ID: Final[str] = "BACKUP_DATA_AUTHORITY_ID"
ENV_MANIFEST_MIN_RETENTION_DAYS: Final[str] = "BACKUP_MANIFEST_MIN_RETENTION_DAYS"

DEFAULT_PREFIX: Final[str] = "company-analysis/"
DEFAULT_REGION: Final[str] = "us-east-1"
DEFAULT_RETENTION_DAYS: Final[int] = 35
DEFAULT_MAX_BACKUPS: Final[int] = 35
MIN_SECRET_BYTES: Final[int] = 32
MAX_BACKUP_POLICY: Final[int] = 3650
_BACKUP_NAME_RE = re.compile(
    r"^storage-backup-\d{8}T\d{12}Z\.sqlite3(?:\.sha256)?$"
)
_RUN_LOCK = threading.Lock()
_TEST_ONLY_MECHANICS_TOKEN = object()
PRODUCTION_TRUST_BLOCKER_MESSAGE: Final[str] = (
    "운영 manifest의 고정 공개키 commit 검증과 독립 checkpoint adapter가 "
    "구현되지 않아 외부 백업을 차단합니다"
)


def _backup_tool():
    # 앱 소스만 복사하는 로컬 실행기는 tools/ 없이도 시작할 수 있어야 한다.
    # 실제 백업 순간에는 Docker 이미지에 함께 넣은 안전한 CLI 구현을 재사용한다.
    from tools import backup_sqlite  # noqa: PLC0415

    return backup_sqlite


class BackupConfigurationError(RuntimeError):
    """외부 백업 설정이 없거나 안전하지 않다."""


class ExternalBackupError(RuntimeError):
    """백업 생성·업로드·검증·보관 정리를 완결하지 못했다."""


class BackupAlreadyRunning(ExternalBackupError):
    """같은 웹 인스턴스에서 백업 한 건이 이미 실행 중이다."""


@dataclass(frozen=True)
class S3BackupConfig:
    bucket: str
    prefix: str
    region: str
    endpoint_url: str
    server_side_encryption: str
    kms_key_id: str
    addressing_style: str
    retention_days: int
    max_backups: int
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str = field(repr=False)
    data_boundary_id: str = ""
    data_authority_id: str = ""
    manifest_minimum_retention_days: int = DEFAULT_RETENTION_DAYS


@dataclass(frozen=True)
class ExternalBackupResult:
    object_key: str
    checksum_key: str
    sha256: str
    deleted_objects: int
    manifest_backup_id: str = ""
    manifest_sequence: int = 0
    manifest_record_sha256: str = ""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BackupConfigurationError(f"{name} 환경변수가 필요합니다")
    return value


def _bounded_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise BackupConfigurationError(f"{name}은 정수여야 합니다") from exc
    if not 1 <= value <= MAX_BACKUP_POLICY:
        raise BackupConfigurationError(
            f"{name}은 1 이상 {MAX_BACKUP_POLICY} 이하여야 합니다"
        )
    return value


def _normalized_prefix(value: str) -> str:
    prefix = str(value or DEFAULT_PREFIX).strip().strip("/")
    if not prefix or len(prefix) > 512:
        raise BackupConfigurationError("BACKUP_S3_PREFIX 길이가 올바르지 않습니다")
    if ".." in prefix or "\\" in prefix or any(ord(char) < 32 for char in prefix):
        raise BackupConfigurationError("BACKUP_S3_PREFIX에 안전하지 않은 문자가 있습니다")
    return prefix + "/"


def _validated_endpoint(value: str) -> str:
    endpoint = str(value or "").strip().rstrip("/")
    if not endpoint:
        return ""
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise BackupConfigurationError("BACKUP_S3_ENDPOINT_URL이 올바르지 않습니다") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BackupConfigurationError(
            "BACKUP_S3_ENDPOINT_URL은 인증정보·query 없는 HTTPS 주소여야 합니다"
        )
    return endpoint


def config_from_env() -> S3BackupConfig:
    """외부 저장소 설정을 읽되 비밀값은 오류 메시지에 싣지 않는다."""

    bucket = _required(ENV_BUCKET)
    if len(bucket) > 255 or any(ord(char) < 33 for char in bucket):
        raise BackupConfigurationError("BACKUP_S3_BUCKET 형식이 올바르지 않습니다")
    sse = os.environ.get(ENV_SSE, "AES256").strip() or "AES256"
    if sse not in {"AES256", "aws:kms"}:
        raise BackupConfigurationError(
            "BACKUP_S3_SERVER_SIDE_ENCRYPTION은 AES256 또는 aws:kms여야 합니다"
        )
    kms_key_id = os.environ.get(ENV_KMS_KEY_ID, "").strip()
    if sse == "aws:kms" and not kms_key_id:
        raise BackupConfigurationError("aws:kms에는 BACKUP_S3_KMS_KEY_ID가 필요합니다")
    addressing_style = os.environ.get(ENV_ADDRESSING_STYLE, "auto").strip() or "auto"
    if addressing_style not in {"auto", "path", "virtual"}:
        raise BackupConfigurationError(
            "BACKUP_S3_ADDRESSING_STYLE은 auto, path, virtual 중 하나여야 합니다"
        )
    return S3BackupConfig(
        bucket=bucket,
        prefix=_normalized_prefix(os.environ.get(ENV_PREFIX, DEFAULT_PREFIX)),
        region=os.environ.get(ENV_REGION, DEFAULT_REGION).strip() or DEFAULT_REGION,
        endpoint_url=_validated_endpoint(os.environ.get(ENV_ENDPOINT_URL, "")),
        server_side_encryption=sse,
        kms_key_id=kms_key_id,
        addressing_style=addressing_style,
        retention_days=_bounded_int(ENV_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
        max_backups=_bounded_int(ENV_MAX_OBJECTS, DEFAULT_MAX_BACKUPS),
        access_key_id=_required(ENV_ACCESS_KEY_ID),
        secret_access_key=_required(ENV_SECRET_ACCESS_KEY),
        session_token=os.environ.get(ENV_SESSION_TOKEN, "").strip(),
        data_boundary_id=_required(ENV_DATA_BOUNDARY_ID),
        data_authority_id=_required(ENV_DATA_AUTHORITY_ID),
        manifest_minimum_retention_days=_bounded_int(
            ENV_MANIFEST_MIN_RETENTION_DAYS, DEFAULT_RETENTION_DAYS
        ),
    )


def trigger_secret_from_env() -> str:
    secret = _required(ENV_TRIGGER_SECRET)
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise BackupConfigurationError(
            f"{ENV_TRIGGER_SECRET}은 {MIN_SECRET_BYTES}바이트 이상이어야 합니다"
        )
    return secret


def _new_s3_client(config: S3BackupConfig):
    # 앱 import만으로 AWS metadata endpoint를 조회하지 않게 실제 백업 순간에 가져온다.
    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    kwargs: dict[str, Any] = {
        "region_name": config.region,
        "aws_access_key_id": config.access_key_id,
        "aws_secret_access_key": config.secret_access_key,
        "config": Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": config.addressing_style},
        ),
    }
    if config.endpoint_url:
        kwargs["endpoint_url"] = config.endpoint_url
    if config.session_token:
        kwargs["aws_session_token"] = config.session_token
    return boto3.client("s3", **kwargs)


def _put_args(config: S3BackupConfig, *, sha256: str, content_type: str) -> dict:
    args: dict[str, Any] = {
        "ServerSideEncryption": config.server_side_encryption,
        "ContentType": content_type,
        "Metadata": {"sha256": sha256},
    }
    if config.kms_key_id:
        args["SSEKMSKeyId"] = config.kms_key_id
    return args


def _upload_file(client, config: S3BackupConfig, path: Path, key: str, sha256: str) -> None:
    content_type = (
        "application/x-sqlite3"
        if path.suffix == ".sqlite3"
        else "text/plain; charset=us-ascii"
    )
    uploaded = False
    try:
        with path.open("rb") as stream:
            client.put_object(
                Bucket=config.bucket,
                Key=key,
                Body=stream,
                ContentLength=path.stat().st_size,
                **_put_args(config, sha256=sha256, content_type=content_type),
            )
        uploaded = True
        head = client.head_object(Bucket=config.bucket, Key=key)
        metadata = {
            str(k).lower(): str(v) for k, v in head.get("Metadata", {}).items()
        }
        if (
            metadata.get("sha256") != sha256
            or int(head.get("ContentLength", -1)) != path.stat().st_size
        ):
            raise ExternalBackupError(
                "업로드한 백업 객체의 크기 또는 지문이 맞지 않습니다"
            )
    except BaseException:
        # put은 성공했지만 HEAD 검증이 실패한 객체도 완성본처럼 남기지 않는다.
        if uploaded:
            try:
                client.delete_object(Bucket=config.bucket, Key=key)
            except Exception:  # noqa: BLE001 — 원래 실패를 보존한다
                pass
        raise


def _download_object(client, config: S3BackupConfig, key: str, target: Path) -> None:
    response = client.get_object(Bucket=config.bucket, Key=key)
    body = response["Body"]
    try:
        with target.open("xb") as stream:
            shutil.copyfileobj(body, stream, length=_backup_tool().HASH_CHUNK_BYTES)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()


def _list_objects(client, config: S3BackupConfig) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    token = ""
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": config.bucket,
            "Prefix": config.prefix,
            "MaxKeys": 1000,
        }
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", ()))
        if not page.get("IsTruncated"):
            break
        token = str(page.get("NextContinuationToken", ""))
        if not token:
            raise ExternalBackupError("외부 백업 목록의 다음 페이지를 확인할 수 없습니다")
    return objects


def _managed_base_key(config: S3BackupConfig, key: str) -> str:
    if not key.startswith(config.prefix):
        return ""
    name = key[len(config.prefix) :]
    if not _BACKUP_NAME_RE.fullmatch(name):
        return ""
    return key[: -len(_backup_tool().CHECKSUM_SUFFIX)] if name.endswith(
        _backup_tool().CHECKSUM_SUFFIX
    ) else key


def _prune_backups(
    client,
    config: S3BackupConfig,
    *,
    current_key: str,
    now: datetime,
) -> int:
    grouped: dict[str, dict[str, Any]] = {}
    for item in _list_objects(client, config):
        key = str(item.get("Key", ""))
        base = _managed_base_key(config, key)
        if not base:
            continue
        modified = item.get("LastModified")
        if not isinstance(modified, datetime):
            raise ExternalBackupError("외부 백업의 수정 시각을 확인할 수 없습니다")
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        entry = grouped.setdefault(base, {"modified": modified, "keys": []})
        entry["modified"] = max(entry["modified"], modified)
        entry["keys"].append(key)

    ordered = sorted(
        grouped.items(), key=lambda pair: (pair[1]["modified"], pair[0]), reverse=True
    )
    cutoff = now.astimezone(timezone.utc) - timedelta(days=config.retention_days)
    delete_keys: list[str] = []
    for index, (base, entry) in enumerate(ordered):
        if base == current_key:
            continue
        if index >= config.max_backups or entry["modified"] < cutoff:
            delete_keys.extend(entry["keys"])
    for key in delete_keys:
        client.delete_object(Bucket=config.bucket, Key=key)
    return len(delete_keys)


def _remote_verify(
    client,
    config: S3BackupConfig,
    *,
    object_key: str,
    checksum_key: str,
    expected_sha256: str,
    directory: Path,
) -> None:
    downloaded = directory / Path(object_key).name
    backup_tool = _backup_tool()
    downloaded_checksum = backup_tool.checksum_path_for(downloaded)
    _download_object(client, config, object_key, downloaded)
    _download_object(client, config, checksum_key, downloaded_checksum)
    actual = backup_tool.verify_backup(downloaded, downloaded_checksum)
    if actual != expected_sha256:
        raise ExternalBackupError("다시 내려받은 백업의 지문이 원본과 다릅니다")


def run_backup(
    *,
    config: S3BackupConfig | None = None,
    source_path: Path | None = None,
    now: datetime | None = None,
    manifest_appender: backup_manifest.BackupManifestAppender | None = None,
) -> ExternalBackupResult:
    """검증된 production trust bundle이 구현될 때까지 모든 외부 백업을 차단한다.

    명시적 appender 주입도 신뢰 승격 근거가 아니다. 이 함수는 S3 client 생성,
    로컬 snapshot, upload, prune보다 먼저 항상 실패한다.
    """

    _ = (config, source_path, now, manifest_appender)
    raise BackupConfigurationError(PRODUCTION_TRUST_BLOCKER_MESSAGE)


def _run_backup_mechanics_test_only(
    *,
    _test_only_token: object,
    config: S3BackupConfig | None = None,
    source_path: Path | None = None,
    now: datetime | None = None,
    manifest_appender: backup_manifest.BackupManifestAppender | None = None,
) -> ExternalBackupResult:
    """원격 사본과 독립 manifest append 검증을 모두 마쳐야 성공한다."""

    if _test_only_token is not _TEST_ONLY_MECHANICS_TOKEN:
        raise BackupConfigurationError("시험 전용 backup mechanics capability가 필요합니다")
    if not _RUN_LOCK.acquire(blocking=False):
        raise BackupAlreadyRunning("외부 백업 한 건이 이미 실행 중입니다")
    try:
        backup_tool = _backup_tool()
        resolved_config = config or config_from_env()
        effective_now = now or datetime.now(timezone.utc)
        if (
            not resolved_config.data_boundary_id
            or not resolved_config.data_authority_id
            or resolved_config.manifest_minimum_retention_days
            < resolved_config.retention_days
        ):
            raise BackupConfigurationError(
                "백업 데이터 경계와 manifest 보존 계약이 필요합니다"
            )
        try:
            resolved_appender = backup_manifest.require_manifest_appender(
                manifest_appender
            )
            resolved_appender.contract.validate(
                minimum_retention_days=resolved_config.manifest_minimum_retention_days,
                now=effective_now,
            )
            manifest_claims = resolved_appender.contract.claims
            if (
                manifest_claims.sink_identity == resolved_config.data_boundary_id
                or manifest_claims.writer_principal_arn
                == resolved_config.data_authority_id
            ):
                raise backup_manifest.ManifestConfigurationError(
                    "manifest 경계가 백업 데이터 경계와 독립적이지 않습니다"
                )
        except backup_manifest.ManifestConfigurationError as exc:
            raise BackupConfigurationError(
                "독립 manifest appender 운영 설정이 올바르지 않습니다"
            ) from exc
        source = source_path or backup_tool.default_db_path()
        client = _new_s3_client(resolved_config)
        with TemporaryDirectory(prefix="company-analysis-backup-") as temp:
            directory = Path(temp)
            local = backup_tool.create_backup(source, directory)
            object_key = resolved_config.prefix + local.backup_path.name
            checksum_key = resolved_config.prefix + local.checksum_path.name
            checksum_sha256 = backup_tool.sha256_file(local.checksum_path)
            uploaded: list[str] = []
            try:
                _upload_file(
                    client,
                    resolved_config,
                    local.backup_path,
                    object_key,
                    local.sha256,
                )
                uploaded.append(object_key)
                _upload_file(
                    client,
                    resolved_config,
                    local.checksum_path,
                    checksum_key,
                    checksum_sha256,
                )
                uploaded.append(checksum_key)
                verify_dir = directory / "remote-verify"
                verify_dir.mkdir(mode=0o700)
                _remote_verify(
                    client,
                    resolved_config,
                    object_key=object_key,
                    checksum_key=checksum_key,
                    expected_sha256=local.sha256,
                    directory=verify_dir,
                )
            except BaseException:
                for key in reversed(uploaded):
                    try:
                        client.delete_object(Bucket=resolved_config.bucket, Key=key)
                    except Exception:  # noqa: BLE001 — 원래 실패를 보존한다
                        pass
                raise
            manifest_request = backup_manifest.BackupManifestRequest(
                scope="storage-db",
                storage_provider="s3",
                storage_bucket=resolved_config.bucket,
                object_key=object_key,
                checksum_key=checksum_key,
                database_name=local.backup_path.name,
                database_sha256=local.sha256,
                database_size_bytes=local.backup_path.stat().st_size,
                checksum_sha256=checksum_sha256,
                created_at=effective_now,
                data_boundary_id=resolved_config.data_boundary_id,
                data_authority_id=resolved_config.data_authority_id,
                minimum_retention_days=(
                    resolved_config.manifest_minimum_retention_days
                ),
            )
            try:
                manifest_receipt = resolved_appender.append_and_readback(
                    manifest_request
                )
                manifest_record = backup_manifest.validate_append_receipt(
                    request=manifest_request,
                    appender=resolved_appender,
                    receipt=manifest_receipt,
                    now=effective_now,
                )
            except (
                backup_manifest.ManifestAppendError,
                backup_manifest.ManifestConfigurationError,
            ) as exc:
                # append 성공·read-back 실패를 구분할 수 없으므로 원격 객체는 보존한다.
                # success는 반환하지 않고 운영자가 독립 원장과 함께 정리한다.
                raise ExternalBackupError(
                    "독립 manifest append 또는 재검증을 완료하지 못했습니다"
                ) from exc
            deleted = _prune_backups(
                client,
                resolved_config,
                current_key=object_key,
                now=effective_now,
            )
            return ExternalBackupResult(
                object_key=object_key,
                checksum_key=checksum_key,
                sha256=local.sha256,
                deleted_objects=deleted,
                manifest_backup_id=manifest_record.backup_id,
                manifest_sequence=manifest_record.sequence,
                manifest_record_sha256=manifest_receipt.readback_record_sha256,
            )
    except (BackupAlreadyRunning, BackupConfigurationError, ExternalBackupError):
        raise
    except Exception as exc:
        try:
            is_backup_error = isinstance(exc, _backup_tool().BackupError)
        except Exception:  # tools/ 자체가 없는 최소 로컬 복사본
            is_backup_error = False
        if is_backup_error:
            raise ExternalBackupError("SQLite 백업 또는 검증에 실패했습니다") from exc
        raise ExternalBackupError("외부 저장소 백업을 완료하지 못했습니다") from exc
    finally:
        _RUN_LOCK.release()
