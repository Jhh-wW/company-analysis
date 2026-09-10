"""실행기가 composer 에 넘기는 호출자들의 «예약값»을 실제 인자로 단정한다.

시험 안에서 클로저를 따로 만들어 검사하면 운영 배선이 어긋나도 초록이 된다.
그래서 여기서는 `real._run_v2_composer` 를 실제로 돌리고, 그 안에서
`_v2_ask_via_provider` 가 «몇 번 · 어떤 stage · 어떤 reserved_calls» 로
불렸는지와 `composer_pipeline.run_v2` 가 «실제로 어떤 객체를» 받았는지를 본다.

지키는 것: 재작성 호출자는 재검수 1 + 필수 후속 3 = 4회를 남기고,
재검수 호출자는 필수 후속 3회를 남긴다. 본문 검수·장 작성·도식·요약은
필수 단계라 아무것도 남기지 않는다(예약 0).
"""

from __future__ import annotations

from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.core import deployment_identity
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.pipeline import real
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.pipeline.tests.test_report_company_id_release_mode import (
    _DATE,
    _EXPECTED_CORP_ID,
    _build_identity,
    _frags,
    _frozen_v2_mode,
    _가짜_ask를_끼운다,
)
from src.shared.report_evidence.constants import ReleaseMode

# ★ 리터럴 오라클 — 생산 상수를 import해 비교하면 값이 내려가도 초록이다.
_필수후속 = 3
_재작성예약 = 4  # 필수 후속 3 + 재검수 1


@pytest.fixture(autouse=True)
def _검증된_배포에서_시험한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """배포 신원이 확정된 상태에서만 v2 경로가 열린다 — 그 전제를 만든다."""
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)


@pytest.fixture(autouse=True)
def _유료문맥():
    """직접 부르는 시험도 웹 worker와 같은 예약·시도 문맥에서 실행한다."""
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
    fake = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    return fake


def _돌린다(
    engine_fake: FakeEngine, monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """SHADOW 경로로 v2 분기를 끝까지 돌리고 배선 기록을 돌려준다."""
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.SHADOW.value)
    _가짜_ask를_끼운다(monkeypatch)
    가짜팩토리 = real._v2_ask_via_provider
    만든호출자: list[dict[str, Any]] = []

    def 기록하는_팩토리(
        _engine, _client, *, stage: str, max_tokens: int, reserved_calls: int = 0,
    ):
        속 = 가짜팩토리(
            _engine, _client, stage=stage, max_tokens=max_tokens,
            reserved_calls=reserved_calls,
        )

        # ★ 생성마다 «새» 객체여야 한다. 가짜 팩토리는 stage 가 같으면 같은
        #   객체를 돌려주므로, 그대로 기록하면 v2_review 호출자 넷이 전부
        #   같은 것이 되어 아래 동일성 단정이 «항상 참»이 된다 —
        #   rewrite_ask 와 recheck_ask 를 뒤바꿔 넘겨도 초록이었다(실측).
        def 감싼다(prompt: str, _속=속) -> str:
            return _속(prompt)

        만든호출자.append(
            {"stage": stage, "max_tokens": max_tokens,
             "reserved_calls": reserved_calls, "ask": 감싼다}
        )
        return 감싼다

    monkeypatch.setattr(real, "_v2_ask_via_provider", 기록하는_팩토리)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)

    원본_run_v2 = composer_pipeline.run_v2
    받은인자: dict[str, Any] = {}

    def 기록하는_run_v2(*args: Any, **kwargs: Any):
        받은인자.update(kwargs)
        return 원본_run_v2(*args, **kwargs)

    monkeypatch.setattr(composer_pipeline, "run_v2", 기록하는_run_v2)

    steps: list[dict[str, Any]] = []
    result = real._run_v2_composer(
        engine=real._MeteredEngine(engine_fake),
        client=object(),
        company_name="가나다전자",
        corp_type="상장사",
        frags=_frags(),
        financials=None,
        filing=None,
        revenue_tables=[],
        sources=[],
        business_date=_DATE,
        model="가짜모델",
        steps=steps,
        corp_id=_EXPECTED_CORP_ID,
        current_fiscal_year=2025,
        source_identity_digest="a" * 64,
        build_identity=_build_identity(),
        generation_mode=_frozen_v2_mode(),
        comparison_result=None,
    )
    assert result.outcome is Outcome.REPORT, (
        f"배선 시험이 보고서를 못 만들었습니다: {result.final_gate_reason} / {steps}"
    )
    assert 받은인자, "run_v2 가 키워드 인자를 하나도 받지 못했습니다"
    return 만든호출자, 받은인자


def test_real이_run_v2에_넘기는_rewrite_recheck_클로저의_예약값을_단정한다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    만든호출자, 받은인자 = _돌린다(engine, monkeypatch)

    검수자들 = [항목 for 항목 in 만든호출자 if 항목["stage"] == "v2_review"]
    assert [항목["reserved_calls"] for 항목 in 검수자들] == [0, 0, _재작성예약, _필수후속], (
        "v2_review 호출자 네 개(검수·최초검수·재작성·재검수)의 예약값이 "
        f"설계와 다릅니다: {[항목['reserved_calls'] for 항목 in 검수자들]}"
    )
    # 필수 단계는 아무것도 남기지 않는다 — 남기면 자기가 자기를 굶긴다.
    for 항목 in 만든호출자:
        if 항목["stage"] in ("v2_compose", "v2_diagram", "news_grounding"):
            assert 항목["reserved_calls"] == 0, (
                f"필수 단계 {항목['stage']} 가 예약을 걸었습니다"
            )

    # ★ 아래 동일성 단정이 뜻을 가지려면 네 호출자가 «서로 다른 객체»여야
    #   한다. 하나라도 같아지면 인자를 뒤바꿔 넘겨도 초록이 되므로 먼저 못 박는다.
    assert len({id(항목["ask"]) for 항목 in 검수자들}) == 4, (
        "v2_review 호출자들이 같은 객체입니다 — 이 시험은 배선을 구분하지 못합니다"
    )

    # ★ run_v2 가 «실제로 받은 객체»가 그 예약을 건 클로저와 같은 것인지 본다.
    #   시험 안에서 새로 만든 클로저와 비교하면 배선 결함을 못 잡는다.
    재작성_클로저 = next(
        항목["ask"] for 항목 in 검수자들 if 항목["reserved_calls"] == _재작성예약
    )
    재검수_클로저 = next(
        항목["ask"] for 항목 in 검수자들 if 항목["reserved_calls"] == _필수후속
    )
    assert 받은인자["rewrite_ask"] is not 받은인자["recheck_ask"], (
        "재작성과 재검수가 같은 호출자를 받았습니다 — 예약값이 서로 다른데 "
        "같은 객체라면 둘 중 하나의 예약이 사라진 것입니다"
    )
    assert 받은인자["rewrite_ask"] is 재작성_클로저, (
        "재작성 자리에 예약 "
        f"{_재작성예약}회짜리가 아닌 호출자가 들어갔습니다"
    )
    assert 받은인자["recheck_ask"] is 재검수_클로저, (
        f"재검수 자리에 예약 {_필수후속}회짜리가 아닌 호출자가 들어갔습니다"
    )
    # 검수·최초검수는 예전 그대로 예약 없는 호출자를 받는다.
    assert 받은인자["reviewer_ask"] is 검수자들[0]["ask"]
    assert 받은인자["initial_reviewer_ask"] is 검수자들[1]["ask"]
    assert 받은인자["rewrite_ask"] is not 받은인자["reviewer_ask"]


def test_재작성_예약이_재검수_예약보다_정확히_한_회_크다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재검수를 못 할 재작성은 시작하지 않는다 — 그 «1회»가 이 차이다."""
    만든호출자, _받은인자 = _돌린다(engine, monkeypatch)
    예약들 = sorted(
        항목["reserved_calls"] for 항목 in 만든호출자 if 항목["stage"] == "v2_review"
    )
    assert 예약들[-1] - 예약들[-2] == 1, (
        "재작성 예약에 재검수 1회분이 빠졌습니다 — 판정 없이 버려질 재작성이 나갑니다"
    )
