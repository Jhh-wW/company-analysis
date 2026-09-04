"""검증된 생성 증거와 실제 본문·PDF·전달을 한 불변 출고 권위로 묶는다.

이 원장의 행이 있다고 해서 생성기가 스스로 공개를 허가할 수 있는 것은 아니다.
웹 출고 경계가 품질 증거를 재검산하고 실제 Content·Delivery·PDF를 같은 SQLite
거래에 쓴 뒤에만 이 행을 마지막으로 추가한다. 캐시와 single-flight waiter는
본문 값이 같다는 이유가 아니라, 원본 권위가 같은 content·artifact를 가리킬 때만
재사용해야 한다.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Final

from src.features.report_delivery import artifact as artifact_store
from src.features.report_delivery import store as lifecycle_store
from src.features.report_delivery.canonical import (
    canonical_digest,
    datetime_from_utc_text,
    require_aware,
    require_sha256_hex,
    utc_text,
)


RELEASE_AUTHORITY_VERSION: Final[str] = "report-release-authority-v1"
TABLE_RELEASE_AUTHORITIES: Final[str] = "report_delivery_release_authorities"


class ReleaseAuthorityKind(str, Enum):
    OWNER = "owner"
    REUSE = "reuse"


class ReleaseAuthorityError(RuntimeError):
    """출고 권위의 불변 결속을 증명하지 못했다."""


class ReleaseAuthorityConflict(ReleaseAuthorityError):
    """이미 사용한 공개 ID나 권위 ID를 다른 값으로 덮으려 했다."""


class ReleaseAuthorityCorrupt(ReleaseAuthorityError):
    """저장된 권위 행과 실제 delivery/content/artifact가 서로 다르다."""


def _required_text(value: object, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label}이 필요합니다")
    return normalized


def _authority_payload(
    *,
    kind: ReleaseAuthorityKind,
    public_id: str,
    delivery_id: str,
    company_id: str,
    billing_bucket_id: str,
    content_snapshot_id: str,
    artifact_id: str,
    report_payload_sha256: str,
    producer_evidence_sha256: str,
    assessment_sha256: str,
    public_content_sha256: str,
    public_manifest_sha256: str,
    evidence_generation_sha256: str,
    build_identity_sha256: str,
    automatic_release_sha256: str,
    charge_run_id: str,
    charge_decision_sha256: str,
    origin_authority_id: str,
    issued_at: dt.datetime,
) -> dict[str, object]:
    return {
        "version": RELEASE_AUTHORITY_VERSION,
        "kind": kind.value,
        "public_id": public_id,
        "delivery_id": delivery_id,
        "company_id": company_id,
        "billing_bucket_id": billing_bucket_id,
        "content_snapshot_id": content_snapshot_id,
        "artifact_id": artifact_id,
        "report_payload_sha256": report_payload_sha256,
        "producer_evidence_sha256": producer_evidence_sha256,
        "assessment_sha256": assessment_sha256,
        "public_content_sha256": public_content_sha256,
        "public_manifest_sha256": public_manifest_sha256,
        "evidence_generation_sha256": evidence_generation_sha256,
        "build_identity_sha256": build_identity_sha256,
        "automatic_release_sha256": automatic_release_sha256,
        "charge_run_id": charge_run_id,
        "charge_decision_sha256": charge_decision_sha256,
        "origin_authority_id": origin_authority_id,
        "issued_at": utc_text(issued_at, label="출고 권위 발급"),
    }


@dataclass(frozen=True)
class ReleaseAuthority:
    """새 생성 또는 승인 원본 재사용 한 건의 내용주소형 출고 영수증."""

    authority_id: str
    kind: ReleaseAuthorityKind
    public_id: str
    delivery_id: str
    company_id: str
    billing_bucket_id: str
    content_snapshot_id: str
    artifact_id: str
    report_payload_sha256: str
    producer_evidence_sha256: str
    assessment_sha256: str
    public_content_sha256: str
    public_manifest_sha256: str
    evidence_generation_sha256: str
    build_identity_sha256: str
    automatic_release_sha256: str
    charge_run_id: str
    charge_decision_sha256: str
    origin_authority_id: str
    issued_at: dt.datetime
    version: str = RELEASE_AUTHORITY_VERSION

    def __post_init__(self) -> None:
        if self.version != RELEASE_AUTHORITY_VERSION:
            raise ValueError("지원하지 않는 출고 권위 버전입니다")
        if not isinstance(self.kind, ReleaseAuthorityKind):
            raise TypeError("출고 권위 종류는 닫힌 enum이어야 합니다")
        text_fields = (
            "public_id",
            "delivery_id",
            "company_id",
            "billing_bucket_id",
            "content_snapshot_id",
            "artifact_id",
            "charge_run_id",
        )
        for name in text_fields:
            object.__setattr__(
                self,
                name,
                _required_text(getattr(self, name), label=name),
            )
        digest_fields = (
            "report_payload_sha256",
            "producer_evidence_sha256",
            "assessment_sha256",
            "public_content_sha256",
            "public_manifest_sha256",
            "evidence_generation_sha256",
            "build_identity_sha256",
            "automatic_release_sha256",
            "charge_decision_sha256",
        )
        for name in digest_fields:
            object.__setattr__(
                self,
                name,
                require_sha256_hex(getattr(self, name), label=name),
            )
        issued_at = require_aware(self.issued_at, label="출고 권위 발급")
        object.__setattr__(self, "issued_at", issued_at)
        origin = str(self.origin_authority_id or "").strip()
        if self.kind is ReleaseAuthorityKind.OWNER:
            if origin:
                raise ValueError("새 생성 권위에는 재사용 원본이 없어야 합니다")
        elif not origin or origin == self.authority_id:
            raise ValueError("재사용 권위에는 다른 원본 권위 ID가 필요합니다")
        object.__setattr__(self, "origin_authority_id", origin)
        payload = _authority_payload(
            kind=self.kind,
            public_id=self.public_id,
            delivery_id=self.delivery_id,
            company_id=self.company_id,
            billing_bucket_id=self.billing_bucket_id,
            content_snapshot_id=self.content_snapshot_id,
            artifact_id=self.artifact_id,
            report_payload_sha256=self.report_payload_sha256,
            producer_evidence_sha256=self.producer_evidence_sha256,
            assessment_sha256=self.assessment_sha256,
            public_content_sha256=self.public_content_sha256,
            public_manifest_sha256=self.public_manifest_sha256,
            evidence_generation_sha256=self.evidence_generation_sha256,
            build_identity_sha256=self.build_identity_sha256,
            automatic_release_sha256=self.automatic_release_sha256,
            charge_run_id=self.charge_run_id,
            charge_decision_sha256=self.charge_decision_sha256,
            origin_authority_id=origin,
            issued_at=issued_at,
        )
        expected = "authority_" + canonical_digest(payload)
        if self.authority_id != expected:
            raise ValueError("출고 권위 ID가 결속된 값의 지문과 다릅니다")

    @classmethod
    def issue_owner(
        cls,
        *,
        public_id: str,
        delivery_id: str,
        company_id: str,
        billing_bucket_id: str,
        content_snapshot_id: str,
        artifact_id: str,
        report_payload_sha256: str,
        producer_evidence_sha256: str,
        assessment_sha256: str,
        public_content_sha256: str,
        public_manifest_sha256: str,
        evidence_generation_sha256: str,
        build_identity_sha256: str,
        automatic_release_sha256: str,
        charge_run_id: str,
        charge_decision_sha256: str,
        issued_at: dt.datetime,
    ) -> "ReleaseAuthority":
        values = {
            "kind": ReleaseAuthorityKind.OWNER,
            "public_id": _required_text(public_id, label="public_id"),
            "delivery_id": _required_text(delivery_id, label="delivery_id"),
            "company_id": _required_text(company_id, label="company_id"),
            "billing_bucket_id": _required_text(
                billing_bucket_id,
                label="billing_bucket_id",
            ),
            "content_snapshot_id": _required_text(
                content_snapshot_id,
                label="content_snapshot_id",
            ),
            "artifact_id": _required_text(artifact_id, label="artifact_id"),
            "report_payload_sha256": require_sha256_hex(
                report_payload_sha256,
                label="report_payload_sha256",
            ),
            "producer_evidence_sha256": require_sha256_hex(
                producer_evidence_sha256,
                label="producer_evidence_sha256",
            ),
            "assessment_sha256": require_sha256_hex(
                assessment_sha256,
                label="assessment_sha256",
            ),
            "public_content_sha256": require_sha256_hex(
                public_content_sha256,
                label="public_content_sha256",
            ),
            "public_manifest_sha256": require_sha256_hex(
                public_manifest_sha256,
                label="public_manifest_sha256",
            ),
            "evidence_generation_sha256": require_sha256_hex(
                evidence_generation_sha256,
                label="evidence_generation_sha256",
            ),
            "build_identity_sha256": require_sha256_hex(
                build_identity_sha256,
                label="build_identity_sha256",
            ),
            "automatic_release_sha256": require_sha256_hex(
                automatic_release_sha256,
                label="automatic_release_sha256",
            ),
            "charge_run_id": _required_text(charge_run_id, label="charge_run_id"),
            "charge_decision_sha256": require_sha256_hex(
                charge_decision_sha256,
                label="charge_decision_sha256",
            ),
            "origin_authority_id": "",
            "issued_at": require_aware(issued_at, label="출고 권위 발급"),
        }
        return cls(
            authority_id="authority_" + canonical_digest(
                _authority_payload(**values)
            ),
            **values,
        )

    @classmethod
    def issue_reuse(
        cls,
        *,
        origin: "ReleaseAuthority",
        public_id: str,
        delivery_id: str,
        billing_bucket_id: str,
        automatic_release_sha256: str,
        charge_run_id: str,
        charge_decision_sha256: str,
        issued_at: dt.datetime,
    ) -> "ReleaseAuthority":
        if type(origin) is not ReleaseAuthority:
            raise TypeError("재사용에는 검증된 원본 출고 권위가 필요합니다")
        if origin.kind is not ReleaseAuthorityKind.OWNER:
            raise ValueError("재사용 권위는 최초 생성 owner 권위만 상속할 수 있습니다")
        clean_bucket = _required_text(
            billing_bucket_id,
            label="billing_bucket_id",
        )
        if clean_bucket != origin.billing_bucket_id:
            raise ValueError("재사용 권위의 비용 통장이 원본 권위와 다릅니다")
        reuse_issued_at = require_aware(issued_at, label="출고 권위 발급")
        if reuse_issued_at < origin.issued_at:
            raise ValueError("재사용 권위가 원본 권위보다 먼저 발급될 수 없습니다")
        if (
            str(public_id).strip() == origin.public_id
            or str(delivery_id).strip() == origin.delivery_id
        ):
            raise ValueError("재사용 권위에는 새 공개 ID와 delivery가 필요합니다")
        clean_charge_run_id = _required_text(
            charge_run_id,
            label="charge_run_id",
        )
        if clean_charge_run_id == origin.charge_run_id:
            raise ValueError("재사용 출고는 원본 조사의 청구 행을 다시 쓸 수 없습니다")
        reuse_release_sha256 = require_sha256_hex(
            automatic_release_sha256,
            label="automatic_release_sha256",
        )
        if reuse_release_sha256 != origin.automatic_release_sha256:
            raise ValueError("재사용 출고는 원본과 같은 자동승인 영수증을 써야 합니다")
        values = {
            "kind": ReleaseAuthorityKind.REUSE,
            "public_id": _required_text(public_id, label="public_id"),
            "delivery_id": _required_text(delivery_id, label="delivery_id"),
            "company_id": origin.company_id,
            "billing_bucket_id": clean_bucket,
            "content_snapshot_id": origin.content_snapshot_id,
            "artifact_id": origin.artifact_id,
            "report_payload_sha256": origin.report_payload_sha256,
            "producer_evidence_sha256": origin.producer_evidence_sha256,
            "assessment_sha256": origin.assessment_sha256,
            "public_content_sha256": origin.public_content_sha256,
            "public_manifest_sha256": origin.public_manifest_sha256,
            "evidence_generation_sha256": origin.evidence_generation_sha256,
            "build_identity_sha256": origin.build_identity_sha256,
            "automatic_release_sha256": reuse_release_sha256,
            "charge_run_id": clean_charge_run_id,
            "charge_decision_sha256": require_sha256_hex(
                charge_decision_sha256,
                label="charge_decision_sha256",
            ),
            "origin_authority_id": origin.authority_id,
            "issued_at": reuse_issued_at,
        }
        return cls(
            authority_id="authority_" + canonical_digest(
                _authority_payload(**values)
            ),
            **values,
        )


_SCHEMA: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_RELEASE_AUTHORITIES} (
        authority_id                TEXT PRIMARY KEY,
        version                     TEXT NOT NULL,
        kind                        TEXT NOT NULL CHECK(kind IN ('owner', 'reuse')),
        public_id                   TEXT NOT NULL UNIQUE,
        delivery_id                 TEXT NOT NULL UNIQUE
                                    REFERENCES {lifecycle_store.TABLE_DELIVERIES}(delivery_id),
        company_id                  TEXT NOT NULL,
        billing_bucket_id           TEXT NOT NULL,
        content_snapshot_id         TEXT NOT NULL
                                    REFERENCES {lifecycle_store.TABLE_CONTENT_SNAPSHOTS}(content_id),
        artifact_id                 TEXT NOT NULL
                                    REFERENCES {artifact_store.TABLE_ARTIFACTS}(artifact_id),
        report_payload_sha256       TEXT NOT NULL,
        producer_evidence_sha256    TEXT NOT NULL,
        assessment_sha256           TEXT NOT NULL,
        public_content_sha256       TEXT NOT NULL,
        public_manifest_sha256      TEXT NOT NULL,
        evidence_generation_sha256  TEXT NOT NULL,
        build_identity_sha256       TEXT NOT NULL,
        automatic_release_sha256    TEXT NOT NULL,
        charge_run_id               TEXT NOT NULL UNIQUE,
        charge_decision_sha256      TEXT NOT NULL,
        origin_authority_id         TEXT NOT NULL,
        issued_at                   TEXT NOT NULL
    )
    """,
    f"""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_report_release_owner_content_artifact
    ON {TABLE_RELEASE_AUTHORITIES}(content_snapshot_id, artifact_id)
    WHERE kind = 'owner'
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_release_authorities_valid_binding
    BEFORE INSERT ON {TABLE_RELEASE_AUTHORITIES}
    WHEN NOT EXISTS (
        SELECT 1
        FROM {lifecycle_store.TABLE_DELIVERIES} AS deliveries
        JOIN {lifecycle_store.TABLE_CONTENT_SNAPSHOTS} AS contents
          ON contents.content_id = deliveries.content_snapshot_id
        JOIN {artifact_store.TABLE_DELIVERY_ARTIFACTS} AS bindings
          ON bindings.delivery_id = deliveries.delivery_id
         AND bindings.channel = 'pdf'
        JOIN {artifact_store.TABLE_ARTIFACTS} AS artifacts
          ON artifacts.artifact_id = bindings.artifact_id
        WHERE deliveries.delivery_id = NEW.delivery_id
          AND deliveries.public_id = NEW.public_id
          AND deliveries.billing_bucket_id = NEW.billing_bucket_id
          AND deliveries.content_snapshot_id = NEW.content_snapshot_id
          AND contents.payload_sha256 = NEW.report_payload_sha256
          AND artifacts.artifact_id = NEW.artifact_id
          AND artifacts.content_snapshot_id = NEW.content_snapshot_id
          AND artifacts.channel = 'pdf'
          AND artifacts.original_state = 'stored'
          AND artifacts.blob_key <> ''
          AND length(artifacts.bytes_sha256) = 64
          AND (
              (NEW.kind = 'owner' AND deliveries.cache_origin_content_id = '')
              OR (
                  NEW.kind = 'reuse'
                  AND deliveries.cache_origin_content_id = NEW.content_snapshot_id
              )
          )
    )
    BEGIN
        SELECT RAISE(ABORT, 'release authority storage binding mismatch');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_release_authorities_valid_origin
    BEFORE INSERT ON {TABLE_RELEASE_AUTHORITIES}
    WHEN NEW.kind = 'reuse' AND NOT EXISTS (
        SELECT 1
        FROM {TABLE_RELEASE_AUTHORITIES} AS origin
        WHERE origin.authority_id = NEW.origin_authority_id
          AND origin.kind = 'owner'
          AND origin.company_id = NEW.company_id
          AND origin.billing_bucket_id = NEW.billing_bucket_id
          AND origin.content_snapshot_id = NEW.content_snapshot_id
          AND origin.artifact_id = NEW.artifact_id
          AND origin.report_payload_sha256 = NEW.report_payload_sha256
          AND origin.producer_evidence_sha256 = NEW.producer_evidence_sha256
          AND origin.assessment_sha256 = NEW.assessment_sha256
          AND origin.public_content_sha256 = NEW.public_content_sha256
          AND origin.public_manifest_sha256 = NEW.public_manifest_sha256
          AND origin.evidence_generation_sha256 = NEW.evidence_generation_sha256
          AND origin.build_identity_sha256 = NEW.build_identity_sha256
          AND origin.automatic_release_sha256 = NEW.automatic_release_sha256
    )
    BEGIN
        SELECT RAISE(ABORT, 'release authority origin mismatch');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_release_authorities_owner_has_no_origin
    BEFORE INSERT ON {TABLE_RELEASE_AUTHORITIES}
    WHEN (NEW.kind = 'owner' AND NEW.origin_authority_id <> '')
      OR (NEW.kind = 'reuse' AND NEW.origin_authority_id = '')
    BEGIN
        SELECT RAISE(ABORT, 'release authority origin shape mismatch');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_release_authorities_no_update
    BEFORE UPDATE ON {TABLE_RELEASE_AUTHORITIES}
    BEGIN
        SELECT RAISE(ABORT, 'release authority is immutable');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_release_authorities_no_delete
    BEFORE DELETE ON {TABLE_RELEASE_AUTHORITIES}
    BEGIN
        SELECT RAISE(ABORT, 'release authority is immutable');
    END
    """,
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """출고 권위 표와 DB 자체 결속·불변 trigger를 멱등 생성한다."""

    for statement in _SCHEMA:
        conn.execute(statement)


def _row_values(authority: ReleaseAuthority) -> tuple[object, ...]:
    return (
        authority.authority_id,
        authority.version,
        authority.kind.value,
        authority.public_id,
        authority.delivery_id,
        authority.company_id,
        authority.billing_bucket_id,
        authority.content_snapshot_id,
        authority.artifact_id,
        authority.report_payload_sha256,
        authority.producer_evidence_sha256,
        authority.assessment_sha256,
        authority.public_content_sha256,
        authority.public_manifest_sha256,
        authority.evidence_generation_sha256,
        authority.build_identity_sha256,
        authority.automatic_release_sha256,
        authority.charge_run_id,
        authority.charge_decision_sha256,
        authority.origin_authority_id,
        utc_text(authority.issued_at, label="출고 권위 발급"),
    )


def save_release_authority(
    conn: sqlite3.Connection,
    authority: ReleaseAuthority,
) -> ReleaseAuthority:
    """실제 저장 객체에 결속된 권위를 최초 한 번만 저장하고 재조회한다."""

    if type(authority) is not ReleaseAuthority:
        raise TypeError("저장할 ReleaseAuthority가 필요합니다")
    ensure_schema(conn)
    values = _row_values(authority)
    try:
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE_RELEASE_AUTHORITIES} (
                authority_id, version, kind, public_id, delivery_id, company_id,
                billing_bucket_id, content_snapshot_id, artifact_id,
                report_payload_sha256, producer_evidence_sha256,
                assessment_sha256, public_content_sha256,
                public_manifest_sha256, evidence_generation_sha256,
                build_identity_sha256, automatic_release_sha256,
                charge_run_id, charge_decision_sha256,
                origin_authority_id, issued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ReleaseAuthorityConflict("출고 권위를 실제 저장 객체에 결속하지 못했습니다") from exc
    if cursor.rowcount == 1:
        # 정확히 이번 INSERT가 처음 성공했을 때만 결속 표를 잠근다. 멱등
        # 재시도(rowcount=0, 같은 값)에서 다시 잠그면 이미 release_locked=1인
        # 행을 또 UPDATE하게 되어 store.py·artifact.py의 자기잠금 트리거
        # 자체에 걸린다 — 그래서 "최초 성공"에서만 한 번 잠근다.
        _lock_release_bindings(conn, authority)
    else:
        existing = conn.execute(
            f"SELECT * FROM {TABLE_RELEASE_AUTHORITIES} WHERE authority_id = ?",
            (authority.authority_id,),
        ).fetchone()
        if existing is None or tuple(existing) != values:
            raise ReleaseAuthorityConflict("같은 출고 권위 ID를 다른 값으로 덮을 수 없습니다")
    stored = load_release_authority(conn, authority.authority_id)
    if stored != authority:
        raise ReleaseAuthorityCorrupt("저장 직후 같은 출고 권위를 다시 읽지 못했습니다")
    return stored


def _lock_release_bindings(conn: sqlite3.Connection, authority: ReleaseAuthority) -> None:
    """content·delivery·artifact 결속을 발급 즉시 잠가 이후 raw UPDATE를 막는다.

    store.py·artifact.py 표의 ``release_locked`` 컬럼을 0에서 1로 뒤집는다.
    ``WHERE ... release_locked = 0``로 이미 잠긴 행은 이 UPDATE의 대상에서
    아예 제외한다 — REUSE 권위는 OWNER와 같은 content_snapshot_id·
    artifact_id를 공유하므로(delivery_id만 새로 만든다), 대상에서 빼지
    않으면 두 번째 잠금 시도가 각 표 자신의 BEFORE UPDATE 트리거에 걸린다.
    각 표 자신의 트리거가 그 뒤 모든 실제 내용 변경 UPDATE를 막으므로,
    호출자는 최초 성공 INSERT 때만 이 함수를 불러야 한다.
    """

    conn.execute(
        f"UPDATE {lifecycle_store.TABLE_CONTENT_SNAPSHOTS} "
        "SET release_locked = 1 WHERE content_id = ? AND release_locked = 0",
        (authority.content_snapshot_id,),
    )
    conn.execute(
        f"UPDATE {lifecycle_store.TABLE_DELIVERIES} "
        "SET release_locked = 1 WHERE delivery_id = ? AND release_locked = 0",
        (authority.delivery_id,),
    )
    conn.execute(
        f"UPDATE {artifact_store.TABLE_ARTIFACTS} "
        "SET release_locked = 1 WHERE artifact_id = ? AND release_locked = 0",
        (authority.artifact_id,),
    )


def _from_row(row: sqlite3.Row | tuple[object, ...]) -> ReleaseAuthority:
    try:
        return ReleaseAuthority(
            authority_id=str(row[0]),
            version=str(row[1]),
            kind=ReleaseAuthorityKind(str(row[2])),
            public_id=str(row[3]),
            delivery_id=str(row[4]),
            company_id=str(row[5]),
            billing_bucket_id=str(row[6]),
            content_snapshot_id=str(row[7]),
            artifact_id=str(row[8]),
            report_payload_sha256=str(row[9]),
            producer_evidence_sha256=str(row[10]),
            assessment_sha256=str(row[11]),
            public_content_sha256=str(row[12]),
            public_manifest_sha256=str(row[13]),
            evidence_generation_sha256=str(row[14]),
            build_identity_sha256=str(row[15]),
            automatic_release_sha256=str(row[16]),
            charge_run_id=str(row[17]),
            charge_decision_sha256=str(row[18]),
            origin_authority_id=str(row[19]),
            issued_at=datetime_from_utc_text(row[20], label="출고 권위 발급"),
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseAuthorityCorrupt("저장된 출고 권위가 손상됐습니다") from exc


def _assert_storage_binding(
    conn: sqlite3.Connection,
    authority: ReleaseAuthority,
) -> None:
    row = conn.execute(
        f"""
        SELECT 1
        FROM {lifecycle_store.TABLE_DELIVERIES} AS deliveries
        JOIN {lifecycle_store.TABLE_CONTENT_SNAPSHOTS} AS contents
          ON contents.content_id = deliveries.content_snapshot_id
        JOIN {artifact_store.TABLE_DELIVERY_ARTIFACTS} AS bindings
          ON bindings.delivery_id = deliveries.delivery_id
         AND bindings.channel = 'pdf'
        JOIN {artifact_store.TABLE_ARTIFACTS} AS artifacts
          ON artifacts.artifact_id = bindings.artifact_id
        WHERE deliveries.delivery_id = ?
          AND deliveries.public_id = ?
          AND deliveries.billing_bucket_id = ?
          AND deliveries.content_snapshot_id = ?
          AND contents.payload_sha256 = ?
          AND artifacts.artifact_id = ?
          AND artifacts.content_snapshot_id = ?
          AND artifacts.channel = 'pdf'
          AND artifacts.original_state = 'stored'
          AND artifacts.blob_key <> ''
          AND length(artifacts.bytes_sha256) = 64
          AND (
              (? = 'owner' AND deliveries.cache_origin_content_id = '')
              OR (
                  ? = 'reuse'
                  AND deliveries.cache_origin_content_id = ?
              )
          )
        """,
        (
            authority.delivery_id,
            authority.public_id,
            authority.billing_bucket_id,
            authority.content_snapshot_id,
            authority.report_payload_sha256,
            authority.artifact_id,
            authority.content_snapshot_id,
            authority.kind.value,
            authority.kind.value,
            authority.content_snapshot_id,
        ),
    ).fetchone()
    if row is None:
        raise ReleaseAuthorityCorrupt("출고 권위와 delivery/content/PDF 결속이 깨졌습니다")
    if authority.kind is ReleaseAuthorityKind.REUSE:
        origin = load_release_authority(conn, authority.origin_authority_id)
        if origin is None or any(
            getattr(origin, field) != getattr(authority, field)
            for field in (
                "company_id",
                "billing_bucket_id",
                "content_snapshot_id",
                "artifact_id",
                "report_payload_sha256",
                "producer_evidence_sha256",
                "assessment_sha256",
                "public_content_sha256",
                "public_manifest_sha256",
                "evidence_generation_sha256",
                "build_identity_sha256",
                "automatic_release_sha256",
            )
        ):
            raise ReleaseAuthorityCorrupt("재사용 권위가 정확한 원본 권위와 다릅니다")


def load_release_authority(
    conn: sqlite3.Connection,
    authority_id: str,
) -> ReleaseAuthority | None:
    ensure_schema(conn)
    row = conn.execute(
        f"SELECT * FROM {TABLE_RELEASE_AUTHORITIES} WHERE authority_id = ?",
        (str(authority_id).strip(),),
    ).fetchone()
    if row is None:
        return None
    authority = _from_row(row)
    _assert_storage_binding(conn, authority)
    return authority


def load_release_authority_by_public_id(
    conn: sqlite3.Connection,
    public_id: str,
) -> ReleaseAuthority | None:
    ensure_schema(conn)
    row = conn.execute(
        f"SELECT authority_id FROM {TABLE_RELEASE_AUTHORITIES} WHERE public_id = ?",
        (str(public_id).strip(),),
    ).fetchone()
    return None if row is None else load_release_authority(conn, str(row[0]))


def load_owner_authority(
    conn: sqlite3.Connection,
    *,
    content_snapshot_id: str,
    artifact_id: str,
) -> ReleaseAuthority | None:
    """캐시/waiter가 상속해야 할 최초 생성 권위 한 건만 읽는다."""

    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT authority_id FROM {TABLE_RELEASE_AUTHORITIES}
        WHERE kind = 'owner' AND content_snapshot_id = ? AND artifact_id = ?
        """,
        (str(content_snapshot_id).strip(), str(artifact_id).strip()),
    ).fetchone()
    return None if row is None else load_release_authority(conn, str(row[0]))


__all__ = [
    "RELEASE_AUTHORITY_VERSION",
    "TABLE_RELEASE_AUTHORITIES",
    "ReleaseAuthority",
    "ReleaseAuthorityConflict",
    "ReleaseAuthorityCorrupt",
    "ReleaseAuthorityError",
    "ReleaseAuthorityKind",
    "ensure_schema",
    "load_owner_authority",
    "load_release_authority",
    "load_release_authority_by_public_id",
    "save_release_authority",
]
