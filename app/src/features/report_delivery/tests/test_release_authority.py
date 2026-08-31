from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from src.features.report_delivery.artifact import (
    ArtifactRetention,
    ArtifactVersion,
    FilesystemArtifactBlobBackend,
    bind_artifact_to_delivery,
    create_blob_write_intent,
    store_approved_pdf,
)
from src.features.report_delivery.authority import (
    TABLE_RELEASE_AUTHORITIES,
    ReleaseAuthority,
    ReleaseAuthorityConflict,
    ReleaseAuthorityCorrupt,
    ReleaseAuthorityKind,
    load_owner_authority,
    load_release_authority,
    load_release_authority_by_public_id,
    save_release_authority,
)
from src.features.report_delivery.models import Delivery, DeliveryPolicy
from src.features.report_delivery.store import save_delivery


def _digest(character: str) -> str:
    return character * 64


def _delivery(
    *,
    public_id: str,
    bucket: str,
    content,
    now: dt.datetime,
    reused: bool,
) -> Delivery:
    return Delivery.issue(
        public_id=public_id,
        billing_bucket_id=bucket,
        content=content,
        delivered_at=now,
        policy=DeliveryPolicy(
            content_max_age=dt.timedelta(days=60),
            public_link_lifetime=dt.timedelta(days=60),
        ),
        reused_from_cache=reused,
    )


def _stored_pdf(
    conn: sqlite3.Connection,
    *,
    content,
    now: dt.datetime,
    root: Path,
):
    pdf_bytes = b"%PDF-1.4\nrelease authority\n%%EOF\n"
    backend = FilesystemArtifactBlobBackend(root)
    intent = create_blob_write_intent(
        conn,
        backend,
        pdf_bytes=pdf_bytes,
        created_at=now,
    )
    return store_approved_pdf(
        conn,
        backend,
        blob_intent=intent,
        content_snapshot_id=content.content_id,
        pdf_bytes=pdf_bytes,
        version=ArtifactVersion(
            renderer_version="renderer-authority-v1",
            font_bundle_version="font-authority-v1",
            checker_version="checker-authority-v1",
        ),
        created_at=now,
        retention=ArtifactRetention(
            policy_id="authority-retention-v1",
            retain_until=None,
        ),
    )


def _owner(
    *,
    delivery: Delivery,
    artifact_id: str,
    content,
    now: dt.datetime,
) -> ReleaseAuthority:
    return ReleaseAuthority.issue_owner(
        public_id=delivery.public_id,
        delivery_id=delivery.delivery_id,
        company_id="00123456",
        billing_bucket_id=delivery.billing_bucket_id,
        content_snapshot_id=content.content_id,
        artifact_id=artifact_id,
        report_payload_sha256=content.payload_sha256,
        producer_evidence_sha256=_digest("a"),
        assessment_sha256=_digest("b"),
        public_content_sha256=_digest("c"),
        public_manifest_sha256=_digest("d"),
        evidence_generation_sha256=_digest("e"),
        build_identity_sha256=_digest("f"),
        automatic_release_sha256=_digest("1"),
        charge_run_id=f"charge:{delivery.public_id}",
        charge_decision_sha256=_digest("2"),
        issued_at=now,
    )


def test_새_출고_권위는_실제_delivery_content_pdf에_결속된다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    delivery = _delivery(
        public_id="authority-owner",
        bucket="bucket-owner",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    authority = _owner(
        delivery=delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )

    assert save_release_authority(conn, authority) == authority
    assert load_release_authority(conn, authority.authority_id) == authority
    assert load_release_authority_by_public_id(conn, delivery.public_id) == authority
    assert (
        load_owner_authority(
            conn,
            content_snapshot_id=content.content_id,
            artifact_id=artifact.artifact_id,
        )
        == authority
    )


def test_본문_지문이나_delivery_결속을_꾸민_권위는_DB가_거절한다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    delivery = _delivery(
        public_id="authority-forged",
        bucket="bucket-forged",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    authority = _owner(
        delivery=delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )
    forged = ReleaseAuthority.issue_owner(
        public_id=authority.public_id,
        delivery_id=authority.delivery_id,
        company_id=authority.company_id,
        billing_bucket_id=authority.billing_bucket_id,
        content_snapshot_id=authority.content_snapshot_id,
        artifact_id=authority.artifact_id,
        report_payload_sha256=_digest("9"),
        producer_evidence_sha256=authority.producer_evidence_sha256,
        assessment_sha256=authority.assessment_sha256,
        public_content_sha256=authority.public_content_sha256,
        public_manifest_sha256=authority.public_manifest_sha256,
        evidence_generation_sha256=authority.evidence_generation_sha256,
        build_identity_sha256=authority.build_identity_sha256,
        automatic_release_sha256=authority.automatic_release_sha256,
        charge_run_id=authority.charge_run_id,
        charge_decision_sha256=authority.charge_decision_sha256,
        issued_at=authority.issued_at,
    )

    with pytest.raises(ReleaseAuthorityConflict, match="결속"):
        save_release_authority(conn, forged)


def test_재사용_권위는_원본의_본문_pdf_생성증거를_그대로_상속한다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    owner_delivery = _delivery(
        public_id="authority-origin",
        bucket="bucket-same",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, owner_delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=owner_delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    owner = _owner(
        delivery=owner_delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )
    save_release_authority(conn, owner)

    reused_delivery = _delivery(
        public_id="authority-waiter",
        bucket="bucket-same",
        content=content,
        now=now + dt.timedelta(seconds=1),
        reused=True,
    )
    save_delivery(conn, reused_delivery)
    bind_artifact_to_delivery(
        conn,
        delivery_id=reused_delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    reused = ReleaseAuthority.issue_reuse(
        origin=owner,
        public_id=reused_delivery.public_id,
        delivery_id=reused_delivery.delivery_id,
        billing_bucket_id=reused_delivery.billing_bucket_id,
        automatic_release_sha256=owner.automatic_release_sha256,
        charge_run_id="charge:authority-waiter",
        charge_decision_sha256=_digest("3"),
        issued_at=now + dt.timedelta(seconds=1),
    )

    assert reused.kind is ReleaseAuthorityKind.REUSE
    assert save_release_authority(conn, reused) == reused
    assert reused.origin_authority_id == owner.authority_id
    assert reused.producer_evidence_sha256 == owner.producer_evidence_sha256
    assert reused.content_snapshot_id == owner.content_snapshot_id
    assert reused.artifact_id == owner.artifact_id
    assert reused.automatic_release_sha256 == owner.automatic_release_sha256
    assert reused.charge_run_id != owner.charge_run_id

    with pytest.raises(ValueError, match="같은 자동승인"):
        ReleaseAuthority.issue_reuse(
            origin=owner,
            public_id="authority-waiter-wrong-release",
            delivery_id="delivery-waiter-wrong-release",
            billing_bucket_id=owner.billing_bucket_id,
            automatic_release_sha256=_digest("9"),
            charge_run_id="charge:authority-waiter-wrong-release",
            charge_decision_sha256=_digest("3"),
            issued_at=now + dt.timedelta(seconds=2),
        )
    with pytest.raises(ValueError, match="청구 행"):
        ReleaseAuthority.issue_reuse(
            origin=owner,
            public_id="authority-waiter-same-charge",
            delivery_id="delivery-waiter-same-charge",
            billing_bucket_id=owner.billing_bucket_id,
            automatic_release_sha256=owner.automatic_release_sha256,
            charge_run_id=owner.charge_run_id,
            charge_decision_sha256=_digest("3"),
            issued_at=now + dt.timedelta(seconds=2),
        )


def test_원본_권위가_없는_재사용은_거절한다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    delivery = _delivery(
        public_id="authority-orphan-reuse",
        bucket="bucket-orphan",
        content=content,
        now=now,
        reused=True,
    )
    save_delivery(conn, delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    imaginary_delivery = _delivery(
        public_id="authority-imaginary-origin",
        bucket=delivery.billing_bucket_id,
        content=content,
        now=now,
        reused=False,
    )
    imaginary_origin = _owner(
        delivery=imaginary_delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )
    reused = ReleaseAuthority.issue_reuse(
        origin=imaginary_origin,
        public_id=delivery.public_id,
        delivery_id=delivery.delivery_id,
        billing_bucket_id=delivery.billing_bucket_id,
        automatic_release_sha256=imaginary_origin.automatic_release_sha256,
        charge_run_id="charge:authority-orphan-reuse",
        charge_decision_sha256=_digest("3"),
        issued_at=now,
    )

    with pytest.raises(ReleaseAuthorityConflict, match="결속"):
        save_release_authority(conn, reused)


def test_다른_비용통장은_승인된_원본을_가져다_재사용할_수_없다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    delivery = _delivery(
        public_id="authority-bucket-origin",
        bucket="bucket-owner-only",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    owner = _owner(
        delivery=delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )

    with pytest.raises(ValueError, match="비용 통장"):
        ReleaseAuthority.issue_reuse(
            origin=owner,
            public_id="authority-other-bucket",
            delivery_id="delivery-other-bucket",
            billing_bucket_id="bucket-attacker",
            automatic_release_sha256=owner.automatic_release_sha256,
            charge_run_id="charge:authority-other-bucket",
            charge_decision_sha256=_digest("3"),
            issued_at=now + dt.timedelta(seconds=1),
        )


def test_저장된_권위는_update_delete할_수_없다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    delivery = _delivery(
        public_id="authority-immutable",
        bucket="bucket-immutable",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    authority = _owner(
        delivery=delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )
    save_release_authority(conn, authority)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            f"UPDATE {TABLE_RELEASE_AUTHORITIES} SET company_id='99999999'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(f"DELETE FROM {TABLE_RELEASE_AUTHORITIES}")


def test_재사용_권위의_자동승인_영수증을_DB에서_바꿔도_조회가_거절한다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    owner_delivery = _delivery(
        public_id="authority-release-owner",
        bucket="bucket-release-binding",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, owner_delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs-release-binding",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=owner_delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    owner = _owner(
        delivery=owner_delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )
    save_release_authority(conn, owner)

    reused_delivery = _delivery(
        public_id="authority-release-waiter",
        bucket=owner.billing_bucket_id,
        content=content,
        now=now + dt.timedelta(seconds=1),
        reused=True,
    )
    save_delivery(conn, reused_delivery)
    bind_artifact_to_delivery(
        conn,
        delivery_id=reused_delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    reused = ReleaseAuthority.issue_reuse(
        origin=owner,
        public_id=reused_delivery.public_id,
        delivery_id=reused_delivery.delivery_id,
        billing_bucket_id=reused_delivery.billing_bucket_id,
        automatic_release_sha256=owner.automatic_release_sha256,
        charge_run_id="charge:authority-release-waiter",
        charge_decision_sha256=_digest("3"),
        issued_at=now + dt.timedelta(seconds=1),
    )
    save_release_authority(conn, reused)

    conn.execute("DROP TRIGGER report_release_authorities_no_update")
    conn.execute(
        f"UPDATE {TABLE_RELEASE_AUTHORITIES} "
        "SET automatic_release_sha256 = ? WHERE authority_id = ?",
        (_digest("9"), reused.authority_id),
    )

    with pytest.raises(ReleaseAuthorityCorrupt, match="손상"):
        load_release_authority(conn, reused.authority_id)


def test_trigger를_우회해_행을_손상해도_조회가_권위로_인정하지_않는다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    delivery = _delivery(
        public_id="authority-corrupt",
        bucket="bucket-corrupt",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    authority = _owner(
        delivery=delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )
    save_release_authority(conn, authority)
    conn.execute("DROP TRIGGER report_release_authorities_no_update")
    conn.execute(
        f"UPDATE {TABLE_RELEASE_AUTHORITIES} SET company_id='99999999'"
    )

    with pytest.raises(ReleaseAuthorityCorrupt, match="손상"):
        load_release_authority(conn, authority.authority_id)


def test_권위_subclass가_검사를_덮어써도_저장하거나_상속할_수_없다(
    conn: sqlite3.Connection,
    content,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    delivery = _delivery(
        public_id="authority-subclass",
        bucket="bucket-subclass",
        content=content,
        now=now,
        reused=False,
    )
    save_delivery(conn, delivery)
    artifact = _stored_pdf(
        conn,
        content=content,
        now=now,
        root=tmp_path / "blobs",
    )
    bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=artifact.artifact_id,
    )
    authority = _owner(
        delivery=delivery,
        artifact_id=artifact.artifact_id,
        content=content,
        now=now,
    )

    class ForgedAuthority(ReleaseAuthority):
        pass

    forged = ForgedAuthority(**authority.__dict__)
    with pytest.raises(TypeError, match="ReleaseAuthority"):
        save_release_authority(conn, forged)
    with pytest.raises(TypeError, match="원본"):
        ReleaseAuthority.issue_reuse(
            origin=forged,
            public_id="authority-subclass-reuse",
            delivery_id="delivery-forged",
            billing_bucket_id="bucket-subclass",
            automatic_release_sha256=authority.automatic_release_sha256,
            charge_run_id="charge:authority-subclass-reuse",
            charge_decision_sha256=_digest("3"),
            issued_at=now,
        )
