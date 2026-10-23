"""세 번째 저장 응답의 무료 소비 재현. 새 AI 판정·원문·서명을 만들지 않는다."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.features.composer.diagram_check import check_diagram_numbers
from src.features.composer.diagram_review_constants import FLOW_ACCOUNTING_REVENUE_CODE
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, FlowRow,
)
from src.features.composer.role_binding import role_binding_problem
from src.features.composer.role_binding_constants import (
    ROLE_BINDING_CLAIM_UNCOVERED, ROLE_BINDING_MISSING,
    ROLE_BINDING_NOT_OWN_CITE, ROLE_BINDING_QUOTE_NOT_IN_SOURCE,
)
from src.features.composer.verify import _apply_grounding


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "third_settlement_flow.json").read_text(encoding="utf-8"))


def _cells():
    return tuple(FIXTURE["rows"]["51"]["칸"])


def _entries():
    return deepcopy(FIXTURE["review_entries"][0]["검증근거"])


def _guard(*, cells=None, source=None, entries=None):
    selected = cells if cells is not None else _cells()
    return role_binding_problem(
        " → ".join(selected),
        {"39": source if source is not None else FIXTURE["sources"]["39"]},
        _entries() if entries is None else entries, cells=selected,
    )


def test_actual_saved_review_and_rows_keep_settlement_and_remove_policy_only():
    """실제 #51의 동일 참 판정은 보존하되 #52의 참이 도식 적합성을 면제하지 않는다."""
    before = deepcopy(FIXTURE)
    cells = {int(n): tuple(row["칸"]) for n, row in FIXTURE["rows"].items()}
    candidates = {
        int(n): (" → ".join(row["칸"]), {fid: FIXTURE["sources"][fid] for fid in row["인용"]})
        for n, row in FIXTURE["rows"].items()
    }
    problems = {}
    verdicts = _apply_grounding(
        json.dumps({"판정": FIXTURE["review_entries"]}, ensure_ascii=False),
        {item["번호"]: item["결과"] for item in FIXTURE["review_entries"]},
        candidates,
        diagnostic_contexts={n: ("business_model", "도식", "확인") for n in cells},
        flow_cells_by_number=cells, grounding_problems=problems,
    )
    assert verdicts == {51: "참", 52: "참"}
    assert problems == {}
    rows = tuple(FlowRow(cells[n], tuple(FIXTURE["rows"][str(n)]["인용"])) for n in cells)
    report = ComposedReport((ComposedSection("business_model", (), flow_rows=rows),))
    fragments = tuple(CollectedFragment(fid, "사업내용", text) for fid, text in FIXTURE["sources"].items())
    checked, diagnostics = check_diagram_numbers(report, fragments)
    assert checked.sections[0].flow_rows == (rows[0],)
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in item for item in diagnostics)
    assert FIXTURE == before
    assert rows[0].review_binding is None  # 무료 소비 검사는 공개용 검수 봉인을 만들지 않는다.


def test_actual_source_77_and_same_current_settlement_are_equivalent():
    assert _guard(source=FIXTURE["sources"]["77"]) == ""


@pytest.mark.parametrize("old,new", [
    ("회사가 수수료·부가가치세 차감 후 가맹점에 지급", "가맹점이 수수료·부가가치세 차감 후 회사에 지급"),
    ("가맹점에 지급", "다른 수취인에게 지급"),
    ("수수료·부가가치세", "배송수수료·부가가치세"),
    ("수수료·부가가치세", "수수료"),
    ("차감 후", "회사 수익으로 인식 후"),
    ("회사가 수수료", "회사가 계약을 체결하는 경우에만 수수료"),
    ("회사가 수수료", "자회사가 수수료"),
    ("가맹점에 지급", "가맹점에 지급하지 않음"),
    ("거래승인 시점에 수익 인식", "부가가치세를 회사 수익으로 인식"),
])
def test_changed_direction_recipient_fees_tax_or_condition_is_not_proven(old, new):
    cells = list(_cells())
    assert old in cells[2]
    cells[2] = cells[2].replace(old, new)
    assert _guard(cells=cells)


def test_tax_revenue_claim_in_another_cell_is_not_proven_by_deduction():
    cells = (*_cells()[:-1], "부가가치세는 회사 수익")
    assert _guard(cells=cells)


@pytest.mark.parametrize("old,new", [
    ("회사는 전자지불", "자회사는 전자지불"),
    ("회사는 전자지불", "한빛회사는 전자지불"),
    ("회사는 전자지불", "회사는 계약을 체결한 경우에만 전자지불"),
    ("최종소비자가 결제한 거래금액에 대해 ", ""),
    ("원천사로부터 대금을 받은 후", "회사가 원천사에 대금을 준 후"),
    ("회사의 가맹점으로", "회사의 다른 수취인에게"),
    ("수수료와 부가가치세", "배송수수료와 부가가치세"),
    ("지급하고 있습니다", "지급하고 있지 않습니다"),
])
def test_changed_source_relationship_with_exact_changed_quote_is_rejected(old, new):
    source = FIXTURE["sources"]["39"].replace(old, new)
    entries = _entries()
    entries["관계"][0]["원문"] = entries["관계"][0]["원문"].replace(old, new)
    assert _guard(source=source, entries=entries)


def test_hypothetical_source_context_does_not_prove_current_settlement():
    assert _guard(source="교육을 위한 가상의 회사 사례입니다. " + FIXTURE["sources"]["77"])


def test_review_binding_still_requires_own_exact_quote_and_submission():
    assert _guard(entries={}) == ROLE_BINDING_MISSING
    entries = _entries()
    entries["관계"][0]["근거"] = "77"
    assert _guard(entries=entries) == ROLE_BINDING_NOT_OWN_CITE
    entries = _entries()
    entries["관계"][0]["원문"] = "회사의 가맹점에 수수료 차감 후 지급합니다"
    assert _guard(entries=entries) == ROLE_BINDING_QUOTE_NOT_IN_SOURCE


def test_settlement_cannot_cover_another_fee_or_prose_binding():
    cells = (*_cells()[:-1], "로열티 수취")
    assert _guard(cells=cells) == ROLE_BINDING_CLAIM_UNCOVERED
    assert role_binding_problem(
        "가맹점은 수수료를 수취합니다.",
        {"39": FIXTURE["sources"]["39"]}, _entries(),
    )
