# -*- coding: utf-8 -*-
"""요청 로컬 «예약액» 소진이 완성돼 가던 보고서를 사유 없이 버리지 못하게 한다.

★ 왜 이 파일이 생겼나 (2026-09-05 실측, 회사 하이브)
  ─────────────────────────────────────────────────────────
  본조사 1건에 미리 잡아 두는 예약액(요청 로컬)이 685원 지출 시점에 다음 호출
  예상액을 감당하지 못해 ProviderBudgetExceeded 가 났다. 그 예외는
  AskFatalError 로 감싸여 재전파됐고, run() 바깥 except 가 Outcome.FAILED 로
  접어 화면에는 사유 없는 「보고서를 만들다 오류가 났습니다」만 남았다.
  그때까지 만든 장은 전부 버려졌다.

★ 이 시험이 지키는 것
  ① 요청 로컬 예약액 소진은 «강등 가능»으로 표시된다 (선택적 단계는 살아남는다)
  ② 필수 단계에서 만나면 멈추되 «닫힌 사유»를 달고 GATE_STOPPED 로 끝난다
  ③ 일일·수명 상한·계정 장애(ProviderBudgetUnavailable)는 예전처럼 재전파   ← 안전선
  ④ 입력 토큰 상한은 provider tokenizer 계수를 쓰고, 못 얻으면 바이트 추정으로 복귀
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.core import deployment_identity
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer.port import AskFatalError
from src.features.pipeline import real
from src.features.pipeline.port import Outcome, RunResult
from src.features.pipeline.tests.test_real_cache import CORP_ID, FakeEngine
from src.features.pipeline.tests.test_real_v2_switch import (
    _DATE,
    _branch_ingredients,
    _build_identity,
    _frozen_v2_mode,
    _run,
)
from src.features.pipeline.tests.test_request_metering import (
    FakeMessages,
    FakeRawEngine,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED,
    SAFE_FINAL_GATE_REASONS,
)
from src.shared.report_evidence.constants import ReleaseMode


@pytest.fixture(autouse=True)
def _검증된_배포에서_시험한다(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)


@pytest.fixture(autouse=True)
def _유료_예약문맥(monkeypatch: pytest.MonkeyPatch):
    """직접 시험도 웹 worker 와 같은 요청별 예약·시도 문맥에서 실행한다."""
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
        lambda: (
            (CORP_ID, "가나다전자", "", "000001", "20260819"),
            ("00999999", "베타전자", "", "999999", "20260819"),
        ),
    )
    return fake


def _ask_로_감싼_예외(cause: BaseException) -> AskFatalError:
    """실제 v2 ask 경계를 통과시켜 깃발이 «코드가 정하는 대로» 붙게 한다."""
    fake = FakeEngine()
    metered = real._MeteredEngine(fake)
    client = real._metered_client(metered, fake._client())

    def failing(**_kwargs: Any) -> Any:
        raise cause

    client.messages.create = failing  # type: ignore[method-assign]
    ask = real._v2_ask_via_provider(
        metered, client, stage="v2_compose", max_tokens=real.V2_WRITER_MAX_TOKENS
    )

    with pytest.raises(AskFatalError) as caught:
        ask("프롬프트 본문")
    return caught.value


# ══════════════════════════════════════════════════════════
# ① ask 경계가 «요청 몫 소진»과 «계정·일일 장애»를 갈라 표시한다
# ══════════════════════════════════════════════════════════


def test_요청예약액_소진은_강등가능으로_표시된다() -> None:
    """★ 예약액 소진은 «돈이 없다»가 아니라 «이 요청 몫을 다 썼다»다."""
    cause = provider_budget.ProviderBudgetExceeded("단계 예약 잔액을 넘습니다")

    잡힘 = _ask_로_감싼_예외(cause)

    assert 잡힘.cause is cause
    assert 잡힘.request_budget is True
    assert 잡힘.call_limit is False, "횟수 상한과 뒤섞지 않는다"
    assert 잡힘.degradable is True


def test_호출횟수_상한은_예전_깃발_그대로다() -> None:
    """회귀 방지 — 새 깃발이 기존 횟수 상한 처리를 덮지 않았다."""
    cause = provider_budget.RequestCallLimitReached("호출 수를 다 썼습니다")

    잡힘 = _ask_로_감싼_예외(cause)

    assert 잡힘.call_limit is True
    assert 잡힘.request_budget is False
    assert 잡힘.degradable is True


def test_계정_원장_장애는_강등되지_않는다() -> None:
    """★ 안전선 — 일일·수명 상한과 billing-uncertain 차단은 요청을 멈춘다."""
    cause = provider_budget.ProviderBudgetUnavailable("provider를 쓸 수 없습니다")

    잡힘 = _ask_로_감싼_예외(cause)

    assert 잡힘.call_limit is False
    assert 잡힘.request_budget is False
    assert 잡힘.degradable is False


# ══════════════════════════════════════════════════════════
# ② 필수 단계에서 만나면 «닫힌 사유»를 달고 멈춘다
# ══════════════════════════════════════════════════════════


def _소진되는_분기(monkeypatch: pytest.MonkeyPatch, cause: BaseException) -> None:
    def failing_run_v2(*_args: Any, **kwargs: Any):
        # 중단 «전»에 실제로 나간 AI 비용이 0원으로 사라지지 않아야 한다.
        kwargs["writer_ask"]("비민감 선행 작성 호출")
        raise AskFatalError(
            cause,
            request_budget=isinstance(cause, provider_budget.ProviderBudgetExceeded),
        )

    monkeypatch.setattr(composer_pipeline, "run_v2", failing_run_v2)


def test_요청예약액_소진은_닫힌사유의_GATE_STOPPED로_끝난다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 이게 수정의 «이유»다 — 사유 없는 실패로 접히면 아무도 원인을 모른다."""
    fake = FakeEngine()
    metered, client, frags, financials, filing = _branch_ingredients(fake)
    _소진되는_분기(
        monkeypatch,
        provider_budget.ProviderBudgetExceeded("단계 예약 잔액을 넘습니다"),
    )
    steps: list[dict[str, Any]] = []

    result = real._run_v2_composer(
        engine=metered,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=filing,
        revenue_tables=[],
        sources=[real.SourceStatus("회사 공식 IR", "ok", "확인")],
        business_date=_DATE,
        model="가짜모델",
        steps=steps,
        build_identity=_build_identity(),
        generation_mode=_frozen_v2_mode(),
    )

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED
    assert result.final_gate_reason in SAFE_FINAL_GATE_REASONS
    assert result.final_gate_reason != FINAL_GATE_REASON_PUBLISH_BLOCKED
    assert result.charged is False
    assert result.cost_krw > 0, "★ 이미 나간 AI 원가가 0원으로 사라지면 안 된다"
    assert result.corp_type == "상장사"
    assert result.sources and result.fragments_collected == len(frags)

    기록 = [항목 for 항목 in steps if 항목.get("step") == "v2_요청예산_소진"]
    assert len(기록) == 1
    assert 기록[0]["사유코드"] == FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED
    assert 기록[0]["지출원"] >= 0


def test_중단_기록에는_예외문도_프롬프트도_남기지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 운영 기록에 provider 예외문·원문이 새면 진단이 곧 유출 통로가 된다."""
    비밀 = "예약-잔액-초과-비밀문구-9f2c"
    fake = FakeEngine()
    metered, client, frags, financials, filing = _branch_ingredients(fake)
    _소진되는_분기(monkeypatch, provider_budget.ProviderBudgetExceeded(비밀))
    steps: list[dict[str, Any]] = []

    result = real._run_v2_composer(
        engine=metered,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=filing,
        revenue_tables=[],
        sources=[],
        business_date=_DATE,
        model="가짜모델",
        steps=steps,
        build_identity=_build_identity(),
        generation_mode=_frozen_v2_mode(),
    )

    assert 비밀 not in repr(steps)
    assert 비밀 not in result.message


def test_계정장애는_예전처럼_원인예외를_재전파한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 안전선 — 강등 갈래가 «진짜 멈춰야 하는» 장애까지 삼키지 않는다."""
    fake = FakeEngine()
    metered, client, frags, financials, filing = _branch_ingredients(fake)
    _소진되는_분기(
        monkeypatch,
        provider_budget.ProviderBudgetUnavailable("provider를 쓸 수 없습니다"),
    )
    steps: list[dict[str, Any]] = []

    with pytest.raises(provider_budget.ProviderBudgetUnavailable):
        real._run_v2_composer(
            engine=metered,
            client=client,
            company_name="가나다전자",
            corp_type="상장사",
            frags=frags,
            financials=financials,
            filing=filing,
            revenue_tables=[],
            sources=[],
            business_date=_DATE,
            model="가짜모델",
            steps=steps,
            build_identity=_build_identity(),
            generation_mode=_frozen_v2_mode(),
        )

    assert [항목 for 항목 in steps if 항목.get("step") == "v2_요청예산_소진"] == []


def test_run_전체에서도_사유없는_FAILED로_접히지_않는다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 실제 사고 경로 그대로 — run() 바깥 except 가 사유를 지우면 안 된다."""
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    _소진되는_분기(
        monkeypatch,
        provider_budget.ProviderBudgetExceeded("단계 예약 잔액을 넘습니다"),
    )

    result: RunResult = _run()

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED
    assert result.charged is False
    assert result.cost_krw > 0
    assert result.ai_cost_events


# ══════════════════════════════════════════════════════════
# ④ 입력 토큰 상한 — provider 계수 우선, 실패하면 바이트 추정
# ══════════════════════════════════════════════════════════

#: 한글은 UTF-8 로 글자당 3바이트라 바이트 추정이 실제 토큰보다 크게 부풀어
#: 오른다. 예약액이 그만큼 과대해져 «쓸 돈이 남았는데» 요청이 죽었다(실측).
_한글_프롬프트 = "회사의 사업 구조와 수익원을 공식 자료 원문에 근거해 설명하라. " * 40


class _계수하는_Messages(FakeMessages):
    """provider tokenizer 응답만 흉내 내는 messages 리소스."""

    def __init__(self, *, counted: int) -> None:
        super().__init__()
        self._counted = counted
        self.count_calls: list[dict] = []

    def count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(input_tokens=self._counted)


class _계수가_터지는_Messages(FakeMessages):
    """SDK·네트워크 사정으로 계수를 못 얻는 경우(예전 경로로 복귀해야 한다)."""

    def count_tokens(self, **kwargs):
        raise RuntimeError("계수 실패")


def _예약된_입력상한(messages: FakeMessages, monkeypatch: pytest.MonkeyPatch) -> int:
    """한 번 호출해 예약 경계가 실제로 받은 입력 token 상한을 돌려준다."""
    기록: list[int] = []
    원래 = provider_budget.ProviderBudget.reserve_call

    def spy(self, *, model: str, input_tokens_upper: int, max_tokens: int):
        기록.append(input_tokens_upper)
        return 원래(
            self,
            model=model,
            input_tokens_upper=input_tokens_upper,
            max_tokens=max_tokens,
        )

    monkeypatch.setattr(provider_budget.ProviderBudget, "reserve_call", spy)
    metered = real._MeteredEngine(FakeRawEngine(messages))
    real._metered_client(metered, metered._client()).messages.create(
        model=FakeRawEngine.MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": _한글_프롬프트}],
    )
    assert len(기록) == 1
    return 기록[0]


def test_정확계수가_있으면_바이트추정보다_작은_상한으로_예약한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 계수 값이 그대로 상한에 흘러야 한다 — 여유분은 상한 의미상 유지된다."""
    적은_계수 = _계수하는_Messages(counted=1_000)
    많은_계수 = _계수하는_Messages(counted=2_000)
    바이트추정 = _계수가_터지는_Messages()

    적은_상한 = _예약된_입력상한(적은_계수, monkeypatch)
    많은_상한 = _예약된_입력상한(많은_계수, monkeypatch)
    복귀_상한 = _예약된_입력상한(바이트추정, monkeypatch)

    # 여유분 상수를 시험에 박지 않고 «계수가 그대로 반영되는가»만 본다.
    assert 많은_상한 - 적은_상한 == 1_000
    assert 적은_상한 < 복귀_상한, "★ 한글 payload 에서 과대 예약이 줄어야 한다"
    # tokenizer 에 이 호출이 실제로 쓸 모델·messages 를 그대로 물어본다.
    assert 적은_계수.count_calls[0]["model"] == FakeRawEngine.MODEL
    assert 적은_계수.count_calls[0]["messages"][0]["content"] == _한글_프롬프트


def test_계수를_못_얻으면_예전_바이트추정으로_돌아간다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 정확 계수는 «개선»이지 호출의 전제 조건이 아니다 — 막으면 안 된다."""
    바이트추정 = _계수가_터지는_Messages()

    상한 = _예약된_입력상한(바이트추정, monkeypatch)

    기대 = provider_budget.estimate_request_tokens(
        {
            "args": (),
            "kwargs": {
                "model": FakeRawEngine.MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": _한글_프롬프트}],
            },
        }
    )
    assert 상한 == 기대
    assert 바이트추정.calls == [FakeRawEngine.MODEL], "복귀해도 호출은 나간다"
