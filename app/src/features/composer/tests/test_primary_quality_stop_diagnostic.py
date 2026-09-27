"""FULL 품질 하한 중단은 기존 출고 처분을 유지하며 정화된 1차 이유를 남긴다."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from src.features.composer.pipeline import run_v2
from src.features.composer.quality_observation_log import record_primary_quality_stop
from src.features.composer.tests.injected_program_fixture import (
    make_numeric_performance_evidence,
)
from src.features.composer.tests.test_evidence_available_report import _StrictThinWriter
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer,
    _strict_fragments,
    _strict_packet_set,
)
from src.features.composer.validate import V2ValidationError
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.composition_diagnostics import observed_composition_steps
from src.shared.report_quality.models import QualityProblemCode


def test_품질하한_중단_직전_닫힌_1차_원인을_남긴다() -> None:
    diagnostics: list[dict[str, object]] = []
    performance_table, _, filing_meta = make_numeric_performance_evidence(
        fragment_number=9
    )
    with pytest.raises(V2ValidationError, match="report_recovery:too_many_underfilled_sections"):
        run_v2(
            "가나다전자",
            _strict_fragments(),
            performance_table,
            writer_ask=_StrictThinWriter(),
            reviewer_ask=_FakeReviewer(),
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_strict_packet_set(),
            filing_meta=filing_meta,
            company_id="00123456",
            build_identity_sha256="b" * 64,
            composition_diagnostics_sink=diagnostics,
        )
    records = [item for item in diagnostics if item.get("step") == "8_FULL1차_품질중단"]
    assert len(records) == 1
    record = records[0]
    assert record["회복사유"] == "too_many_underfilled_sections"
    assert record["품질코드"]
    assert record["안전문제수"] == 0
    assert record["안전유형"] == {}
    assert [item for item in observed_composition_steps(diagnostics)
            if item.get("step") == "8_FULL1차_품질중단"] == records


def test_primary_stop_producer_never_emits_private_raw_text() -> None:
    private_marker = "PRIVATE_RAW_MARKER"
    quality = SimpleNamespace(
        problem_codes=(QualityProblemCode.ONE_CLAIM_SECTIONS,),
        notice_only_sections=(),
        one_claim_sections=("business_model",),
        underfilled_sections=("business_model",),
        semantic_underfilled_sections=(),
        shortfall_reasons=(private_marker,),
    )
    assessment = SimpleNamespace(
        quality=quality,
        safety=SimpleNamespace(problems=(private_marker,)),
    )
    sink: list[dict[str, object]] = []
    record_primary_quality_stop(
        sink,
        assessment,
        "too_many_underfilled_sections",
        logger=logging.getLogger(__name__),
    )
    assert len(sink) == 1
    assert private_marker not in repr(sink)
    assert sink[0]["안전유형"] == {"other": 1}


def test_primary_stop_recording_is_noop_for_other_reason() -> None:
    sink: list[dict[str, object]] = []
    record_primary_quality_stop(
        sink,
        SimpleNamespace(),
        "not_a_quality_stop",
        logger=logging.getLogger(__name__),
    )
    assert sink == []


def test_primary_stop_recording_failure_does_not_interrupt_or_log_private_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_marker = "PRIVATE_RAW_MARKER"
    sink: list[dict[str, object]] = []
    assessment = SimpleNamespace(quality=SimpleNamespace(shortfall_reasons=(private_marker,)))
    with caplog.at_level(logging.WARNING):
        record_primary_quality_stop(
            sink,
            assessment,
            "too_many_underfilled_sections",
            logger=logging.getLogger(__name__),
        )
    assert sink == []
    assert private_marker not in caplog.text
