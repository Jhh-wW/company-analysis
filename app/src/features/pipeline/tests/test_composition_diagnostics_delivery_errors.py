"""진단 기록의 오염이 공개 근거·예산 오류 처리 결과를 바꾸지 않는지 확인한다."""
from __future__ import annotations

from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.features.budget import provider_budget
from src.features.composer.port import AskFatalError
from src.features.pipeline import real
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.pipeline.tests.test_real_v2_switch import (
    _branch_ingredients,
    _build_identity,
    _frozen_v2_mode,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID,
    FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED,
    classify_v2_validation_final_gate_reason,
)
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.canonical import PublicManifestError

# 실제 step 이름·닫힌 필드 계약 — composition_diagnostic_constants.py 그대로.
_PROTOCOL_EVENT = {
    "step": "8_본문검수_응답판독",
    "경로": "flat", "판독": "ok", "추출방식": "direct",
    "시도": 1, "입력문자": 10, "응답문자": 20, "요청번호수": 1,
    "응답행수": 1, "유효행수": 1, "미응답번호수": 0, "요청밖번호수": 0,
    "json시작offset": 0, "json끝offset": 5,
    "행탈락": {"not_mapping": 1},
}
_SUMMARY_EVENT = {
    "step": "8_핵심요약_단계", "경로": "legacy", "도달단계": "작성",
    "본문후보수": 2, "초안수": 0, "검수후수": None,
    "첫보충후수": None, "수치검사후수": None, "최종수": None,
    "작성한도도달": False, "검수한도도달": False,
}


def _contaminated_sink() -> list[Any]:
    """본문/응답/임의오류/오염된 숫자/지원하지 않는 enum/미지 step을 섞는다."""
    return [
        # 살아남아야 하는 것 — 닫힌 필드만 남기고 정상 정규화된다.
        {**_PROTOCOL_EVENT, "본문": "원문 후보 문장 전체", "응답": "provider 원문 응답"},
        {**_SUMMARY_EVENT, "response": "비공개 응답", "error": "비공개 오류문"},
        # 죽어야 하는 것 — 아래 전부 observed_composition_steps()가 걸러야 한다.
        "오염",  # Mapping이 아닌 raw 문자열
        {**_PROTOCOL_EVENT, "판독": "지원하지않는_코드"},  # 지원하지 않는 enum
        {**_SUMMARY_EVENT, "최종수": "본문"},  # 오염된 숫자(문자열)
        {**_PROTOCOL_EVENT, "시도": -1},  # 오염된 숫자(허용 범위 밖 음수)
        {"step": "알수없는_단계", "아무거나": 1},  # 계약에 없는 step
    ]


def _run(*, steps: list[dict], mutate_sink, raise_exc: BaseException):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    try:
        engine, client, frags, financials, filing = _branch_ingredients(FakeEngine())

        def fake_run_v2(*_args: Any, **kwargs: Any):
            kwargs["composition_diagnostics_sink"].extend(mutate_sink())
            raise raise_exc

        monkeypatch.setattr(composer_pipeline, "run_v2", fake_run_v2)
        return real._run_v2_composer(
            engine=engine, client=client, company_name="가나다전자",
            corp_type="상장사", frags=frags, financials=financials,
            filing=filing, revenue_tables=[], sources=[],
            business_date=real.today_kst(), model="가짜모델", steps=steps,
            build_identity=_build_identity(), generation_mode=_frozen_v2_mode(),
            release_mode_override=ReleaseMode.SHADOW,
        ), engine
    finally:
        monkeypatch.undo()


def _closed_only(steps: list[dict]) -> list[dict]:
    return [step for step in steps if step.get("step") in (
        _PROTOCOL_EVENT["step"], _SUMMARY_EVENT["step"],
    )]


def test_manifest_error_preserves_reason_and_delivers_closed_diagnostics() -> None:
    steps: list[dict] = []
    result, _engine = _run(
        steps=steps, mutate_sink=_contaminated_sink,
        raise_exc=PublicManifestError("시험용 manifest 결속 오류"),
    )

    assert result.outcome is Outcome.GATE_STOPPED
    # ★ real.py는 FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID를 그대로
    #   final_gate_reason에 쓰지 않는다 — classify_v2_validation_final_gate_
    #   reason이 «내부 근거 계약 오류» 상위 사유로 묶는다(같은 함수가 공식
    #   자료 부족·일시 실패 등과 우선순위를 가르는 하나의 권위이기 때문).
    #   V2ValidationError 갈래와 같은 분류기를 타므로 이 값도 그것으로 계산한다.
    assert result.final_gate_reason == classify_v2_validation_final_gate_reason(
        (FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID,)
    )

    delivered = _closed_only(steps)
    assert {**_PROTOCOL_EVENT} in delivered
    assert {**_SUMMARY_EVENT} in delivered
    # 정확히 이 두 닫힌 레코드와 같아야 한다 — 딕셔너리 동등 비교 자체가
    # sink에 얹었던 "response"/"error"/"본문"/"응답" 여분 키가 새지 않았음을
    # 증명한다("행탈락" 값도 계약이 허용한 사유만 남아야 같은 dict가 된다).
    assert len(delivered) == 2, "오염 항목이 닫힌 필드 대신 그대로 새면 안 됩니다"


def test_budget_error_preserves_reason_cost_and_delivers_closed_diagnostics() -> None:
    steps: list[dict] = []
    result, engine = _run(
        steps=steps, mutate_sink=_contaminated_sink,
        raise_exc=AskFatalError(provider_budget.ProviderBudgetExceeded("시험용 예산 소진")),
    )

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED
    assert result.cost_krw == real._request_spent_krw(engine)

    delivered = _closed_only(steps)
    assert {**_PROTOCOL_EVENT} in delivered
    assert {**_SUMMARY_EVENT} in delivered
    assert len(delivered) == 2, "오염 항목이 닫힌 필드 대신 그대로 새면 안 됩니다"


def test_nonbudget_fatal_reraises_original_cause() -> None:
    """회귀 방지 참고선 — 예산 소진이 아닌 AskFatalError까지 이 새 분기로
    잘못 흡수되지 않는지 확인한다(기존 파일의 «공급자오류» 갈래와 같은
    경로지만, 이번엔 오염된 sink를 함께 흘려 두 분기가 서로 간섭하지
    않는지도 함께 본다)."""
    steps: list[dict] = []
    with pytest.raises(RuntimeError, match="시험용 비-예산"):
        _run(
            steps=steps, mutate_sink=_contaminated_sink,
            raise_exc=AskFatalError(RuntimeError("시험용 비-예산 공급자 오류")),
        )

    delivered = _closed_only(steps)
    assert {**_PROTOCOL_EVENT} in delivered
    assert {**_SUMMARY_EVENT} in delivered
    assert len(delivered) == 2
