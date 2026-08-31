"""웹 완료·조회 경계와 report_delivery 수직 슬라이스를 잇는 adapter.

report_delivery 자체는 storage·composer·PDF feature를 import하지 않는다.
이 파일이 각 feature의 DTO와 bytes만 조립해 경계를 유지한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.export_pdf.automatic_release import report_sha256
from src.features.export_pdf import constants as pdf_constants
from src.features.export_pdf import release_store as pdf_release_store
from src.features.pipeline.port import Report
from src.features.provenance.sources import Source, SourceKind
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import retention as delivery_retention
from src.features.report_delivery import singleflight as delivery_singleflight
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheLookupKey, CacheNamespace
from src.features.report_delivery.models import (
    ContentSnapshot,
    Delivery,
    DeliveryPolicy,
)
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared.automatic_release_record import AUTOMATIC_CHECKER_VERSION
from src.shared.automatic_release_record import AutomaticReleaseRecord
from src.shared.report_source_identity import (
    ReportSourceIdentity,
    normalize_dart_receipt_numbers,
)


_ADAPTER_VERSION: Final[str] = "web-completion-v1"
_ARTIFACT_DIR_NAME: Final[str] = "report-artifacts"
_ARTIFACT_CAPACITY_ENV: Final[str] = "REPORT_ARTIFACT_CAPACITY_BYTES"
_DATA_ROOT_ENV: Final[str] = "APP_DATA_ROOT"
_DETERMINISTIC_MODEL_ID: Final[str] = "deterministic-no-provider"
_UNCACHEABLE_LOCAL_RELEASE_ID: Final[str] = "unverified-local-no-cache-v1"
_DART_RECEIPT_LENGTH: Final[int] = 14
_KST: Final[dt.tzinfo] = dt.timezone(dt.timedelta(hours=9))


class DeliveryAdapterError(RuntimeError):
    """웹 전달 경계에서 불변 snapshot을 확정하거나 읽지 못했다."""


@dataclass(frozen=True)
class PublicDelivery:
    """한 공개 ID에 고정된 본문·PDF 조회 결과."""

    delivery: Delivery
    content: ContentSnapshot
    report: Report
    artifact: delivery_artifact.ArtifactMetadata | None
    inspection: delivery_artifact.ArtifactInspection | None


@dataclass(frozen=True)
class LegacyPublicReport:
    """불변 delivery 도입 전에 저장된 본문 한 벌.

    ``payload_json``은 조회 뒤 현재 공개 게이트나 legacy 정규화 함수가 본문을
    바꾸지 않았음을 시험할 수 있게 함께 보존한다. ``report``는 그 JSON을 화면
    DTO로만 역직렬화한 값이며, 새 출고물로 승격하거나 PDF를 다시 만들 근거가
    아니다.
    """

    report: Report
    payload_json: str
    generated_at: str
    stored_at: str


def require_public_delivery(
    public_id: str,
    *,
    required_at: dt.datetime,
) -> delivery_store.DeliveryIntent:
    """새 보고서임을 artifact 생성보다 먼저 별도 거래로 남긴다."""

    with storage_db.connect() as conn:
        return delivery_store.mark_delivery_required(
            conn,
            public_id=public_id,
            required_at=required_at,
        )


def fail_public_delivery(
    public_id: str,
    *,
    failure_code: str,
    failed_at: dt.datetime,
) -> delivery_store.DeliveryIntent:
    """출고 실패를 원문 없는 기계 코드로 남겨 legacy fallback을 막는다."""

    with storage_db.connect() as conn:
        return delivery_store.mark_delivery_failed(
            conn,
            public_id=public_id,
            failure_code=failure_code,
            failed_at=failed_at,
        )


def load_public_delivery_intent(
    public_id: str,
) -> delivery_store.DeliveryIntent | None:
    """GET 경계가 새 파일·schema 없이 전달 의무만 읽는다."""

    with storage_db.connect_readonly_existing() as conn:
        if conn is None:
            return None
        return delivery_store.load_delivery_intent(conn, public_id)


def configured_artifact_backend() -> delivery_artifact.FilesystemArtifactBlobBackend:
    """영속 데이터 루트 아래의 교체 가능한 파일 backend를 만든다."""

    configured_root = os.environ.get(_DATA_ROOT_ENV, "").strip()
    data_root = (
        Path(configured_root)
        if configured_root
        else storage_db.default_db_path().resolve().parent
    )
    raw_capacity = os.environ.get(_ARTIFACT_CAPACITY_ENV, "").strip()
    capacity: int | None = None
    if raw_capacity:
        try:
            capacity = int(raw_capacity)
        except ValueError as exc:
            raise DeliveryAdapterError("artifact 저장 한도가 숫자가 아닙니다") from exc
        if capacity <= 0:
            raise DeliveryAdapterError("artifact 저장 한도는 0보다 커야 합니다")
    return delivery_artifact.FilesystemArtifactBlobBackend(
        data_root / _ARTIFACT_DIR_NAME,
        capacity_bytes=capacity,
    )


def prepare_approved_pdf_blob_intent(
    backend: delivery_artifact.ArtifactBlobBackend,
    *,
    pdf_bytes: bytes,
    prepared_at: dt.datetime,
) -> delivery_artifact.ArtifactBlobIntent:
    """PDF blob을 쓰기 전 intent를 본 출고 transaction과 별도로 commit한다."""

    with storage_db.connect() as intent_conn:
        intent = delivery_artifact.create_blob_write_intent(
            intent_conn,
            backend,
            pdf_bytes=pdf_bytes,
            created_at=prepared_at,
        )
    # context manager가 commit·close한 뒤에만 반환하므로, 이후
    # 본 transaction rollback이 intent를 지울 수 없다.
    return intent


def reconcile_configured_artifact_blob_intents(
    *,
    now: dt.datetime,
    grace: dt.timedelta = delivery_artifact.BLOB_INTENT_RECONCILE_GRACE,
) -> delivery_artifact.BlobIntentReconcileReport:
    """시작 시 오래된 미결속 blob intent를 전용 즉시 transaction으로 정리한다."""

    backend = configured_artifact_backend()
    with storage_db.connect() as conn:
        # schema bootstrap에서 시작된 거래가 있을 수 있어 전용 연결의
        # bootstrap만 먼저 확정한다. 사용자 데이터 거래를 가로채지 않는다.
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        return delivery_artifact.reconcile_blob_write_intents(
            conn,
            backend,
            now=now,
            grace=grace,
        )


def _release_identity(
    identity: build_identity_contract.EngineBuildIdentity | None = None,
) -> tuple[str, str]:
    identity = identity or build_identity_contract.capture_engine_build_identity()
    if identity.cache_usable:
        # 한 raw snapshot에서 revision과 contract-version build ID를 함께 만든다.
        return (
            identity.deployment_revision,
            f"generator-build:{identity.build_id}",
        )
    # full commit이 없어도 새 보고서와 PDF 자체는 저장할 수 있어야 한다.
    # 이 값은 composer build id로 usable하지 않고, 정식 cache_namespace가 없는
    # 출고는 cache entry를 결속하지 않으므로 로컬 캐시 권위가 되지 않는다.
    return "", _UNCACHEABLE_LOCAL_RELEASE_ID


def _assert_frozen_identity_is_current(
    identity: build_identity_contract.EngineBuildIdentity,
) -> None:
    """정상 배포에서 시작한 생성이 다른 배포에서 출고되지 않게 막는다.

    unknown은 정상 commit으로 승격하지 않는다. 해당 Job의 release는 계속 로컬
    sentinel이고 캐시 결속·재사용이 금지된다.
    """

    try:
        build_identity_contract.assert_engine_build_identity_current(identity)
    except (
        TypeError,
        build_identity_contract.EngineBuildIdentityChangedError,
    ) as exc:
        raise DeliveryAdapterError(str(exc).replace("저장 시점", "출고 시점")) from exc


def _is_unverified_local_release(release: tuple[str, str]) -> bool:
    revision, image = release
    return not revision and image == _UNCACHEABLE_LOCAL_RELEASE_ID


def _namespace_matches_release(
    namespace: CacheNamespace,
    release: tuple[str, str],
) -> bool:
    """호출자가 준 namespace가 adapter가 직접 읽은 배포와 정확히 같은가."""

    revision, image = release
    return bool(
        revision
        and image != _UNCACHEABLE_LOCAL_RELEASE_ID
        and namespace.deployment_revision == revision
        and namespace.image_digest == image
    )


def _model_mapping(actual_models: tuple[str, ...]) -> dict[str, str]:
    models = tuple(
        dict.fromkeys(
            str(model).strip() for model in actual_models if str(model).strip()
        )
    )
    if not models:
        return {"pipeline": _DETERMINISTIC_MODEL_ID}
    return {f"model_{position}": model for position, model in enumerate(models, 1)}


def _source_date(report: Report, *, fallback: dt.date) -> dt.date:
    for value in (report.as_of_date, report.generated_at):
        try:
            return dt.date.fromisoformat(str(value).strip())
        except ValueError:
            continue
    return fallback


def _content_generated_at(
    report: Report,
    *,
    fallback: dt.datetime,
) -> dt.datetime:
    """본문 생성일과 새 링크 전달일을 같은 시각으로 덮어쓰지 않는다."""

    raw = str(report.generated_at).strip()
    if not raw:
        return fallback
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        # 현재 저장 계약의 날짜-only 값만 KST 자정으로 확장한다. 시간대 없는
        # 임의 시각을 서버 지역 시간이라고 추정하지 않는다.
        if len(raw) == 10 and parsed.time() == dt.time.min:
            return parsed.replace(tzinfo=_KST)
        return fallback
    return parsed


def _source_ids(report: Report) -> tuple[tuple[str, ...], tuple[str, ...]]:
    receipts: set[str] = set()
    documents: set[str] = set()
    for citation in report.citations:
        if not isinstance(citation, Source):
            continue
        document_id = citation.document_id.strip()
        host = citation.host.strip().lower()
        if document_id:
            documents.add(f"{host or 'unknown-host'}:{document_id}")
        if (
            citation.kind is SourceKind.FILING
            and host == "dart.fss.or.kr"
            and len(document_id) == _DART_RECEIPT_LENGTH
            and document_id.isdigit()
        ):
            receipts.add(document_id)
    return tuple(sorted(receipts)), tuple(sorted(documents))


def _font_bundle_version() -> str:
    digest = hashlib.sha256()
    for path in (
        pdf_constants.FONT_REGULAR_PATH,
        pdf_constants.FONT_SEMIBOLD_PATH,
    ):
        try:
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
        except OSError as exc:
            raise DeliveryAdapterError("PDF font bundle 신원을 읽지 못했습니다") from exc
    return "sha256:" + digest.hexdigest()


def _renderer_version(release: tuple[str, str] | None = None) -> str:
    revision, build = release or _release_identity()
    return f"deployment:{revision}" if revision else build


def persist_approved_delivery(
    conn: sqlite3.Connection,
    backend: delivery_artifact.ArtifactBlobBackend,
    *,
    blob_intent: delivery_artifact.ArtifactBlobIntent,
    public_id: str,
    corp_id: str,
    billing_bucket_id: str,
    report: Report,
    pdf_bytes: bytes,
    completed_at: dt.datetime,
    actual_models: tuple[str, ...],
    reused_from_cache: bool,
    dart_receipt_numbers: tuple[str, ...] = (),
    financial_payload_digest: str = "",
    cache_namespace: CacheNamespace | None = None,
    preflight_identity_digest: str = "",
    bind_cache_entry: bool,
    engine_build_identity: build_identity_contract.EngineBuildIdentity | None = None,
) -> PublicDelivery:
    """자동검사가 승인한 본문·delivery·PDF를 한 DB 거래에 결속한다.

    ``bind_cache_entry=False``여도 현재 사용자의 불변 Delivery와 PDF는 모두
    저장한다. 단지 이 결과를 이후 새 조사에서 장기 재사용하는 열쇠만 만들지 않는다.
    """

    if not str(corp_id).strip():
        raise DeliveryAdapterError("회사 고유번호가 없어 출처 신원을 확정할 수 없습니다")
    cache_action = bool(
        cache_namespace is not None
        or str(preflight_identity_digest).strip()
        or bind_cache_entry
        or reused_from_cache
    )
    if engine_build_identity is None and cache_action:
        raise DeliveryAdapterError(
            "캐시 출고에는 생성 시작 때 고정한 엔진 빌드 신원이 필요합니다"
        )
    frozen_identity = (
        engine_build_identity
        or build_identity_contract.capture_engine_build_identity()
    )
    if engine_build_identity is not None:
        _assert_frozen_identity_is_current(frozen_identity)
    release = _release_identity(frozen_identity)
    unverified_local = _is_unverified_local_release(release)
    if unverified_local and (
        cache_namespace is not None
        or bool(str(preflight_identity_digest).strip())
        or bind_cache_entry
        or reused_from_cache
    ):
        raise DeliveryAdapterError(
            "검증되지 않은 로컬 출고는 캐시 결속·재사용을 허용하지 않습니다"
        )
    if cache_namespace is not None and not _namespace_matches_release(
        cache_namespace,
        release,
    ):
        raise DeliveryAdapterError(
            "캐시 namespace가 현재 검증된 배포 신원과 다릅니다"
        )
    if bool(cache_namespace) != bool(str(preflight_identity_digest).strip()):
        raise DeliveryAdapterError(
            "정식 캐시에는 생성기 namespace와 사전 출처 지문이 함께 필요합니다"
        )
    existing = delivery_store.load_delivery_by_public_id(conn, public_id)
    payload = report_store.report_to_json(report).encode("utf-8")
    if existing is not None:
        content = delivery_store.load_content_snapshot(
            conn, existing.content_snapshot_id
        )
        if content is None or content.payload != payload:
            raise DeliveryAdapterError("공개 ID의 기존 본문과 새 승인 본문이 다릅니다")
        if unverified_local and existing.cache_origin_content_id:
            raise DeliveryAdapterError(
                "검증되지 않은 로컬 출고에 캐시 출처가 기록돼 있습니다"
            )
        delivery = existing
    else:
        revision, image = release
        models = _model_mapping(actual_models)
        namespace = cache_namespace or CacheNamespace.create(
            product="company-analysis",
            schema_version=report.schema_version or "legacy-report-schema",
            deployment_revision=revision,
            image_digest=image,
            requested_models=models,
            output_settings={
                "delivery_contract": _ADAPTER_VERSION,
                "report_schema": report.schema_version,
            },
        )
        if namespace.schema_version != (
            report.schema_version or "legacy-report-schema"
        ):
            raise DeliveryAdapterError(
                "캐시 생성기 namespace와 보고서 schema가 다릅니다"
            )
        citation_receipts, document_ids = _source_ids(report)
        # pipeline이 실제 preflight에서 확정해 운반한 접수번호가 있으면 그
        # 완전한 신원을 그대로 쓴다. 본문 인용에서 다시 긁은 번호를 합치면
        # 생성 전에 owner를 고른 source digest와 출고 digest가 달라진다.
        # 옛 RunResult처럼 비어 있는 경우에만 인용 번호를 보조 근거로 쓴다.
        receipts = normalize_dart_receipt_numbers(
            dart_receipt_numbers or citation_receipts
        )
        source = SourceSnapshot.capture(
            dart_receipt_nos=receipts,
            financial_payload=None,
            # 수집 시점에 실제 응답으로 계산한 지문만 받는다. 옛 RunResult처럼
            # 비어 있으면 표 값을 지어내지 않고 cache_usable=False로 남긴다.
            financial_payload_sha256=financial_payload_digest,
            captured_at=completed_at,
            source_as_of=_source_date(report, fallback=completed_at.date()),
            official_document_ids=document_ids,
            adapter_versions={"report_delivery": _ADAPTER_VERSION},
        )
        content = ContentSnapshot.create(
            payload=payload,
            source_snapshot=source,
            cache_namespace=namespace,
            content_generated_at=_content_generated_at(
                report,
                fallback=completed_at,
            ),
            actual_models=tuple(models.values()),
        )
        policy = DeliveryPolicy(
            content_max_age=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
            public_link_lifetime=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
        )
        delivery = Delivery.issue(
            public_id=public_id,
            billing_bucket_id=billing_bucket_id,
            content=content,
            delivered_at=completed_at,
            policy=policy,
            reused_from_cache=reused_from_cache,
        )
        delivery_store.save_source_snapshot(conn, source)
        delivery_store.save_cache_namespace(conn, namespace)
        delivery_store.save_content_snapshot(conn, content)
        delivery_store.save_delivery(conn, delivery)

    metadata = delivery_artifact.store_approved_pdf(
        conn,
        backend,
        blob_intent=blob_intent,
        content_snapshot_id=content.content_id,
        pdf_bytes=pdf_bytes,
        version=delivery_artifact.ArtifactVersion(
            renderer_version=_renderer_version(release),
            font_bundle_version=_font_bundle_version(),
            checker_version=AUTOMATIC_CHECKER_VERSION,
        ),
        created_at=completed_at,
        # 생성일이 아니라 관리자가 휴지통으로 옮긴 날부터 30일을 센다.
        # 아직 휴지통에 가지 않은 artifact에는 절대 만료시각이 없으므로
        # retain_until은 비우고, 정리 adapter가 trash 사건과 결속해 계산한다.
        retention=delivery_artifact.ArtifactRetention(
            policy_id=delivery_retention.TRASH_RETENTION_POLICY_ID,
            retain_until=None,
        ),
    )
    delivery_artifact.bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=metadata.artifact_id,
    )
    inspection = delivery_artifact.inspect_artifact(
        conn, backend, metadata.artifact_id
    )
    if (
        inspection is None
        or inspection.status is not delivery_artifact.ArtifactInspectionStatus.AVAILABLE
        or inspection.pdf_bytes != pdf_bytes
    ):
        raise DeliveryAdapterError("저장한 PDF artifact를 동일한 bytes로 다시 읽지 못했습니다")
    if cache_namespace is not None:
        stored_source = delivery_store.load_source_snapshot(
            conn,
            content.source_snapshot_id,
        )
        if stored_source is None:
            raise DeliveryAdapterError("정식 캐시 보고서의 출처 snapshot이 없습니다")
        expected_preflight = ReportSourceIdentity(
            dart_receipt_numbers=stored_source.dart_receipt_nos,
            financial_payload_digest=stored_source.financial_payload_sha256,
        )
        if (
            not expected_preflight.cache_usable
            or expected_preflight.cache_digest != preflight_identity_digest
        ):
            raise DeliveryAdapterError(
                "정식 캐시의 사전 출처 지문이 실제 DART 원본과 다릅니다"
            )
        if bind_cache_entry:
            delivery_store.bind_cache_entry(
                conn,
                key=CacheLookupKey.from_preflight(
                    billing_bucket_id=billing_bucket_id,
                    corp_id=corp_id,
                    namespace=cache_namespace,
                    preflight_identity_digest=preflight_identity_digest,
                    preflight_cache_usable=True,
                ),
                content=content,
                artifact_id=metadata.artifact_id,
                cached_at=completed_at,
            )
    delivery_store.mark_delivery_complete(
        conn,
        public_id=public_id,
        completed_at=completed_at,
    )
    return PublicDelivery(
        delivery=delivery,
        content=content,
        report=report,
        artifact=metadata,
        inspection=inspection,
    )


def persist_reused_delivery(
    conn: sqlite3.Connection,
    backend: delivery_artifact.ArtifactBlobBackend,
    *,
    public_id: str,
    corp_id: str,
    billing_bucket_id: str,
    report: Report,
    completed_at: dt.datetime,
    content_snapshot_id: str,
    artifact_id: str,
    dart_receipt_numbers: tuple[str, ...],
    financial_payload_digest: str,
    cache_key: CacheLookupKey | None = None,
    reuse_singleflight_key: delivery_singleflight.LeaseKey | None = None,
    engine_build_identity: build_identity_contract.EngineBuildIdentity | None = None,
) -> tuple[PublicDelivery, AutomaticReleaseRecord]:
    """owner의 불변 본문·최초 PDF를 검증하고 새 Delivery만 발급한다.

    새 SourceSnapshot·ContentSnapshot·PDF bytes는 만들지 않는다. 같은
    content ID가 다른 비용 통장에도 있을 수 있으므로, 요청 통장과 같은
    기존 Delivery가 이 artifact를 실제로 소유하는지도 함께 확인한다.
    """

    if engine_build_identity is None:
        raise DeliveryAdapterError(
            "재사용 출고에는 생성 시작 때 고정한 엔진 빌드 신원이 필요합니다"
        )
    _assert_frozen_identity_is_current(engine_build_identity)
    release = _release_identity(engine_build_identity)
    if _is_unverified_local_release(release):
        raise DeliveryAdapterError(
            "검증되지 않은 로컬 출고 결과는 새 요청에 재사용할 수 없습니다"
        )
    clean_content_id = str(content_snapshot_id).strip()
    clean_artifact_id = str(artifact_id).strip()
    clean_bucket = str(billing_bucket_id).strip()
    clean_corp = str(corp_id).strip()
    if (
        not clean_content_id
        or not clean_artifact_id
        or not clean_bucket
        or not clean_corp
    ):
        raise DeliveryAdapterError(
            "재사용 출고에는 content·artifact·비용 통장 신원이 필요합니다"
        )
    content = delivery_store.load_content_snapshot(conn, clean_content_id)
    if content is None:
        raise DeliveryAdapterError("재사용할 보고서 원본이 없습니다")
    origin_namespace = delivery_store.load_cache_namespace(
        conn,
        content.cache_namespace_id,
    )
    if origin_namespace is None or not _namespace_matches_release(
        origin_namespace,
        release,
    ):
        raise DeliveryAdapterError(
            "재사용할 보고서가 현재 검증된 배포의 캐시 원본이 아닙니다"
        )
    payload = report_store.report_to_json(report).encode("utf-8")
    if content.payload != payload:
        raise DeliveryAdapterError("재사용 보고서 값이 불변 원본과 다릅니다")
    source = delivery_store.load_source_snapshot(conn, content.source_snapshot_id)
    if source is None:
        raise DeliveryAdapterError("재사용 보고서의 출처 snapshot이 없습니다")
    expected_source = ReportSourceIdentity(
        dart_receipt_numbers=dart_receipt_numbers,
        financial_payload_digest=financial_payload_digest,
    )
    stored_source = ReportSourceIdentity(
        dart_receipt_numbers=source.dart_receipt_nos,
        financial_payload_digest=source.financial_payload_sha256,
    )
    if not expected_source.cache_usable or (
        stored_source.cache_digest != expected_source.cache_digest
    ):
        raise DeliveryAdapterError("재사용 보고서의 DART 출처 신원이 다릅니다")

    metadata = delivery_artifact.load_artifact_metadata(conn, clean_artifact_id)
    if metadata is None or metadata.content_snapshot_id != content.content_id:
        raise DeliveryAdapterError("재사용 PDF가 보고서 원본과 결속되지 않았습니다")
    inspection = delivery_artifact.inspect_artifact(
        conn, backend, metadata.artifact_id
    )
    if (
        inspection is None
        or inspection.status
        is not delivery_artifact.ArtifactInspectionStatus.AVAILABLE
        or inspection.pdf_bytes is None
    ):
        raise DeliveryAdapterError("재사용할 최초 승인 PDF bytes를 확인할 수 없습니다")
    pointer = metadata.blob_pointer
    if pointer is None:
        raise DeliveryAdapterError("재사용 PDF의 내용주소 hash가 없습니다")

    policy = DeliveryPolicy(
        content_max_age=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
        public_link_lifetime=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
    )
    if cache_key is None:
        proof = reuse_singleflight_key
        if (
            not isinstance(proof, delivery_singleflight.LeaseKey)
            or proof.billing_bucket_id != clean_bucket
            or proof.corp_id != clean_corp
            or proof.cache_namespace_id != content.cache_namespace_id
            or proof.source_identity_digest != expected_source.cache_digest
            or not delivery_singleflight.completed_result_matches(
                conn,
                key=proof,
                content_snapshot_id=content.content_id,
                artifact_id=metadata.artifact_id,
                now=completed_at,
            )
        ):
            raise DeliveryAdapterError(
                "정식 캐시 결속이나 정확한 single-flight 완료 증거가 없습니다"
            )
    else:
        if cache_key.billing_bucket_id != clean_bucket:
            raise DeliveryAdapterError(
                "정식 캐시 열쇠와 새 Delivery의 비용 통장이 다릅니다"
            )
        cached = delivery_store.load_cache_hit(
            conn,
            key=cache_key,
            policy=policy,
            delivered_at=completed_at,
        )
        if (
            cached is None
            or cached.content.content_id != content.content_id
            or cached.artifact_id != metadata.artifact_id
        ):
            raise DeliveryAdapterError(
                "재사용 원본이 정식 캐시의 content·PDF 결속과 다릅니다"
            )
    origin_deliveries = tuple(
        delivery
        for delivery in delivery_artifact.deliveries_for_artifact(
            conn, artifact_id=metadata.artifact_id
        )
        if delivery.billing_bucket_id == clean_bucket
    )
    if not origin_deliveries:
        raise DeliveryAdapterError("같은 비용 통장의 owner Delivery가 PDF 원본을 소유하지 않습니다")
    digest = report_sha256(report)
    release_record: AutomaticReleaseRecord | None = None
    for origin in origin_deliveries:
        candidate_record = pdf_release_store.load_automatic_release_record(
            conn,
            report_id=origin.public_id,
            report_sha256=digest,
            pdf_sha256=pointer.sha256,
            checker_version=metadata.version.checker_version,
        )
        if candidate_record is not None:
            release_record = candidate_record
            break
    if release_record is None:
        raise DeliveryAdapterError(
            "owner PDF의 hash 결속 자동승인 기록을 확인할 수 없습니다"
        )

    existing = delivery_store.load_delivery_by_public_id(conn, public_id)
    if existing is not None:
        if existing.content_snapshot_id != content.content_id:
            raise DeliveryAdapterError("공개 ID가 다른 보고서 원본에 이미 쓰였습니다")
        delivery = existing
    else:
        delivery = Delivery.issue(
            public_id=public_id,
            billing_bucket_id=clean_bucket,
            content=content,
            delivered_at=completed_at,
            policy=policy,
            reused_from_cache=True,
        )
        delivery_store.save_delivery(conn, delivery)
    delivery_artifact.bind_artifact_to_delivery(
        conn,
        delivery_id=delivery.delivery_id,
        artifact_id=metadata.artifact_id,
    )
    delivery_store.mark_delivery_complete(
        conn,
        public_id=public_id,
        completed_at=completed_at,
    )
    return (
        PublicDelivery(
            delivery=delivery,
            content=content,
            report=report,
            artifact=metadata,
            inspection=inspection,
        ),
        release_record,
    )


def load_public_delivery(public_id: str) -> PublicDelivery | None:
    """공개 ID의 불변 본문·PDF를 파일·schema 생성 없이 읽는다."""

    backend = configured_artifact_backend()
    with storage_db.connect_readonly_existing() as conn:
        if conn is None:
            return None
        delivery = delivery_store.load_delivery_by_public_id(conn, public_id)
        if delivery is None:
            return None
        content = delivery_store.load_content_snapshot(
            conn, delivery.content_snapshot_id
        )
        if content is None:
            raise DeliveryAdapterError("delivery의 본문 snapshot이 없습니다")
        metadata = delivery_artifact.artifact_for_delivery(
            conn, delivery_id=delivery.delivery_id
        )
        inspection = (
            delivery_artifact.inspect_artifact(conn, backend, metadata.artifact_id)
            if metadata is not None
            else None
        )
    try:
        report = report_store.report_from_json(content.payload.decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise DeliveryAdapterError("delivery 본문 snapshot을 읽지 못했습니다") from exc
    return PublicDelivery(
        delivery=delivery,
        content=content,
        report=report,
        artifact=metadata,
        inspection=inspection,
    )


def load_legacy_public_report(public_id: str) -> LegacyPublicReport | None:
    """과거 ``reports`` 행을 schema·행 변경 없이 원문 JSON 그대로 읽는다.

    이 경계에는 ``report_store.load``를 쓰지 않는다. 그 함수는 오래된 6칸
    보고서를 현재 칸 구조로 정규화하므로, 저장 당시 본문 대신 오늘 코드가 만든
    본문을 공개하게 된다. 당시 HTML/PDF 바이트를 저장하지 않은 과거 자료는
    JSON 본문만 정직하게 복원하고, 현재 validator·renderer로 재출고하지 않는다.

    DB 자체가 없으면 호출자가 「없는 보고서」와 「저장소 없음」을 구분할 수
    있도록 ``DeliveryAdapterError``를 낸다. 행이 없는 경우만 정상 ``None``이다.
    """

    clean_public_id = str(public_id).strip()
    with storage_db.connect_readonly_existing() as conn:
        if conn is None:
            raise DeliveryAdapterError("공개 보고서 저장소가 없습니다")
        try:
            row = conn.execute(
                f"""
                SELECT payload_json, generated_at, created_at
                FROM {report_store.TABLE_REPORTS}
                WHERE report_id = ?
                """,
                (clean_public_id,),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise DeliveryAdapterError("공개 보고서 저장소를 읽지 못했습니다") from exc
    if row is None:
        return None
    payload_json = str(row[0])
    try:
        report = report_store.report_from_json(payload_json)
    except (KeyError, TypeError, ValueError) as exc:
        raise DeliveryAdapterError("과거 보고서 본문을 읽지 못했습니다") from exc
    return LegacyPublicReport(
        report=report,
        payload_json=payload_json,
        generated_at=str(row[1] or ""),
        stored_at=str(row[2] or ""),
    )
