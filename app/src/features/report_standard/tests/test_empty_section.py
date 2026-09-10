"""실제 공개 내용과 명시 안내만으로 무봉인 v2 빈 장을 판단한다."""

from types import SimpleNamespace

import pytest

from src.features.report_standard.empty_section import empty_section_notice
from src.features.report_standard.empty_section_constants import EMPTY_SECTION_NOTICE
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION


def _report(**changes):
    return SimpleNamespace(**{"schema_version": ENGINE_V2_SCHEMA_VERSION, "release_mode": "SHADOW", "public_projection": None, **changes})


@pytest.mark.parametrize("content", [
    {"prose_paragraphs": ["정상 설명"]},
    {"prose_lines": [("정상 설명", "[1]")]},
    {"tables": [SimpleNamespace(presentation="table")]},
    {"tables": [SimpleNamespace(presentation="card")]},
])
def test_adds_no_fallback_notice_when_body_table_or_card_exists(content):
    report = _report(quality_observation={"section_public_sentence_counts": [["future_strategy", 0]]})
    assert empty_section_notice(report, SimpleNamespace(**content)) == ""


def test_prefers_and_deduplicates_explicit_empty_section_notices():
    section = SimpleNamespace(empty_reason="기존 한계", guidance_lines=["기존 한계", "추가 한계"])
    assert empty_section_notice(_report(), section) == "기존 한계\n추가 한계"


@pytest.mark.parametrize("changes", [{"schema_version": "company-report-v4"}, {"public_projection": object()}, {"release_mode": "FULL"}])
def test_adds_no_notice_for_v1_full_or_sealed_projection(changes):
    assert empty_section_notice(_report(**changes), SimpleNamespace()) == ""


def test_ignores_whitespace_and_internal_records_without_inferring_global_cause():
    report = _report(shortfall_reasons=["성장 전략 자료가 전혀 없습니다"])
    section = SimpleNamespace(prose_paragraphs=["  "], prose_lines=[("\n", "")], lines=[("감사용 원문", "")], fact_ids=["내부 장부 ID"])
    before = vars(section).copy()
    assert empty_section_notice(report, section) == EMPTY_SECTION_NOTICE
    assert vars(section) == before
