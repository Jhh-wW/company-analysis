"""웹 유료 실행과 report_delivery single-flight를 잇는 adapter.

서로 다른 비용 통장은 같은 회사라도 절대 합치지 않는다. 소유자가
확정된 뒤에만 본조사 유료 phase를 만들어, waiter의 중복 예약이
owner를 입구에서 막는 순서 역전을 피한다.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import os
import sqlite3
import threading
import time
from dataclasses import InitVar, dataclass, field
from typing import Any, Final

from src.core import clock
from src.core.constants import MAX_AI_CALLS_PER_REQUEST
from src.features.budget.constants import PAID_PHASE_LEASE_SEC, SPEND_PHASE_PIPELINE
from src.features.pipeline.constants import ANTHROPIC_TIMEOUT_SEC
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import authority as authority_store
from src.features.report_delivery import singleflight
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheLookupKey
from src.features.report_delivery.models import DeliveryPolicy
from src.features.report_delivery.policy import CacheMissReason
from src.features.storage import cache as cache_store
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared import generation_coordination
from src.shared.generation_cache_identity import GenerationCacheNamespace
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_evidence.release_mode import (
    REPORT_RELEASE_MODE_ENV_NAME,
    parse_release_mode,
)
from src.web import paid_runtime


logger = logging.getLogger(__name__)

# 단일 provider timeout보다 넉넉하게 잡아, heartbeat 직후 process가 멎어도
# 살아 있는 provider 호출과 takeover가 겹치지 않게 한다.
LEASE_TTL: Final[dt.timedelta] = dt.timedelta(minutes=15)
HEARTBEAT_INTERVAL_SEC: Final[float] = 30.0
RESULT_FANOUT_TTL: Final[dt.timedelta] = dt.timedelta(minutes=2)
FAILURE_FANOUT_TTL: Final[dt.timedelta] = dt.timedelta(minutes=2)
WAITER_POLL_SEC: Final[float] = 0.2

# ``MAX_RESPONSE_SEC=300``은 진행 화면의 안내 기준이지 작업 강제 종료 시간이
# 아니다. 실제 유료 경계의 근거 있는 상한은 다음 두 기존 계약이다.
#
# * 한 요청의 provider 호출은 최대 15회
# * 한 호출의 SDK timeout은 180초
#
# 최악의 provider 대기 45분에 DART·공식 웹 수집과 로컬 검증 여유 15분을 더한
# 기존 paid-phase lease 1시간을 single-flight owner의 절대 상한으로도 쓴다.
# 이 값 뒤에는 heartbeat를 더 연장하지 않아 멈춘 thread가 영구 owner가 될 수 없다.
OWNER_MAX_AGE: Final[dt.timedelta] = dt.timedelta(seconds=PAID_PHASE_LEASE_SEC)
PROVIDER_IN_FLIGHT_GRACE: Final[dt.timedelta] = dt.timedelta(
    seconds=ANTHROPIC_TIMEOUT_SEC + (2 * HEARTBEAT_INTERVAL_SEC)
)
OWNER_PROVIDER_ADMISSION_AGE: Final[dt.timedelta] = (
    OWNER_MAX_AGE - PROVIDER_IN_FLIGHT_GRACE
)
WAITER_MAX_AGE_SEC: Final[float] = float(PAID_PHASE_LEASE_SEC)


def _assert_full_report_source_identity(
    *,
    report: Any,
    source: Any,
    preflight_identity_digest: str,
    cache_key: CacheLookupKey | None = None,
    reuse_singleflight_key: singleflight.LeaseKey | None = None,
) -> None:
    """FULL 저장본의 producer 세대와 실제 cache/lease 출처 세대를 맞춘다."""

    if str(getattr(report, "release_mode", "") or "") != ReleaseMode.FULL.value:
        return
    # 순환 import를 피한다. report_completion은 웹 완료 경계에서만 쓰는
    # canonical producer 검산이라 cache hit가 실제로 발견된 때만 적재한다.
    from src.web import report_completion  # noqa: PLC0415

    evidence = report_completion.require_release_evidence(report)
    report_completion.assert_release_preflight_identity(
        evidence=evidence,
        preflight_identity_digest=preflight_identity_digest,
    )
    report_completion.assert_release_stored_source_identity(
        evidence=evidence,
        source=source,
        cache_key=cache_key,
        reuse_singleflight_key=reuse_singleflight_key,
    )


def _commit_connection(conn: Any) -> None:
    """완료 신원 최종 검사 직후 commit하며 시험은 실패를 이 seam에 주입한다."""

    conn.commit()


def _completion_receipt_matches_exactly(
    conn: Any,
    *,
    handle: singleflight.LeaseHandle,
    content_snapshot_id: str,
    artifact_id: str,
    completed_at: dt.datetime,
    cache_key: CacheLookupKey | None,
) -> bool:
    """완료 fan-out과 선택적 장기 cache가 같은 epoch 원본인지 재대조한다."""

    try:
        if not singleflight.completed_result_matches(
            conn,
            key=handle.key,
            content_snapshot_id=content_snapshot_id,
            artifact_id=artifact_id,
            now=completed_at,
        ):
            return False
        return cache_key is None or delivery_store.cache_entry_matches_exactly(
            conn,
            key=cache_key,
            content_snapshot_id=content_snapshot_id,
            artifact_id=artifact_id,
        )
    except (sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False


def _quarantine_completion_receipt(
    conn: Any,
    *,
    handle: singleflight.LeaseHandle,
    cache_key: CacheLookupKey | None,
    committed_completion_known: bool,
    quarantined_at: dt.datetime,
) -> None:
    """모순된 완료표와 cache key를 다음 waiter·조회의 권위에서 뺀다."""

    conn.rollback()
    completed_quarantined = (
        singleflight.quarantine_completed_key_after_receipt_mismatch(
            conn,
            key=handle.key,
            now=quarantined_at,
        )
    )
    cache_quarantined = False
    # commit 자체가 실패했으면 기존 장기 cache는 이번 UPDATE와 무관하다.
    # 정상 commit 뒤 drift였거나 실제 COMPLETED 행이 보일 때만 같은 epoch의
    # cache key도 격리해, 정상인 과거 cache를 응답 오류만으로 지우지 않는다.
    if cache_key is not None and (
        committed_completion_known or completed_quarantined
    ):
        cache_quarantined = (
            delivery_store.quarantine_cache_key_after_receipt_mismatch(
                conn,
                key=cache_key,
                reason_code="completion_receipt_mismatch",
                invalidated_at=quarantined_at,
            )
        )
    if completed_quarantined or cache_quarantined:
        # 오류 주입용 commit seam을 다시 통과하면 같은 응답 손실을 반복한다.
        # 격리는 별도 보상 거래이며 SQLite 자체 commit으로 즉시 닫는다.
        conn.commit()


if MAX_AI_CALLS_PER_REQUEST * ANTHROPIC_TIMEOUT_SEC > (
    OWNER_PROVIDER_ADMISSION_AGE.total_seconds()
):  # pragma: no cover - 서로 다른 정본 상수가 어긋나면 import부터 실패한다.
    raise RuntimeError("provider 최악 대기보다 single-flight owner 상한이 짧습니다")


def _attach_origin_public_projection(
    conn: Any,
    *,
    artifact_id: str,
    content_id: str,
    billing_bucket_id: str,
    report: Any,
) -> Any:
    """재사용하는 본문에 «원래 발급 Delivery»의 공개 봉인을 다시 붙인다.

    봉인은 보고서 payload가 아니라 별도 표에 report_id로 저장한다.
    재사용 경로가 손에 쥔 것은 content snapshot 문자열뿐이라
    report_id가 없다 — 그 artifact를 실제로 발급받은 Delivery의 공개 ID가
    그 자리다. 같은 통장·같은 내용 원본인 Delivery만 본다(다른 통장의 PDF를
    우회로 가져오지 못하게 하는 ``deliveries_for_artifact``의 경계와 동일).

    맞는 Delivery가 없으면 봉인을 붙이지 않고 그대로 돌려준다 — 그건 오류가
    아니라 「봉인 없음」이라는 정의된 상태다. 봉인이 «있는데» 저장본과 어긋나면
    ``attach_public_projection``이 ValueError를 올리고, 호출부가 그 경로의
    기존 방식대로 닫는다(I3 fail-closed).
    """

    for origin in delivery_artifact.deliveries_for_artifact(
        conn, artifact_id=artifact_id
    ):
        if origin.billing_bucket_id != billing_bucket_id:
            continue
        if origin.content_snapshot_id != content_id:
            continue
        return report_store.attach_public_projection(conn, origin.public_id, report)
    return report


class GenerationSingleflightUnavailable(
    generation_coordination.GenerationCoordinationError
):
    """lease/DB 무결성을 확인할 수 없어 provider를 열지 않는다."""


class _FullOwnerAuthorityUnavailable(RuntimeError):
    """FULL 재사용 후보에 상속 가능한 OWNER 권위가 없다."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _require_full_owner_authority(
    conn: Any,
    *,
    report: Any,
    content: Any,
    artifact_id: str,
    billing_bucket_id: str,
    automatic_release_sha256: str | None = None,
) -> authority_store.ReleaseAuthority | None:
    """FULL cache/fan-out 후보만 OWNER 권위에 결속하고 non-FULL은 그대로 둔다."""

    if str(getattr(report, "release_mode", "") or "") != ReleaseMode.FULL.value:
        return None
    from src.web import report_completion  # noqa: PLC0415

    try:
        owner = authority_store.load_owner_authority(
            conn,
            content_snapshot_id=content.content_id,
            artifact_id=artifact_id,
        )
    except authority_store.ReleaseAuthorityError as exc:
        raise _FullOwnerAuthorityUnavailable(
            "owner_authority_corrupt",
            "FULL 재사용 원본의 OWNER 출고 권위가 손상됐습니다",
        ) from exc
    if owner is None:
        raise _FullOwnerAuthorityUnavailable(
            "owner_authority_missing",
            "FULL 재사용 원본의 OWNER 출고 권위가 없습니다",
        )
    try:
        evidence = report_completion.require_release_evidence(report)
        report_completion.assert_owner_release_authority_identity(
            authority=owner,
            evidence=evidence,
            billing_bucket_id=billing_bucket_id,
            content=content,
            artifact_id=artifact_id,
            automatic_release_sha256=automatic_release_sha256,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        raise _FullOwnerAuthorityUnavailable(
            "owner_authority_mismatch",
            "FULL 재사용 원본의 OWNER 권위가 생성 원본과 다릅니다",
        ) from exc
    return owner


def _full_origin_automatic_release_sha256(
    conn: Any,
    *,
    report: Any,
    content_id: str,
    artifact: Any,
    billing_bucket_id: str,
) -> str:
    """COMPLETED 후보의 실제 자동승인 행을 찾아 OWNER 권위와 맞댄다."""

    pointer = getattr(artifact, "blob_pointer", None)
    if pointer is None:
        raise _FullOwnerAuthorityUnavailable(
            "owner_approval_missing",
            "FULL 완료 원본의 PDF 내용주소가 없습니다",
        )
    from src.features.export_pdf import release_store as pdf_release_store  # noqa: PLC0415
    from src.features.export_pdf.automatic_release import report_sha256  # noqa: PLC0415

    try:
        for origin in delivery_artifact.deliveries_for_artifact(
            conn,
            artifact_id=artifact.artifact_id,
        ):
            if (
                origin.billing_bucket_id != billing_bucket_id
                or origin.content_snapshot_id != content_id
            ):
                continue
            record = pdf_release_store.load_automatic_release_record(
                conn,
                report_id=origin.public_id,
                report_sha256=report_sha256(report),
                pdf_sha256=pointer.sha256,
                checker_version=artifact.version.checker_version,
            )
            if record is not None:
                digest = str(getattr(record, "record_sha256", "")).strip()
                if digest:
                    return digest
    except (RuntimeError, TypeError, ValueError) as exc:
        raise _FullOwnerAuthorityUnavailable(
            "owner_approval_corrupt",
            "FULL 완료 원본의 자동승인 기록이 손상됐습니다",
        ) from exc
    raise _FullOwnerAuthorityUnavailable(
        "owner_approval_missing",
        "FULL 완료 원본의 자동승인 기록이 없습니다",
    )


class PaidGenerationAdmissionUnavailable(
    generation_coordination.GenerationCoordinationError
):
    """owner의 본조사 비용 phase를 예약하지 못했다."""


@dataclass
class GenerationSession:
    """배경 Job 하나의 lease·대기·지연 유료 phase 상태."""

    run_id: str
    share_key: str
    billing_bucket_id: str
    cap_krw: float | None
    on_paid_phase: Any
    build_identity: InitVar[build_identity_contract.EngineBuildIdentity]
    _state: str = field(default="new", init=False)
    _key: singleflight.LeaseKey | None = field(default=None, init=False)
    _cache_namespace: GenerationCacheNamespace | None = field(default=None, init=False)
    _preflight_identity_digest: str = field(default="", init=False)
    _handle: singleflight.LeaseHandle | None = field(default=None, init=False)
    _paid_phase: paid_runtime.PaidPhase | None = field(default=None, init=False)
    _provider_stack: contextlib.ExitStack | None = field(default=None, init=False)
    _cancel_wait: threading.Event = field(default_factory=threading.Event, init=False)
    _stop_heartbeat: threading.Event = field(default_factory=threading.Event, init=False)
    _heartbeat_thread: threading.Thread | None = field(default=None, init=False)
    _lease_error: BaseException | None = field(default=None, init=False)
    _frozen_build_identity: build_identity_contract.EngineBuildIdentity = field(init=False)
    # Job scheduler가 이 세션을 만들 때부터 한 시간 전체 마감이 흐른다.
    # owner를 늦게 얻었다고 다시 한 시간을 주면 preflight→wait→takeover가
    # 이어질 때 한 요청의 슬롯 수명이 계속 늘어나므로, 최초 요청 시각 한 벌을
    # 모든 waiter·owner·bypass 경계가 함께 쓴다.
    _execution_started_at: dt.datetime | None = field(default=None, init=False)
    _execution_started_monotonic: float = field(default=0.0, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def __post_init__(
        self,
        build_identity: build_identity_contract.EngineBuildIdentity,
    ) -> None:
        exact = build_identity_contract.require_exact_engine_build_identity(
            build_identity
        )
        build_identity_contract.assert_engine_build_identity_current(exact)
        if not exact.cache_usable:
            raise TypeError("유료 보고서 생성에는 정상 engine epoch 영수증이 필요합니다")
        self._frozen_build_identity = exact
        # 함수 객체를 dataclass default_factory에 고정하지 않는다. 시험 clock과
        # 운영 clock adapter가 교체돼도 생성 순간의 같은 두 시계를 읽어야 한다.
        self._execution_started_at = clock.now_kst()
        self._execution_started_monotonic = time.monotonic()

    @property
    def callbacks(self) -> generation_coordination.GenerationCallbacks:
        return generation_coordination.GenerationCallbacks(
            coordinate=self.coordinate,
            ensure_paid_phase=self.ensure_paid_phase,
            engine_build_identity=self._frozen_build_identity,
        )

    @property
    def engine_build_identity(self) -> build_identity_contract.EngineBuildIdentity:
        """세션 생성 순간 한 번 고정한 배포·빌드 신원."""

        return self._frozen_build_identity

    @property
    def owns_generation(self) -> bool:
        with self._lock:
            return self._state == "owner"

    @property
    def paid_phase(self) -> paid_runtime.PaidPhase | None:
        with self._lock:
            return self._paid_phase

    @property
    def cache_namespace(self) -> GenerationCacheNamespace | None:
        """pipeline이 provider 전에 확정해 이 세션에 맡긴 생성기 신원."""

        with self._lock:
            return self._cache_namespace

    @property
    def preflight_identity_digest(self) -> str:
        """DART 접수번호·재무 응답으로 싸게 재검증한 출처 지문."""

        with self._lock:
            return self._preflight_identity_digest

    @property
    def completed_reuse_key(self) -> singleflight.LeaseKey | None:
        """정식 캐시가 아닌 waiter 재사용을 증명할 완료 lease 열쇠."""

        with self._lock:
            return self._key if self._state == "reused" else None

    def _set_owner(
        self,
        *,
        key: singleflight.LeaseKey,
        handle: singleflight.LeaseHandle,
    ) -> None:
        with self._lock:
            self._key = key
            self._handle = handle
            self._state = "owner"
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"generation-lease:{self.run_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _owner_deadlines(
        self,
        handle: singleflight.LeaseHandle | None,
    ) -> tuple[dt.datetime, dt.datetime]:
        """새 provider 마감과 절대 마감을 요청 전체 시작 시각에서 계산한다.

        ``handle``은 owner 호출부와 기존 시험의 인터페이스를 유지하기 위해 받는다.
        bypass에서는 ``None``이며, 마감 기준은 어느 경우든 세션 생성 시각이다.
        """

        del handle
        started_at = self._execution_started_at
        if started_at is None:  # pragma: no cover - dataclass 생성 계약 방어
            raise GenerationSingleflightUnavailable(
                "보고서 생성 전체 시작 시각이 없습니다"
            )
        started_at = started_at.astimezone(dt.timezone.utc)

        return (
            started_at + OWNER_PROVIDER_ADMISSION_AGE,
            started_at + OWNER_MAX_AGE,
        )

    def _monotonic_owner_remaining(self) -> dt.timedelta:
        """벽시계가 뒤로 움직여도 owner 수명이 늘지 않게 하는 두 번째 시계."""

        with self._lock:
            started = self._execution_started_monotonic
        remaining_sec = (
            started + OWNER_MAX_AGE.total_seconds() - time.monotonic()
        )
        return dt.timedelta(seconds=max(0.0, remaining_sec))

    def _bounded_owner_ttl(self, now: dt.datetime) -> dt.timedelta:
        """최초 획득과 heartbeat 모두 요청 전체 절대 상한 안에 묶는다.

        무료 preflight가 오래 걸린 뒤 owner가 되면 ``acquire``의 기본 15분을
        그대로 주는 것만으로도 전체 한 시간이 다시 늘어난다. 그래서 아직
        handle이 없는 최초 획득도 최초 요청 시각의 남은 시간만 받는다.
        """

        _provider_deadline, lease_deadline = self._owner_deadlines(None)
        remaining = min(
            lease_deadline - now.astimezone(dt.timezone.utc),
            self._monotonic_owner_remaining(),
        )
        if remaining <= dt.timedelta(0):
            raise generation_coordination.GenerationExecutionDeadlineExceeded(
                "보고서 생성 owner의 최대 실행 시간을 넘었습니다"
            )
        return min(LEASE_TTL, remaining)

    def _bounded_heartbeat_ttl(
        self,
        handle: singleflight.LeaseHandle,
        now: dt.datetime,
    ) -> dt.timedelta:
        """heartbeat가 최초 요청의 절대 상한을 한 번도 넘지 않게 한다."""

        del handle
        return self._bounded_owner_ttl(now)

    def _require_provider_admission_time(
        self,
        handle: singleflight.LeaseHandle | None,
        now: dt.datetime,
    ) -> None:
        provider_deadline, _lease_deadline = self._owner_deadlines(handle)
        with self._lock:
            started = self._execution_started_monotonic
        monotonic_deadline = (
            started + OWNER_PROVIDER_ADMISSION_AGE.total_seconds()
        )
        if (
            now.astimezone(dt.timezone.utc) >= provider_deadline
            or time.monotonic() >= monotonic_deadline
        ):
            raise generation_coordination.GenerationExecutionDeadlineExceeded(
                "보고서 생성 제한시간이 가까워 새 provider 호출을 시작하지 않습니다"
            )

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(HEARTBEAT_INTERVAL_SEC):
            try:
                with self._lock:
                    handle = self._handle
                    if self._state != "owner" or handle is None:
                        return
                heartbeat_at = clock.now_kst()
                lease_ttl = self._bounded_heartbeat_ttl(handle, heartbeat_at)
                with storage_db.connect() as conn:
                    refreshed = singleflight.heartbeat(
                        conn,
                        handle=handle,
                        now=heartbeat_at,
                        lease_ttl=lease_ttl,
                    )
                if refreshed is None:
                    raise GenerationSingleflightUnavailable(
                        "보고서 생성 lease 소유권을 잃었습니다"
                    )
                with self._lock:
                    self._handle = refreshed
            except BaseException as exc:  # noqa: BLE001 - 다음 provider를 닫는다
                with self._lock:
                    self._lease_error = exc
                logger.exception(
                    "보고서 생성 lease heartbeat 실패 run_id=%s",
                    self.run_id,
                )
                return

    def _read_completed(
        self,
        conn: Any,
        *,
        key: singleflight.LeaseKey,
        content_id: str,
        artifact_id: str,
    ) -> generation_coordination.ReusedGeneration:
        content = delivery_store.load_content_snapshot(conn, content_id)
        if content is None:
            raise GenerationSingleflightUnavailable(
                "완료 lease가 가리키는 보고서 내용이 없습니다"
            )
        if content.cache_namespace_id != key.cache_namespace_id:
            raise GenerationSingleflightUnavailable(
                "완료 lease와 보고서의 생성기 신원이 다릅니다"
            )
        if content.engine_epoch_digest != key.engine_epoch_digest:
            raise GenerationSingleflightUnavailable(
                "완료 lease와 보고서의 engine epoch가 다릅니다"
            )
        source = delivery_store.load_source_snapshot(conn, content.source_snapshot_id)
        if source is None:
            raise GenerationSingleflightUnavailable(
                "완료된 보고서의 출처 snapshot이 없습니다"
            )
        if source.preflight_identity_digest != key.source_identity_digest:
            raise GenerationSingleflightUnavailable(
                "lease와 보고서의 출처 신원이 다릅니다"
            )
        artifact = delivery_artifact.load_artifact_metadata(conn, artifact_id)
        if artifact is None or artifact.content_snapshot_id != content.content_id:
            raise GenerationSingleflightUnavailable(
                "완료 lease의 PDF artifact가 보고서 원본과 다릅니다"
            )
        try:
            report = report_store.report_from_json(content.payload.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise GenerationSingleflightUnavailable(
                "완료된 보고서 내용을 읽지 못했습니다"
            ) from exc
        try:
            report = _attach_origin_public_projection(
                conn,
                artifact_id=artifact.artifact_id,
                content_id=content.content_id,
                billing_bucket_id=key.billing_bucket_id,
                report=report,
            )
        except ValueError as exc:
            raise GenerationSingleflightUnavailable(
                "완료된 보고서의 공개 봉인이 저장본과 다릅니다"
            ) from exc
        requested_mode = self._requested_release_mode()
        if requested_mode is ReleaseMode.FULL and not (
            cache_store.reusable_for_requested_release_mode(
                str(getattr(report, "release_mode", "") or ""),
                requested_mode,
            )
        ):
            raise _FullOwnerAuthorityUnavailable(
                "release_mode_mismatch",
                "완료된 보고서의 릴리스 모드가 현재 요청과 다릅니다",
            )
        if requested_mode is ReleaseMode.FULL:
            try:
                _assert_full_report_source_identity(
                    report=report,
                    source=source,
                    preflight_identity_digest=key.source_identity_digest,
                    reuse_singleflight_key=key,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                # 여기까지 왔으면 COMPLETED 행·content·source·artifact를 모두
                # 정상적으로 읽었고, 실패한 것은 이미 읽힌 후보와 지금 요청의
                # producer/source-generation 결속뿐이다. 장기 cache의 같은
                # 손상은 격리 뒤 정상 miss로 내리므로(:865 아래), fan-out도
                # 같은 사유로 격리해 새 fencing token owner가 이어받게 한다.
                # DB/lock/I/O 오류는 이 try 밖에서 기존 fail-closed 경로를 타므로
                # 완료 여부가 불확실한 상태에서 provider를 겹쳐 열지 않는다.
                raise _FullOwnerAuthorityUnavailable(
                    "generation_evidence_source_mismatch",
                    "완료된 FULL 보고서와 lease의 근거 세대가 다릅니다",
                ) from exc
            automatic_release_sha256 = _full_origin_automatic_release_sha256(
                conn,
                report=report,
                content_id=content.content_id,
                artifact=artifact,
                billing_bucket_id=key.billing_bucket_id,
            )
            _require_full_owner_authority(
                conn,
                report=report,
                content=content,
                artifact_id=artifact.artifact_id,
                billing_bucket_id=key.billing_bucket_id,
                automatic_release_sha256=automatic_release_sha256,
            )
        cache_key = CacheLookupKey(
            billing_bucket_id=key.billing_bucket_id,
            corp_id=key.corp_id,
            namespace_id=key.cache_namespace_id,
            preflight_identity_digest=key.source_identity_digest,
            engine_epoch_digest=key.engine_epoch_digest,
        )
        cache_eligible = delivery_store.cache_entry_matches_exactly(
            conn,
            key=cache_key,
            content_snapshot_id=content.content_id,
            artifact_id=artifact.artifact_id,
        )
        return generation_coordination.ReusedGeneration(
            content_snapshot_id=content.content_id,
            artifact_id=artifact.artifact_id,
            report=report,
            actual_models=content.actual_models,
            generation_cache_eligible=cache_eligible,
        )

    def _requested_release_mode(self) -> ReleaseMode | None:
        """지금 요청이 «어떤 릴리스 모드로» 만들려는지. 모르면 ``None``.

        ★ 왜 세션이 직접 읽나
          이 값의 정본은 pipeline(`features/pipeline/real.py`)이 읽는 것과
          같은 환경값 한 곳이다. 인자로 받으려면 `generation_coordination`의
          callback 서명(shared)이나 세션 생성부(`web/job_runtime.py`)를 고쳐야
          하는데 둘 다 이 변경의 소유가 아니다. 같은 환경값을 같은 파서로 읽으므로
          두 곳이 갈릴 여지는 없다.
        ★ 모르면 «예전 동작». 값이 없거나 계약 밖 문자열이면 `None`이고,
          그때 아래 판정은 재사용을 그대로 허용한다. FULL 요청은 환경값이
          반드시 있다(없거나 오타면 pipeline이 AI 호출 전에 막는다).
        """
        raw = os.environ.get(REPORT_RELEASE_MODE_ENV_NAME)
        if not raw:
            return None
        try:
            return parse_release_mode(raw)
        except ValueError:
            return None

    def _read_cached_release(
        self,
        conn: Any,
        *,
        key: CacheLookupKey,
    ) -> generation_coordination.ReusedGeneration | None:
        """전역 캐시의 본문·최초 PDF 결속을 한 묶음으로 읽는다."""

        policy = DeliveryPolicy(
            content_max_age=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
            public_link_lifetime=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
        )
        lookup = delivery_store.load_cache_lookup(
            conn,
            key=key,
            policy=policy,
            delivered_at=clock.now_kst(),
        )
        lease_key = singleflight.LeaseKey(
            billing_bucket_id=key.billing_bucket_id,
            corp_id=key.corp_id,
            cache_namespace_id=key.namespace_id,
            source_identity_digest=key.preflight_identity_digest,
            engine_epoch_digest=key.engine_epoch_digest,
        )

        def drop_cache_entry(
            content_snapshot_id: str,
            artifact_id: str,
            reason_code: str,
        ) -> None:
            invalidated_at = clock.now_kst()
            removed = delivery_store.invalidate_cache_entry(
                conn,
                key=key,
                expected_content_snapshot_id=content_snapshot_id,
                expected_artifact_id=artifact_id,
                reason_code=reason_code,
                invalidated_at=invalidated_at,
            )
            if removed:
                singleflight.expire_completed_result(
                    conn,
                    key=lease_key,
                    content_snapshot_id=content_snapshot_id,
                    artifact_id=artifact_id,
                    now=invalidated_at,
                )

        cached = lookup.hit
        if cached is None:
            if (
                lookup.miss_reason is CacheMissReason.CONTENT_EXPIRED
                and lookup.expired_content_snapshot_id
                and lookup.expired_artifact_id
            ):
                # ★ 재사용 한도 나이를 지난 열쇠는 «읽지 않는 것»으로 끝내면
                #   안 된다. 행이 남아 옛 본문을 계속 가리키면, 새로 만든
                #   보고서를 같은 열쇠에 결속하는 마지막 단계에서 막혀
                #   재조사가 몇 번을 다시 해도 같은 자리에서 실패한다.
                #   여기서 사유를 남기고 지운 뒤 미적중으로 내려간다.
                drop_cache_entry(
                    lookup.expired_content_snapshot_id,
                    lookup.expired_artifact_id,
                    CacheMissReason.CONTENT_EXPIRED.value,
                )
            return None

        def invalidate(reason_code: str) -> None:
            drop_cache_entry(
                cached.content.content_id,
                cached.artifact_id,
                reason_code,
            )

        try:
            metadata = delivery_artifact.load_artifact_metadata(
                conn,
                cached.artifact_id,
            )
        except delivery_artifact.ArtifactError:
            invalidate("artifact_metadata_corrupt")
            return None
        if (
            metadata is None
            or metadata.content_snapshot_id != cached.content.content_id
            or metadata.blob_pointer is None
        ):
            invalidate("artifact_binding_missing")
            return None
        # backend와 자동승인 원장은 web adapter가 소유한다. 실제 생성 조회에서만
        # 읽고, 과거 공개 GET의 fail-closed 계약은 건드리지 않는다.
        from src.features.export_pdf import release_store as pdf_release_store  # noqa: PLC0415
        from src.features.export_pdf.automatic_release import (  # noqa: PLC0415
            report_sha256,
        )
        from src.web import report_delivery_adapter  # noqa: PLC0415

        try:
            inspection = delivery_artifact.inspect_artifact(
                conn,
                report_delivery_adapter.configured_artifact_backend(),
                metadata.artifact_id,
            )
        except delivery_artifact.ArtifactError:
            invalidate("artifact_inspection_failed")
            return None
        if (
            inspection is None
            or inspection.status
            is not delivery_artifact.ArtifactInspectionStatus.AVAILABLE
            or inspection.pdf_bytes is None
        ):
            invalidate("artifact_bytes_unavailable")
            return None
        try:
            report = report_store.report_from_json(
                cached.content.payload.decode("utf-8")
            )
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            invalidate("report_payload_invalid")
            return None
        approval_record = None
        try:
            for origin in delivery_artifact.deliveries_for_artifact(
                conn,
                artifact_id=metadata.artifact_id,
            ):
                if origin.billing_bucket_id != key.billing_bucket_id:
                    continue
                record = pdf_release_store.load_automatic_release_record(
                    conn,
                    report_id=origin.public_id,
                    report_sha256=report_sha256(report),
                    pdf_sha256=metadata.blob_pointer.sha256,
                    checker_version=metadata.version.checker_version,
                )
                if record is not None:
                    approval_record = record
                    break
        except (ValueError, RuntimeError):
            invalidate("approval_record_corrupt")
            return None
        if approval_record is None:
            invalidate("approval_record_missing")
            return None
        try:
            report = _attach_origin_public_projection(
                conn,
                artifact_id=metadata.artifact_id,
                content_id=cached.content.content_id,
                billing_bucket_id=key.billing_bucket_id,
                report=report,
            )
        except ValueError:
            invalidate("public_projection_mismatch")
            return None
        requested_mode = self._requested_release_mode()
        if requested_mode is ReleaseMode.FULL and not (
            cache_store.reusable_for_requested_release_mode(
                str(getattr(report, "release_mode", "") or ""),
                requested_mode,
            )
        ):
            invalidate("release_mode_mismatch")
            return None
        if requested_mode is ReleaseMode.FULL:
            source = delivery_store.load_source_snapshot(
                conn,
                cached.content.source_snapshot_id,
            )
            if source is None:
                invalidate("source_snapshot_missing")
                return None
            try:
                _assert_full_report_source_identity(
                    report=report,
                    source=source,
                    preflight_identity_digest=key.preflight_identity_digest,
                    cache_key=key,
                    reuse_singleflight_key=lease_key,
                )
            except (RuntimeError, TypeError, ValueError):
                # 손상 cache를 실패 보고서로 내보내지 않고 격리한 뒤 정상 miss로
                # 내려가 새 owner가 같은 현재 출처로 다시 만들게 한다.
                invalidate("generation_evidence_source_mismatch")
                return None
            try:
                _require_full_owner_authority(
                    conn,
                    report=report,
                    content=cached.content,
                    artifact_id=metadata.artifact_id,
                    billing_bucket_id=key.billing_bucket_id,
                    automatic_release_sha256=getattr(
                        approval_record, "record_sha256", None
                    ),
                )
            except _FullOwnerAuthorityUnavailable as exc:
                invalidate(exc.reason_code)
                return None
        return generation_coordination.ReusedGeneration(
            content_snapshot_id=cached.content.content_id,
            artifact_id=cached.artifact_id,
            report=report,
            actual_models=cached.content.actual_models,
            generation_cache_eligible=True,
        )

    def coordinate(
        self,
        corp_id: str,
        cache_namespace: GenerationCacheNamespace | None,
        preflight_identity_digest: str,
    ) -> generation_coordination.ReusedGeneration | None:
        """전역 cache를 먼저 읽고, miss면 같은 사전 신원 owner를 정한다."""

        build_identity_contract.assert_engine_build_identity_current(
            self._frozen_build_identity
        )

        with self._lock:
            if self._state != "new":
                raise GenerationSingleflightUnavailable(
                    "한 조사에서 생성 조정을 두 번 시작할 수 없습니다"
                )
        clean_corp = str(corp_id).strip()
        clean_namespace = (
            cache_namespace.namespace_id
            if isinstance(cache_namespace, GenerationCacheNamespace)
            else ""
        )
        clean_source = str(preflight_identity_digest).strip()
        if cache_namespace is not None:
            frozen = self._frozen_build_identity
            expected_image = f"generator-build:{frozen.build_id}"
            if (
                not frozen.cache_usable
                or cache_namespace.deployment_revision
                != frozen.deployment_revision
                or cache_namespace.image_digest != expected_image
            ):
                raise GenerationSingleflightUnavailable(
                    "생성 namespace가 요청 시작 때 고정한 엔진 빌드 신원과 다릅니다"
                )
        # 부분 지문으로 예전 결과를 공유하는 것이 더 위험하다. 이때는
        # lease를 작대기로 만들지 않고 요청별로 새로 생성한다.
        if not clean_corp or not clean_namespace or not clean_source:
            with self._lock:
                self._cache_namespace = None
                self._preflight_identity_digest = ""
                self._state = "bypass"
            return None
        with self._lock:
            self._cache_namespace = cache_namespace
            self._preflight_identity_digest = clean_source
        key = singleflight.LeaseKey(
            billing_bucket_id=self.billing_bucket_id,
            corp_id=clean_corp,
            cache_namespace_id=clean_namespace,
            source_identity_digest=clean_source,
            engine_epoch_digest=self._frozen_build_identity.epoch_digest,
        )
        cache_key = CacheLookupKey.from_preflight(
            billing_bucket_id=self.billing_bucket_id,
            corp_id=clean_corp,
            namespace=cache_namespace,
            preflight_identity_digest=clean_source,
            preflight_cache_usable=True,
            engine_epoch_digest=self._frozen_build_identity.epoch_digest,
        )

        # DART preflight가 오래 걸렸다고 여기서 대기 시간을 새로 한 시간 주지
        # 않는다. 요청 전체 절대 마감 한 벌을 owner와 waiter가 같이 쓴다.
        wait_deadline = self._execution_started_monotonic + WAITER_MAX_AGE_SEC
        while True:
            build_identity_contract.assert_engine_build_identity_current(
                self._frozen_build_identity
            )
            if self._cancel_wait.is_set():
                with self._lock:
                    self._state = "cancelled"
                raise generation_coordination.GenerationWaitCancelled(
                    "보고서 생성 대기 요청이 취소됐습니다"
                )
            wait_remaining = wait_deadline - time.monotonic()
            if wait_remaining <= 0:
                with self._lock:
                    self._state = "timed_out"
                raise generation_coordination.GenerationWaitTimedOut(
                    "먼저 시작한 보고서 생성을 기다리는 최대 시간을 넘었습니다"
                )
            try:
                with storage_db.connect() as conn:
                    # namespace 저장·cache 조회·foreign epoch 확인·owner INSERT를
                    # 같은 write lock 아래 둔다. namespace INSERT가 먼저 deferred
                    # transaction을 열게 두면 A/B process가 모두 foreign owner가
                    # 없다고 읽은 뒤 각각 provider를 열 수 있다.
                    conn.execute("BEGIN IMMEDIATE")
                    delivery_store.save_cache_namespace(conn, cache_namespace)
                    cached = self._read_cached_release(conn, key=cache_key)
                    if cached is not None and not (
                        cache_store.reusable_for_requested_release_mode(
                            str(
                                getattr(cached.report, "release_mode", "") or ""
                            ),
                            self._requested_release_mode(),
                        )
                    ):
                        # ★ 여기서 «히트»로 인정하면 상태가 cache_reused로 굳고,
                        #   그 뒤 호출자가 이 결과를 버려도 ensure_paid_phase()가
                        #   owner/bypass가 아니라며 막아 요청이 통째로 실패한다.
                        #   캐시 항목은 남아 있어 재시도도 같은 이유로 계속
                        #   실패한다. 그래서 «버리기»가 아니라 «처음부터 미적중»
                        #   으로 다뤄 그대로 owner 선정으로 내려간다(C6).
                        logger.info(
                            "요청 릴리스 모드와 다른 저장본이라 재사용하지 않고 "
                            "새로 만듭니다"
                        )
                        cached = None
                    if cached is not None:
                        with self._lock:
                            self._key = key
                            self._state = "cache_reused"
                        return cached
                    acquired_at = clock.now_kst()
                    acquired = singleflight.acquire(
                        conn,
                        key=key,
                        owner_id=self.run_id,
                        now=acquired_at,
                        lease_ttl=self._bounded_owner_ttl(acquired_at),
                    )
                    if acquired.disposition is singleflight.AcquireDisposition.COMPLETED:
                        try:
                            reused = self._read_completed(
                                conn,
                                key=key,
                                content_id=acquired.completed_content_id,
                                artifact_id=acquired.completed_artifact_id,
                            )
                        except _FullOwnerAuthorityUnavailable as exc:
                            quarantined_at = clock.now_kst()
                            singleflight.expire_completed_result(
                                conn,
                                key=key,
                                content_snapshot_id=acquired.completed_content_id,
                                artifact_id=acquired.completed_artifact_id,
                                now=quarantined_at,
                            )
                            delivery_store.invalidate_cache_entry(
                                conn,
                                key=cache_key,
                                expected_content_snapshot_id=(
                                    acquired.completed_content_id
                                ),
                                expected_artifact_id=acquired.completed_artifact_id,
                                reason_code=exc.reason_code,
                                invalidated_at=quarantined_at,
                            )
                            logger.warning(
                                "FULL 완료 재사용 후보의 OWNER 권위를 격리합니다 "
                                "reason=%s",
                                exc.reason_code,
                            )
                            acquired = singleflight.acquire(
                                conn,
                                key=key,
                                owner_id=self.run_id,
                                now=quarantined_at,
                                lease_ttl=self._bounded_owner_ttl(quarantined_at),
                            )
                        else:
                            with self._lock:
                                self._key = key
                                self._state = "reused"
                            return reused
                    if acquired.disposition is singleflight.AcquireDisposition.FAILED:
                        with self._lock:
                            self._key = key
                            self._state = "failed"
                        raise generation_coordination.GenerationOwnerFailed(
                            f"먼저 시작한 보고서 생성이 실패했습니다: "
                            f"{acquired.failure_code or 'generation_failed'}"
                        )
            except generation_coordination.GenerationCoordinationError:
                raise
            except BaseException as exc:  # noqa: BLE001 - DB 미확정 뒤 중복 과금 금지
                raise GenerationSingleflightUnavailable(
                    "보고서 단일 실행 상태를 확인하지 못해 provider를 호출하지 않습니다"
                ) from exc

            if acquired.disposition in (
                singleflight.AcquireDisposition.ACQUIRED,
                singleflight.AcquireDisposition.TAKEOVER,
            ):
                if acquired.handle is None:  # pragma: no cover - primitive 계약 방어
                    raise GenerationSingleflightUnavailable(
                        "획득한 보고서 lease 표식이 없습니다"
                    )
                self._set_owner(key=key, handle=acquired.handle)
                return None
            # WAIT인 동안 owner의 lease를 바꾸지 않고, 취소 신호를 짧게 본다.
            self._cancel_wait.wait(min(WAITER_POLL_SEC, wait_remaining))

    def _refresh_owner_lease(self) -> None:
        with self._lock:
            if self._lease_error is not None:
                raise GenerationSingleflightUnavailable(
                    "lease heartbeat를 확인하지 못해 provider를 호출하지 않습니다"
                ) from self._lease_error
            handle = self._handle
        if handle is None:
            return
        heartbeat_at = clock.now_kst()
        self._require_provider_admission_time(handle, heartbeat_at)
        lease_ttl = self._bounded_heartbeat_ttl(handle, heartbeat_at)
        try:
            with storage_db.connect() as conn:
                refreshed = singleflight.heartbeat(
                    conn,
                    handle=handle,
                    now=heartbeat_at,
                    lease_ttl=lease_ttl,
                )
        except BaseException as exc:  # noqa: BLE001 - provider 전이므로 안전하게 중단
            raise GenerationSingleflightUnavailable(
                "provider 호출 전 lease heartbeat를 저장하지 못했습니다"
            ) from exc
        if refreshed is None:
            raise GenerationSingleflightUnavailable(
                "provider 호출 전 보고서 생성 lease를 잃었습니다"
            )
        with self._lock:
            self._handle = refreshed

    def ensure_paid_phase(self) -> None:
        """owner/bypass만 첫 provider 전에 비용 phase와 attempt 문맥을 연다."""

        build_identity_contract.assert_engine_build_identity_current(
            self._frozen_build_identity
        )

        if self._cancel_wait.is_set():
            raise generation_coordination.GenerationWaitCancelled(
                "취소된 보고서 요청에서 provider를 시작할 수 없습니다"
            )
        with self._lock:
            state = self._state
            provider_context_is_open = self._provider_stack is not None
        if state not in {"owner", "bypass"}:
            raise GenerationSingleflightUnavailable(
                "보고서 owner 확정 전에는 provider를 호출할 수 없습니다"
            )
        # context가 이미 열렸더라도 provider 호출마다 lease 오류와 fencing을
        # 다시 확인한다. 첫 호출 뒤 heartbeat가 죽었는데 여기서 곧장 return하면
        # takeover owner와 다음 provider 호출이 겹쳐 이중 과금될 수 있다.
        if state == "owner":
            self._refresh_owner_lease()
        else:
            # 부분 지문으로 single-flight를 우회해도 같은 요청 전체 마감은
            # 우회하지 않는다. 그렇지 않으면 bypass 경로만 호출을 영원히 보낼 수 있다.
            self._require_provider_admission_time(None, clock.now_kst())
        if provider_context_is_open:
            return
        ticket = paid_runtime._begin_paid_phase(
            run_id=self.run_id,
            phase=SPEND_PHASE_PIPELINE,
            share_key=self.share_key,
            cap_krw=self.cap_krw,
        )
        if ticket is None:
            raise PaidGenerationAdmissionUnavailable(
                "본조사 비용 한도 예약을 얻지 못했습니다"
            )
        if self._cancel_wait.is_set():
            paid_runtime._cancel_paid_phase(ticket)
            raise generation_coordination.GenerationWaitCancelled(
                "비용 phase 예약 중 요청이 취소되어 provider를 호출하지 않습니다"
            )
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(paid_runtime._activate_paid_provider(ticket))
        except BaseException:
            paid_runtime._cancel_paid_phase(ticket)
            raise
        with self._lock:
            # 한 worker 문맥에서만 불리므로 중복 진입은 계약 위반이다.
            if self._provider_stack is not None:  # pragma: no cover - 동일 스레드 방어
                stack.close()
                paid_runtime._cancel_paid_phase(ticket)
                return
            self._paid_phase = ticket
            self._provider_stack = stack
        self.on_paid_phase(ticket)

    def close_provider_context(self) -> None:
        """pipeline worker 스레드에서 ContextVar를 제자리로 돌린다."""

        with self._lock:
            stack = self._provider_stack
            self._provider_stack = None
        if stack is not None:
            stack.close()

    def complete(
        self,
        content_snapshot_id: str,
        artifact_id: str,
        *,
        cache_eligible: bool,
    ) -> None:
        """불변 content 저장까지 끝난 owner만 waiter를 풀어 준다.

        장기 캐시 불가 결과도 같은 순간 대기자에게는 2분 동안 공유해 중복
        과금을 막는다. 이때 정식 캐시 결속만 요구하지 않으며, fan-out 만료 뒤
        다음 요청은 새 owner가 된다.
        """

        with self._lock:
            handle = self._handle
            is_owner = self._state == "owner"
        if not is_owner or handle is None:
            return
        self._stop_heartbeat.set()
        try:
            with storage_db.connect_explicit_commit() as conn:
                cache_key: CacheLookupKey | None = None
                content = delivery_store.load_content_snapshot(
                    conn, content_snapshot_id
                )
                artifact = delivery_artifact.load_artifact_metadata(
                    conn, artifact_id
                )
                if (
                    content is None
                    or artifact is None
                    or artifact.content_snapshot_id != content.content_id
                ):
                    raise GenerationSingleflightUnavailable(
                        "owner의 content와 최초 PDF artifact 결속이 없습니다"
                    )
                if content.cache_namespace_id != handle.key.cache_namespace_id:
                    raise GenerationSingleflightUnavailable(
                        "owner의 content와 lease 생성기 신원이 다릅니다"
                    )
                if content.engine_epoch_digest != handle.key.engine_epoch_digest:
                    raise GenerationSingleflightUnavailable(
                        "owner의 content와 lease engine epoch가 다릅니다"
                    )
                source = delivery_store.load_source_snapshot(
                    conn, content.source_snapshot_id
                )
                if (
                    source is None
                    or source.preflight_identity_digest
                    != handle.key.source_identity_digest
                ):
                    raise GenerationSingleflightUnavailable(
                        "owner의 content와 lease 출처 신원이 다릅니다"
                    )
                if cache_eligible:
                    namespace = self.cache_namespace
                    if namespace is None:
                        raise GenerationSingleflightUnavailable(
                            "owner의 cache namespace 원본을 잃었습니다"
                        )
                    cache_key = CacheLookupKey.from_preflight(
                        billing_bucket_id=handle.key.billing_bucket_id,
                        corp_id=handle.key.corp_id,
                        namespace=namespace,
                        preflight_identity_digest=handle.key.source_identity_digest,
                        preflight_cache_usable=True,
                        engine_epoch_digest=handle.key.engine_epoch_digest,
                    )
                    cached = delivery_store.load_cache_hit(
                        conn,
                        key=cache_key,
                        policy=DeliveryPolicy(
                            content_max_age=dt.timedelta(
                                days=REPORT_LINK_MAX_AGE_DAYS
                            ),
                            public_link_lifetime=dt.timedelta(
                                days=REPORT_LINK_MAX_AGE_DAYS
                            ),
                        ),
                        delivered_at=clock.now_kst(),
                    )
                    if (
                        cached is None
                        or cached.content.content_id != content.content_id
                        or cached.artifact_id != artifact.artifact_id
                    ):
                        raise GenerationSingleflightUnavailable(
                            "owner의 content·PDF가 정식 캐시에 결속되지 않았습니다"
                        )
                # content/cache 검증이 끝난 뒤, COMPLETED UPDATE와 같은 거래에서
                # 생성 시작 신원을 다시 읽는다. 달라졌으면 waiter 증거를 쓰지 않는다.
                build_identity_contract.assert_engine_build_identity_current(
                    self._frozen_build_identity
                )
                completed_at = clock.now_kst()
                completed = singleflight.complete(
                    conn,
                    handle=handle,
                    content_snapshot_id=content_snapshot_id,
                    artifact_id=artifact_id,
                    now=completed_at,
                    result_fanout_ttl=RESULT_FANOUT_TTL,
                )
                # UPDATE 도중 drift가 생겼으면 connect context가 commit하기 전에
                # 예외를 내 전체 거래를 rollback한다.
                build_identity_contract.assert_engine_build_identity_current(
                    self._frozen_build_identity
                )
                try:
                    _commit_connection(conn)
                except sqlite3.Error:
                    # SQLite commit이 실제로 끝난 뒤 응답만 잃을 수 있다. rollback
                    # 뒤 exact key·epoch·content·artifact 완료행이 보일 때만 성공을
                    # 복구하고, 그 외에는 원래 오류를 그대로 실패 처리한다.
                    conn.rollback()
                    if not _completion_receipt_matches_exactly(
                        conn,
                        handle=handle,
                        content_snapshot_id=content_snapshot_id,
                        artifact_id=artifact_id,
                        completed_at=completed_at,
                        cache_key=cache_key,
                    ):
                        _quarantine_completion_receipt(
                            conn,
                            handle=handle,
                            cache_key=cache_key,
                            committed_completion_known=False,
                            quarantined_at=clock.now_kst(),
                        )
                        raise
                else:
                    # 성공 응답도 직후 영수증 한 벌을 다시 읽는다. 다른 연결이
                    # commit 뒤 cache/완료표를 바꾼 TOCTOU를 성공으로 내보내지 않는다.
                    if not _completion_receipt_matches_exactly(
                        conn,
                        handle=handle,
                        content_snapshot_id=content_snapshot_id,
                        artifact_id=artifact_id,
                        completed_at=completed_at,
                        cache_key=cache_key,
                    ):
                        _quarantine_completion_receipt(
                            conn,
                            handle=handle,
                            cache_key=cache_key,
                            committed_completion_known=True,
                            quarantined_at=clock.now_kst(),
                        )
                        raise GenerationSingleflightUnavailable(
                            "완료 commit 영수증이 저장 결과와 다릅니다"
                        )
                try:
                    build_identity_contract.assert_engine_build_identity_current(
                        self._frozen_build_identity
                    )
                except (RuntimeError, TypeError, ValueError):
                    _quarantine_completion_receipt(
                        conn,
                        handle=handle,
                        cache_key=cache_key,
                        committed_completion_known=True,
                        quarantined_at=clock.now_kst(),
                    )
                    raise
        except BaseException as exc:  # noqa: BLE001
            raise GenerationSingleflightUnavailable(
                "완료된 보고서를 waiter에게 공유하지 못했습니다"
            ) from exc
        if not completed:
            raise GenerationSingleflightUnavailable(
                "완료 직전 보고서 생성 lease 소유권을 잃었습니다"
            )
        with self._lock:
            self._state = "completed"

    def fail(self, failure_code: str = "generation_failed") -> None:
        """owner 실패를 짧게 fan-out해 동시 재호출 폭주를 막는다."""

        with self._lock:
            handle = self._handle
            is_owner = self._state == "owner"
        if not is_owner or handle is None:
            return
        self._stop_heartbeat.set()
        try:
            with storage_db.connect() as conn:
                failed = singleflight.fail(
                    conn,
                    handle=handle,
                    failure_code=failure_code,
                    now=clock.now_kst(),
                    failure_fanout_ttl=FAILURE_FANOUT_TTL,
                )
        except BaseException as exc:  # noqa: BLE001
            raise GenerationSingleflightUnavailable(
                "보고서 생성 실패 fan-out을 저장하지 못했습니다"
            ) from exc
        if failed:
            with self._lock:
                self._state = "failed"

    def cancel_waiter(self) -> None:
        """waiter만 깨우고 owner lease는 바꾸지 않는다."""

        self._cancel_wait.set()

    def abandon(self) -> None:
        """강제 종료는 성공·실패를 지어내지 않고 lease 만료에 맡긴다."""

        self._cancel_wait.set()
        self._stop_heartbeat.set()
