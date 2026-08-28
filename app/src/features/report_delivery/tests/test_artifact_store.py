from __future__ import annotations

import datetime as dt
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.features.report_delivery import artifact as artifact_module
from src.features.report_delivery.artifact import (
    ArtifactError,
    ArtifactCapacityExceeded,
    ArtifactImmutableConflict,
    ArtifactInspectionStatus,
    ArtifactRetention,
    ArtifactRootBusy,
    ArtifactVersion,
    BlobPointer,
    FilesystemArtifactBlobBackend,
    TABLE_ARTIFACTS,
    artifact_for_delivery,
    bind_artifact_to_delivery,
    create_blob_write_intent,
    inspect_artifact,
    register_legacy_original_unknown,
    retention_review_due,
    store_approved_pdf as _store_approved_pdf,
)
from src.features.report_delivery.canonical import sha256_hex
from src.features.report_delivery.models import ContentSnapshot, Delivery, DeliveryPolicy
from src.features.report_delivery.store import save_delivery


def _version(renderer: str = "renderer-1") -> ArtifactVersion:
    return ArtifactVersion(
        renderer_version=renderer,
        font_bundle_version="fonts-2026-08",
        checker_version="checker-3",
    )


def _retention(now: dt.datetime, *, days: int = 365) -> ArtifactRetention:
    return ArtifactRetention(
        policy_id="audit-v1",
        retain_until=now + dt.timedelta(days=days),
    )


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n% immutable approved bytes\n%%EOF\n"


def _external_lock_holder(root: Path, ready: Path, release: Path) -> subprocess.Popen:
    script = r"""
import sys
import time
from pathlib import Path
from src.shared.bounded_file_lock import exclusive_file_lock

root, ready, release = map(Path, sys.argv[1:4])
with exclusive_file_lock(root / '.artifact-root.lock', timeout_seconds=5):
    ready.write_text('locked', encoding='ascii')
    deadline = time.monotonic() + 10
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
"""
    return subprocess.Popen(
        [sys.executable, "-c", script, str(root), str(ready), str(release)],
        cwd=Path(__file__).resolve().parents[4],
    )


def store_approved_pdf(
    conn: sqlite3.Connection,
    backend: FilesystemArtifactBlobBackend,
    **kwargs: object,
):
    """단위 시험도 새 blob을 쓰기 전 intent 계약을 거친다."""

    pdf_bytes = kwargs.get("pdf_bytes")
    created_at = kwargs.get("created_at")
    assert isinstance(pdf_bytes, bytes)
    assert isinstance(created_at, dt.datetime)
    intent = create_blob_write_intent(
        conn,
        backend,
        pdf_bytes=pdf_bytes,
        created_at=created_at,
    )
    return _store_approved_pdf(
        conn,
        backend,
        blob_intent=intent,
        **kwargs,
    )


def test_read_only_backend_creation_does_not_create_storage_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact-blobs"
    backend = FilesystemArtifactBlobBackend(root, capacity_bytes=1024)
    missing = BlobPointer(
        key="sha256/aa/" + "a" * 64 + ".blob",
        sha256="a" * 64,
        byte_length=1,
    )

    assert root.exists() is False
    assert backend.usage().used_bytes == 0
    assert backend.read(missing) is None
    assert root.exists() is False


def test_windows_directory_fsync_불가를_성공으로_가장하지않는다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows Python은 directory handle을 못 여는 경계를 반환값으로 드러낸다."""

    def must_not_open(*_args, **_kwargs):
        raise AssertionError("Windows 경계에서 directory open을 시도하면 안 됩니다")

    monkeypatch.setattr(artifact_module.os, "open", must_not_open)

    assert artifact_module._fsync_directory(  # noqa: SLF001 - 내구성 경계 회귀
        tmp_path,
        platform_name="nt",
    ) is False


def test_blob_게시와_삭제는_directory_entry를_DB사건보다_먼저_봉인한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "artifact-blobs").resolve()
    backend = FilesystemArtifactBlobBackend(root)
    data = b"directory-entry-durability"
    calls: list[Path] = []

    def record(directory: Path, *, platform_name: str | None = None) -> bool:
        del platform_name
        calls.append(Path(directory))
        return True

    monkeypatch.setattr(artifact_module, "_fsync_directory", record)
    pointer = backend.put_immutable(sha256=sha256_hex(data), data=data)

    target = root / pointer.key
    assert calls == [
        target.parent,
        target.parent.parent,
        root,
        target.parent,
        target.parent.parent,
        root,
    ]

    calls.clear()
    assert backend.delete_retired_if_exact(pointer).value == "deleted"
    assert calls == [target.parent]


def test_directory_fsync_실패뒤_재시도는_같은bytes를_봉인하고_복구한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "artifact-blobs").resolve()
    backend = FilesystemArtifactBlobBackend(root)
    data = b"crash-between-link-and-database-commit"
    digest = sha256_hex(data)
    original = artifact_module._fsync_directory  # noqa: SLF001
    sync_calls = 0

    def fail_once(directory: Path, *, platform_name: str | None = None) -> bool:
        nonlocal sync_calls
        sync_calls += 1
        # 첫 chain은 새 shard directory를 봉인한다. hard-link와 임시 파일 제거가
        # 끝난 두 번째 chain의 첫 fsync에서 전원 중단을 주입한다.
        if sync_calls == 4:
            raise ArtifactError("주입한 directory fsync 실패")
        return original(directory, platform_name=platform_name)

    monkeypatch.setattr(artifact_module, "_fsync_directory", fail_once)
    with pytest.raises(ArtifactError, match="주입한 directory fsync 실패"):
        backend.put_immutable(sha256=digest, data=data)

    # hard-link는 이미 생겼지만 호출은 성공하지 않았다. 다음 시도가 기존 bytes를
    # 검증하고 directory를 다시 봉인해야 DB metadata commit이 허용된다.
    pointer = backend.expected_pointer(sha256=digest, byte_length=len(data))
    assert (root / pointer.key).read_bytes() == data
    assert backend.put_immutable(sha256=digest, data=data) == pointer


def test_깊은_Windows경로에서도_임시명이_최종blob보다_길어지지않는다(
    tmp_path: Path,
) -> None:
    data = b"deep-windows-artifact-path"
    digest = sha256_hex(data)
    root = tmp_path / "deep-artifact-root"
    target = root / "sha256" / digest[:2] / f"{digest}.blob"
    # 고전 Windows MAX_PATH 바로 아래에 최종 내용주소를 둔다. 옛 64자 digest
    # 임시 prefix는 최종명보다 9자 길어 260을 넘었지만 짧은 임시명은 안전하다.
    desired_target_length = 252
    while len(str(target)) + 11 <= desired_target_length:
        root /= "deep-part-x"
        target = root / "sha256" / digest[:2] / f"{digest}.blob"
    remaining = desired_target_length - len(str(target))
    if remaining >= 2:
        root /= "x" * (remaining - 1)
        target = root / "sha256" / digest[:2] / f"{digest}.blob"
    assert len(str(target)) == desired_target_length
    old_temporary_length = len(str(target)) + 9
    assert old_temporary_length > 260

    backend = FilesystemArtifactBlobBackend(root)
    pointer = backend.put_immutable(sha256=digest, data=data)

    assert (root.resolve() / pointer.key).read_bytes() == data


def test_Windows_지원길이를_넘는_최종경로는_backend_생성때_거절한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic = Path("C:/") / ("x" * 180) / ("y" * 100)
    assert artifact_module._exceeds_windows_legacy_path(  # noqa: SLF001
        synthetic,
        platform_name="nt",
    )
    assert not artifact_module._exceeds_windows_legacy_path(  # noqa: SLF001
        synthetic,
        platform_name="posix",
    )

    monkeypatch.setattr(
        artifact_module,
        "_exceeds_windows_legacy_path",
        lambda _path: True,
    )
    with pytest.raises(ArtifactError) as caught:
        FilesystemArtifactBlobBackend(tmp_path / "지원길이초과-root")

    assert str(caught.value) == "artifact 저장 경로가 Windows 지원 길이를 넘습니다"
    assert str(tmp_path) not in str(caught.value)


def test_hardlink_OS오류는_경로를_숨긴_고정_artifact오류로_닫힌다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "artifact-blobs"
    backend = FilesystemArtifactBlobBackend(root)
    data = b"hard-link-failure"
    digest = sha256_hex(data)

    def fail_link(_source: Path, _target: Path) -> None:
        raise FileNotFoundError(f"비밀 저장 경로: {tmp_path}")

    monkeypatch.setattr(artifact_module.os, "link", fail_link)
    with pytest.raises(ArtifactError) as caught:
        backend.put_immutable(sha256=digest, data=data)

    assert str(caught.value) == "artifact 최종 내용주소 파일을 만들 수 없습니다"
    assert str(tmp_path) not in str(caught.value)
    pointer = backend.expected_pointer(sha256=digest, byte_length=len(data))
    assert not (root.resolve() / pointer.key).exists()
    assert list(root.rglob("*.tmp")) == []


def test_다른_process가_root_lock을_놓지않아도_요청은_유한시간에_닫힌다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "artifact-blobs"
    root.mkdir()
    ready = tmp_path / "holder-ready"
    release = tmp_path / "holder-release"
    holder = _external_lock_holder(root, ready, release)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            if holder.poll() is not None:
                pytest.fail(f"외부 lock holder가 먼저 종료했습니다: {holder.returncode}")
            time.sleep(0.02)
        assert ready.exists()
        monkeypatch.setattr(
            artifact_module,
            "ARTIFACT_ROOT_LOCK_TIMEOUT_SECONDS",
            0.2,
        )
        backend = FilesystemArtifactBlobBackend(root)

        started = time.monotonic()
        with pytest.raises(ArtifactRootBusy, match="다른 process"):
            backend.usage()
        elapsed = time.monotonic() - started

        assert 0.15 <= elapsed < 1.5
    finally:
        release.touch()
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=5)


def test_root_lock이_다른경로와_hardlink면_저장소를_신뢰하지않는다(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact-blobs"
    root.mkdir()
    lock_path = root / ".artifact-root.lock"
    lock_path.write_bytes(b"\0")
    alias = tmp_path / "outside-lock-alias"
    try:
        alias.hardlink_to(lock_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"이 파일시스템은 hard-link 시험을 지원하지 않습니다: {exc}")

    backend = FilesystemArtifactBlobBackend(root)

    with pytest.raises(ArtifactError, match="잠금 파일을 신뢰할 수 없습니다"):
        backend.usage()


def test_approved_pdf_returns_exact_stored_bytes_after_renderer_version_changes(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    original = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version("renderer-1"),
        created_at=now,
        retention=_retention(now),
    )
    next_renderer = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version("renderer-2"),
        created_at=now + dt.timedelta(minutes=1),
        retention=_retention(now),
    )

    assert original.artifact_id != next_renderer.artifact_id
    assert original.blob_pointer == next_renderer.blob_pointer
    inspected = inspect_artifact(conn, backend, original.artifact_id)
    assert inspected is not None
    assert inspected.status is ArtifactInspectionStatus.AVAILABLE
    assert inspected.pdf_bytes == _pdf_bytes()


def test_delivery_pdf_binding_cannot_be_replaced_by_new_renderer(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    policy = DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60))
    delivery = Delivery.issue(
        public_id="public-pdf-fixed",
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=now,
        policy=policy,
        reused_from_cache=False,
    )
    save_delivery(conn, delivery)
    first = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version("renderer-1"),
        created_at=now,
        retention=_retention(now),
    )
    second = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes() + b"new-version",
        version=_version("renderer-2"),
        created_at=now + dt.timedelta(minutes=1),
        retention=_retention(now),
    )
    bind_artifact_to_delivery(
        conn, delivery_id=delivery.delivery_id, artifact_id=first.artifact_id
    )
    assert artifact_for_delivery(conn, delivery_id=delivery.delivery_id) == first

    with pytest.raises(ArtifactImmutableConflict, match="바꿀 수 없습니다"):
        bind_artifact_to_delivery(
            conn, delivery_id=delivery.delivery_id, artifact_id=second.artifact_id
        )


def test_missing_pdf_is_explicit_and_never_rerendered(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact-blobs"
    backend = FilesystemArtifactBlobBackend(root)
    metadata = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version(),
        created_at=now,
        retention=_retention(now),
    )
    assert metadata.blob_pointer is not None
    (root / metadata.blob_pointer.key).unlink()

    inspected = inspect_artifact(conn, backend, metadata.artifact_id)

    assert inspected is not None
    assert inspected.status is ArtifactInspectionStatus.MISSING
    assert inspected.pdf_bytes is None


def test_artifact_retry_keeps_first_metadata_and_does_not_rewrite_blob(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact-blobs"
    backend = FilesystemArtifactBlobBackend(root)
    first = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version(),
        created_at=now,
        retention=_retention(now),
    )
    assert first.blob_pointer is not None
    blob_path = root / first.blob_pointer.key
    blob_path.unlink()

    retry = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version(),
        created_at=now + dt.timedelta(minutes=5),
        retention=ArtifactRetention(
            policy_id="different-policy-on-retry",
            retain_until=now + dt.timedelta(days=10),
        ),
    )

    assert retry == first
    assert not blob_path.exists()
    inspected = inspect_artifact(conn, backend, first.artifact_id)
    assert inspected is not None
    assert inspected.status is ArtifactInspectionStatus.MISSING


def test_checksum_mismatch_is_reported_as_corruption(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifact-blobs"
    backend = FilesystemArtifactBlobBackend(root)
    metadata = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version(),
        created_at=now,
        retention=_retention(now),
    )
    assert metadata.blob_pointer is not None
    (root / metadata.blob_pointer.key).write_bytes(b"corrupted")

    inspected = inspect_artifact(conn, backend, metadata.artifact_id)

    assert inspected is not None
    assert inspected.status is ArtifactInspectionStatus.CORRUPT
    assert inspected.pdf_bytes is None


def test_artifact_metadata_fingerprint_corruption_is_rejected(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    metadata = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version(),
        created_at=now,
        retention=_retention(now),
    )
    conn.execute(
        f"UPDATE {TABLE_ARTIFACTS} SET renderer_version = ? WHERE artifact_id = ?",
        ("tampered-renderer", metadata.artifact_id),
    )

    with pytest.raises(ArtifactError, match="metadata가 손상"):
        inspect_artifact(conn, backend, metadata.artifact_id)


def test_legacy_original_unknown_is_not_disguised_as_current_rerender(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    legacy = register_legacy_original_unknown(
        conn,
        content_snapshot_id=content.content_id,
        legacy_reference="old-release-record-sha256",
        recorded_at=now,
        retention=_retention(now),
    )

    inspected = inspect_artifact(conn, backend, legacy.artifact_id)

    assert legacy.blob_pointer is None
    assert inspected is not None
    assert inspected.status is ArtifactInspectionStatus.LEGACY_ORIGINAL_UNKNOWN
    assert inspected.pdf_bytes is None


def test_retention_review_never_deletes_due_artifact(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    metadata = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version(),
        created_at=now - dt.timedelta(days=2),
        retention=ArtifactRetention(
            policy_id="review-only",
            retain_until=now - dt.timedelta(days=1),
        ),
    )

    due = retention_review_due(conn, now=now)
    inspected = inspect_artifact(conn, backend, metadata.artifact_id)

    assert tuple(item.artifact_id for item in due) == (metadata.artifact_id,)
    assert inspected is not None
    assert inspected.status is ArtifactInspectionStatus.AVAILABLE
    assert not hasattr(backend, "delete")


def test_active_new_delivery_keeps_old_artifact_out_of_retention_review(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    metadata = store_approved_pdf(
        conn,
        backend,
        content_snapshot_id=content.content_id,
        pdf_bytes=_pdf_bytes(),
        version=_version(),
        created_at=now,
        retention=ArtifactRetention(
            policy_id="short-base-retention",
            retain_until=now + dt.timedelta(days=30),
        ),
    )
    delivered_at = now + dt.timedelta(days=59)
    delivery = Delivery.issue(
        public_id="public-active-old-artifact",
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=delivered_at,
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        reused_from_cache=True,
    )
    save_delivery(conn, delivery)
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=metadata.artifact_id,
    )

    assert retention_review_due(conn, now=delivered_at) == ()
    assert retention_review_due(conn, now=delivery.expires_at) == (metadata,)


def test_capacity_limit_fails_without_garbage_collecting_existing_blobs(
    tmp_path: Path,
) -> None:
    first = b"first-approved-pdf"
    second = b"second-approved-pdf"
    backend = FilesystemArtifactBlobBackend(
        tmp_path / "artifact-blobs", capacity_bytes=len(first) + len(second) - 1
    )
    backend.put_immutable(sha256=sha256_hex(first), data=first)

    with pytest.raises(ArtifactCapacityExceeded, match="자동으로.*지우지"):
        backend.put_immutable(sha256=sha256_hex(second), data=second)

    assert backend.usage().used_bytes == len(first)


def test_같은_root의_서로다른_backend도_용량검사를_한번에_직렬화한다(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared-capacity-artifacts"
    first_backend = FilesystemArtifactBlobBackend(root, capacity_bytes=100)
    second_backend = FilesystemArtifactBlobBackend(root, capacity_bytes=100)
    # 요청마다 새 backend를 만들어도 같은 물리 저장소의 한도 lock은 하나다.
    assert first_backend._lock is second_backend._lock
    payloads = (b"a" * 60, b"b" * 60)

    def store(backend: FilesystemArtifactBlobBackend, data: bytes) -> str:
        try:
            backend.put_immutable(sha256=sha256_hex(data), data=data)
        except ArtifactCapacityExceeded:
            return "capacity"
        return "stored"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(store, backend, data)
            for backend, data in zip((first_backend, second_backend), payloads)
        )
        outcomes = tuple(future.result(timeout=5) for future in futures)

    assert sorted(outcomes) == ["capacity", "stored"]
    assert first_backend.usage().used_bytes == 60
