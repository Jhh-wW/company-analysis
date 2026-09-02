"""ReleaseAuthority 발급 뒤에는 content·delivery·artifact 결속을 DB가 막는다.

★ 지키는 것: content_snapshots·deliveries·artifacts 세 표는 전부
  content-addressed다(PK가 나머지 컬럼 전체의 canonical hash) — 정상
  코드는 이 표들을 UPDATE하지 않는다. 그런데 raw SQL(버그 있는 마이그레이션,
  직접 DB 조작)은 여전히 UPDATE할 수 있었다. ReleaseAuthority가 이미 그
  content·delivery·artifact를 «출고 완료」로 서명한 뒤에는, DB trigger가
  그 raw SQL 우회조차 막는다(32장 §4-3 「Python 검사만 두지 않는다」).

★ 음성 대조: authority가 **아직 없는** 행은 이 트리거의 대상이 아니다 —
  기존 손상 재현 시험(test_delivery_store.py·test_artifact_store.py)이
  같은 표를 raw UPDATE로 그대로 오염시킬 수 있어야 그 시험들의 read-time
  검증(``LifecycleStoreCorrupt`` 등)이 계속 실제로 시험된다. 이 파일의
  「음성」시험이 그 계약을 지킨다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path

import pytest

from src.features.report_delivery import authority as authority_store
from src.features.report_delivery.artifact import (
    TABLE_ARTIFACTS,
    ArtifactRetention,
    ArtifactVersion,
    FilesystemArtifactBlobBackend,
    bind_artifact_to_delivery,
    create_blob_write_intent,
)
from src.features.report_delivery.artifact import store_approved_pdf as _store_approved_pdf
from src.features.report_delivery.models import ContentSnapshot, Delivery, DeliveryPolicy
from src.features.report_delivery.store import (
    TABLE_CONTENT_SNAPSHOTS,
    TABLE_DELIVERIES,
    save_delivery,
)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n% immutability trigger fixture\n%%EOF\n"


def _store_pdf(
    conn: sqlite3.Connection,
    backend: FilesystemArtifactBlobBackend,
    *,
    content_snapshot_id: str,
    now: dt.datetime,
):
    intent = create_blob_write_intent(conn, backend, pdf_bytes=_pdf_bytes(), created_at=now)
    return _store_approved_pdf(
        conn,
        backend,
        blob_intent=intent,
        content_snapshot_id=content_snapshot_id,
        pdf_bytes=_pdf_bytes(),
        version=ArtifactVersion(
            renderer_version="renderer-immutability",
            font_bundle_version="fonts-2026-08",
            checker_version="checker-3",
        ),
        created_at=now,
        retention=ArtifactRetention(policy_id="audit-v1", retain_until=None),
    )


def _issue_owner_authority(
    *,
    delivery: Delivery,
    content: ContentSnapshot,
    artifact_id: str,
    issued_at: dt.datetime,
) -> authority_store.ReleaseAuthority:
    """DB 결속 트리거만 시험한다 — GenerationProducerEvidence는 불필요하다.

    ``authority.ReleaseAuthority``는 evidence 객체를 직접 검증하지 않는다.
    발급 뒤 DB 결속(content·delivery·artifact exact 지문)만 트리거가
    지킨다 — 그래서 나머지 sha256 필드는 형식만 맞으면 된다.
    """

    return authority_store.ReleaseAuthority.issue_owner(
        public_id=delivery.public_id,
        delivery_id=delivery.delivery_id,
        company_id="00123456",
        billing_bucket_id=delivery.billing_bucket_id,
        content_snapshot_id=content.content_id,
        artifact_id=artifact_id,
        report_payload_sha256=content.payload_sha256,
        producer_evidence_sha256=_sha("producer"),
        assessment_sha256=_sha("assessment"),
        public_content_sha256=_sha("public-content"),
        public_manifest_sha256=_sha("public-manifest"),
        evidence_generation_sha256=_sha("evidence-generation"),
        build_identity_sha256=_sha("build-identity"),
        automatic_release_sha256=_sha("automatic-release"),
        charge_run_id=delivery.public_id,
        charge_decision_sha256=_sha("charge-decision"),
        issued_at=issued_at,
    )


def test_authority_발급전에는_content_delivery_artifact를_그대로_UPDATE할수있다(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    """음성 대조 — authority가 없으면 손상 재현 시험의 raw UPDATE가 여전히 통과한다."""

    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    delivery = Delivery.issue(
        public_id="public-before-authority",
        billing_bucket_id="bucket-before",
        content=content,
        delivered_at=now,
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        reused_from_cache=False,
    )
    save_delivery(conn, delivery)
    artifact = _store_pdf(conn, backend, content_snapshot_id=content.content_id, now=now)
    bind_artifact_to_delivery(
        conn, delivery_id=delivery.delivery_id, artifact_id=artifact.artifact_id
    )

    conn.execute(
        f"UPDATE {TABLE_CONTENT_SNAPSHOTS} SET payload = ? WHERE content_id = ?",
        (b"tampered-without-authority", content.content_id),
    )
    conn.execute(
        f"UPDATE {TABLE_DELIVERIES} SET billing_bucket_id = ? WHERE delivery_id = ?",
        ("tampered-bucket", delivery.delivery_id),
    )
    conn.execute(
        f"UPDATE {TABLE_ARTIFACTS} SET renderer_version = ? WHERE artifact_id = ?",
        ("tampered-renderer", artifact.artifact_id),
    )


def test_authority_발급후에는_content_delivery_artifact_UPDATE가_전부_막힌다(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifact-blobs")
    delivery = Delivery.issue(
        public_id="public-after-authority",
        billing_bucket_id="bucket-after",
        content=content,
        delivered_at=now,
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        reused_from_cache=False,
    )
    save_delivery(conn, delivery)
    artifact = _store_pdf(conn, backend, content_snapshot_id=content.content_id, now=now)
    bind_artifact_to_delivery(
        conn, delivery_id=delivery.delivery_id, artifact_id=artifact.artifact_id
    )
    authority = _issue_owner_authority(
        delivery=delivery, content=content, artifact_id=artifact.artifact_id, issued_at=now
    )
    authority_store.save_release_authority(conn, authority)

    with pytest.raises(sqlite3.IntegrityError, match="release authority"):
        conn.execute(
            f"UPDATE {TABLE_CONTENT_SNAPSHOTS} SET payload = ? WHERE content_id = ?",
            (b"tampered-after-authority", content.content_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="release authority"):
        conn.execute(
            f"UPDATE {TABLE_DELIVERIES} SET billing_bucket_id = ? WHERE delivery_id = ?",
            ("tampered-bucket", delivery.delivery_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="release authority"):
        conn.execute(
            f"UPDATE {TABLE_ARTIFACTS} SET renderer_version = ? WHERE artifact_id = ?",
            ("tampered-renderer", artifact.artifact_id),
        )
