"""성공·출고 차단·예외 모두 원문 없는 구성 진단을 전달한다."""

from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.features.composer.port import AskFatalError
from src.features.composer.validate import V2ValidationError
from src.features.pipeline import real
from src.features.pipeline.port import Grade, Outcome, Report
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.pipeline.tests.test_real_v2_switch import (
    _branch_ingredients,
    _build_identity,
    _frozen_v2_mode,
)
from src.shared.report_evidence.constants import ReleaseMode


@pytest.mark.parametrize("failure", ("성공", "출고차단", "조립오류", "공급자오류"))
def test_composition_diagnostics_delivered_once_without_report(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    engine, client, frags, financials, filing = _branch_ingredients(FakeEngine())
    event = {
        "step": "8_핵심요약_단계", "경로": "legacy", "도달단계": "작성",
        "본문후보수": 2, "초안수": 0, "검수후수": None,
        "첫보충후수": None, "수치검사후수": None, "최종수": None,
        "작성한도도달": False, "검수한도도달": False,
    }

    def fake_run_v2(*_args: Any, **kwargs: Any):
        kwargs["composition_diagnostics_sink"].extend([
            {**event, "response": "비공개 응답", "error": "비공개 오류문"},
            "오염", {**event, "최종수": "본문"},
        ])
        if failure == "출고차단":
            raise V2ValidationError(("핵심 요약이 2문장입니다",))
        if failure == "조립오류":
            raise RuntimeError("시험용 조립 오류")
        if failure == "공급자오류":
            raise AskFatalError(RuntimeError("시험용 공급자 오류"))
        return composer_pipeline.V2RunOutput(
            report=Report(
                company="가나다전자", job="", corp_type="상장사",
                grade=Grade.COMPLETE, sections=[], citations=[],
            ),
            composed_sentences=1, verified_sentences=1,
        )

    monkeypatch.setattr(composer_pipeline, "run_v2", fake_run_v2)
    steps: list[dict] = []

    def run():
        return real._run_v2_composer(
            engine=engine, client=client, company_name="가나다전자",
            corp_type="상장사", frags=frags, financials=financials,
            filing=filing, revenue_tables=[], sources=[],
            business_date=real.today_kst(), model="가짜모델", steps=steps,
            build_identity=_build_identity(), generation_mode=_frozen_v2_mode(),
            release_mode_override=ReleaseMode.SHADOW,
        )

    if failure in ("조립오류", "공급자오류"):
        with pytest.raises(RuntimeError, match="시험용"):
            run()
    else:
        result = run()
        assert result.outcome is (
            Outcome.REPORT if failure == "성공" else Outcome.GATE_STOPPED
        )
    assert [step for step in steps if step.get("step") == event["step"]] == [event]
