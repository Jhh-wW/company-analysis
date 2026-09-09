"""마지막 수치 본문 제외 시 안내·정상 문장·표를 보존하는 회귀."""

from dataclasses import replace

import pytest

from src.features.composer.constants import NOTICE_NUMERIC_BODY_WITHHELD
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence, FlowRow, NewsRow
from src.features.composer.structured_claims import enforce_public_numeric_safety


def _unsafe():
    return ComposedSentence("2027년 매출이 25% 증가할 것으로 해석됩니다.", ("1",), "해석")


@pytest.mark.parametrize("notice", ["수집을 완료하지 못했습니다.", "검사를 완료하지 못했습니다.", "확인한 자료가 충분하지 않습니다."])
def test_preserves_existing_notice_table_rows_and_citations(notice):
    section = ComposedSection(
        "future_strategy", (_unsafe(),), notice=notice,
        flow_rows=(FlowRow(("계획", "공식 발표"), ("2",)),),
        news_rows=(NewsRow(("2026-09-01", "언론", "보도 본문"), ("3",)),),
    )
    safe, _ = enforce_public_numeric_safety(ComposedReport((section,)))
    assert safe.sections[0] == replace(section, sentences=())


def test_withholds_numeric_body_without_claiming_section_has_no_data():
    row = FlowRow(("계획", "공식 발표"), ("2",))
    safe, _ = enforce_public_numeric_safety(ComposedReport((ComposedSection("future_strategy", (_unsafe(),), flow_rows=(row,)),)))
    assert safe.sections[0].flow_rows == (row,)
    assert safe.sections[0].notice == NOTICE_NUMERIC_BODY_WITHHELD
    assert "본문 설명" in safe.sections[0].notice
    assert "자료가 없다는 뜻은 아닙니다" in safe.sections[0].notice


def test_adds_no_numeric_notice_when_valid_body_remains_or_section_was_empty():
    normal = ComposedSentence("회사는 해외 사업 확대 계획을 밝혔습니다.", ("1",), "확인")
    report = ComposedReport((ComposedSection("future_strategy", (_unsafe(), normal)), ComposedSection("culture", ())))
    safe, _ = enforce_public_numeric_safety(report)
    assert safe.sections[0].sentences == (normal,)
    assert safe.sections[0].notice == ""
    assert safe.sections[1] == report.sections[1]
