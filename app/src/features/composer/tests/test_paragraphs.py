"""본문 문단 나누기를 못 박는다.

★ 왜 (실측 결함) — 화면(result.html)도 PDF(export_pdf/logic.py)도 한 장의
  문장을 «전부 이어 붙여» 한 문단으로 냈다. 진영 2장은 8문장이 줄바꿈 없이
  한 덩어리로 나갔다. 가독성 신고의 큰 원인이다.
★ 나누는 기준은 «같은 출처를 인용하는 문장 묶음»이다. 인용이 바뀌면 이야기
  주제가 바뀐 것이고, 절충안이 번호를 다는 자리(묶음의 끝)와 정확히 맞는다.
  해석 문장은 앞 문장의 뜻풀이라 묶음을 끊지 않는다.
★ prose_lines(문장 단위)는 «그대로» 둔다 — 출고 검증과 저장이 그 단위를 쓴다.
  문단은 표시용으로 따로 담는다.
"""

from __future__ import annotations

from typing import Any

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    PARAGRAPH_MAX_SENTENCES,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.render import render_report


def _fragments() -> dict[int, dict[str, Any]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 검사 장비를 만든다."},
        2: {"종류": "재무", "원문": "2025년 매출은 1,200억원이다."},
    }


def _s(text: str, citations: tuple[str, ...], grade: str = GRADE_CONFIRMED):
    return ComposedSentence(text=text, citations=citations, grade=grade)


def _render(sentences: tuple[ComposedSentence, ...]):
    sections = tuple(
        ComposedSection(
            section_id=sid, sentences=sentences if sid == "identity" else ()
        )
        for sid in SECTION_IDS
    )
    composed = ComposedReport(
        sections=sections,
        summary=(_s("검사 장비 중심 구조다.", ("1",)),),
    )
    return render_report("가나다전자(주)", composed, _fragments(), None)


def _paragraphs(report) -> list[str]:
    for section in report.sections:
        if section.cell == "identity":
            return list(section.prose_paragraphs)
    raise AssertionError("identity 장이 없습니다")


def _sentence_count(report) -> int:
    for section in report.sections:
        if section.cell == "identity":
            return len(section.prose_lines)
    raise AssertionError("identity 장이 없습니다")


def test_출처가_바뀌면_문단이_나뉜다():
    report = _render(
        (
            _s("검사 장비를 만든다.", ("1",)),
            _s("2025년 매출은 1,200억원이다.", ("2",)),
        )
    )

    assert len(_paragraphs(report)) == 2


def test_같은_출처가_이어지면_한_문단이다():
    report = _render(
        (
            _s("검사 장비를 만든다.", ("1",)),
            _s("장비는 반도체 공정에 쓰인다.", ("1",)),
        )
    )

    assert len(_paragraphs(report)) == 1


def test_해석은_앞_문장과_같은_문단에_붙는다():
    """해석은 앞 문장의 뜻풀이다 — 떼어 놓으면 무엇에 대한 해석인지 흐려진다."""
    report = _render(
        (
            _s("검사 장비를 만든다.", ("1",)),
            _s("장비 회사로 읽힌다.", ("1",), GRADE_INTERPRETED),
        )
    )

    assert len(_paragraphs(report)) == 1


def test_출처가_안_바뀌어도_상한에서_끊는다():
    """안 끊으면 문단이 다시 벽이 된다."""
    많은문장 = tuple(
        _s(f"{i}번째 문장이다.", ("1",))
        for i in range(PARAGRAPH_MAX_SENTENCES * 2)
    )

    report = _render(많은문장)

    assert len(_paragraphs(report)) == 2


def test_문장_단위_기록은_그대로_남는다():
    """출고 검증과 저장이 문장 단위를 쓴다 — 문단을 만들되 문장을 잃지 않는다."""
    문장 = (
        _s("검사 장비를 만든다.", ("1",)),
        _s("2025년 매출은 1,200억원이다.", ("2",)),
        _s("장비 회사로 읽힌다.", ("1",), GRADE_INTERPRETED),
    )

    report = _render(문장)

    assert _sentence_count(report) == len(문장)


def test_문단을_이어_붙이면_문장_전부가_들어_있다():
    """문단으로 묶는 과정에서 글자를 잃지 않는다."""
    문장 = (
        _s("검사 장비를 만든다.", ("1",)),
        _s("2025년 매출은 1,200억원이다.", ("2",)),
    )

    report = _render(문장)
    이어붙임 = " ".join(_paragraphs(report))

    assert "검사 장비를 만든다." in 이어붙임
    assert "2025년 매출은 1,200억원이다." in 이어붙임


def test_안내문도_문단_하나로_나간다():
    """자료가 없어 안내문만 있는 장도 문단이 있어야 화면이 안 빈다."""
    sections = tuple(
        ComposedSection(section_id=sid, sentences=(), notice="자료를 찾지 못했습니다.")
        for sid in SECTION_IDS
    )
    composed = ComposedReport(
        sections=sections, summary=(_s("요약이다.", ("1",)),)
    )
    report = render_report("가나다전자(주)", composed, _fragments(), None)

    assert _paragraphs(report) == ["자료를 찾지 못했습니다."]
