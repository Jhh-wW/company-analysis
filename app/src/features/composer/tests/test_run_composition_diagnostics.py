"""실제 작성·검수·요약·최종 게이트를 지나는 요청 로컬 진단 배선."""

import json

import pytest

from src.features.composer import pipeline
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer,
    _FakeWriter,
    _raw_fragments,
)
from src.features.composer.validate import V2ValidationError
from src.shared.report_quality.composition_diagnostics import observed_composition_steps


@pytest.mark.parametrize("stop_at_gate", (False, True))
def test_real_run_preserves_output_calls_and_records_across_final_gate(
    monkeypatch: pytest.MonkeyPatch, stop_at_gate: bool,
) -> None:
    def run(writer, reviewer, sink):
        return pipeline.run_v2(
            "가나다전자", _raw_fragments(), None,
            writer_ask=writer, reviewer_ask=reviewer,
            generated_at="2026-09-10", as_of_date="2026-09-10",
            composition_diagnostics_sink=sink,
        )

    baseline_writer, baseline_reviewer = _FakeWriter(), _FakeReviewer()
    baseline = run(baseline_writer, baseline_reviewer, None)
    original_validate = pipeline.validate_v2

    if stop_at_gate:
        def block_after_validation(*args, **kwargs):
            original_validate(*args, **kwargs)
            raise V2ValidationError(("시험용 최종 출고 차단",))

        monkeypatch.setattr(pipeline, "validate_v2", block_after_validation)

    writer, reviewer = _FakeWriter(), _FakeReviewer()
    sink: list[dict] = []
    if stop_at_gate:
        with pytest.raises(V2ValidationError, match="시험용 최종 출고 차단"):
            run(writer, reviewer, sink)
    else:
        actual = run(writer, reviewer, sink)
        assert actual == baseline

    assert writer.prompts == baseline_writer.prompts
    assert reviewer.prompts == baseline_reviewer.prompts
    records = observed_composition_steps(sink)
    assert len(records) == len(sink)
    parses = [record for record in records if record["step"] == "8_본문검수_응답판독"]
    summaries = [record for record in records if record["step"] == "8_핵심요약_단계"]
    # ★ 본문 검수 «한 번»만 이 판독 경계를 지난다 (2026-09-11). 예전에는 요약
    #   재검증이 같은 경계를 한 번 더 지나 2건이었다. 요약이 검증된 본문 문장을
    #   글자 그대로 싣게 되면서 다시 판독할 응답이 없어졌다.
    assert len(parses) >= 1
    assert all(record["판독"] == "ok" for record in parses)
    assert len(summaries) == 1
    assert summaries[0]["최종수"] == len(baseline.report.summary_items)
    assert summaries[0]["도달단계"] == "최종"
    serialized = json.dumps(records, ensure_ascii=False)
    assert "가나다전자" not in serialized
    assert "반도체" not in serialized
    assert "시험용 최종 출고 차단" not in serialized
