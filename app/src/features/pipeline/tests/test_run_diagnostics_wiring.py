"""파이프라인이 실제로 진단 자리를 열고 끝에 요약을 남기는지 본다.

★ 이 시험이 지키는 것 — 요약 만드는 코드가 아무리 옳아도, 파이프라인이
  그것을 부르지 않으면 운영에는 아무것도 안 남는다. 실측으로 두 번 막혔던
  자리가 바로 여기다.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import deployment_identity
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.observability import constants as obs
from src.features.observability import run_diagnostics
from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.shared import engine_build_identity as build_identity_contract
from src.shared import generation_coordination


#: 파이프라인이 뉴스 수집 결과를 남길 때 쓰는 단계 이름.
NEWS_INTAKE_STEP = "5b_뉴스_수집"


@pytest.fixture(autouse=True)
def _paid_provider_budget_context():
    """직접 pipeline 단위시험도 운영 경계와 같은 요청별 예약 문맥을 쓴다."""

    def begin(provider: str, operation: str, reserved_krw: float) -> int:
        return 1

    def noop(token: int, *args: object) -> None:
        return None

    callbacks = ProviderAttemptCallbacks(begin, noop, noop, noop)
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        yield


@pytest.fixture(autouse=True)
def _verified_process_build(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    build_identity_contract.freeze_process_engine_build_identity()


class _FakeRawEngine:
    MODEL = "claude-haiku-4-5"

    def _client(self):
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **_: None))


def _card() -> CompanyCard:
    return CompanyCard(
        legal_name="회사",
        typed_name="회사",
        address="서울",
        ceo="대표",
        founded="20200101",
        ref="00126380",
    )


def _user_input() -> UserInput:
    return UserInput(company="회사", job="직무", region="서울")


def _install_stub(monkeypatch: pytest.MonkeyPatch, *, outcome: Outcome) -> None:
    """본체가 열린 자리에 단계를 쌓고 끝나는 상황만 재현한다."""

    def stub(_self, _user_input, _card, _on_step, **_kwargs):
        steps = run_diagnostics.current_steps()
        steps.append(
            {"step": "5b_뉴스_수집", "검색": 7, "선별": 2, "조각": 1, "실패": None}
        )
        steps.append({"step": "6_수집_홈페이지", "주소": "https://ex.example.com/a"})
        if outcome is Outcome.FAILED:
            raise RuntimeError("본체 실패")
        return RunResult(outcome=outcome)

    monkeypatch.setattr(real, "_engine", lambda: _FakeRawEngine())
    monkeypatch.setattr(real.RealPipeline, "_run_metered", stub)


@pytest.mark.parametrize("outcome", [Outcome.REPORT, Outcome.FAILED])
def test_실행이_끝나면_성공이든_실패든_요약을_한_줄_남긴다(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: Outcome,
):
    _install_stub(monkeypatch, outcome=outcome)
    caplog.set_level(logging.INFO, logger=run_diagnostics.logger.name)

    real.RealPipeline().run(_user_input(), _card())

    lines = [
        record.getMessage()
        for record in caplog.records
        if obs.RUN_SUMMARY_LOG_PREFIX in record.getMessage()
    ]
    assert len(lines) == 1
    assert "corp_code=00126380" in lines[0]
    assert "5b_뉴스_수집" in lines[0]
    # 허용 목록에 없는 단계와 그 주소는 로그로 새 나가지 않는다.
    assert "6_수집_홈페이지" not in lines[0]
    assert "://" not in lines[0]


def test_실행이_끝나면_단계기록_원본이_수집칸으로_넘어온다(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_stub(monkeypatch, outcome=Outcome.REPORT)

    with run_diagnostics.capture() as captured:
        real.RealPipeline().run(_user_input(), _card())

    assert captured.filled is True
    assert [item["step"] for item in captured.steps] == [
        "5b_뉴스_수집",
        "6_수집_홈페이지",
    ]


def test_waiter는_원owner의_닫힌사유를_실행진단에_남긴다(
    monkeypatch: pytest.MonkeyPatch,
):
    """owner의 GATE 사유가 일반 generation_failed로 다시 뭉개지지 않는다."""

    def stopped(_self, _user_input, _card, _on_step, **_kwargs):
        run_diagnostics.current_steps().append(
            {
                "step": "6_수집_공식근거사전검사",
                "판정": "READY_FOR_GENERATION",
            }
        )
        raise generation_coordination.GenerationOwnerFailed(
            "official_evidence_insufficient"
        )

    monkeypatch.setattr(real, "_engine", lambda: _FakeRawEngine())
    monkeypatch.setattr(real.RealPipeline, "_run_metered", stopped)

    with run_diagnostics.capture() as captured:
        with pytest.raises(generation_coordination.GenerationOwnerFailed):
            real.RealPipeline().run(_user_input(), _card())

    failure = captured.steps[-1]
    assert failure == {
        "step": "runtime_failure",
        "phase": "coordination",
        "state": "failed",
        "role": "waiter",
        "exception_class": "GenerationOwnerFailed",
        "reason_code": "generation_owner_failed",
        "owner_reason_code": "official_evidence_insufficient",
    }


def test_예상밖_pipeline실패는_예외메시지없이_경계와_종류만_남긴다(
    monkeypatch: pytest.MonkeyPatch,
):
    secret = "https://private.example/report?token=secret 원문"

    def failed(_self, _user_input, _card, _on_step, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(real, "_engine", lambda: _FakeRawEngine())
    monkeypatch.setattr(real.RealPipeline, "_run_metered", failed)

    with run_diagnostics.capture() as captured:
        result = real.RealPipeline().run(_user_input(), _card())

    assert result.outcome is Outcome.FAILED
    assert captured.steps == [
        {
            "step": "runtime_failure",
            "phase": "pipeline",
            "state": "failed",
            "role": "worker",
            "exception_class": "RuntimeError",
            "reason_code": "unexpected_pipeline_failure",
        }
    ]
    assert secret not in repr(captured.steps)


def test_실행이_끝나면_자리를_닫아_다음_실행과_섞이지_않는다(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_stub(monkeypatch, outcome=Outcome.REPORT)

    with run_diagnostics.capture() as first:
        real.RealPipeline().run(_user_input(), _card())
    with run_diagnostics.capture() as second:
        real.RealPipeline().run(_user_input(), _card())

    assert len(first.steps) == 2
    assert len(second.steps) == 2
    assert run_diagnostics.current_steps() == []


def test_요약_로그가_공식_웹_문서_수를_그대로_싣는다():
    """생산 코드가 만든 진짜 `5b_뉴스_수집` 단계를 요약기에 그대로 넣는다.

    ★ 손으로 지어낸 단계로 검사하면 「요약기가 옮긴다」만 확인된다. 정작
      파이프라인이 그 숫자를 안 적으면 운영 로그는 여전히 비어 있다.
      그래서 실제 `_collect_news_intake`를 네트워크 없이 불러 단계를 받는다.
    """

    official_web_documents = 3
    steps: list[dict] = []

    # 미달 장이 없고 공식 웹 문서가 있으면 대상 장이 없어 검색조차 하지 않는다.
    # 이 갈래도 단계는 남기므로 네트워크 없이 진짜 생산 코드를 지날 수 있다.
    def _must_not_be_called(*_args: object, **_kwargs: object):
        raise AssertionError("대상 장이 없으면 외부 호출을 하면 안 됩니다")

    real._collect_news_intake(
        search_news=_must_not_be_called,
        classify=_must_not_be_called,
        fetch_text=_must_not_be_called,
        company_name="회사",
        company_aliases=(),
        company_domain="",
        executive_names=(),
        corp_id="00126380",
        section_ready={
            section_id: True
            for section_id in real.REQUIRED_EVIDENCE_SECTION_IDS
        },
        official_web_documents=official_web_documents,
        as_of=dt.date(2026, 9, 6),
        collected_on="2026-09-06",
        steps=steps,
    )

    news_steps = [item for item in steps if item.get("step") == "5b_뉴스_수집"]
    assert len(news_steps) == 1
    assert news_steps[0]["공식웹문서수"] == official_web_documents

    line = run_diagnostics.summary_json(steps)
    payload = json.loads(line)
    summarized = [
        item
        for item in payload[run_diagnostics.KEY_STEPS]
        if item["step"] == "5b_뉴스_수집"
    ]
    assert len(summarized) == 1
    assert summarized[0]["공식웹문서수"] == official_web_documents


def test_뉴스_단계를_남기는_모든_자리가_공식_웹_문서_수를_적는다():
    """세 갈래 중 한 곳만 검사하면 나머지 두 곳은 조용히 빠진다.

    ★ 위 시험은 「대상 장이 없어 검색조차 안 한」 갈래만 지난다. 성공 갈래와
      내부 오류 갈래는 실행 흐름으로 닿기 어려워, 그 두 곳에서 항목이
      사라져도 아무 시험도 빨간불이 되지 않았다(실측). 여기서 소스를 직접
      읽어 세 갈래를 모두 센다.
    """

    source_path = Path(real.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    news_step_dicts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        names = {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        step_values = {
            value.value
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant)
            and key.value == "step"
            and isinstance(value, ast.Constant)
        }
        if NEWS_INTAKE_STEP in step_values:
            news_step_dicts.append(names)

    # 갈래가 늘거나 줄면 이 수를 보고 사람이 확인한다.
    assert len(news_step_dicts) == 3
    missing = [names for names in news_step_dicts if "공식웹문서수" not in names]
    assert missing == []
