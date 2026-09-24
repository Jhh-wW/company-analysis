"""FULL 공개 안전 차단 줄이 real.py 경계를 지나 실행 진단 수집칸과 요약 로그까지 간다.

★ CI 에서 도는 시험이다(local_integration 아님) — 로컬 대용량 자료 없이 운영 경계
  ``real._run_v2_composer`` 를 그대로 지난다. composer 본체(run_v2)만 가짜로 바꿔,
  운영 줄 생성기(safety_block_record)가 만든 줄을 넣고 출고 검증 차단 예외를 던진다.
★ 단정은 «실제 호출 인자»다 — real.py 의 finally 가 정화기
  (observed_composition_steps)에 넘긴 목록이 run_v2 가 받은 «바로 그» 목록인지(is),
  그 결과가 run_diagnostics 수집칸(웹 Job 이 저장해 평가 산출물로 내보내는 steps)과
  요약 로그 한 줄에 실렸는지 본다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.company_comparison.tests.test_logic import _v2_comparison_result
from src.features.composer import pipeline as composer_pipeline
from src.features.composer.quality_observation_log import safety_block_record
from src.features.composer.validate import V2ValidationError
from src.features.observability import run_diagnostics
from src.features.pipeline import real
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality import composition_diagnostics
from src.web.tests.test_v2_composer_stop_propagation import (
    _CORP_ID,
    _build_identity,
    _full_생산입력,
    _v2_모드,
    _계량_경계,
    _기준일,
)

_STEP = "8_공개안전_차단유형"
_REASON = "post_validation_safety_blocked"
#: 유료 문맥의 예산(원). 가짜 run_v2 는 AI 를 부르지 않으므로 넉넉하기만 하면 된다.
_BUDGET_KRW = 100_000.0
#: 두 장에 걸친 안전 문제 네 건 — 사실의 소유 장으로 장을 가린다.
_PROBLEMS = (
    "fact-portfolio의 수치에 versioned NumericBinding이 없습니다",
    "fact-portfolio의 구조화 수치 이름표가 비었습니다",
    "fact-culture의 원문·주장 결속 지문이 유효하지 않습니다",
    "검증하지 못한 공개 claim이 있습니다",
)
_FACT_SECTIONS = {"fact-portfolio": "portfolio", "fact-culture": "culture"}
_EXPECTED = {
    "step": _STEP,
    "회차": "1차",
    "문제수": 4,
    "유형별": {
        "numeric_labels_missing": 1,
        "numeric_binding_missing": 1,
        "unverified_claim": 1,
        "evidence_binding_invalid": 1,
    },
    # 미검증 claim 집계는 장을 가릴 수 없어 장별에 없다.
    "장별": {"portfolio": 2, "culture": 1},
}


@pytest.fixture(autouse=True)
def _유료_문맥() -> Iterator[None]:
    """웹 worker 와 같은 예산·시도 원장 문맥에서 real 경계를 부른다."""

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(_BUDGET_KRW), attempt_context.activate(callbacks):
        yield


def _run_v2_composer(steps: list[dict[str, Any]]):
    fake_engine = FakeEngine()
    engine, client = _계량_경계(fake_engine)
    fragments, financials, filing = _full_생산입력(fake_engine)
    return real._run_v2_composer(
        engine=engine,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=fragments,
        financials=financials,
        filing=filing,
        revenue_tables=[],
        sources=[],
        business_date=_기준일,
        model="가짜모델",
        steps=steps,
        corp_id=_CORP_ID,
        current_fiscal_year=2025,
        source_identity_digest="a" * 64,
        build_identity=_build_identity(),
        generation_mode=_v2_모드(),
        comparison_result=_v2_comparison_result(),
    )


def test_공개안전_차단_줄은_real_경계의_정화기를_지나_실행_진단_수집칸과_요약에_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    received_sinks: list[list[dict]] = []

    def blocked_run_v2(*_args: Any, **kwargs: Any) -> None:
        sink = kwargs["composition_diagnostics_sink"]
        received_sinks.append(sink)
        sink.append(
            safety_block_record(_PROBLEMS, _FACT_SECTIONS, validation_round="1차"),
        )
        raise V2ValidationError((f"report_recovery:{_REASON}",))

    monkeypatch.setattr(composer_pipeline, "run_v2", blocked_run_v2)
    sanitizer_arguments: list[object] = []
    original_sanitizer = composition_diagnostics.observed_composition_steps

    def watched_sanitizer(diagnostics: object) -> tuple[dict[str, object], ...]:
        sanitizer_arguments.append(diagnostics)
        return original_sanitizer(diagnostics)

    # real.py 는 finally 안에서 이 이름을 모듈에서 꺼내 부른다 — 모듈 속성을 감싼다.
    monkeypatch.setattr(
        composition_diagnostics, "observed_composition_steps", watched_sanitizer,
    )

    with run_diagnostics.capture() as captured:
        collector = run_diagnostics.begin_run()
        result = _run_v2_composer(run_diagnostics.current_steps())
        summary_line = collector.finish(corp_code=_CORP_ID)

    assert result.outcome is Outcome.GATE_STOPPED
    # 정화기가 받은 목록은 run_v2 가 받아 채운 «바로 그» 목록이다.
    assert len(received_sinks) == 1
    assert len(sanitizer_arguments) == 1
    assert sanitizer_arguments[0] is received_sinks[0]
    # 웹 Job 이 저장하는 steps 원본(수집칸)에 한 줄이 그대로 있다.
    assert captured.filled is True
    assert [step for step in captured.steps if step.get("step") == _STEP] == [_EXPECTED]
    # 출고 검증 차단 줄 «뒤»에 온다 — 예외 처리 뒤 finally 가 옮긴다.
    names = [step.get("step") for step in captured.steps]
    assert names.index("v2_출고검증_차단") < names.index(_STEP)
    # 요약 로그 한 줄(허용 목록)에도 같은 값으로 실린다.
    summary = json.loads(summary_line)
    assert [
        step for step in summary["단계"] if step.get("step") == _STEP
    ] == [_EXPECTED]
    # 문제 문장·fact_id 는 수집칸에도 요약에도 없다.
    stored = json.dumps(captured.steps, ensure_ascii=False)
    for secret in ("fact-portfolio", "fact-culture", "NumericBinding", "결속 지문"):
        assert secret not in stored
        assert secret not in summary_line


def test_정화기가_버린_공개안전_차단_줄은_수집칸에_남지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """계약 밖 줄(문장이 유형 열쇠로 든 줄)은 real 경계에서 통째로 버려진다."""

    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)

    def leaking_run_v2(*_args: Any, **kwargs: Any) -> None:
        kwargs["composition_diagnostics_sink"].append({
            "step": _STEP,
            "회차": "1차",
            "문제수": 1,
            "유형별": {_PROBLEMS[0]: 1},
            "장별": {"portfolio": 1},
        })
        raise V2ValidationError((f"report_recovery:{_REASON}",))

    monkeypatch.setattr(composer_pipeline, "run_v2", leaking_run_v2)
    steps: list[dict[str, Any]] = []

    result = _run_v2_composer(steps)

    assert result.outcome is Outcome.GATE_STOPPED
    assert [step for step in steps if step.get("step") == _STEP] == []
    assert _PROBLEMS[0] not in json.dumps(steps, ensure_ascii=False)
