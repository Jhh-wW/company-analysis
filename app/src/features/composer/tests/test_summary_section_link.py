"""요약 카드가 «어느 장 이야기인지» 가리키는지 못 박는다.

★ 왜 (실측 결함) — v2는 SummaryItem에 section_id를 안 채웠다. 그래서 화면
  요약 카드의 주제 라벨이 전부 「핵심결론」으로 나오고, 「→ 4장」 링크 칸이
  통째로 비었다. 독자가 이 요약이 어느 장 이야기인지 알 수 없다.
★ 요약은 검증된 본문을 재료로 «새로» 쓰므로 출처 장이 기록되지 않는다.
  대신 «같은 근거를 가장 많이 공유하는 장»을 찾는다.
★ 겹치는 장이 없으면 비운다 — 틀린 장을 가리키는 것이 라벨 없는 것보다 나쁘다.
"""

from __future__ import annotations

from typing import Any

from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.render import render_report
from src.features.report_standard.section_content import summary_topic


def _fragments() -> dict[int, dict[str, Any]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 검사 장비를 만든다."},
        2: {"종류": "재무", "원문": "2025년 매출은 1,200억원이다."},
        3: {"종류": "홈페이지", "원문": "고객 최우선.", "출처": "https://e.test/a"},
    }


def _s(text: str, citations: tuple[str, ...]):
    return ComposedSentence(text=text, citations=citations, grade=GRADE_CONFIRMED)


def _render(summary: tuple[ComposedSentence, ...]):
    sections = []
    for sid in SECTION_IDS:
        sentences: tuple[ComposedSentence, ...] = ()
        if sid == "identity":
            sentences = (_s("검사 장비를 만든다.", ("1",)),)
        elif sid == "past_changes":
            sentences = (_s("2025년 매출은 1,200억원이다.", ("2",)),)
        elif sid == "culture":
            sentences = (_s("고객 최우선을 내건다.", ("3",)),)
        sections.append(ComposedSection(section_id=sid, sentences=sentences))
    return render_report(
        "가나다전자(주)",
        ComposedReport(sections=tuple(sections), summary=summary),
        _fragments(),
        None,
    )


def test_요약이_근거를_공유하는_장을_가리킨다():
    report = _render(
        (
            _s("검사 장비 중심 구조다.", ("1",)),
            _s("최근 매출은 성장 흐름이다.", ("2",)),
            _s("고객 가치를 앞세운다.", ("3",)),
        )
    )

    assert [item.section_id for item in report.summary_items] == [
        "identity",
        "past_changes",
        "culture",
    ]


def test_주제_라벨이_핵심결론에서_실제_이름으로_바뀐다():
    """이게 안 되면 요약 다섯 줄이 전부 「핵심결론」으로 보인다."""
    report = _render(
        (
            _s("검사 장비 중심 구조다.", ("1",)),
            _s("최근 매출은 성장 흐름이다.", ("2",)),
        )
    )

    labels = [summary_topic(item.section_id) for item in report.summary_items]
    assert labels == ["기업정체", "주요변화"]
    assert "핵심결론" not in labels


def test_근거가_없으면_장을_가리키지_않는다():
    """틀린 장을 가리키는 것이 라벨 없는 것보다 나쁘다."""
    report = _render((_s("종합적인 판단이다.", ()),))

    assert report.summary_items[0].section_id == ""
    assert summary_topic(report.summary_items[0].section_id) == "핵심결론"


def test_어느_장에도_없는_근거면_장을_가리키지_않는다():
    report = _render((_s("어디에도 없는 근거를 쓴다.", ("99",)),))

    assert report.summary_items[0].section_id == ""
