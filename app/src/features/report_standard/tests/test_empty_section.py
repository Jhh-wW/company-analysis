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


def test_fallback_notice_is_the_exact_sentence_readers_see_elsewhere():
    """★ 같은 화면에 «세 번째 문구»가 나오지 않게 글자를 맞춰 둔다.

    실측 — (주)메디라인액티브코리아 2026-09-18 보고서는 4장에 본문 안내문
    「확인된 자료가 부족해 이 장은 비어 있습니다.」를 싣고, 9장에는 이 계층이
    만든 다른 문구를 실었다. 독자에게는 같은 상황인데 글이 달라 보인다.

    두 계층은 서로 import하지 않으므로(경계 유지) 글자가 갈라지면 아무도
    모른다. 그래서 여기서 «값»으로 못 박는다 — 어느 한쪽을 고치면 이 시험이
    먼저 깨진다.
    """
    from src.features.composer.constants import NOTICE_INSUFFICIENT_EVIDENCE

    assert EMPTY_SECTION_NOTICE == "확인된 자료가 부족해 이 장은 비어 있습니다."
    assert EMPTY_SECTION_NOTICE == NOTICE_INSUFFICIENT_EVIDENCE
