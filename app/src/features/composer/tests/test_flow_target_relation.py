"""원문에 나란히 있는 대상들을 같은 공급·후원 관계로 합치지 않는다."""
import json

import pytest

from src.features.composer.flow_target_relation import (
    flow_target_relation_hint, flow_target_relation_pairs, flow_target_relation_problem,
)
from src.features.composer.port import FlowRow
from src.features.composer.diagram_check import _review_rows
from src.features.composer.verify import _apply_grounding

SECTION = "operations_partners"


def problem(cells, source, entries=None):
    return flow_target_relation_problem(cells, {"a": source}, entries, section_id=SECTION)


def proof(target, action, quote, source_id="a"):
    return {"유형": "경로", "대상": target, "역할값": action, "원문": quote, "근거": source_id}


@pytest.mark.parametrize("source", [
    "회사는 대회에 제품을 공급하고 있으며, 'ALPHA'와 파트너십을 확대한다.",
    "회사는 대회에 제품을 공급한다. 'ALPHA'와 파트너십을 확대한다.",
    "회사는 대회에 제품을 공급하며 'ALPHA'를 후원한다.",
])
def test_distinct_relations_do_not_share_target(source):
    cells = ("제품", "공급", "대회 및 ALPHA")
    assert problem(cells, source) == "scope_condition_unbound"
    assert problem(cells, source, {"관계": [proof("ALPHA", "공급", source)]}) == "scope_condition_unbound"


@pytest.mark.parametrize("source", [
    "회사는 'ALPHA'와 'BETA'에 제품을 공급하고 후원한다.",
    "회사는 'ALPHA'에 제품을 공급한다. 회사는 'ALPHA'를 후원한다.",
])
def test_same_target_can_have_multiple_supported_actions(source):
    targets = "ALPHA 및 BETA" if "BETA" in source else "ALPHA"
    assert not problem(("제품", "공급 및 후원", targets), source)


def test_explicit_proofs_use_own_quote_and_every_target_action():
    source = "회사는 'ALPHA'와 'BETA'에 제품을 공급하고 후원한다."
    cells = ("제품", "공급 및 후원", "ALPHA 및 BETA")
    entries = {"관계": [proof(target, action, source)
                        for target in ("ALPHA", "BETA") for action in ("공급", "후원")]}
    assert not problem(cells, source, entries)
    entries["관계"][0]["근거"] = "b"
    assert problem(cells, source, entries) == "scope_condition_unbound"


@pytest.mark.parametrize("entries", [
    {"관계": [proof("ALPHA", "공급", "회사는 ALPHA에 다른 제품을 공급한다.")]},
    {"관계": [proof("ALPHA", "후원", "회사는 'ALPHA'에 제품을 공급한다.")]},
    {"관계": [proof("ALPHA", "공급", "회사는 'ALPHA'에 제품을 공급한다.", "b")]},
])
def test_present_but_wrong_proof_is_not_repaired(entries):
    assert problem(("제품", "공급", "ALPHA"), "회사는 'ALPHA'에 제품을 공급한다.", entries)


def test_negation_and_source_actor_reversal_are_not_positive_support():
    cells = ("제품", "공급", "ALPHA")
    assert problem(cells, "회사는 'ALPHA'에 제품을 공급하지 않는다.")
    assert problem(cells, "ALPHA는 제품을 공급한다.")
    assert not problem(cells, "ALPHA는 제품을 공급받는다.")


def test_other_sections_and_generic_summary_keep_existing_semantic_review():
    cells = ("제품", "공급", "고객")
    assert not problem(cells, "회사는 고객에게 제품을 공급한다.")
    assert flow_target_relation_pairs(("제품", "공급", "ALPHA"), {"a": "ALPHA 공급"}, section_id="business_model") == ()


def test_hint_and_parser_require_the_same_pairs():
    cells = ("제품", "공급 및 후원", "ALPHA 및 BETA")
    source = "회사는 'ALPHA'에 공급하며 'BETA'와 협력한다."
    pairs = flow_target_relation_pairs(cells, {"a": source}, section_id=SECTION)
    hint = flow_target_relation_hint(cells, {"a": source}, section_id=SECTION)
    assert len(pairs) == 4
    assert all(f"{target} → {action}" in hint for target, action in pairs)
    assert "동일" in hint or "같은" in hint


@pytest.mark.parametrize("recipients", ["ALPHA에 공급 및 BETA와 협력", "ALPHA에 공급, BETA와 협력", "ALPHA에 공급\nBETA와 협력"])
def test_explicit_roles_are_split_without_losing_the_second_target(recipients):
    cells = ("제품", "공급 및 협력", recipients)
    source = "회사는 ALPHA에 제품을 공급한다. 회사는 BETA와 협력한다."
    assert flow_target_relation_pairs(cells, {"a": source}, section_id=SECTION) == (("ALPHA", "공급"), ("BETA", "협력"))
    assert not problem(cells, source)
    wrong = "회사는 ALPHA에 제품을 공급한다. 회사는 ALPHA와 협력한다. BETA에는 부품을 납품한다."
    assert problem(cells, wrong) == "scope_condition_unbound"


@pytest.mark.parametrize("action,recipients,source", [
    ("공급", "ALPHA 및 BETA에 공급", "회사는 ALPHA와 BETA에 제품을 공급한다."),
    ("공급 및 후원", "ALPHA에 공급 및 후원", "회사는 ALPHA에 제품을 공급하고 후원한다."),
    ("공급 및 후원 및 협력", "ALPHA에 공급·후원/협력", "회사는 ALPHA에 제품을 공급하고 후원하고 협력한다."),
    ("공급 및 협력", "ALPHA 및 BETA에 공급 및 GAMMA와 협력", "회사는 ALPHA와 BETA에 제품을 공급한다. 회사는 GAMMA와 협력한다."),
    ("공급", "서비스센터 및 물류센터", "회사는 서비스센터와 물류센터에 제품을 공급한다."),
])
def test_plain_target_lists_and_same_target_actions_keep_existing_support(action, recipients, source):
    assert not problem(("제품", action, recipients), source)


def test_grouped_model_true_is_rejected_without_borrowing_another_clause():
    cells = ("제품", "공급", "ALPHA")
    source = "회사는 대회에 제품을 공급하고 있으며 'ALPHA'와 파트너십을 확대한다."
    raw = json.dumps({"판정": [{"번호": 1, "장": SECTION, "근거": ["a"], "결과": "참", "검증근거": {}}]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: "참"}, {1: (" → ".join(cells), {"a": source})},
                             diagnostic_contexts={1: (SECTION, "도식", " ".join(cells))},
                             flow_cells_by_number={1: cells}, grounding_problems=problems)
    assert result[1] == "근거결속실패"
    assert problems[1] == "scope_condition_unbound"


@pytest.mark.parametrize("recipients,actions,pairs,source,wrong", [
    ("ALPHA에 공급 및 후원 및 BETA와 협력", "공급 및 후원 및 협력",
     (("ALPHA", "공급"), ("ALPHA", "후원"), ("BETA", "협력")),
     "회사는 ALPHA에 제품을 공급하고 후원한다. 회사는 BETA와 협력한다.",
     "회사는 ALPHA에 제품을 공급한다. 회사는 BETA와 협력한다."),
    ("ALPHA에 공급 및 BETA 및 GAMMA와 협력", "공급 및 협력",
     (("ALPHA", "공급"), ("BETA", "협력"), ("GAMMA", "협력")),
     "회사는 ALPHA에 제품을 공급한다. 회사는 BETA 및 GAMMA와 협력한다.",
     "회사는 ALPHA에 제품을 공급한다. 회사는 BETA와 협력한다."),
])
def test_role_boundary_keeps_adjacent_action_and_target_lists(recipients, actions, pairs, source, wrong):
    cells = ("제품", actions, recipients)
    assert flow_target_relation_pairs(cells, {"a": source}, section_id=SECTION) == pairs
    assert not problem(cells, source)
    assert problem(cells, wrong) == "scope_condition_unbound"


def test_legacy_review_uses_same_guard_and_keeps_supported_row():
    source = "회사는 'ALPHA'에 제품을 공급하고 있으며 'BETA'와 파트너십을 확대한다."
    rows = (FlowRow(("제품", "공급", "ALPHA"), ("a",)),
            FlowRow(("제품", "공급", "BETA"), ("a",)))
    raw = json.dumps({"판정": [{"번호": number, "결과": "참", "검증근거": {}} for number in (1, 2)]}, ensure_ascii=False)
    kept, dropped = _review_rows([(SECTION, rows)], {"a": source}, lambda _: raw)
    # 출처 객체가 없는 legacy는 최종 바인딩에서 두 행 모두 제외할 수 있다.
    # 관계 가드가 지원된 첫 행을 추가로 거절하지 않고 둘째 사유를 남기는지 본다.
    assert any("scope_condition_unbound" in item for item in dropped)
    assert not any("1번" in item and "scope_condition_unbound" in item for item in dropped)
