"""실제 뤼튼 도식의 회계 처리가 반복 수익으로 공개되는 회귀를 막는다."""

import pytest

from src.features.composer import diagram_check
from src.features.composer.constants import BUSINESS_FLOW_HEADERS, BUSINESS_FLOW_SECTION_ID
from src.features.composer.diagram_check import check_diagram_numbers, check_diagrams
from src.features.composer.diagram_review_constants import (
    BUSINESS_FLOW_ACTION_HEADER,
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


def make_report(
    label=LIVE_CELLS[-1],
    section_id=BUSINESS_FLOW_SECTION_ID,
    action_label="고객의 콘텐츠 제공받음",
):
    cells = list(LIVE_CELLS)
    cells[BUSINESS_FLOW_HEADERS.index(BUSINESS_FLOW_REVENUE_HEADER)] = label
    cells[BUSINESS_FLOW_HEADERS.index(BUSINESS_FLOW_ACTION_HEADER)] = action_label
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


@pytest.mark.parametrize("action_label", [
    "일련의 구별되는 용역 제공; 청구권이 있는 금액으로 수익 인식(실무적 간편법)",
    "청구권이 있는 금액으로 수익 인식(실무적 간편법)",
    "수행의무 이행 시점에 수익 인식; 일부는 청구권 기준 실무적 간편법 적용",
    "일부는 청구권 기준 실무적 간편법 적용; 수행의무 이행 시점에 수익 인식",
    "수행의무 이행 시점에 수익 인식",
])
def test_pure_revenue_recognition_policy_in_customer_action_drops_row(action_label):
    report, fragments = make_report(label="", action_label=action_label)
    checked, problems = check_diagram_numbers(report, fragments)

    assert checked.sections[0].flow_rows == ()
    assert checked.sections[0].sentences == report.sections[0].sentences
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)


def test_action_policy_is_removed_before_legacy_relation_review():
    report, fragments = make_report(
        label="",
        action_label="청구권이 있는 금액으로 수익 인식(실무적 간편법)",
    )

    def unexpected_call(_prompt):
        raise AssertionError("순수 회계 문구 행을 관계 검수에 보내면 안 됩니다")

    checked, problems = check_diagrams(report, fragments, unexpected_call)

    assert checked.sections[0].flow_rows == ()
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)


@pytest.mark.parametrize("action_label", [
    "가맹점 결제액의 0.8%를 PG 수수료로 받는다",
    "고객 자금을 관리하고 계좌이체와 정산 서비스를 제공한다",
    "가맹점 결제액의 0.8%를 수수료로 받고, 청구권이 있는 금액으로 수익 인식(실무적 간편법)",
])
def test_real_customer_transaction_fee_treasury_and_mixed_action_are_preserved(action_label):
    report, fragments = make_report(
        label="주문별 결제 수수료", action_label=action_label,
    )
    fragments = (CollectedFragment(
        fragment_id="1", kind="사업내용", text=LIVE_SOURCE + " " + action_label,
    ),)
    checked, problems = check_diagram_numbers(report, fragments)

    assert checked.sections[0].flow_rows == report.sections[0].flow_rows
    assert not any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)


def test_third_two_recognition_policies_do_not_erase_added_customer_transaction():
    policy = "일부는 청구권 기준 수익 인식(실무적 간편법); 일부는 수행의무 이행 시점에 수익 인식"
    action = "고객에게 월 구독료를 받는다; " + policy
    report, fragments = make_report(label="", action_label=action)
    checked, problems = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == report.sections[0].flow_rows
    assert not any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)


@pytest.mark.parametrize("transaction", [
    "고객에게 월 구독료를 받는다",
    "원천사로부터 대금을 받아 수수료와 부가가치세를 제외해 가맹점에 지급한다",
    "고객 자금을 관리하고 계좌이체와 정산 서비스를 제공한다",
])
@pytest.mark.parametrize("transaction_first", [True, False])
def test_seventh_accounting_clauses_preserve_real_transaction_in_either_order(
    transaction, transaction_first,
):
    policy = "수행의무 이행 시점에 수익 인식; 일부는 청구권 기준 실무적 간편법 적용"
    parts = (transaction, policy) if transaction_first else (policy, transaction)
    report, fragments = make_report(label="", action_label="; ".join(parts))
    checked, problems = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == report.sections[0].flow_rows
    assert not any(FLOW_ACCOUNTING_REVENUE_CODE in reason for reason in problems)


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

@pytest.mark.parametrize("cells,kept", [
    (("인공지능 콘텐츠", "콘텐츠 제공", "미확인"), False),
    (("AI 콘텐츠", "콘텐츠 제공", ""), False),
    (("뤼튼 크랙", "콘텐츠 제공", ""), True),
    (("인공지능 콘텐츠", "콘텐츠 제공", "기업 고객"), True),
    (("AI 콘텐츠", "개인별 학습 자료 생성", ""), True),
])
def test_운영_도식은_대상이_없고_일반_설명뿐인_경우만_제외한다(cells, kept):
    from dataclasses import replace
    from src.features.composer.constants import OPERATIONS_FLOW_SECTION_ID
    report, fragments = make_report(section_id=OPERATIONS_FLOW_SECTION_ID)
    row = replace(report.sections[0].flow_rows[0], cells=cells)
    report = replace(report, sections=(replace(report.sections[0], flow_rows=(row,)),))
    checked, _ = check_diagram_numbers(report, fragments)
    assert bool(checked.sections[0].flow_rows) is kept
    assert checked.sections[0].sentences == report.sections[0].sentences
