"""2장 매출 구성표가 v2 보고서에 실리는지 못 박는다.

★ 왜 이 시험이 있나 (실측 결함) — v1은 매출 구성표를 만들어 2장에 붙이는데
  (`pipeline/real.py`의 tables_by_section["business_model"]), v2 호출부가
  그 표를 넘기지 않아 «표도 도식도» 통째로 빠져 있었다. JYP 실측에서 9개 장
  중 표를 받은 장은 4장 하나뿐이었다.
★ 도식은 표에서 나온다 — `report_standard/visualization.py`가 ReportTable을
  보고 100% 누적 막대를 그릴지 정한다. 그래서 «표가 없으면 도식도 없다».
  이 시험은 그 출발점인 표가 제자리에 붙는지를 지킨다.
"""

from __future__ import annotations

from typing import Any

from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
    composition_table_from_raw,
)
from src.features.composer.render import (
    COMPOSITION_PRESENTATION,
    COMPOSITION_TABLE_SECTION_ID,
    render_report,
)


def _raw_fragments() -> dict[int, dict[str, Any]]:
    return {1: {"종류": "매출수주", "원문": "음반 31.4%, 매니지먼트 68.6%."}}


def _composed() -> ComposedReport:
    sections = []
    for section_id in SECTION_IDS:
        sentences: tuple[ComposedSentence, ...] = ()
        if section_id == COMPOSITION_TABLE_SECTION_ID:
            sentences = (
                ComposedSentence(
                    text="음반·음원과 매니지먼트 두 부문에서 수익이 난다.",
                    citations=("1",),
                    grade=GRADE_CONFIRMED,
                ),
            )
        sections.append(
            ComposedSection(section_id=section_id, sentences=sentences)
        )
    return ComposedReport(
        sections=tuple(sections),
        summary=(
            ComposedSentence(
                text="두 부문 구조다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
        ),
    )


def _composition() -> PerformanceTable:
    return PerformanceTable(
        caption="2025년 부문별 매출 구성",
        headers=("부문", "매출 비중"),
        rows=(("음반·음원", "31.4"), ("매니지먼트", "68.6")),
        unit="",
        cite="조각 1·매출수주",
    )


def _section_of(report, cell: str):
    for section in report.sections:
        if section.cell == cell:
            return section
    raise AssertionError(f"{cell} 장이 없습니다")


def test_구성표가_2장에_실린다():
    report = render_report(
        "가나다전자(주)",
        _composed(),
        _raw_fragments(),
        None,
        composition_table=_composition(),
    )

    section = _section_of(report, COMPOSITION_TABLE_SECTION_ID)
    assert len(section.tables) == 1
    assert section.tables[0].caption == "2025년 부문별 매출 구성"
    assert section.tables[0].rows == [["음반·음원", "31.4"], ["매니지먼트", "68.6"]]


def test_구성표는_구성_표시방식으로_실린다():
    """도식 판정기가 이 값을 보고 100% 누적 막대를 그릴지 정한다."""
    report = render_report(
        "가나다전자(주)",
        _composed(),
        _raw_fragments(),
        None,
        composition_table=_composition(),
    )

    assert (
        _section_of(report, COMPOSITION_TABLE_SECTION_ID).tables[0].presentation
        == COMPOSITION_PRESENTATION
    )


def test_구성표가_없으면_2장에_표를_만들지_않는다():
    """자료가 없으면 억지로 만들지 않는다 — 빈 표는 사고다."""
    report = render_report(
        "가나다전자(주)", _composed(), _raw_fragments(), None, composition_table=None
    )

    assert _section_of(report, COMPOSITION_TABLE_SECTION_ID).tables == []


def test_구성표는_다른_장에_번지지_않는다():
    report = render_report(
        "가나다전자(주)",
        _composed(),
        _raw_fragments(),
        None,
        composition_table=_composition(),
    )

    for section in report.sections:
        if section.cell != COMPOSITION_TABLE_SECTION_ID:
            assert section.tables == [], section.cell


# ══════════════════════════════════════════════════════════
# revenuemix 출력 → 구성표 어댑터
# ══════════════════════════════════════════════════════════


def test_수집표_목록을_구성표로_바꾼다():
    표 = composition_table_from_raw(
        [
            {
                "caption": "매출 구성 (2025년)",
                "headers": ["부문", "매출 비중"],
                "rows": [["음반·음원", "31.4"], ["매니지먼트", "68.6"]],
                "cite": "[1]",
            }
        ]
    )

    assert 표 is not None
    assert 표.caption == "매출 구성 (2025년)"
    assert 표.rows == (("음반·음원", "31.4"), ("매니지먼트", "68.6"))
    assert 표.cite == "[1]"


def test_표가_없으면_None이다():
    assert composition_table_from_raw([]) is None
    assert composition_table_from_raw(None) is None


def test_행이_비면_None이다():
    assert (
        composition_table_from_raw([{"caption": "빈 표", "headers": ["a"], "rows": []}])
        is None
    )


def test_표가_여럿이면_첫_표만_쓴다():
    """제품별·지역별 두 표를 2장에 다 넣으면 같은 매출을 두 번 보여 준다."""
    표 = composition_table_from_raw(
        [
            {"caption": "제품별", "headers": ["부문", "비중"], "rows": [["A", "60"]]},
            {"caption": "지역별", "headers": ["지역", "비중"], "rows": [["국내", "70"]]},
        ]
    )

    assert 표 is not None
    assert 표.caption == "제품별"
