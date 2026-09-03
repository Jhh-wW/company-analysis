"""캐시가 «돌았다는 사실»이 사용자 눈에 보이는지 못 박는다.

이 시험이 잡는 것 — **기능은 붙였는데 화면·이력이 옛말을 하는 경우**.
캐시를 붙여도 이력이 「없음」 고정이면 대시보드 ⑤는 영영 0건이고,
화면에 안내가 없으면 사용자는 방금 새로 조사한 줄 안다.

★ 여기서 두 곳을 한꺼번에 본다 — 하나만 고치면 나머지가 조용히 어긋난다.
  ① `RunResult.cache_hit` → ② 이력 1행 → ③ 결과 화면 안내

  대시보드 ⑤ 집계 시험은 뺐다. 그 식이 살던 `dashboard.html` 은 어떤 라우트도
  렌더하지 않아 지웠고, 남겨 두면 시험이 «자기가 복사해 둔 문자열을 자기가 렌더해
  자기와 비교»하는 꼴이 되어 아무것도 못 지키면서 영원히 통과한다.
"""

from __future__ import annotations

import contextlib
import datetime as dt

from src.core.constants import CACHE_HIT_LAYER1, CACHE_HIT_MESSAGE
from src.features.observability import constants as obs
from src.features.pipeline.port import Grade, Outcome, Report, RunResult, UserInput
from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.report_delivery.models import (
    ContentSnapshot,
    Delivery,
    DeliveryPolicy,
)
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.sharelink.constants import RESULT_REUSED_REPORT_NOTICE
from src.shared import engine_build_identity as build_identity_contract
from src.web import recording
from src.web.routers import reports as reports_router

# ══════════════════════════════════════════════════════════
# ① 이력 — 파이프라인이 실은 값이 그대로 실려야 한다
# ══════════════════════════════════════════════════════════


def test_캐시로_돌려준_요청은_이력에_1층으로_남는다(tmp_path, monkeypatch):
    monkeypatch.setattr(recording.paths, "APP_ROOT", tmp_path)
    result = RunResult(
        outcome=Outcome.REPORT,
        message=CACHE_HIT_MESSAGE.format(generated_at="2026-08-15"),
        cache_hit=CACHE_HIT_LAYER1,
    )

    recording.record_run(UserInput(company="가나다", job="영업", region=""), result, 1.0)

    written = recording.records_path().read_text(encoding="utf-8")
    assert f'"cache_hit": "{obs.CACHE_HIT_L1}"' in written


def test_새로_조사한_요청은_이력에_없음으로_남는다(tmp_path, monkeypatch):
    """캐시를 안 썼는데 「썼다」고 적으면 비용 지표가 통째로 거짓이 된다."""
    monkeypatch.setattr(recording.paths, "APP_ROOT", tmp_path)
    result = RunResult(outcome=Outcome.REPORT)

    recording.record_run(UserInput(company="가나다", job="영업", region=""), result, 1.0)

    written = recording.records_path().read_text(encoding="utf-8")
    assert f'"cache_hit": "{obs.CACHE_HIT_NONE}"' in written


# ══════════════════════════════════════════════════════════
# ② 결과 화면 — 「저장해 둔 것」이라고 말해야 한다
# ══════════════════════════════════════════════════════════


def test_캐시_안내_문구는_조사한_날짜를_반드시_담는다():
    """날짜 없이 「저장된 결과」라고만 하면 «언제 것인지» 알 수 없다.

    3년 지난 자료로 자소서를 쓰면 안 된다 — 신선도는 사용자가 판단할 몫이다.
    """
    message = CACHE_HIT_MESSAGE.format(generated_at="2026-08-15")

    assert "2026-08-15" in message


def test_결과화면_템플릿이_캐시일_때만_안내를_그린다():
    """조건을 빼먹으면 새로 조사한 보고서에도 「저장해 둔 결과」가 붙는다."""
    from src.core import paths

    template = (
        paths.APP_ROOT / "src" / "web" / "templates" / "result.html"
    ).read_text(encoding="utf-8")

    assert "result.cache_hit" in template, (
        "결과 화면이 캐시 여부를 안 읽습니다 — 캐시가 돌아도 사용자가 모릅니다"
    )
    assert "result.message" in template


def test_결과화면_출처표는_공통_공개citation_목록만_순회한다() -> None:
    from src.core import paths

    template = (
        paths.APP_ROOT / "src" / "web" / "templates" / "result.html"
    ).read_text(encoding="utf-8")

    assert "{% if public_citations %}" in template
    assert "{% for c in public_citations %}" in template
    assert "{% for c in report.citations %}" not in template


# ══════════════════════════════════════════════════════════
# ③ 결과 화면 — 다시 보여주는 보고서는 «만든 날짜»를 말해야 한다
# ══════════════════════════════════════════════════════════


def _저장된_전달기록(*, 다시_보여주는가: bool) -> Delivery:
    """실제 발급 규칙을 그대로 태워 만든 전달 기록 한 벌."""

    commit = "c" * 40
    identity = build_identity_contract.EngineBuildIdentity(
        deployment_revision=commit,
        build_id=f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:{commit}",
    )
    made = dt.datetime(2026, 8, 15, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    source = SourceSnapshot.capture(
        dart_receipt_nos=("20260815000123",),
        financial_payload=None,
        financial_payload_sha256="d" * 64,
        captured_at=made,
        source_as_of=made.date(),
        adapter_versions={"report_delivery": "test-v1"},
    )
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version="company-report-v2-composer",
        deployment_revision=commit,
        image_digest=f"generator-build:{identity.build_id}",
        requested_models={"pipeline": "claude-test"},
        output_settings={"temperature": 0},
    )
    content = ContentSnapshot.create(
        payload=b"reused-report-payload",
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=made,
        engine_epoch_digest=identity.epoch_digest,
        actual_models=("claude-test",),
    )
    return Delivery.issue(
        public_id="report-1",
        billing_bucket_id="bucket-a",
        content=content,
        delivered_at=made + dt.timedelta(days=3),
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        reused_from_cache=다시_보여주는가,
    )


def _저장기록을_대신_읽게_한다(monkeypatch, delivery: Delivery) -> None:
    monkeypatch.setattr(
        reports_router.storage_db,
        "connect_readonly_existing",
        lambda: contextlib.nullcontext(object()),
    )
    monkeypatch.setattr(
        reports_router.delivery_store,
        "load_delivery_by_public_id",
        lambda _conn, _public_id: delivery,
    )


def _보고서(generated_at: str = "2026-08-15") -> Report:
    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
        generated_at=generated_at,
        schema_version="company-report-v2-composer",
        as_of_date=generated_at,
    )


def test_다시_보여주는_보고서에는_원본을_만든_날짜가_붙는다(monkeypatch):
    """안내가 없으면 손님은 «오늘 새로 조사한 것»으로 읽는다.

    두 달 전 숫자로 자소서를 쓰면 통째로 어긋난다 — 날짜는 손님이 판단할 몫이다.
    """
    _저장기록을_대신_읽게_한다(monkeypatch, _저장된_전달기록(다시_보여주는가=True))

    chrome = reports_router._link_result_chrome(
        _보고서(),
        bound_report=False,
        public_id="report-1",
    )

    assert chrome.freshness_note == RESULT_REUSED_REPORT_NOTICE.format(
        made_on="2026년 8월 15일"
    )


def test_새로_만든_보고서에는_다시_보여준다는_안내를_붙이지_않는다(monkeypatch):
    """조건을 빼먹으면 방금 조사한 보고서에도 「다시 보여드립니다」가 붙는다."""
    _저장기록을_대신_읽게_한다(monkeypatch, _저장된_전달기록(다시_보여주는가=False))

    chrome = reports_router._link_result_chrome(
        _보고서(),
        bound_report=False,
        public_id="report-1",
    )

    assert chrome.freshness_note == ""


def test_다시_보여준다는_안내는_내부_용어를_쓰지_않는다():
    """손님 화면에 만든 쪽 낱말이 새면 무슨 말인지 알 수 없다."""
    for 내부어 in ("캐시", "cache", "delivery", "LINK", "MEMBER", "재사용"):
        assert 내부어 not in RESULT_REUSED_REPORT_NOTICE
