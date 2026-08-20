"""회사 사실과 프로그램 제안 질문의 경계 회귀 시험."""

from __future__ import annotations

from src.features.company_use.logic import build_company_use_section
from src.features.pipeline.port import ReportSection


def test_인용된_회사사실과_비사실_준비질문을_물리적으로_분리한다() -> None:
    sections = [
        ReportSection(
            cell="1",
            title="사업 구조",
            prose_lines=[("가나다는 구독 매출을 낸다.", "[1]")],
        ),
        ReportSection(
            cell="3",
            title="최근 실적",
            lines=[("매출은 전년보다 증가했다.", "[2]")],
        ),
    ]

    result = build_company_use_section(sections)

    assert result.cell == "활용"
    assert result.lines == [
        ("가나다는 구독 매출을 낸다.", "[1]"),
        ("매출은 전년보다 증가했다.", "[2]"),
    ]
    assert result.guidance_lines
    assert all(cite for _text, cite in result.lines)
    assert all("활용 질문" in line for line in result.guidance_lines)
    assert all("직무" not in line and "채용공고" not in line for line in result.guidance_lines)
    assert not any(question in text for text, _cite in result.lines for question in result.guidance_lines)


def test_인용가능한_회사사실이_없으면_질문도_만들지_않는다() -> None:
    result = build_company_use_section(
        [ReportSection(cell="1", title="사업 구조", lines=[("출처 없는 문장", "")])]
    )

    assert result.lines == []
    assert result.guidance_lines == []
    assert "인용 가능한 회사 사실" in result.empty_reason
