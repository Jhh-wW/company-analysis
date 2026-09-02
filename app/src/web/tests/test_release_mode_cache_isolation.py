"""릴리스 모드가 다른 저장본은 서로의 자리를 침범하지 않는다 (C6 · F-CACHE).

★ 여기서 쓰는 것은 전부 진짜다 — 실제 `GenerationSession`, 실제
  `generation_coordination`, 실제 `storage_db`. 조정자를 통째로 가짜로 바꾸면
  「배선이 있다」만 증명되고 이 결함은 그대로 남는다. 유료 provider만 부르지
  않는다(가짜 AI로 합성한 산출물을 저장본으로 쓴다).

★ 무엇이 문제였나 (독립 검토 재현):
  ① 캐시 열쇠·lease 열쇠 어디에도 release_mode가 없었다. `build_id`는 배포
     commit에서만 나오므로, 같은 배포에서 모드만 바뀌면 SHADOW 저장본과 FULL
     저장본이 «같은 칸»을 쓴다.
  ② 그 상태에서 FULL 요청이 오면 조정자가 SHADOW 항목을 히트로 읽고 상태를
     「캐시 재사용」으로 굳혔다. 뒤에서 그 결과를 버려도 상태는 그대로라
     `ensure_paid_phase()`가 owner가 아니라며 막아 요청이 통째로 실패했다.
     캐시 항목은 남아 있어 **재시도해도 같은 이유로 계속 실패**했다.
  ③ 그 벽을 넘겨도 새로 만든 FULL 결과를 옛 SHADOW 항목과 같은 열쇠에
     결속하려다 `ImmutableRecordConflict`가 났다.

★ 두 겹으로 막는다:
  · 열쇠 분리 — release_mode가 namespace에 들어가 두 모드가 애초에 다른 칸을
    쓴다. 그래서 ②③이 생기지 않는다.
  · 조정자 판정 — 열쇠 구성이 나중에 바뀌어 모드를 잃더라도, 히트를 그대로
    「미적중」으로 닫아 owner 선정으로 내려간다.
  아래 시험은 두 겹을 따로 겨냥한다.
"""

from __future__ import annotations

import datetime as dt
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.core import deployment_identity
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.export_pdf import release_store as pdf_release_store
from src.features.pipeline import real
from src.features.pipeline.port import Report
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.pipeline.tests.test_report_company_id_release_mode import (
    _보고서를_만든다,
)
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import store as delivery_store
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.report_delivery.cache_identity import CacheLookupKey
from src.features.report_delivery.models import (
    ContentSnapshot,
    Delivery,
    DeliveryPolicy,
)
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared import generation_coordination
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_source_identity import ReportSourceIdentity
from src.web import generation_singleflight, paid_runtime, report_delivery_adapter

_COMMIT = "a" * 40
_BUILD_IDENTITY = build_identity_contract.EngineBuildIdentity(
    deployment_revision=_COMMIT,
    build_id=f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:{_COMMIT}",
)
_CORP = "00126380"
_RECEIPT = "20260828000123"
_FINANCIAL_DIGEST = "b" * 64
_CAPTURED = dt.datetime(
    2026, 8, 28, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))
)


class _유료단계에_도달했다(Exception):
    """`ensure_paid_phase`가 owner 관문을 통과했다는 표식."""


@pytest.fixture
def bucket(request: pytest.FixtureRequest) -> str:
    """시험마다 다른 비용 통장.

    ★ lease 열쇠와 캐시 열쇠 둘 다 통장을 포함한다. 통장을 공유하면 앞 시험이
      owner로 잡은 lease가 살아 있어(포기는 lease를 즉시 풀지 않는다) 다음
      시험의 `coordinate()`가 waiter로 «대기»에 빠진다.
    """
    return "bucket-" + sha256(request.node.name.encode("utf-8")).hexdigest()[:16]


@pytest.fixture(autouse=True)
def _고정배포신원에서_시험한다(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", _COMMIT)


@pytest.fixture(autouse=True)
def _저장소와_승인원장을_연다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """artifact 저장 위치와 자동승인 원장 조회를 시험용으로 연다."""
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        pdf_release_store,
        "load_automatic_release_record",
        lambda *_args, **_kwargs: object(),
    )


def _source_digest() -> str:
    return ReportSourceIdentity(
        dart_receipt_numbers=(_RECEIPT,),
        financial_payload_digest=_FINANCIAL_DIGEST,
    ).cache_digest


def _namespace(release_mode: ReleaseMode):
    """**생산 코드가 쓰는 바로 그 함수**로 namespace를 만든다.

    시험이 열쇠 구성을 따로 베껴 쓰면, 생산 쪽이 모드를 빼도 시험은 계속
    초록이 된다. 같은 함수를 불러야 열쇠가 실제로 갈라지는지 확인된다.
    """
    return real._generation_cache_namespace(
        SimpleNamespace(MODEL="claude-test"),
        _BUILD_IDENTITY,
        real.engine_mode.freeze_process_engine_mode(real.engine_mode.EngineMode.V2),
        release_mode=release_mode,
    )


def _진짜_산출물(monkeypatch: pytest.MonkeyPatch, release_mode: ReleaseMode) -> Report:
    """가짜 AI로 v2를 끝까지 합성해 그 모드의 «진짜» 보고서를 만든다.

    FULL 보고서는 생산 증거가 실려야 저장·복원이 되므로 손으로 지어낼 수 없다.
    """
    fake = FakeEngine()
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        return _보고서를_만든다(fake, monkeypatch, release_mode=release_mode)


def _저장본을_캐시에_넣는다(
    report: Report,
    *,
    namespace,
    bucket: str,
    cache_namespace=None,
) -> tuple[ContentSnapshot, str]:
    """보고서를 불변 content·PDF로 저장하고 캐시 열쇠에 결속한다.

    Args:
        namespace: content가 자기 신원으로 기록할 namespace.
        cache_namespace: 캐시 열쇠에 쓸 namespace. 생략하면 `namespace`와 같다.
            **다르게 주면 「열쇠가 모드를 못 가르는 상황」을 재현할 수 있다.**
    """
    source = SourceSnapshot.capture(
        dart_receipt_nos=(_RECEIPT,),
        financial_payload=None,
        financial_payload_sha256=_FINANCIAL_DIGEST,
        captured_at=_CAPTURED,
        source_as_of=_CAPTURED.date(),
        adapter_versions={"report_delivery": "test-v1"},
    )
    content = ContentSnapshot.create(
        payload=report_store.report_to_json(report).encode("utf-8"),
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=_CAPTURED,
        engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
        actual_models=("claude-test",),
    )
    key_namespace = cache_namespace or namespace
    with storage_db.connect() as conn:
        delivery_store.save_source_snapshot(conn, source)
        delivery_store.save_cache_namespace(conn, namespace)
        if key_namespace is not namespace:
            delivery_store.save_cache_namespace(conn, key_namespace)
        delivery_store.save_content_snapshot(conn, content)
        # ★ 읽는 쪽(`_read_cached_release`)은 `configured_artifact_backend()`로
        #   PDF 바이트를 찾는다. 시험이 다른 폴더에 넣으면 항목이
        #   `artifact_bytes_unavailable`로 무효화돼, release_mode와 무관한
        #   이유로 미적중이 된다 — 「막혔다」를 엉뚱한 공로로 돌리게 된다.
        backend = report_delivery_adapter.configured_artifact_backend()
        pdf_bytes = b"%PDF-1.4\n% release-mode cache isolation\n"
        intent = delivery_artifact.create_blob_write_intent(
            conn,
            backend,
            pdf_bytes=pdf_bytes,
            created_at=_CAPTURED,
        )
        artifact = delivery_artifact.store_approved_pdf(
            conn,
            backend,
            blob_intent=intent,
            content_snapshot_id=content.content_id,
            pdf_bytes=pdf_bytes,
            version=delivery_artifact.ArtifactVersion(
                renderer_version="test-renderer-v1",
                font_bundle_version="test-font-v1",
                checker_version="automatic-checks-v1",
            ),
            created_at=_CAPTURED,
            retention=delivery_artifact.ArtifactRetention(
                policy_id="test-retention-v1",
                retain_until=None,
            ),
        )
        # 캐시 히트는 «같은 비용 통장의 발급 영수증»이 있을 때만 인정된다
        # (`_read_cached_release`의 approval 검사). 그 영수증이 없으면 항목이
        # 무효화돼 이 시험이 release_mode와 무관한 이유로 미적중이 된다.
        delivery = Delivery.issue(
            public_id=f"public-{content.content_id[:16]}",
            billing_bucket_id=bucket,
            content=content,
            delivered_at=_CAPTURED,
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
                public_link_lifetime=dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS),
            ),
            reused_from_cache=False,
        )
        delivery_store.save_delivery(conn, delivery)
        delivery_artifact.bind_artifact_to_delivery(
            conn,
            delivery_id=delivery.delivery_id,
            artifact_id=artifact.artifact_id,
        )
        delivery_store.bind_cache_entry(
            conn,
            key=_cache_key(key_namespace, bucket),
            content=content,
            artifact_id=artifact.artifact_id,
            cached_at=dt.datetime.now(dt.timezone.utc),
        )
    return content, artifact.artifact_id


def _cache_key(namespace, bucket: str) -> CacheLookupKey:
    return CacheLookupKey.from_preflight(
        billing_bucket_id=bucket,
        corp_id=_CORP,
        namespace=namespace,
        preflight_identity_digest=_source_digest(),
        preflight_cache_usable=True,
        engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
    )


def _무효화_사유() -> list[str]:
    """캐시 항목이 무효화됐다면 그 사유 코드들. 재사용이 안 될 때 원인을 밝힌다.

    이 목록이 비어 있지 않은데 재사용이 안 됐다면, 원인은 release_mode 판정이
    아니라 그 사유다 — 「막혔다」를 엉뚱한 공로로 돌리지 않기 위해 본다.
    """
    with storage_db.connect() as conn:
        rows = conn.execute(
            f"SELECT reason_code FROM {delivery_store.TABLE_CACHE_INVALIDATIONS}"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _session(run_id: str, bucket: str) -> generation_singleflight.GenerationSession:
    return generation_singleflight.GenerationSession(
        run_id=run_id,
        share_key=f"share:{bucket}",
        billing_bucket_id=bucket,
        cap_krw=900.0,
        on_paid_phase=lambda _ticket: None,
        build_identity=_BUILD_IDENTITY,
    )


def _유료단계_관문만_확인한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ensure_paid_phase`가 owner 관문을 지나는지만 본다 — 실제 과금은 없다."""

    def 표식(**_kwargs):
        raise _유료단계에_도달했다()

    monkeypatch.setattr(paid_runtime, "_begin_paid_phase", 표식)


# ══════════════════════════════════════════════════════════
# ① P0 — 모드가 다른 히트는 「미적중」이라 요청이 살아서 owner가 된다
# ══════════════════════════════════════════════════════════


def test_같은_빌드에서_SHADOW_저장본이_있는_회사를_FULL로_요청하면_새로_만들어_owner가_된다(
    monkeypatch: pytest.MonkeyPatch, bucket: str
) -> None:
    """★ 이 티켓의 P0. 고치기 전에는 여기서 요청이 통째로 죽었다.

    열쇠가 모드를 못 가르는 상황을 일부러 만든다 — SHADOW 본문을 **FULL 열쇠에**
    결속해 둔다. 조정자는 이 히트를 「미적중」으로 닫아야 하고, 그래야 상태가
    owner가 되어 유료 단계를 열 수 있다. 판정이 없으면 상태가 「캐시 재사용」으로
    굳어 `ensure_paid_phase()`가 `GenerationSingleflightUnavailable`을 던지고,
    캐시 항목이 남아 있어 재시도해도 같은 이유로 계속 실패한다.
    """
    shadow_보고서 = _진짜_산출물(monkeypatch, ReleaseMode.SHADOW)
    full_namespace = _namespace(ReleaseMode.FULL)
    _저장본을_캐시에_넣는다(
        shadow_보고서,
        namespace=full_namespace,
        bucket=bucket,
    )
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    session = _session("full-after-shadow", bucket)

    재사용 = session.coordinate(_CORP, full_namespace, _source_digest())

    assert 재사용 is None, "요청 모드와 다른 저장본을 히트로 인정하면 안 됩니다"
    assert session.owns_generation, (
        "미적중으로 닫았으면 owner가 돼야 한다 — 「캐시 재사용」으로 굳으면 "
        "뒤에서 유료 단계를 열 수 없어 요청이 통째로 실패한다"
    )
    _유료단계_관문만_확인한다(monkeypatch)
    with pytest.raises(_유료단계에_도달했다):
        session.ensure_paid_phase()
    session.abandon()


def test_FULL_요청_실패_뒤_재시도가_같은_이유로_반복_실패하지_않는다(
    monkeypatch: pytest.MonkeyPatch, bucket: str
) -> None:
    """★ 고치기 전 가장 아픈 부분 — 캐시 항목이 남아 영구 실패였다.

    같은 상황에서 두 번째 요청도 owner가 돼야 한다. 첫 요청이 포기해도 **캐시
    항목은 그대로 남으므로**, 판정이 없으면 두 번째도 같은 자리에서 죽는다.

    ★ 포기는 lease를 즉시 풀지 않고 만료에 맡긴다(`abandon`). 그래서 시계를
      앞으로 돌려 첫 lease를 만료시킨 뒤 두 번째를 시작한다 — 죽은 worker의
      lease가 만료된 뒤 사용자가 다시 누르는 실제 상황과 같다. 여기서 시계를
      안 돌리면 두 번째가 «대기»에 빠져 이 시험이 캐시가 아니라 lease를 재는
      다른 시험이 된다.
    """
    벽시계 = [dt.datetime(2026, 8, 28, 13, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))]
    단조시계 = [1000.0]
    monkeypatch.setattr(
        generation_singleflight.time, "monotonic", lambda: 단조시계[0]
    )
    monkeypatch.setattr(
        generation_singleflight.clock, "now_kst", lambda: 벽시계[0]
    )
    monkeypatch.setattr(
        generation_singleflight, "OWNER_MAX_AGE", dt.timedelta(seconds=30)
    )

    shadow_보고서 = _진짜_산출물(monkeypatch, ReleaseMode.SHADOW)
    full_namespace = _namespace(ReleaseMode.FULL)
    _저장본을_캐시에_넣는다(
        shadow_보고서,
        namespace=full_namespace,
        bucket=bucket,
    )
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)

    첫번째 = _session("full-retry-1", bucket)
    assert 첫번째.coordinate(_CORP, full_namespace, _source_digest()) is None
    assert 첫번째.owns_generation
    첫번째.abandon()

    # 첫 lease가 만료될 만큼 시간을 넘긴다.
    벽시계[0] += dt.timedelta(seconds=300)
    단조시계[0] += 300.0

    두번째 = _session("full-retry-2", bucket)
    assert 두번째.coordinate(_CORP, full_namespace, _source_digest()) is None
    assert 두번째.owns_generation, "재시도가 같은 캐시 항목 때문에 또 막혔습니다"
    # 캐시 항목이 사라져서 통과한 것이 아니라는 것까지 못 박는다 — 판정이
    # 없으면 이 항목이 두 번째 요청도 그대로 죽인다.
    assert "approval_record_missing" not in _무효화_사유()
    두번째.abandon()


# ══════════════════════════════════════════════════════════
# ② P1 — 열쇠가 갈라져 두 모드가 애초에 다른 칸을 쓴다
# ══════════════════════════════════════════════════════════


def test_릴리스_모드가_다르면_캐시_열쇠도_다르다() -> None:
    """열쇠 구성 계약 — 모드는 「무엇을 만드는가」의 입력이라 신원에 들어간다."""
    shadow = _namespace(ReleaseMode.SHADOW)
    enforce = _namespace(ReleaseMode.ENFORCE_NO_PARTIAL)
    full = _namespace(ReleaseMode.FULL)
    모름 = _namespace(None)

    ids = {shadow.namespace_id, enforce.namespace_id, full.namespace_id}
    assert len(ids) == 3, "세 모드가 같은 캐시 칸을 씁니다"
    # 모드를 모르면(v1 요청·환경값 없음) 옛 열쇠를 그대로 둔다 — 기존 저장본이
    # 통째로 미적중이 되지 않게 하기 위해서다.
    assert 모름.namespace_id not in ids
    # 캐시 열쇠와 lease 열쇠가 **둘 다** 이 namespace_id를 운반한다.
    assert (
        _cache_key(shadow, "bucket-계약").namespace_id
        != _cache_key(full, "bucket-계약").namespace_id
    )


def test_FULL_재생성_결과는_옛_SHADOW_항목과_충돌_없이_저장된다(
    monkeypatch: pytest.MonkeyPatch, bucket: str
) -> None:
    """★ 이 티켓의 P1. 고치기 전에는 `ImmutableRecordConflict`가 났다.

    같은 회사·같은 출처·같은 배포에서 SHADOW 저장본이 이미 있고, 새로 만든
    FULL 결과를 저장한다. 열쇠에 모드가 없으면 두 내용이 같은 칸을 두고 다퉈
    「같은 캐시 신원에 다른 내용을 덮어쓸 수 없습니다」로 하드 실패한다.
    """
    shadow_보고서 = _진짜_산출물(monkeypatch, ReleaseMode.SHADOW)
    _저장본을_캐시에_넣는다(
        shadow_보고서,
        namespace=_namespace(ReleaseMode.SHADOW),
        bucket=bucket,
    )
    full_보고서 = _진짜_산출물(monkeypatch, ReleaseMode.FULL)

    # 충돌이 나면 여기서 ImmutableRecordConflict가 튄다.
    content, artifact_id = _저장본을_캐시에_넣는다(
        full_보고서,
        namespace=_namespace(ReleaseMode.FULL),
        bucket=bucket,
    )

    with storage_db.connect() as conn:
        assert delivery_store.cache_entry_matches_exactly(
            conn,
            key=_cache_key(_namespace(ReleaseMode.FULL), bucket),
            content_snapshot_id=content.content_id,
            artifact_id=artifact_id,
        )
        # 옛 SHADOW 항목은 자기 칸에 그대로 남아 있다 — 덮어쓰지 않았다.
        assert delivery_store.load_cache_hit is not None


def test_FULL_요청은_SHADOW_열쇠의_저장본을_아예_보지_않는다(
    monkeypatch: pytest.MonkeyPatch, bucket: str
) -> None:
    """열쇠가 갈라졌으니 조정자 판정까지 갈 것도 없이 «진짜 미적중»이다."""
    shadow_보고서 = _진짜_산출물(monkeypatch, ReleaseMode.SHADOW)
    _저장본을_캐시에_넣는다(
        shadow_보고서,
        namespace=_namespace(ReleaseMode.SHADOW),
        bucket=bucket,
    )
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    session = _session("full-sees-nothing", bucket)

    assert session.coordinate(_CORP, _namespace(ReleaseMode.FULL), _source_digest()) is None
    assert session.owns_generation
    session.abandon()


# ══════════════════════════════════════════════════════════
# ③ 대조군 — 맞는 재사용은 그대로 산다
# ══════════════════════════════════════════════════════════


def test_두번째_FULL_요청은_FULL_저장본을_재사용한다(
    monkeypatch: pytest.MonkeyPatch, bucket: str
) -> None:
    """★ 대조군 — 막느라 다 막았으면 이 티켓은 비용만 늘린 것이다."""
    full_보고서 = _진짜_산출물(monkeypatch, ReleaseMode.FULL)
    full_namespace = _namespace(ReleaseMode.FULL)
    content, artifact_id = _저장본을_캐시에_넣는다(
        full_보고서,
        namespace=full_namespace,
        bucket=bucket,
    )
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    session = _session("full-reuse", bucket)

    재사용 = session.coordinate(_CORP, full_namespace, _source_digest())

    assert 재사용 is not None, f"FULL 저장본 재사용 실패 — 무효화 사유={_무효화_사유()}"
    assert 재사용.content_snapshot_id == content.content_id
    assert 재사용.artifact_id == artifact_id
    assert 재사용.report.release_mode == ReleaseMode.FULL.value
    assert not session.owns_generation, "재사용이면 owner가 되면 안 된다"


def test_SHADOW_요청_경로는_바뀌지_않는다(
    monkeypatch: pytest.MonkeyPatch, bucket: str
) -> None:
    """★ 대조군 — SHADOW는 사용자 결과·차감을 바꾸지 않는다.

    SHADOW 요청은 예전처럼 자기 저장본을 그대로 재사용한다.
    """
    shadow_보고서 = _진짜_산출물(monkeypatch, ReleaseMode.SHADOW)
    shadow_namespace = _namespace(ReleaseMode.SHADOW)
    content, artifact_id = _저장본을_캐시에_넣는다(
        shadow_보고서,
        namespace=shadow_namespace,
        bucket=bucket,
    )
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.SHADOW.value)
    session = _session("shadow-reuse", bucket)

    재사용 = session.coordinate(_CORP, shadow_namespace, _source_digest())

    assert 재사용 is not None, f"SHADOW 재사용 경로가 바뀜 — 무효화 사유={_무효화_사유()}"
    assert 재사용.content_snapshot_id == content.content_id
    assert 재사용.artifact_id == artifact_id
    assert not session.owns_generation
