# -*- coding: utf-8 -*-
"""축약된 culture 행이 «자기 인용»의 순수 회계 측정 정책을 옮겨 적은 경우만 막는다.

실제 보고서에서 살아남은 세 칸 「신용위험 관리 / 전체기간 기대신용손실 간편법 적용 /
(빈칸)」이 출발점이다. 그 칸에는 「신용위험 특성」·「연체일」이 없어 산문 검사의 세
표지 결합이 성립하지 않았고, 문화 회계 검사는 산문에만 연결돼 있었다.

각 시험은 «막아야 하는 것»과 «살려야 하는 것»을 짝으로 둔다 — 한쪽만 있으면
가드가 전부 막아도 녹색이 되기 때문이다.

★ 「재무」라는 낱말을 이유로 모두 막지 않는다. 이사회 감독·담당부서 검토·승인·
  유동성 모니터링처럼 실제 절차를 적은 행은 그대로 남아야 한다.
"""

import json

import pytest

from src.features.composer.culture_constants import (
    CULTURE_ACCOUNTING_POLICY_MISPLACED,
    CULTURE_SECTION_EVIDENCE_OFFCONTRACT,
)
from src.features.composer.culture_guard import (
    culture_accounting_flow_problem,
    culture_accounting_policy_problem,
)
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    FlowRow,
)
from src.features.composer.verify import verify_report

CULTURE_SECTION_ID = "culture"

#: 실제 보고서에 남았던 세 칸과 그 행이 인용한 원문. 값은 실측 그대로다.
REAL_ECL_CELLS = ("신용위험 관리", "전체기간 기대신용손실 간편법 적용", "")
REAL_ECL_SOURCE = (
    "연결회사는 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
    "인식하는 간편법을 적용하며, 기대신용손실을 측정하기 위하여 매출채권 및 계약자산은 "
    "신용위험 특성과 연체일을 기준으로 구분하였습니다."
)

#: 같은 절 안에서 회계처리와 결속된 «실제» 검토·승인 절차. 이것은 살려야 한다.
BOUND_PROCEDURE_SOURCE = (
    "연결회사는 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
    "인식하는 간편법을 적용하며 신용위험 특성과 연체일을 기준으로 산출한 결과를 "
    "재무 담당부서가 검토하고 이사회가 승인합니다."
)
#: 절차가 «다른 절»에 있는 원문. 면제 근거가 되어서는 안 된다.
PROCEDURE_IN_ANOTHER_CLAUSE_SOURCE = (
    "연결회사는 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
    "인식하는 간편법을 적용하며 신용위험 특성과 연체일을 기준으로 구분하였습니다. "
    "위험관리 절차는 이사회가 감독합니다."
)
#: 부정된 승인. 실제로 수행된 절차가 아니므로 면제가 아니다.
NEGATED_APPROVAL_SOURCE = (
    "연결회사는 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
    "인식하는 간편법을 적용하며 신용위험 특성과 연체일을 기준으로 구분한 결과는 "
    "이사회 승인을 받지 않습니다."
)
OVERSIGHT_SOURCE = (
    "연결회사는 이사회를 중심으로 위험관리 체계를 구축하고 감독 책임을 이사회에 "
    "두고 있습니다."
)
LIQUIDITY_SOURCE = (
    "연결회사는 영업활동 현금흐름을 정기적으로 모니터링하고 자본관리 목표에 따라 "
    "부채비율을 관리하고 있습니다."
)


def cited(source_text, source_id="s1"):
    return {source_id: source_text}


# ── 막아야 하는 것 ───────────────────────────────────────────────────
def test_the_compact_ecl_row_is_rejected_against_its_own_citation():
    """실제 반례 — 칸이 줄어 있어도 그 행의 원문이 순수 회계 측정이면 막는다."""

    assert culture_accounting_flow_problem(
        REAL_ECL_CELLS, cited(REAL_ECL_SOURCE)
    ) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_review_wording_added_only_by_the_candidate_does_not_exempt():
    """원문에 없는 검토·승인을 칸에 덧붙였다고 면제되지 않는다."""

    cells = ("신용위험 관리", "전체기간 기대신용손실 간편법 적용을 이사회가 승인", "")
    assert culture_accounting_flow_problem(
        cells, cited(REAL_ECL_SOURCE)
    ) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_a_procedure_in_another_source_clause_does_not_exempt():
    """다른 절의 감독 문장으로는 그 회계 절이 «순수 회계»임을 뒤집지 못한다."""

    assert culture_accounting_flow_problem(
        REAL_ECL_CELLS, cited(PROCEDURE_IN_ANOTHER_CLAUSE_SOURCE)
    ) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_a_negated_approval_does_not_exempt():
    """부정된 승인은 실제로 수행된 절차가 아니므로 면제가 아니다."""

    assert culture_accounting_flow_problem(
        REAL_ECL_CELLS, cited(NEGATED_APPROVAL_SOURCE)
    ) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_the_verdict_does_not_depend_on_any_entity_name():
    """회사·법인 이름을 바꿔도 판정이 같다 — 이름에 기대지 않는다는 증거다."""

    renamed_source = REAL_ECL_SOURCE.replace("연결회사", "당사")
    assert culture_accounting_flow_problem(
        REAL_ECL_CELLS, cited(renamed_source)
    ) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_the_source_id_is_not_part_of_the_rule():
    """조각 id 가 무엇이든 같은 판정이어야 한다."""

    for source_id in ("44", "s-any", "zzz"):
        assert culture_accounting_flow_problem(
            REAL_ECL_CELLS, cited(REAL_ECL_SOURCE, source_id)
        ) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ── 살려야 하는 것 ───────────────────────────────────────────────────
def test_a_procedure_bound_in_the_same_source_clause_is_kept():
    """같은 절에서 회계처리와 결속된 실제 검토·승인 절차는 보존한다."""

    cells = ("신용위험 관리", "손실충당금 산출 결과를 재무 담당부서가 검토하고 이사회가 승인", "")
    assert culture_accounting_flow_problem(cells, cited(BOUND_PROCEDURE_SOURCE)) == ""


@pytest.mark.parametrize(
    ("cells", "source_text"),
    [
        (("위험관리 책임의 명확화", "이사회 중심의 위험관리 체계 구축 및 감독", ""), OVERSIGHT_SOURCE),
        (("재무 건전성 유지", "영업활동 현금흐름 모니터링 및 부채비율 관리", ""), LIQUIDITY_SOURCE),
    ],
    ids=("board-oversight", "liquidity-monitoring"),
)
def test_real_procedure_rows_are_kept(cells, source_text):
    """「재무」라는 낱말이 있다는 이유로 실제 절차 행을 막지 않는다."""

    assert culture_accounting_flow_problem(cells, cited(source_text)) == ""


def test_a_row_whose_recognition_wording_is_absent_from_the_pure_clause_is_kept():
    """칸이 쓴 인식 표현이 그 절에 없으면 결속으로 보지 않는다."""

    other_measurement_source = (
        "연결회사는 매출채권에 대하여 손실충당금을 인식하며 신용위험 특성과 연체일을 "
        "기준으로 구분하였습니다."
    )
    cells = ("신용위험 관리", "전체기간 기대신용손실 간편법 적용", "")
    assert culture_accounting_flow_problem(cells, cited(other_measurement_source)) == ""


def test_a_row_shape_other_than_three_cells_is_left_to_the_diagram_validator():
    for cells in (("신용위험 관리", "전체기간 기대신용손실 간편법 적용"), "신용위험 관리"):
        assert culture_accounting_flow_problem(cells, cited(REAL_ECL_SOURCE)) == ""


def test_the_prose_guard_keeps_its_existing_contract():
    """산문 검사의 기존 강한 조건은 그대로다."""

    assert culture_accounting_policy_problem(
        REAL_ECL_SOURCE) == CULTURE_ACCOUNTING_POLICY_MISPLACED
    assert culture_accounting_policy_problem(BOUND_PROCEDURE_SOURCE) == ""
    assert culture_accounting_policy_problem(OVERSIGHT_SOURCE) == ""
    assert culture_accounting_policy_problem(LIQUIDITY_SOURCE) == ""


# ── 실제 진입점 — 평면 검수와 묶음 검수 ─────────────────────────────
def _run(section_id, cells, source_text, grouped, *, diagnostics=None):
    row = FlowRow(tuple(cells), ("s1",))
    draft = ComposedReport((ComposedSection(section_id, (), flow_rows=(row,)),))
    fragments = (CollectedFragment("s1", "공시", source_text),)
    answer = json.dumps(
        {"판정": [{"번호": 1, "결과": "참", "장": section_id, "근거": ["s1"]}]},
        ensure_ascii=False,
    )
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return answer

    if grouped:
        result = verify_report(
            draft, fragments, None, ask,
            allowed_fragment_ids_by_section={section_id: frozenset({"s1"})},
            diagnostics=diagnostics,
        )
    else:
        result, _dropped = check_diagrams(
            draft, fragments, ask, diagnostics=diagnostics
        )
    return result.sections[0].flow_rows, row, calls


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_the_compact_ecl_row_is_dropped_at_both_entry_points(grouped):
    rows, _row, calls = _run(
        CULTURE_SECTION_ID, REAL_ECL_CELLS, REAL_ECL_SOURCE, grouped)
    assert rows == ()
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize(
    ("cells", "source_text"),
    [
        (("위험관리 책임의 명확화", "이사회 중심의 위험관리 체계 구축 및 감독", ""), OVERSIGHT_SOURCE),
        (("신용위험 관리", "손실충당금 산출 결과를 재무 담당부서가 검토하고 이사회가 승인", ""),
         BOUND_PROCEDURE_SOURCE),
    ],
    ids=("board-oversight", "bound-procedure"),
)
def test_procedure_rows_survive_both_entry_points(cells, source_text, grouped):
    """원문이 «누가» 맡는지 말한 절차 행은 두 진입점에서 그대로 남는다."""

    rows, row, calls = _run(CULTURE_SECTION_ID, cells, source_text, grouped)
    assert rows == (row,)
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_a_monitoring_row_without_an_actor_now_leaves_by_the_section_contract(grouped):
    """★ 의도가 바뀐 근거 — 8장은 «누가» 맡는지 말한 재무 문장만 예외로 둔다.

    LIQUIDITY_SOURCE는 「연결회사는 … 모니터링하고 … 관리하고 있습니다」로,
    조직 주체도 사람·조직 제도 소재도 없다. 예전에는 이 행이 8장에 그대로
    실렸는데, 실측 실행에서 8장이 이런 재무 문장으로 통째로 채워지는 일이
    반복돼 «원문 절» 긍정 계약(culture_section_evidence_problem)을 새로 걸었다.
    이 행은 그 계약에 걸려 빠진다.

    ★ 이 시험이 지키는 것 — ① 회계 측정 가드의 계약은 그대로다(아래 첫 단정:
      「재무」라는 낱말만으로 막지 않는다) ② 빠지는 사유는 회계 정책이 아니라
      «장 계약»이다 ③ 다른 장에서는 같은 행이 멀쩡히 남는다(아래 마지막 단정).
    """

    cells = ("재무 건전성 유지", "영업활동 현금흐름 모니터링 및 부채비율 관리", "")
    # ① 회계 측정 가드는 여전히 이 행을 막지 않는다.
    assert culture_accounting_flow_problem(cells, cited(LIQUIDITY_SOURCE)) == ""

    # ② 8장에서는 원문 절 계약에 걸려 빠지고, 사유 코드가 그것임을 단정한다.
    diagnostics: list[dict] = []
    rows, _row, calls = _run(
        CULTURE_SECTION_ID, cells, LIQUIDITY_SOURCE, grouped,
        diagnostics=diagnostics,
    )
    assert rows == ()
    assert len(calls) == 1
    assert [event["reason_code"] for event in diagnostics] == [
        CULTURE_SECTION_EVIDENCE_OFFCONTRACT
    ]

    # ③ 같은 행이 다른 장에 있으면 이 계약의 대상이 아니다.
    other_rows, other_row, _calls = _run(
        "operations_partners", cells, LIQUIDITY_SOURCE, grouped
    )
    assert other_rows == (other_row,)


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize(
    "section_id", ("business_model", "operations_partners"), ids=("chapter2", "chapter7"),
)
def test_the_same_row_in_another_chapter_is_untouched(section_id, grouped):
    """다른 장의 정상 회계 설명은 이 배치 제한을 지나가지 않는다."""

    rows, row, calls = _run(section_id, REAL_ECL_CELLS, REAL_ECL_SOURCE, grouped)
    assert rows == (row,)
    assert len(calls) == 1
