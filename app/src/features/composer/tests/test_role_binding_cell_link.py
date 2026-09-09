# -*- coding: utf-8 -*-
"""칸-경계 fallback 은 «대상을 적은 칸과 이어진» 자리까지만 덮는다.

행이 칸을 묶는다는 이유로 칸을 넘는 결속을 허용하지만, 사이에 다른 내용을 담은 칸이
끼면 그 칸이 자기 주체를 데려온 것이라 대상을 건너뛰어 빌려올 수 없다.

각 시험은 «막아야 하는 것»과 «살려야 하는 것»을 짝으로 둔다 — 한쪽만 있으면
가드가 전부 막아도 녹색이 되기 때문이다.

★ 회사 이름을 아는 검사가 아니다. 끼어든 칸이 이름이든 시점이든 똑같이 끊긴다.
"""

import json

import pytest

from src.features.composer.constants import STRATEGY_TABLE_SECTION_ID
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.direct_support_constants import RELATION_KEY
from src.features.composer.future_plan_constants import FUTURE_KEY
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    FlowRow,
)
from src.features.composer.role_binding import role_binding_problem
from src.features.composer.verify import verify_report

DEV_SOURCE = "가람은 신제품을 개발합니다."
PLAN_SOURCE = "당사는 신제품을 자체 개발할 계획입니다."
OPERATIONS_SECTION_ID = "operations_partners"


def proof(target, role, quote, source_id="s1"):
    return {"유형": "역할", "대상": target, "역할값": role,
            "근거": source_id, "원문": quote}


def guard(cells, target, role, source, quote=None):
    return role_binding_problem(
        " ".join(cells), {"s1": source},
        {RELATION_KEY: [proof(target, role, quote or source)]}, cells,
    )


# ── 막아야 하는 것 ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("cells", "target", "role", "source"),
    [
        (("가람이 신제품을 개발", "나래", "개발 위탁"), "가람", "개발", DEV_SOURCE),
        (("가람", "제작", "나래", "제작"), "가람", "제작", "가람은 음반을 제작합니다."),
    ],
    ids=("three-cells", "four-cells"),
)
def test_a_role_cell_behind_another_cell_is_not_covered(cells, target, role, source):
    """다른 내용을 담은 칸을 건너뛴 역할 칸은 그 근거로 덮이지 않는다."""

    assert guard(cells, target, role, source) != ""


@pytest.mark.parametrize(
    ("blocking_cell", "reason"),
    [("나래", "회사 이름처럼 보이는 칸"), ("2026년 2분기", "시점 칸"), ("국내", "범위 칸")],
    ids=("name-like", "period-like", "scope-like"),
)
def test_any_unrelated_cell_breaks_the_link_not_only_a_company_name(blocking_cell, reason):
    """끼어든 칸이 무엇이든 똑같이 끊긴다 — 이름 목록에 기대지 않는다는 증거다."""

    cells = ("가람이 신제품을 개발", blocking_cell, "개발 위탁")
    assert guard(cells, "가람", "개발", DEV_SOURCE) != "", reason


def test_renaming_the_actors_does_not_change_the_verdict():
    """같은 모양에 이름만 바꿔도 결과가 같아야 한다."""

    assert guard(("바다가 신제품을 개발", "하늘", "개발 위탁"),
                 "바다", "개발", "바다는 신제품을 개발합니다.") != ""


def test_another_actor_inside_the_same_cell_is_still_rejected():
    """예전부터 막던 «같은 칸 안 다른 주체»는 그대로 막힌다."""

    assert guard(("가람이 신제품을 개발", "나래 개발"), "가람", "개발", DEV_SOURCE) != ""


# ── 살려야 하는 것 ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    "cells",
    [
        ("신제품 개발", "개발"),
        ("개발", "신제품 개발"),
        ("신제품", "개발"),
    ],
    ids=("direct-then-role", "role-then-direct", "target-cell-then-role-cell"),
)
def test_an_adjacent_role_cell_is_still_covered_by_one_proof(cells):
    """대상 칸 바로 옆의 역할 칸은 근거 하나로 덮인다 — fallback 자체를 죽이지 않는다."""

    assert guard(cells, "신제품", "개발", DEV_SOURCE) == ""


@pytest.mark.parametrize(
    "cells",
    [
        ("신제품 개발", "신제품 개발", "신제품 개발"),
        ("신제품 개발", "개발", "개발"),
    ],
    ids=("all-direct", "direct-then-two-role-cells"),
)
def test_a_pure_repetition_of_the_same_target_is_kept(cells):
    """같은 대상·역할만 되풀이한 칸은 이어진 것으로 보고 덮는다."""

    assert guard(cells, "신제품", "개발", DEV_SOURCE) == ""


@pytest.mark.parametrize(
    "cells",
    [
        ("신제품 개발", "", "개발"),
        ("신제품 개발", "", "", "개발"),
    ],
    ids=("one-blank", "two-blanks"),
)
def test_blank_cells_between_do_not_break_the_link(cells):
    """빈 칸은 내용이 아니므로 잇는 것을 끊지 않는다 — 시점 칸을 비워 두는 표가 있다."""

    assert guard(cells, "신제품", "개발", DEV_SOURCE) == ""


def test_a_summary_cell_and_the_quoted_source_cell_are_kept():
    """요약 칸 + 원문 칸 직결 반복은 그대로 통과한다."""

    cells = ("신제품 자체 개발 계획", PLAN_SOURCE)
    assert guard(cells, "신제품", "개발", PLAN_SOURCE) == ""


def test_a_blank_period_cell_between_repeated_source_cells_is_kept():
    """미래표처럼 시점 칸이 비어 있는 원문 반복도 통과한다."""

    cells = ("신제품 자체 개발 계획", "", PLAN_SOURCE)
    assert guard(cells, "신제품", "개발", PLAN_SOURCE) == ""


# ── 실제 진입점 — 평면 검수와 묶음 검수 ─────────────────────────────
FRAGMENTS = (CollectedFragment("s1", "공시", DEV_SOURCE),)


def _response(section_id, payload):
    record = {"번호": 1, "결과": "참", "장": section_id, "근거": ["s1"],
              "검증근거": payload}
    return json.dumps({"판정": [record]}, ensure_ascii=False)


def _run(section_id, cells, fragments, payload, grouped):
    row = FlowRow(tuple(cells), ("s1",))
    draft = ComposedReport((ComposedSection(section_id, (), flow_rows=(row,)),))
    answer = _response(section_id, payload)
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return answer

    if grouped:
        result = verify_report(
            draft, fragments, None, ask,
            allowed_fragment_ids_by_section={section_id: frozenset({"s1"})},
        )
    else:
        result, _dropped = check_diagrams(draft, fragments, ask)
    return result.sections[0].flow_rows, row, calls


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_the_borrowed_role_row_is_dropped_at_both_entry_points(grouped):
    """운영 흐름표 검수에서 그 행이 실제로 지워진다."""

    payload = {RELATION_KEY: [proof("가람", "개발", DEV_SOURCE)]}
    rows, _row, calls = _run(
        OPERATIONS_SECTION_ID, ("가람이 신제품을 개발", "나래", "개발 위탁"),
        FRAGMENTS, payload, grouped)
    assert rows == ()
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize(
    "cells",
    [("신제품 개발", "개발"), ("신제품 개발", "개발", "개발")],
    ids=("two-cells", "three-cells"),
)
def test_a_linked_role_row_survives_both_entry_points(cells, grouped):
    """대조군 — 이어진 행은 같은 진입점에서 그대로 남는다."""

    payload = {RELATION_KEY: [proof("신제품", "개발", DEV_SOURCE)]}
    rows, row, calls = _run(
        OPERATIONS_SECTION_ID, cells, FRAGMENTS, payload, grouped)
    assert rows == (row,)
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_the_future_table_row_with_a_blank_period_cell_survives(grouped):
    """미래표는 역할 가드와 미래계획 가드가 함께 걸리는 자리라 따로 태운다."""

    payload = {
        RELATION_KEY: [proof("신제품", "개발", PLAN_SOURCE)],
        FUTURE_KEY: [{"근거": "s1", "대상": "신제품", "활동": "자체 개발",
                      "원문": PLAN_SOURCE, "양태": "계획"}],
    }
    fragments = (CollectedFragment("s1", "공시", PLAN_SOURCE),)
    rows, row, calls = _run(
        STRATEGY_TABLE_SECTION_ID, ("신제품 자체 개발 계획", "", PLAN_SOURCE),
        fragments, payload, grouped)
    assert rows == (row,)
    assert len(calls) == 1
