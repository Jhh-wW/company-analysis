"""부분 경로에서도 명시된 의미칸과 기존 장 메타를 잃지 않는다."""

import pytest

from src.features.composer.logic import _sanitize_report_to_section_evidence
from src.features.composer.port import ComposedReport, ComposedSection, FlowRow, NewsRow


SECTION = "current_challenges"
ALLOWED = {SECTION: frozenset({"1", "2", "3"})}
SUPPORTED = {
    "1": frozenset(),
    "2": frozenset({"current_challenges:issue"}),
    "3": frozenset({"current_challenges:response"}),
}


@pytest.mark.parametrize("citations,kept", (
    (("2",), False),
    (("2", "3"), True),
    (("1", "2"), False),
    (("1",), True),
))
def test_부분도식은_명시된_과제근거로_대응칸을_만들지_않는다(citations, kept):
    row = FlowRow(("공급 지연이 발생했다.", "공급처를 추가했다."), citations)
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(row,)),))

    checked = _sanitize_report_to_section_evidence(
        report, ALLOWED, supported_claim_slots_by_fragment_id=SUPPORTED,
        enforce_declared_claim_slot_support=True,
    )

    assert checked.sections[0].flow_rows == ((row,) if kept else ())


def test_부분도식은_비어_있는_대응칸의_근거를_강제로_요구하지_않는다():
    row = FlowRow(("공급 지연이 발생했다.", ""), ("2",))
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(row,)),))

    checked = _sanitize_report_to_section_evidence(
        report, ALLOWED, supported_claim_slots_by_fragment_id=SUPPORTED,
        enforce_declared_claim_slot_support=True,
    )

    assert checked.sections[0].flow_rows == (row,)


def test_부분근거정리는_이미_정리한_이동안내와_뉴스행을_소실시키지_않는다():
    news = NewsRow(("2026-09-01", "시험매체", "공급 지연이 발생했다."),
                   ("2",), ("공급 지연이 발생했다.",))
    section = ComposedSection(
        SECTION, (), notice="관련 내용을 다른 장으로 모았습니다.",
        news_rows=(news,), news_decisions=(("2", "제외", "기존 판정"),),
        moved_to_sections=("operations_partners",),
    )
    report = ComposedReport((section,))

    checked = _sanitize_report_to_section_evidence(
        report, ALLOWED, supported_claim_slots_by_fragment_id=SUPPORTED,
        enforce_declared_claim_slot_support=True,
    )

    assert checked.sections == (section,)
