"""같은 대상·역할이 여러 자리에 반복될 때의 결속을 실제 진입점까지 검증한다.

한 근거가 자리마다 주체·부정·조건을 따로 확인해 여러 자리를 덮을 수 있고, 한 자리가
어긋나도 다른 자리의 유효한 증명이 지워지지 않는다. 다른 대상·다른 주체·조건 누락과
「수수료 근거로 로열티」는 그대로 거절한다.
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
from src.features.composer.role_binding_constants import (
    RELATION_FEE,
    ROLE_BINDING_CLAIM_UNCOVERED,
    ROLE_BINDING_CONDITION_DROPPED,
    RELATION_QUOTE_KEY,
    RELATION_ROLE,
    RELATION_ROLE_KEY,
    RELATION_SOURCE_KEY,
    RELATION_TARGET_KEY,
    RELATION_TYPE_KEY,
)
from src.features.composer.verify import verify_report

SUMMARY_AND_SOURCE = (
    "신제품 자체 개발 계획",
    "당사는 신제품을 자체 개발할 계획입니다.",
)
PRODUCT_SOURCE = "당사는 신제품을 자체 개발할 계획입니다."


def proof(target, role, source_id, quote, kind=RELATION_ROLE):
    return {
        RELATION_TYPE_KEY: kind,
        RELATION_TARGET_KEY: target,
        RELATION_ROLE_KEY: role,
        RELATION_SOURCE_KEY: source_id,
        RELATION_QUOTE_KEY: quote,
    }


def evidence(*items):
    return {RELATION_KEY: list(items)}


# ① 요약 칸 + 원문 칸 반복 — 근거 하나로 덮인다
def test_summary_cell_and_quoted_source_cell_share_one_proof():
    assert role_binding_problem(
        " ".join(SUMMARY_AND_SOURCE),
        {"s1": PRODUCT_SOURCE},
        evidence(proof("신제품", "개발", "s1", PRODUCT_SOURCE)),
        SUMMARY_AND_SOURCE,
    ) == ""


def test_repeating_the_same_proof_twice_is_still_accepted():
    same = proof("신제품", "개발", "s1", PRODUCT_SOURCE)
    assert role_binding_problem(
        " ".join(SUMMARY_AND_SOURCE),
        {"s1": PRODUCT_SOURCE},
        evidence(same, dict(same)),
        SUMMARY_AND_SOURCE,
    ) == ""


# ② 덮이면 안 되는 자리는 그대로 거절
def test_a_different_target_is_not_covered():
    source = "가람은 음반을 제작합니다."
    assert role_binding_problem(
        "음반 제작 영상 제작", {"s1": source},
        evidence(proof("음반", "제작", "s1", source)),
        ("음반 제작", "영상 제작"),
    ) != ""


def test_a_different_actor_is_not_covered():
    source = "가람은 음반을 제작합니다."
    assert role_binding_problem(
        "가람 제작 나래 제작", {"s1": source},
        evidence(proof("가람", "제작", "s1", source)),
        ("가람 제작", "나래 제작"),
    ) != ""


def test_a_fee_proof_does_not_cover_a_royalty_claim():
    source = "가람은 서비스 이용 대가로 수수료를 청구합니다."
    binding = evidence(proof("가람", "수수료", "s1", source, RELATION_FEE))
    assert role_binding_problem(
        "가람 수수료 청구", {"s1": source}, binding, ("가람 수수료 청구",),
    ) == ""
    assert role_binding_problem(
        "가람 수수료 청구 가람 로열티 청구", {"s1": source}, binding,
        ("가람 수수료 청구", "가람 로열티 청구"),
    ) == ROLE_BINDING_CLAIM_UNCOVERED


# ③ 조건 누락은 «양쪽 순서» 모두에서 거절
CONDITION_SOURCE = "가람은 계약을 체결한 경우에만 신제품을 개발합니다."


@pytest.mark.parametrize(
    "cells",
    [
        ("계약을 체결한 경우에만 신제품 개발", "신제품 개발"),
        ("신제품 개발", "계약을 체결한 경우에만 신제품 개발"),
    ],
    ids=("condition-first", "condition-second"),
)
def test_a_cell_that_drops_the_condition_keeps_its_own_reason(cells):
    """거절만이 아니라 «왜»도 지킨다 — 조건을 뺀 자리는 그 사유로 남아야 한다.

    자리별 검증으로 바꾸면서 이 사유가 「덮이지 않았다」로 뭉뚱그려질 수 있어
    사유 코드까지 못 박는다.
    """

    assert role_binding_problem(
        " ".join(cells), {"s1": CONDITION_SOURCE},
        evidence(proof("신제품", "개발", "s1", CONDITION_SOURCE)),
        cells,
    ) == ROLE_BINDING_CONDITION_DROPPED


#: 조건 «표지»가 서로 다른 두 자리. 표지가 같으면 근거 하나로도 통과해 버려
#: 이 시험이 아무것도 지키지 못한다 — 그래서 「경우에만」과 「전제로」로 갈랐다.
CONSIGNMENT_SOURCE = "가람은 위탁계약을 체결한 경우에만 신제품을 개발합니다."
APPROVAL_SOURCE = "가람은 정부 승인을 전제로 신제품을 개발합니다."
TWO_CONDITION_CELLS = (
    "위탁계약을 체결한 경우에만 신제품 개발",
    "정부 승인을 전제로 신제품 개발",
)


def test_two_positions_each_proven_by_their_own_citation_are_kept():
    """조건이 서로 다른 두 자리를 각자의 자기 인용으로 증명하면 보존한다.

    한 자리의 조건 불일치가 다른 자리의 유효한 증명을 지우면 안 된다.
    """

    assert role_binding_problem(
        " ".join(TWO_CONDITION_CELLS),
        {"consign": CONSIGNMENT_SOURCE, "approve": APPROVAL_SOURCE},
        evidence(
            proof("신제품", "개발", "consign", CONSIGNMENT_SOURCE),
            proof("신제품", "개발", "approve", APPROVAL_SOURCE),
        ),
        TWO_CONDITION_CELLS,
    ) == ""


def test_one_citation_cannot_cover_a_position_with_another_condition():
    """반대쪽 — 근거 하나로 다른 조건의 자리까지 덮지는 못한다."""

    assert role_binding_problem(
        " ".join(TWO_CONDITION_CELLS),
        {"consign": CONSIGNMENT_SOURCE, "approve": APPROVAL_SOURCE},
        evidence(proof("신제품", "개발", "consign", CONSIGNMENT_SOURCE)),
        TWO_CONDITION_CELLS,
    ) == ROLE_BINDING_CONDITION_DROPPED


# ④ 직결 자리 + 칸-경계 자리 혼합
def test_direct_and_local_prefix_positions_are_collected_together():
    """대상이 함께 있는 칸(직결)과 역할만 있는 칸(칸-경계)이 섞여도 한 근거로 덮인다."""

    source = "가람은 신제품을 개발합니다."
    cells = ("신제품 개발", "개발")
    assert role_binding_problem(
        " ".join(cells), {"s1": source},
        evidence(proof("신제품", "개발", "s1", source)),
        cells,
    ) == ""


def test_a_local_prefix_position_with_another_actor_is_still_rejected():
    source = "가람은 신제품을 개발합니다."
    cells = ("신제품 개발", "나래가 개발")
    assert role_binding_problem(
        " ".join(cells), {"s1": source},
        evidence(proof("신제품", "개발", "s1", source)),
        cells,
    ) != ""


# ⑤ 실제 진입점 — legacy 도식과 packet 묶음 검수
ROW = FlowRow(SUMMARY_AND_SOURCE, ("s1",))
FRAGMENTS = (CollectedFragment("s1", "공시", PRODUCT_SOURCE),)


def _response(evidence_payload, section_id="operations_partners"):
    record = {"번호": 1, "결과": "참", "장": section_id, "근거": ["s1"]}
    if evidence_payload is not None:
        record["검증근거"] = evidence_payload
    return json.dumps({"판정": [record]}, ensure_ascii=False)


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_repeated_role_row_survives_both_entry_points(grouped):
    draft = ComposedReport((ComposedSection("operations_partners", (), flow_rows=(ROW,)),))
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return _response(evidence(proof("신제품", "개발", "s1", PRODUCT_SOURCE)))

    if grouped:
        result = verify_report(
            draft, FRAGMENTS, None, ask,
            allowed_fragment_ids_by_section={"operations_partners": frozenset({"s1"})},
        )
    else:
        result, _dropped = check_diagrams(draft, FRAGMENTS, ask)
    assert result.sections[0].flow_rows == (ROW,)
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_repeated_role_row_without_evidence_still_drops(grouped):
    draft = ComposedReport((ComposedSection("operations_partners", (), flow_rows=(ROW,)),))
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return _response(None)

    if grouped:
        result = verify_report(
            draft, FRAGMENTS, None, ask,
            allowed_fragment_ids_by_section={"operations_partners": frozenset({"s1"})},
        )
    else:
        result, _dropped = check_diagrams(draft, FRAGMENTS, ask)
    assert result.sections[0].flow_rows == ()
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_repeated_future_role_row_survives_both_guards(grouped):
    row = FlowRow((SUMMARY_AND_SOURCE[0], "", PRODUCT_SOURCE), ("s1",))
    draft = ComposedReport((ComposedSection(STRATEGY_TABLE_SECTION_ID, (), flow_rows=(row,)),))
    payload = evidence(proof("신제품", "개발", "s1", PRODUCT_SOURCE))
    payload[FUTURE_KEY] = [{
        "근거": "s1", "대상": "신제품", "활동": "자체 개발",
        "원문": PRODUCT_SOURCE, "양태": "계획",
    }]
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return _response(payload, STRATEGY_TABLE_SECTION_ID)

    if grouped:
        result = verify_report(
            draft, FRAGMENTS, None, ask,
            allowed_fragment_ids_by_section={STRATEGY_TABLE_SECTION_ID: frozenset({"s1"})},
        )
    else:
        result, _dropped = check_diagrams(draft, FRAGMENTS, ask)
    assert result.sections[0].flow_rows == (row,)
    assert len(calls) == 1
