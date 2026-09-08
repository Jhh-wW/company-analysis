"""최종 품질게이트 예외를 지나서도 원문 없는 검수 관측을 전달한다."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.features.composer.pipeline import run_v2
from src.features.composer.tests.test_section_public_manifest import (
    _BoundGroupedReviewer,
    _RecoveringPacketWriter,
    _packets,
)
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


def _event(**extra: object) -> dict[str, object]:
    return {
        "section_id": "identity",
        "kind": "본문",
        "reason_code": "semantic_grounding_invalid",
        "candidate_sha256": sha256("제외 후보".encode()).hexdigest(),
        "verification_items": (),
        **extra,
    }


def test_full_최종품질게이트_예외에도_요청로컬_관측이_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _RecoveringPacketWriter(("identity",), remain_thin=True)
    reviewer = _BoundGroupedReviewer()
    sink: list[dict] = []
    original_verify_report = composer_pipeline.verify_report

    def recording_verify_report(*args: Any, **kwargs: Any):
        kwargs["diagnostics"].append(_event())
        return original_verify_report(*args, **kwargs)

    monkeypatch.setattr(composer_pipeline, "verify_report", recording_verify_report)

    with pytest.raises(
        V2ValidationError,
        match="report_recovery:post_supplement_quality_failed",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
            review_diagnostics_sink=sink,
        )

    assert sink
    assert sink[0]["reason_code"] == "semantic_grounding_invalid"
    assert len(writer.prompts) == 10
    assert len(reviewer.prompts) == 2


@pytest.mark.parametrize(
    ("fails", "expected_final_output"),
    ((False, True), (True, False)),
)
def test_real_성공실패_진단은_한국어_닫힌필드만_저장한다(
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
    expected_final_output: bool,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    fake = FakeEngine()
    engine, client, frags, financials, filing = _branch_ingredients(fake)
    raw_event = _event(
        raw_text="1,234억원 원문",
        response="검수 응답 전체",
        amount="1,234억원",
    )

    def fake_run_v2(*_args: Any, **kwargs: Any):
        if fails:
            kwargs["review_diagnostics_sink"].extend(
                [raw_event, "문자열 오염", {**raw_event, "section_id": 1}]
            )
            raise V2ValidationError(("report_recovery:post_supplement_quality_failed",))
        return composer_pipeline.V2RunOutput(
            report=Report(
                company="가나다전자",
                job="",
                corp_type="상장사",
                grade=Grade.COMPLETE,
                sections=[],
                citations=[],
            ),
            composed_sentences=1,
            verified_sentences=1,
            review_diagnostics=(raw_event,),
        )

    monkeypatch.setattr(composer_pipeline, "run_v2", fake_run_v2)
    steps: list[dict[str, Any]] = []

    result = real._run_v2_composer(
        engine=engine,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=filing,
        revenue_tables=[],
        sources=[],
        business_date=real.today_kst(),
        model="가짜모델",
        steps=steps,
        build_identity=_build_identity(),
        generation_mode=_frozen_v2_mode(),
        release_mode_override=ReleaseMode.SHADOW,
    )

    diagnostic_steps = [step for step in steps if step["step"] == "8_근거검수_제외"]
    assert len(diagnostic_steps) == 1
    diagnostic_step = diagnostic_steps[0]
    assert diagnostic_step["최종출력"] is expected_final_output
    assert len(diagnostic_step["항목"]) == 1
    assert set(diagnostic_step["항목"][0]) == {
        "장", "종류", "사유코드", "후보지문", "검증항목",
    }
    assert diagnostic_step["항목"][0]["검증항목"] == []
    assert "1,234억원" not in repr(diagnostic_step)
    assert "검수 응답 전체" not in repr(diagnostic_step)
    assert result.outcome is (Outcome.GATE_STOPPED if fails else Outcome.REPORT)
