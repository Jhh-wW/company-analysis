"""수행한 FULL 보충을 생략으로 표시하지 않으며 기존 처분·호출 수를 보존한다."""

import logging

import pytest

from src.features.composer import pipeline
from src.features.composer.quality_observation_log import record_supplement_quality_stop
from src.features.composer.tests.test_section_public_manifest import (
    _RecoveringPacketWriter, _BoundGroupedReviewer, _NoDiagram,
    _numeric_table_and_fragment, _packets,
)
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.composition_diagnostics import observed_composition_steps
from src.features.composer.validate import V2ValidationError


@pytest.mark.parametrize("fallback", (False, True))
@pytest.mark.parametrize("summary_only", (False, True))
def test_보충수행후실패는_생략이아닌_관측이며_기존처분을유지한다(
    monkeypatch, fallback, summary_only,
):
    original = pipeline.select_extractive_summary
    summary_calls = 0

    class UnreadySummary:
        release_ready = False

        def __init__(self, original):
            self.original = original

        def __getattr__(self, name):
            return getattr(self.original, name)

    def summary(*args, **kwargs):
        nonlocal summary_calls
        summary_calls += 1
        result = original(*args, **kwargs)
        return UnreadySummary(result) if summary_only and summary_calls == 2 else result

    monkeypatch.setattr(pipeline, "select_extractive_summary", summary)
    writer = _RecoveringPacketWriter(("identity",), remain_thin=not summary_only)
    reviewer = _BoundGroupedReviewer()
    diagnostics = []
    kwargs = dict(
        writer_ask=writer, reviewer_ask=reviewer, diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.FULL, section_evidence_packets=_packets(),
        company_id="00123456", build_identity_sha256="b" * 64,
        evidence_available_fallback=fallback,
        composition_diagnostics_sink=diagnostics,
    )
    reason = "supplement_summary_insufficient" if summary_only else "post_supplement_quality_failed"
    if fallback:
        output = pipeline.run_v2("가나다전자", (), _numeric_table_and_fragment()[0], **kwargs)
        assert output.degraded_reason == "quality_floor"
        assert "full_supplement" not in output.ai_stages_skipped
    else:
        with pytest.raises(V2ValidationError, match=reason):
            pipeline.run_v2("가나다전자", (), _numeric_table_and_fragment()[0], **kwargs)
    assert len(writer.prompts) == 10
    assert len(reviewer.prompts) == 2
    records = [item for item in diagnostics if item.get("step") == "8_FULL보충_품질중단"]
    assert records == [{"step": "8_FULL보충_품질중단", "상태": "수행후품질하한미달", "회복사유": reason}]
    assert list(observed_composition_steps(records)) == records


def test_보충진단은_닫힌사유만남기고_원문필드는제거한다():
    sink = []
    record_supplement_quality_stop(sink, "PRIVATE_RAW", logger=logging.getLogger(__name__))
    assert sink == []
    raw = {"step": "8_FULL보충_품질중단", "상태": "수행후품질하한미달",
           "회복사유": "post_supplement_quality_failed", "원문": "PRIVATE_RAW"}
    assert "PRIVATE_RAW" not in repr(observed_composition_steps((raw,)))
