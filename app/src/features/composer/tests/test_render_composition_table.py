"""2장 매출 구성표가 v2 보고서에 실리는지 못 박는다.

★ 왜 이 시험이 있나 (실측 결함) — v1은 매출 구성표를 만들어 2장에 붙이는데
  (`pipeline/real.py`의 tables_by_section["business_model"]), v2 호출부가
  그 표를 넘기지 않아 «표도 도식도» 통째로 빠져 있었다. JYP 실측에서 9개 장
  중 표를 받은 장은 4장 하나뿐이었다.
★ 도식은 표에서 나온다 — `report_standard/visualization.py`가 ReportTable을
  보고 100% 누적 막대를 그릴지 정한다. 그래서 «표가 없으면 도식도 없다».
  이 시험은 그 출발점인 표가 제자리에 붙는지를 지킨다.

★ 설계 변경 (과제 2·3) — 「한 장에 표는 하나」라는 암묵적 단수
  가정을 걷어냈다.
  - `composition_table`(단수) → `composition_tables`(복수). 제품별·지역별
    두 표를 «둘 다» 2장에 붙인다(예전에는 첫 표만 썼다).
  - 2장에 «사업 흐름» 경로표(FLOW_HEADERS_BY_SECTION의 business_model)를
    추가했다. 흐름표와 구성표가 «같은 장에 함께» 실릴 수 있다 — 예전에는
    프로그램표 자리를 쓴 장은 흐름표를 아예 그리지 않았다(배타 조건).
"""

from __future__ import annotations

from typing import Any

from src.features.composer.constants import (
    BUSINESS_FLOW_CAPTION,
    BUSINESS_FLOW_HEADERS,
    FLOW_PRESENTATION,
    GRADE_CONFIRMED,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    PerformanceTable,
    composition_tables_from_raw,
)
from src.features.composer.render import (
    COMPOSITION_PRESENTATION,
    COMPOSITION_TABLE_SECTION_ID,
    render_report,
)


def _raw_fragments() -> dict[int, dict[str, Any]]:
    return {1: {"종류": "매출수주", "원문": "음반 31.4%, 매니지먼트 68.6%."}}


def _composed(*, flow_rows: tuple[FlowRow, ...] = ()) -> ComposedReport:
    sections = []
    for section_id in SECTION_IDS:
        sentences: tuple[ComposedSentence, ...] = ()
        section_flow_rows: tuple[FlowRow, ...] = ()
        if section_id == COMPOSITION_TABLE_SECTION_ID:
            sentences = (
                ComposedSentence(
                    text="음반·음원과 매니지먼트 두 부문에서 수익이 난다.",
                    citations=("1",),
                    grade=GRADE_CONFIRMED,
                ),
            )
            section_flow_rows = flow_rows
        sections.append(
            ComposedSection(
                section_id=section_id, sentences=sentences, flow_rows=section_flow_rows
            )
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


def _region_composition() -> PerformanceTable:
    return PerformanceTable(
        caption="2025년 지역별 매출 구성",
        headers=("지역", "매출 비중"),
        rows=(("국내", "42.7"), ("해외", "57.3")),
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
        composition_tables=(_composition(),),
    )

    section = _section_of(report, COMPOSITION_TABLE_SECTION_ID)
    assert len(section.tables) == 1
    assert section.tables[0].caption == "2025년 부문별 매출 구성"
    assert section.tables[0].rows == [["음반·음원", "31.4"], ["매니지먼트", "68.6"]]


def test_구성표_두_개가_모두_2장에_실린다():
    """★★ 과제 2 — 제품별·지역별 표를 «둘 다» 낸다. 다른 장으로 옮기지 않는다."""
    report = render_report(
        "가나다전자(주)",
        _composed(),
        _raw_fragments(),
        None,
        composition_tables=(_composition(), _region_composition()),
    )

    section = _section_of(report, COMPOSITION_TABLE_SECTION_ID)
    assert len(section.tables) == 2
    assert section.tables[0].caption == "2025년 부문별 매출 구성"
    assert section.tables[1].caption == "2025년 지역별 매출 구성"
    assert all(table.presentation == COMPOSITION_PRESENTATION for table in section.tables)


def test_구성표는_구성_표시방식으로_실린다():
    """도식 판정기가 이 값을 보고 100% 누적 막대를 그릴지 정한다."""
    report = render_report(
        "가나다전자(주)",
        _composed(),
        _raw_fragments(),
        None,
        composition_tables=(_composition(),),
    )

    assert (
        _section_of(report, COMPOSITION_TABLE_SECTION_ID).tables[0].presentation
        == COMPOSITION_PRESENTATION
    )


def test_구성표가_없으면_2장에_표를_만들지_않는다():
    """자료가 없으면 억지로 만들지 않는다 — 빈 표는 사고다."""
    report = render_report(
        "가나다전자(주)", _composed(), _raw_fragments(), None, composition_tables=()
    )

    assert _section_of(report, COMPOSITION_TABLE_SECTION_ID).tables == []


def test_구성표는_다른_장에_번지지_않는다():
    report = render_report(
        "가나다전자(주)",
        _composed(),
        _raw_fragments(),
        None,
        composition_tables=(_composition(),),
    )

    for section in report.sections:
        if section.cell != COMPOSITION_TABLE_SECTION_ID:
            assert section.tables == [], section.cell


# ══════════════════════════════════════════════════════════
# 과제 3 — 2장 «사업 흐름» 경로표 (구성표와 공존)
# ══════════════════════════════════════════════════════════


def test_2장_흐름표가_구성표와_함께_실린다():
    """★★ 과제 3 — 예전에는 프로그램표(구성표) 자리를 쓴 장은 흐름표를 아예
    안 그렸다(배타 조건). 2장은 이제 «흐름표 + 구성표»를 함께 낸다.
    """
    flow_rows = (
        FlowRow(
            cells=("아티스트 IP", "음반·음원 제작·유통", "팬 구매", "음원 스트리밍 반복 매출"),
            citations=("1",),
        ),
    )
    report = render_report(
        "가나다전자(주)",
        _composed(flow_rows=flow_rows),
        _raw_fragments(),
        None,
        composition_tables=(_composition(), _region_composition()),
    )

    section = _section_of(report, COMPOSITION_TABLE_SECTION_ID)
    표현들 = [table.presentation for table in section.tables]
    assert 표현들 == [FLOW_PRESENTATION, COMPOSITION_PRESENTATION, COMPOSITION_PRESENTATION], (
        f"2장 표 순서·구성이 흐름→구성 두 개가 아닙니다: {표현들}"
    )
    assert section.tables[0].caption == BUSINESS_FLOW_CAPTION
    assert section.tables[0].headers == list(BUSINESS_FLOW_HEADERS)


def test_흐름표만_있고_구성표가_없어도_2장에_흐름표는_실린다():
    flow_rows = (
        FlowRow(
            cells=("아티스트 IP", "음반·음원 제작·유통", "팬 구매", "음원 스트리밍 반복 매출"),
            citations=("1",),
        ),
    )
    report = render_report(
        "가나다전자(주)",
        _composed(flow_rows=flow_rows),
        _raw_fragments(),
        None,
        composition_tables=(),
    )

    section = _section_of(report, COMPOSITION_TABLE_SECTION_ID)
    assert len(section.tables) == 1
    assert section.tables[0].presentation == FLOW_PRESENTATION


# ══════════════════════════════════════════════════════════
# revenuemix 출력 → 구성표 어댑터
# ══════════════════════════════════════════════════════════


def test_수집표_목록을_구성표로_바꾼다():
    표들 = composition_tables_from_raw(
        [
            {
                "caption": "매출 구성 (2025년)",
                "headers": ["부문", "매출 비중"],
                "rows": [["음반·음원", "31.4"], ["매니지먼트", "68.6"]],
                "cite": "[1]",
            }
        ]
    )

    assert len(표들) == 1
    표 = 표들[0]
    assert 표.caption == "매출 구성 (2025년)"
    assert 표.rows == (("음반·음원", "31.4"), ("매니지먼트", "68.6"))
    assert 표.cite == "[1]"


def test_표가_없으면_빈_튜플이다():
    assert composition_tables_from_raw([]) == ()
    assert composition_tables_from_raw(None) == ()


def test_행이_비면_그_표만_빠진다():
    assert (
        composition_tables_from_raw([{"caption": "빈 표", "headers": ["a"], "rows": []}])
        == ()
    )


def test_표가_여럿이면_전부_쓴다():
    """★★ 과제 2 — 제품별·지역별 두 표를 2장에 «다» 넣는다.

    예전(«첫 표만 쓴다»)에는 지역별 표가 통째로 사라졌다. 소유권
    표(2장 = 「고객·지역·채널 우선순위」)에 지역 우선순위가 명시돼 있으므로
    첫 표만 쓰는 것은 v2만의 축소였다 — 다른 장으로 옮기지 않고 2장에 함께
    낸다(제품 결정).
    """
    표들 = composition_tables_from_raw(
        [
            {"caption": "제품별", "headers": ["부문", "비중"], "rows": [["A", "60"], ["B", "40"], ["C", "0"]]},
            {"caption": "지역별", "headers": ["지역", "비중"], "rows": [["국내", "70"], ["해외", "30"], ["기타", "0"]]},
        ]
    )

    assert len(표들) == 2
    assert 표들[0].caption == "제품별"
    assert 표들[1].caption == "지역별"
