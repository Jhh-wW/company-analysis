"""실제 뤼튼 도식의 회계 처리가 반복 수익으로 공개되는 회귀를 막는다."""

import pytest

from src.features.composer import diagram_check
from src.features.composer.constants import BUSINESS_FLOW_HEADERS, BUSINESS_FLOW_SECTION_ID
from src.features.composer.diagram_check import check_diagram_numbers, check_diagrams
from src.features.composer.diagram_review_constants import (
    BUSINESS_FLOW_REVENUE_HEADER,
    FLOW_ACCOUNTING_REVENUE_CODE,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)

LIVE_CELLS = (
    "인공지능 콘텐츠 생성 플랫폼", "AI 콘텐츠",
    "고객의 콘텐츠 제공받음", "선수수익으로 미수행의무 관리",
)
LIVE_SOURCE = "고객이 콘텐츠를 제공받는 시점에 수익을 인식하고 미수행의무는 선수수익으로 관리한다."


def make_report(label=LIVE_CELLS[-1], section_id=BUSINESS_FLOW_SECTION_ID):
    cells = list(LIVE_CELLS)
    cells[BUSINESS_FLOW_HEADERS.index(BUSINESS_FLOW_REVENUE_HEADER)] = label
    sentence = ComposedSentence(
        text="회사의 영업수익은 용역 매출과 인공지능 콘텐츠 매출로 구성된다.",
        citations=("1",), grade="확인",
    )
    row = FlowRow(cells=tuple(cells), citations=("1",))
    report = ComposedReport(sections=(ComposedSection(
        section_id=section_id, sentences=(sentence,), flow_rows=(row,),
    ),))
    return report, (CollectedFragment(fragment_id="1", kind="수익인식", text=LIVE_SOURCE),)


def test_live_accounting_flow_is_removed_without_changing_prose():
    report, fragments = make_report()
    checked, problems = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == ()
    assert checked.sections[0].sentences == report.sections[0].sentences
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)


@pytest.mark.parametrize("label", [
    "선수수익으로 미수행의무 관리",
    "선수수익으로 미수행 의무 관리.",
    "계약부채로 미이행의무 계상",
    "미수행의무를 선수수익으로 관리한다",
])
def test_accounting_only_labels_do_not_become_revenue_paths(label):
    report, fragments = make_report(label)
    checked, _ = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == ()


@pytest.mark.parametrize("label", [
    "월 구독료와 사용량 기반 추가 결제",
    "고객의 연간 선결제 후 계약 갱신",
    "정기 구독료를 선결제받아 선수수익으로 관리",
    "선수수익 10억원",
    "선수수익 관리 시스템 구독료",
    "미수행의무 관리 서비스 이용료",
    "계약부채 감소와 구독료 반복 결제",
])
def test_specific_commercial_and_mixed_labels_are_preserved(label):
    report, fragments = make_report(label)
    # 수치 포함 대조도 원문에 실제 있는 경우를 사용한다.
    fragments = (CollectedFragment(
        fragment_id="1", kind="사업내용", text=LIVE_SOURCE + " " + label,
    ),)
    checked, problems = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == report.sections[0].flow_rows
    assert not any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)


def test_other_section_and_other_column_are_preserved():
    report, fragments = make_report(section_id="operations_partners")
    checked, _ = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == report.sections[0].flow_rows

    report, fragments = make_report("정기 구독료")
    row = report.sections[0].flow_rows[0]
    cells = list(row.cells)
    cells[0] = LIVE_CELLS[-1]
    from dataclasses import replace
    report = replace(report, sections=(replace(
        report.sections[0], flow_rows=(replace(row, cells=tuple(cells)),),
    ),))
    checked, _ = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == report.sections[0].flow_rows


def test_revenue_column_is_selected_by_header(monkeypatch):
    report, fragments = make_report()
    headers = tuple(reversed(BUSINESS_FLOW_HEADERS))
    monkeypatch.setitem(diagram_check.FLOW_HEADERS_BY_SECTION, BUSINESS_FLOW_SECTION_ID, headers)
    from dataclasses import replace
    row = report.sections[0].flow_rows[0]
    report = replace(report, sections=(replace(
        report.sections[0], flow_rows=(replace(row, cells=tuple(reversed(row.cells))),),
    ),))
    checked, _ = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == ()


def test_legacy_review_does_not_spend_a_call_on_the_rejected_flow():
    report, fragments = make_report()

    def unexpected_call(_prompt):
        raise AssertionError("이미 거절한 회계 경로를 AI에 다시 보내면 안 됩니다")

    checked, problems = check_diagrams(report, fragments, unexpected_call)
    assert checked.sections[0].flow_rows == ()
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)