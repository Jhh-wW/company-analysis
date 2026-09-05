"""3장 제품·서비스명은 인용 조각에 있는 표면 문자열만 공개한다."""

from __future__ import annotations

import pytest

from src.features.composer.constants import (
    PORTFOLIO_TABLE_GUIDE_V2,
    PORTFOLIO_TABLE_SECTION_ID,
)
from src.features.composer.diagram_check import (
    PORTFOLIO_NAME_NOT_IN_SOURCE_CODE,
    check_diagram_numbers,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)


def _fragment(fragment_id: str, text: str) -> CollectedFragment:
    return CollectedFragment(fragment_id=fragment_id, kind="사업내용", text=text)


def _report(
    portfolio_row: FlowRow,
    other_row: FlowRow | None = None,
) -> ComposedReport:
    sections = [
        ComposedSection(
            section_id=PORTFOLIO_TABLE_SECTION_ID,
            sentences=(
                ComposedSentence(
                    text="회사는 공식 제품을 운영한다.",
                    citations=portfolio_row.citations,
                    grade="확인",
                ),
            ),
            flow_rows=(portfolio_row,),
        )
    ]
    if other_row is not None:
        sections.append(
            ComposedSection(
                section_id="operations_partners",
                sentences=(),
                flow_rows=(other_row,),
            )
        )
    return ComposedReport(sections=tuple(sections))


def _portfolio_row(report: ComposedReport) -> FlowRow:
    section = next(
        item
        for item in report.sections
        if item.section_id == PORTFOLIO_TABLE_SECTION_ID
    )
    return section.flow_rows[0]


def test_인용_조각에_글자_그대로_있는_이름은_유지한다() -> None:
    row = FlowRow(
        cells=("카카오T", "모빌리티 서비스", "운영 확대", "주력"),
        citations=("1", "2"),
    )

    checked, problems = check_diagram_numbers(
        _report(row),
        (
            _fragment("1", "다른 제품 설명이다."),
            _fragment("2", "카카오T는 모빌리티 서비스를 운영한다."),
        ),
    )

    assert _portfolio_row(checked) == row
    assert problems == ()


def test_어느_인용_조각에도_없는_이름은_첫_칸만_비운다() -> None:
    cells = ("지어낸 이름", "공식 서비스 범위", "운영 확대", "주력")
    row = FlowRow(cells=cells, citations=("1",))

    checked, problems = check_diagram_numbers(
        _report(row), (_fragment("1", "공식 서비스 범위를 운영하고 있다."),)
    )

    grounded = _portfolio_row(checked)
    assert grounded.cells == ("", *cells[1:])
    assert grounded.citations == row.citations
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


@pytest.mark.parametrize(
    ("source", "name"),
    (
        ("카카오 T를 운영한다.", "카카오T"),
        ("카카오-T를 운영한다.", "카카오 T"),
        ("ＡＢＣ 서비스를 운영한다.", "abc서비스"),
    ),
)
def test_호환문자_대소문자_공백_구두점_차이는_같은_이름이다(
    source: str, name: str
) -> None:
    row = FlowRow(
        cells=(name, "서비스 범위", "운영 확대", "주력"), citations=("1",)
    )

    checked, problems = check_diagram_numbers(
        _report(row), (_fragment("1", source),)
    )

    assert _portfolio_row(checked) == row
    assert problems == ()


def test_서로_다른_인용_조각의_경계에_걸친_이름은_접지가_아니다() -> None:
    row = FlowRow(
        cells=("카카오T", "서비스 범위", "운영 확대", "주력"),
        citations=("1", "2"),
    )

    checked, problems = check_diagram_numbers(
        _report(row),
        (_fragment("1", "카카오"), _fragment("2", "T를 운영한다.")),
    )

    assert _portfolio_row(checked).cells[0] == ""
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


def test_빈_이름과_다른_장과_3장의_다른_칸은_동작이_같다() -> None:
    empty_name = FlowRow(
        cells=("", "공식 범위", "운영 확대", "주력"), citations=("1",)
    )
    other_row = FlowRow(
        cells=("요약 이름", "공식 운영", "고객"), citations=("1",)
    )
    original = _report(empty_name, other_row)

    checked, problems = check_diagram_numbers(
        original, (_fragment("1", "공식 범위를 운영하며 고객에게 제공한다."),)
    )

    assert checked == original
    assert _portfolio_row(checked).cells[1:] == empty_name.cells[1:]
    assert checked.sections[1].flow_rows == (other_row,)
    assert problems == ()


def test_3장_새_안내문은_이름_접지_네_문장과_숫자_금지를_함께_둔다() -> None:
    guide = PORTFOLIO_TABLE_GUIDE_V2

    assert "인용한 근거 조각에 글자 그대로 있는 이름만 쓴다" in guide
    assert "줄임·번역·조합 금지" in guide
    assert (
        "원문위치에 이름 표 표기(제품·브랜드·사업부문·종속회사·주요 계약)가 "
        "있는 조각이 있으면 그 이름을 우선 쓴다"
    ) in guide
    assert (
        "이름이 종속회사나 주요 계약이면 「제품·서비스 범위」 칸에 그 성격"
        "(종속회사 사업 / 주요 계약)을 한 구절로 밝힌다"
    ) in guide
    assert "원문에서 이름을 못 찾으면 그 칸을 비운다" in guide
    assert "숫자·퍼센트·연도를 쓰지 않는다" in guide
