"""보완조사 상태 source의 닫힌 공개 계약."""

from __future__ import annotations

from src.features.pipeline.port import SourceStatus
from src.features.pipeline.research_continuation_status import (
    add_research_continuation_source_status,
)
from src.features.pipeline.research_continuation_status_constants import (
    CLASSIFIER_COVERAGE_GAP_CODE,
    CLASSIFIER_STATUS_DETAIL,
    CLASSIFIER_STATUS_NAME,
    CLASSIFIER_STATUS_STATE,
    OFFICIAL_EVIDENCE_INSUFFICIENT_CODE,
    READINESS_STATUS_DETAIL,
    READINESS_STATUS_NAME,
    READINESS_STATUS_STATE,
    RESEARCH_CONTINUATION_REASON_KEY,
    RESEARCH_CONTINUATION_STEP,
)


def _sources() -> list[SourceStatus]:
    return [
        SourceStatus("전자공시", "ok", "공시 조각 2개"),
        SourceStatus("뉴스", "none", "검색 0건 · 채택 조건 통과 0건"),
    ]


def test_no_supplementary_stage_preserves_existing_sources_object() -> None:
    sources = _sources()

    result = add_research_continuation_source_status(sources, [])

    assert result is sources
    assert result == _sources()


def test_classifier_gap_exposes_only_official_original_and_section_scope() -> None:
    sources = _sources()

    result = add_research_continuation_source_status(
        sources,
        [
            {
                "step": RESEARCH_CONTINUATION_STEP,
                RESEARCH_CONTINUATION_REASON_KEY: CLASSIFIER_COVERAGE_GAP_CODE,
                "임의오류": "https://secret.example/raw?token=never-public",
            }
        ],
    )

    assert sources == _sources()
    assert result[:-1] == sources
    assert result[-1] == SourceStatus(
        CLASSIFIER_STATUS_NAME,
        CLASSIFIER_STATUS_STATE,
        CLASSIFIER_STATUS_DETAIL,
    )
    assert "URL" not in result[-1].detail
    assert "token" not in result[-1].detail


def test_official_preparation_shortfall_does_not_claim_total_dart_absence_or_missing_company_info() -> None:
    result = add_research_continuation_source_status(
        _sources(),
        [
            {
                "step": RESEARCH_CONTINUATION_STEP,
                RESEARCH_CONTINUATION_REASON_KEY: OFFICIAL_EVIDENCE_INSUFFICIENT_CODE,
            }
        ],
    )

    assert result[-1] == SourceStatus(
        READINESS_STATUS_NAME,
        READINESS_STATUS_STATE,
        READINESS_STATUS_DETAIL,
    )
    assert "DART" not in result[-1].detail
    assert "회사 자체 정보" not in result[-1].detail


def test_out_of_contract_reason_and_shape_error_do_not_create_or_expose_new_status() -> None:
    sources = _sources()

    result = add_research_continuation_source_status(
        sources,
        [
            {
                "step": RESEARCH_CONTINUATION_STEP,
                RESEARCH_CONTINUATION_REASON_KEY: "provider_timeout:https://private.example",
            }
        ],
    )

    assert result is sources
    assert all("private" not in status.detail for status in result)
