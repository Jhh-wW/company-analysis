"""cron 전용 외부 백업 경로의 인증·오류 은닉 시험."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.backup import s3
from src.web import main
from src.web.routers import backup as backup_router


SECRET = "backup-trigger-secret-that-is-at-least-32-bytes"


def test_관리자로그인벽_안에서도_Bearer_없이는_백업을_실행하지_않는다(
    monkeypatch,
) -> None:
    called = False

    def should_not_run():
        nonlocal called
        called = True

    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(s3.ENV_TRIGGER_SECRET, SECRET)
    monkeypatch.setattr(backup_router.backup_service, "run_backup", should_not_run)
    with TestClient(main.app) as client:
        missing = client.post("/internal/backup/run", follow_redirects=False)
        wrong = client.post(
            "/internal/backup/run",
            headers={"Authorization": "Bearer wrong-secret"},
            follow_redirects=False,
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "location" not in missing.headers
    assert not called


def test_올바른_호출비밀이면_원격검증_결과만_돌려준다(monkeypatch) -> None:
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(s3.ENV_TRIGGER_SECRET, SECRET)
    monkeypatch.setattr(
        backup_router.backup_service,
        "run_backup",
        lambda: s3.ExternalBackupResult(
            object_key="company-analysis/storage-backup.sqlite3",
            checksum_key="company-analysis/storage-backup.sqlite3.sha256",
            sha256="a" * 64,
            deleted_objects=2,
        ),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/internal/backup/run",
            headers={"Authorization": f"Bearer {SECRET}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "object_key": "company-analysis/storage-backup.sqlite3",
        "checksum_key": "company-analysis/storage-backup.sqlite3.sha256",
        "sha256": "a" * 64,
        "deleted_objects": 2,
    }
    assert response.headers["cache-control"] == "private, no-store"


def test_설정이나_외부저장소_오류의_내부내용은_응답에_노출하지_않는다(
    monkeypatch,
) -> None:
    monkeypatch.setenv(s3.ENV_TRIGGER_SECRET, SECRET)

    def fail():
        raise s3.ExternalBackupError("bucket-and-private-detail")

    monkeypatch.setattr(backup_router.backup_service, "run_backup", fail)
    with TestClient(main.app) as client:
        response = client.post(
            "/internal/backup/run",
            headers={"Authorization": f"Bearer {SECRET}"},
        )

    assert response.status_code == 503
    assert response.json() == {"status": "failed"}
    assert "bucket-and-private-detail" not in response.text


def test_백업이_이미_돌고_있으면_재시도_시간을_알린다(monkeypatch) -> None:
    monkeypatch.setenv(s3.ENV_TRIGGER_SECRET, SECRET)

    def busy():
        raise s3.BackupAlreadyRunning("already running")

    monkeypatch.setattr(backup_router.backup_service, "run_backup", busy)
    with TestClient(main.app) as client:
        response = client.post(
            "/internal/backup/run",
            headers={"Authorization": f"Bearer {SECRET}"},
        )

    assert response.status_code == 409
    assert response.headers["retry-after"] == "60"
