"""FULL 요청은 FULL로 만든 저장본만 캐시에서 재사용한다 (C6).

★ 왜 이 시험이 필요한가:
  v2 캐시 열쇠(`storage/cache.py`의 `_v2_requirements`)에는 release_mode가
  없다. 재료는 schema·build_id·출처 지문뿐이고, `build_id`는 배포 commit에서만
  나온다(`shared/engine_build_identity.py`의 `_namespace`). 그래서 **같은 배포에서
  릴리스 모드만 바꾸면 열쇠가 그대로**다. 재사용 경로도 저장본의 release_mode를
  지금 요청의 모드와 대조하지 않았다. 결과적으로 SHADOW로 만든 보고서가 FULL
  요청에 그대로 나갈 수 있었다 — FULL의 봉인·생산 증거·품질 게이트를 한 번도
  지나지 않은 산출물이 FULL인 척 나가는 것이라 거짓 표기다.

★ 여기서 지키는 것:
  ① FULL 요청은 SHADOW 저장본을 재사용하지 않고 새로 만든다.
  ② 비FULL 요청의 재사용은 예전 그대로다 (사용자 결과·차감 불변, I9).
  ③ FULL 저장본은 FULL 요청에 그대로 재사용된다 (막느라 다 막지 않았다).

★ 재사용 겹이 «둘»이라 둘 다 본다:
  · coordination — 운영 웹 경로(`generation_coordination.coordinate`)
  · 옛 1층 캐시 — demo·단위 경로(`_v2_cache_lookup`, coordination이 꺼졌을 때)
  한 겹만 막으면 다른 겹으로 그대로 샌다. 그래서 모든 시험을 두 겹에 대해
  각각 돌린다.

★ 진짜 엔진·AI·네트워크는 부르지 않는다 — 저장본도 가짜 AI로 합성한다.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from src.core import deployment_identity
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.pipeline import real
from src.features.pipeline.port import (
    CompanyCard,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.pipeline.tests.test_real_cache import (
    CORP_ID,
    JOB,
    POSTING,
    FakeEngine,
)
from src.features.pipeline.tests.test_report_company_id_release_mode import (
    _보고서를_만든다,
)
from src.features.storage import cache as cache_store
from src.shared import generation_coordination
from src.shared.report_evidence.constants import ReleaseMode

#: 「생성기를 새로 불렀다」를 알아보는 표식. 이 글자가 결과에 실리면 재사용이
#: 아니라 새 생성으로 갔다는 뜻이다.
_새로_만들었다 = "새-생성-표식"

#: 재사용 겹 이름 — 두 겹 모두에서 같은 규칙이 서야 한다.
_겹_조정 = "coordination"
_겹_옛캐시 = "layer1"
_재사용_겹 = (_겹_조정, _겹_옛캐시)


@pytest.fixture(autouse=True)
def _검증된_배포에서_시험한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """배포 신원이 확정된 상태에서만 v2 경로가 열린다 — 그 전제를 만든다."""
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)


@pytest.fixture(autouse=True)
def _유료_문맥에서_시험한다(monkeypatch: pytest.MonkeyPatch):
    """웹 worker와 같은 예산·시도 문맥을 연다 (가짜 provider라 돈은 안 나간다)."""
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.SHADOW.value)
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        yield


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """진짜 엔진 대신 가짜를 끼운다 — 이 시험에서 돈이 나갈 길이 없다."""
    fake = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: ((CORP_ID, "가나다전자", "", "000001", "20260819"),),
    )
    return fake


def _조사한다() -> RunResult:
    """사용자 요청 하나를 처음부터 끝까지 흘린다 (재사용 판정을 포함해서)."""
    user_input = UserInput(
        company="가나다전자", job=JOB, region="서울 강남구", posting_text=POSTING
    )
    card = CompanyCard(
        legal_name="가나다전자",
        typed_name="가나다전자",
        address="서울특별시 강남구 테헤란로 1",
        ceo="홍길동",
        founded="20000101",
        ref=CORP_ID,
    )
    return real.RealPipeline().run(user_input, card)


def _생성기를_표식으로_바꾼다(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """새 생성으로 가면 진짜 생성기 대신 표식을 돌려준다.

    재사용이 막혔는지 보려고 진짜 AI 합성을 한 번 더 돌릴 이유가 없다.
    """
    calls: list[dict[str, Any]] = []

    def 표식(**kwargs: Any) -> RunResult:
        calls.append(kwargs)
        return RunResult(
            outcome=Outcome.REPORT, message=_새로_만들었다, charged=True
        )

    monkeypatch.setattr(real, "_run_v2_composer", 표식)
    return calls


def _저장본을_끼운다(
    monkeypatch: pytest.MonkeyPatch, *, 겹: str, 저장본: Report
) -> None:
    """지정한 재사용 겹이 이 저장본을 물어 오게 만든다."""
    if 겹 == _겹_조정:
        monkeypatch.setattr(real.generation_coordination, "is_active", lambda: True)
        monkeypatch.setattr(
            real.generation_coordination,
            "coordinate",
            lambda **_kwargs: generation_coordination.ReusedGeneration(
                content_snapshot_id="c" * 32,
                artifact_id="d" * 32,
                report=저장본,
                actual_models=("가짜모델",),
                generation_cache_eligible=True,
            ),
        )
        # 이 겹이 켜지면 옛 1층 캐시는 아예 보지 않는다 — 그래도 명시적으로
        # 막아 «어느 겹이 물어 왔는지»를 헷갈리지 않게 한다.
        monkeypatch.setattr(
            real,
            "_v2_cache_lookup",
            lambda **_kwargs: pytest.fail("coordination 겹에서 옛 캐시를 보면 안 됩니다"),
        )
    elif 겹 == _겹_옛캐시:
        monkeypatch.setattr(real.generation_coordination, "is_active", lambda: False)
        monkeypatch.setattr(
            real.generation_coordination, "coordinate", lambda **_kwargs: None
        )
        monkeypatch.setattr(real, "_v2_cache_lookup", lambda **_kwargs: 저장본)
    else:  # pragma: no cover - 오타 방어
        raise AssertionError(f"모르는 재사용 겹입니다: {겹}")


def _요청을_흘린다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    저장본_모드: ReleaseMode,
    요청_모드: ReleaseMode,
    겹: str,
) -> tuple[RunResult, Report, list[dict[str, Any]]]:
    """`저장본_모드`로 만든 보고서가 캐시에 있을 때 `요청_모드` 요청을 흘린다.

    Returns:
        (결과, 끼워 둔 저장본, 새 생성 호출 기록)
    """
    저장본 = _보고서를_만든다(engine, monkeypatch, release_mode=저장본_모드)
    # 저장본이 옛 재사용 거부 사슬(지표 없음·엄격인데 관측 없음)에 걸려
    # 엉뚱한 이유로 미적중이 되면 이 시험이 거짓 초록이 된다. 먼저 못 박는다.
    assert 저장본.generation_metrics is not None, (
        "저장본에 생성 지표가 없어 release_mode와 무관하게 거부됩니다 — "
        "이 시험은 그 상태로는 아무것도 증명하지 못합니다"
    )
    if 저장본_모드 is not ReleaseMode.SHADOW:
        assert 저장본.quality_observation is not None

    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, 요청_모드.value)
    _저장본을_끼운다(monkeypatch, 겹=겹, 저장본=저장본)
    새_생성 = _생성기를_표식으로_바꾼다(monkeypatch)
    return _조사한다(), 저장본, 새_생성


# ══════════════════════════════════════════════════════════
# ① FULL 요청은 비FULL 저장본을 재사용하지 않는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("겹", _재사용_겹)
def test_FULL_요청은_SHADOW_저장본을_재사용하지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, 겹: str
) -> None:
    """FULL의 봉인·증거·품질 게이트를 지나지 않은 산출물이 FULL인 척 나가면 안 된다.

    막는 방식은 오류가 아니라 «미적중»이다 — 사용자는 새로 만든 결과를 받는다.
    """
    결과, 저장본, 새_생성 = _요청을_흘린다(
        engine,
        monkeypatch,
        저장본_모드=ReleaseMode.SHADOW,
        요청_모드=ReleaseMode.FULL,
        겹=겹,
    )

    assert 결과.report is not 저장본, f"{겹} 겹이 SHADOW 저장본을 FULL 요청에 내보냈습니다"
    assert 결과.message == _새로_만들었다
    assert len(새_생성) == 1, "재사용을 막았으면 새로 만들어야 한다 (오류로 끝내지 않는다)"


@pytest.mark.parametrize("겹", _재사용_겹)
def test_FULL_요청은_ENFORCE_저장본도_재사용하지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, 겹: str
) -> None:
    """SHADOW만이 아니다 — ENFORCE_NO_PARTIAL도 FULL 게이트를 지나지 않았다."""
    결과, 저장본, 새_생성 = _요청을_흘린다(
        engine,
        monkeypatch,
        저장본_모드=ReleaseMode.ENFORCE_NO_PARTIAL,
        요청_모드=ReleaseMode.FULL,
        겹=겹,
    )

    assert 결과.report is not 저장본
    assert 결과.message == _새로_만들었다
    assert len(새_생성) == 1


# ══════════════════════════════════════════════════════════
# ② 비FULL 요청의 재사용은 예전 그대로 (I9 — 결과·차감 불변)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("겹", _재사용_겹)
@pytest.mark.parametrize(
    "저장본_모드",
    [ReleaseMode.SHADOW, ReleaseMode.ENFORCE_NO_PARTIAL, ReleaseMode.FULL],
    ids=["shadow저장본", "enforce저장본", "full저장본"],
)
@pytest.mark.parametrize(
    "요청_모드",
    [ReleaseMode.SHADOW, ReleaseMode.ENFORCE_NO_PARTIAL],
    ids=["shadow요청", "enforce요청"],
)
def test_비FULL_요청의_재사용은_그대로다(
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    겹: str,
    저장본_모드: ReleaseMode,
    요청_모드: ReleaseMode,
) -> None:
    """★ 대조군 — 막느라 비FULL까지 막지 않았다.

    비FULL 요청은 어느 저장본이든 예전처럼 재사용한다. 여기가 빨간불이면
    캐시 적중률이 떨어져 조사마다 본조사 비용이 새로 나간다.
    """
    결과, 저장본, 새_생성 = _요청을_흘린다(
        engine,
        monkeypatch,
        저장본_모드=저장본_모드,
        요청_모드=요청_모드,
        겹=겹,
    )

    assert 결과.report is 저장본, f"{겹} 겹의 비FULL 재사용이 막혔습니다"
    assert 새_생성 == [], "재사용했으면 생성기를 부르면 안 된다"
    assert 결과.charged is False, "캐시 반환은 0원 차감이다"


# ══════════════════════════════════════════════════════════
# ③ FULL 저장본은 FULL 요청에 그대로 재사용된다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("겹", _재사용_겹)
def test_FULL_저장본은_FULL_요청에_재사용된다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, 겹: str
) -> None:
    """★ 대조군 — 맞는 재사용까지 막았으면 이 티켓은 비용만 늘린 것이다."""
    결과, 저장본, 새_생성 = _요청을_흘린다(
        engine,
        monkeypatch,
        저장본_모드=ReleaseMode.FULL,
        요청_모드=ReleaseMode.FULL,
        겹=겹,
    )

    assert 결과.report is 저장본, f"{겹} 겹이 맞는 FULL 재사용까지 막았습니다"
    assert 새_생성 == []
    assert 결과.charged is False


# ══════════════════════════════════════════════════════════
# ④ 판정 함수 자체 — 두 겹이 함께 부르는 순수 함수
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("저장본_표기", "요청_모드", "재사용_가능"),
    [
        # FULL 요청은 FULL 저장본만
        (ReleaseMode.FULL.value, ReleaseMode.FULL, True),
        (ReleaseMode.ENFORCE_NO_PARTIAL.value, ReleaseMode.FULL, False),
        ("", ReleaseMode.FULL, False),  # SHADOW 저장본은 표기가 빈 문자열이다
        (ReleaseMode.SHADOW.value, ReleaseMode.FULL, False),
        # 비FULL 요청은 무엇이든 예전 그대로
        (ReleaseMode.FULL.value, ReleaseMode.SHADOW, True),
        ("", ReleaseMode.SHADOW, True),
        (ReleaseMode.FULL.value, ReleaseMode.ENFORCE_NO_PARTIAL, True),
        ("", ReleaseMode.ENFORCE_NO_PARTIAL, True),
        # 모드를 모르면(v1 요청·환경값 없음·계약 밖 문자열) 예전 그대로
        ("", None, True),
        (ReleaseMode.FULL.value, None, True),
    ],
)
def test_재사용_판정은_FULL_요청만_좁힌다(
    저장본_표기: str, 요청_모드: Optional[ReleaseMode], 재사용_가능: bool
) -> None:
    """판정 규칙을 한자리에 못 박는다 — 두 재사용 겹이 이 함수 하나를 쓴다."""
    assert (
        cache_store.reusable_for_requested_release_mode(저장본_표기, 요청_모드)
        is 재사용_가능
    )


def test_모드를_모르면_예전_동작이라_FULL이_새지_않는다() -> None:
    """★ 「모르겠다」를 관대하게 처리해도 구멍이 아닌 이유를 못 박는다.

    `_requested_release_mode`는 v1 요청·환경값 없음·계약 밖 문자열에 `None`을
    돌려주고, 그때 판정은 예전처럼 재사용을 허용한다. FULL 요청은 환경값이
    반드시 있고(없으면 `_run_v2_composer`가 AI 호출 전에 입력 계약으로 막는다)
    계약 밖 문자열도 같은 곳에서 막히므로, `None`으로 FULL이 새지 않는다.
    """
    import os

    # v1 요청은 환경값이 FULL이어도 None이다 — v2 전용 판정이기 때문이다.
    이전 = os.environ.get(real.REPORT_RELEASE_MODE_ENV_NAME)
    try:
        os.environ[real.REPORT_RELEASE_MODE_ENV_NAME] = ReleaseMode.FULL.value
        assert (
            real._requested_release_mode(real.engine_mode.EngineMode.V1) is None
        )
        assert (
            real._requested_release_mode(real.engine_mode.EngineMode.V2)
            is ReleaseMode.FULL
        )
        os.environ[real.REPORT_RELEASE_MODE_ENV_NAME] = "계약-밖-문자열"
        assert real._requested_release_mode(real.engine_mode.EngineMode.V2) is None
        del os.environ[real.REPORT_RELEASE_MODE_ENV_NAME]
        assert real._requested_release_mode(real.engine_mode.EngineMode.V2) is None
    finally:
        if 이전 is None:
            os.environ.pop(real.REPORT_RELEASE_MODE_ENV_NAME, None)
        else:
            os.environ[real.REPORT_RELEASE_MODE_ENV_NAME] = 이전
