"""직접 인용·인과 단언만 원문과 대조하는 경계. 회사명·날짜에 기대지 않는다.

실제 감사에서 나온 두 결함의 «모양»만 중립 문장으로 재현한다. 실제 문장·원문으로
돌린 기록은 .local-artifacts/resume-20260909-semantic-direct-support-fix 에 있다.
독립 검토가 새로 확인한 우회(방향 낱말만 적은 칸, 단언 여럿에 관계 하나, 완곡 부정,
빈 근거 id)와 그 반대편의 과도 삭제(정상 부정 후보)는 .local-artifacts/
resume-20260909-causal-coverage-patch 에 있다.
"""

import json

from src.features.composer.direct_support import (
    causal_relation_problem, direct_support_problem, superlative_attribution_problem,
)
from src.features.composer.direct_support_constants import (
    CAUSE_DIRECTION_REVERSED, CAUSE_NEGATED_IN_SOURCE, CAUSE_PAIR_MISSING,
    CAUSE_PAIR_NOT_IN_CLAIM, CAUSE_PAIR_NOT_IN_QUOTE, CAUSE_RELATION_MISSING,
    CAUSE_PAIR_DEGENERATE, CAUSE_RELATION_NOT_CAUSAL, CAUSE_RELATION_NOT_IN_SOURCE,
    CAUSE_CLAIM_ROLES_MISMATCH, CAUSE_ROLES_UNPROVEN, RELATION_FIELD_TYPE_INVALID,
    CAUSE_CLAIM_UNCOVERED, CAUSE_HEDGED_IN_SOURCE,
    CAUSE_SLOT_DIRECTION_ONLY, CAUSE_SOURCE_ID_EMPTY,
    DIRECT_SUPPORT_REASON_TEXTS, SUPERLATIVE_WITHOUT_SOURCE,
)
from src.features.composer.direct_support import support_entries_by_number


# ══════════════════════════════════════════════════════════
# ① 회사 표현으로 돌린 서열·최상급
# ══════════════════════════════════════════════════════════

_LAUNCH_SOURCE = {
    "1": "2025년 4월 해당 사업이 부수업무로 인정됨에 따라 회사는 신규 서비스를 오픈하여 사업에 진출하였습니다."
}


def test_superlative_attributed_to_company_without_source_is_rejected() -> None:
    """원문에 없는 «최초»를 회사 표현으로 돌리면 막는다."""

    claim = "회사는 신규 서비스 출시를 '업계 최초' 진출로 표현했다."

    assert superlative_attribution_problem(claim, _LAUNCH_SOURCE) == SUPERLATIVE_WITHOUT_SOURCE


def test_superlative_present_in_the_cited_source_is_kept() -> None:
    """원문이 실제로 «최초»라고 쓴 경우는 그대로 둔다."""

    source = {"1": "회사는 우리나라 최초 민족자본으로 창립된 '옛 상호'를 모태로 한 기업입니다."}
    claim = "회사는 자신을 '우리나라 최초 민족자본으로 창립된 옛 상호를 모태로 한 기업'으로 밝혔다."

    assert superlative_attribution_problem(claim, source) == ""


def test_attribution_without_any_superlative_is_not_examined() -> None:
    """서열 표현이 없는 귀속 문장은 이 검사의 대상이 아니다."""

    source = {"1": "회사는 자체개발 B2B 전용 MP(Market Place) 서비스를 출시하였습니다."}
    claim = "회사는 이를 '자체개발 B2B 전용 마켓플레이스 서비스'로 소개했다."

    assert superlative_attribution_problem(claim, source) == ""


def test_writer_interpretation_without_attribution_verb_is_not_examined() -> None:
    """귀속 서술어가 없는 작성자 해석 문장은 막지 않는다."""

    claim = "이 사업은 업계 최초 사례로 볼 여지가 있다."

    assert superlative_attribution_problem(claim, _LAUNCH_SOURCE) == ""


# ══════════════════════════════════════════════════════════
# ② 인과 단언의 관계 근거
# ══════════════════════════════════════════════════════════

_CONCESSIVE_QUOTE = "비용 부담에도 불구하고 안정적인 수익을 창출하였습니다"
_CAUSAL_QUOTE = "종속회사 실적 개선이 전반적인 성장을 견인하였습니다"
_DENIAL_QUOTE = "비용 부담 증가는 이익 감소의 원인이 아닙니다"
_BOUND_QUOTE = "비용 부담 증가로 인해 이익 감소가 발생하였습니다"
_MIXED_SOURCE = {
    "1": "회사는 " + _CONCESSIVE_QUOTE + ". 한편 " + _CAUSAL_QUOTE + ". " + _BOUND_QUOTE + ".",
}
_DENIAL_SOURCE = {"1": _CONCESSIVE_QUOTE + ". " + _DENIAL_QUOTE + "."}
_CAUSE_CLAIM = "이익 감소는 비용 부담 증가가 주요 배경이었다."
_GROWTH_CLAIM = "전반적인 성장의 주요 배경으로는 종속회사 실적 개선이 지목되었다."
_COST_PAIR = {"원인": "비용 부담 증가", "결과": "이익 감소"}
_GROWTH_PAIR = {"원인": "종속회사 실적 개선", "결과": "전반적인 성장"}


def _item(source_id: str, quote: str, kind: str = "인과", **pair: object) -> dict:
    return {"근거": source_id, "원문": quote, "유형": kind, **pair}


def _relation(source_id: str, quote: str, kind: str = "인과", **pair: object) -> dict:
    return {"관계": [_item(source_id, quote, kind, **pair)]}


def _relations(*items: dict) -> dict:
    return {"관계": list(items)}


def test_causal_claim_without_any_relation_evidence_is_rejected() -> None:
    """인과를 단언하고 관계 근거를 대지 않으면 막는다."""

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, None) == CAUSE_RELATION_MISSING
    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, {}) == CAUSE_RELATION_MISSING


def test_relation_quote_absent_from_the_cited_source_is_rejected() -> None:
    """원문에 없는 구절을 관계 근거로 지어내면 막는다."""

    invented = _relation("1", "비용 부담 증가가 이익 감소의 주요 원인이었습니다", **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, invented) == CAUSE_RELATION_NOT_IN_SOURCE


def test_relation_quote_that_is_concessive_in_the_source_is_rejected() -> None:
    """원문이 «에도 불구하고»로 붙인 구절을 인과라고 내면 막는다."""

    reversed_relation = _relation("1", _CONCESSIVE_QUOTE, **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, reversed_relation) == CAUSE_PAIR_NOT_IN_QUOTE


def test_relation_quote_that_is_causal_in_the_source_is_kept() -> None:
    """원문이 스스로 «견인하였습니다»라고 쓴 인과 설명은 그대로 둔다."""

    entry = _relation("1", _CAUSAL_QUOTE, **_GROWTH_PAIR)

    assert causal_relation_problem(_GROWTH_CLAIM, _MIXED_SOURCE, entry) == ""


def test_relation_quote_from_another_citation_is_rejected() -> None:
    """후보가 인용하지 않은 근거 id로는 관계를 세울 수 없다."""

    borrowed = _relation("9", _CAUSAL_QUOTE, **_GROWTH_PAIR)

    assert causal_relation_problem(_GROWTH_CLAIM, _MIXED_SOURCE, borrowed) == CAUSE_RELATION_NOT_IN_SOURCE


def test_sentence_that_does_not_assert_a_cause_is_not_examined() -> None:
    """양보를 그대로 유지한 문장은 관계 근거를 요구하지 않는다."""

    kept = "회사는 안정적인 수익 창출에도 불구하고 비용 부담 증가에 직면하고 있다."

    assert causal_relation_problem(kept, _MIXED_SOURCE, None) == ""


def test_combined_entry_point_reports_the_first_problem_only() -> None:
    """묶음 진입점은 두 검사 중 먼저 걸린 사유 하나만 돌려준다."""

    assert direct_support_problem(_CAUSE_CLAIM, _MIXED_SOURCE, None) == CAUSE_RELATION_MISSING
    assert direct_support_problem("회사는 이를 '업계 최초'로 밝혔다.", _LAUNCH_SOURCE, None) == (
        SUPERLATIVE_WITHOUT_SOURCE
    )
    assert direct_support_problem("회사는 사업에 진출하였다고 밝혔다.", _LAUNCH_SOURCE, None) == ""


# ══════════════════════════════════════════════════════════
# ③ 과도 판정을 막는 정상 경계 — 실제 감사에서 내가 틀렸던 문장
# ══════════════════════════════════════════════════════════

def test_product_condition_attached_to_one_product_is_not_a_scope_error() -> None:
    """조건 관형절이 «부문»이 아니라 바로 뒤 «상품»을 꾸미는 문장은 정상이다.

    감사에서 이 모양을 범위 오류로 적었다가 철회했다. 조건은 그 상품의 것이고
    부문은 제공 주체일 뿐이라 원문과 어긋나지 않는다. 이 검사도 막지 않는다.
    """

    source = {
        "1": "당행 신용등급 BB+ 등급 이상 법인 및 개인사업자를 대상으로 운전 및 시설자금을 지원하는 범용 대출상품",
        "2": "신성장산업 품목을 생산하는 기업을 대상으로 하는 특화 대출상품",
    }
    claim = ("기업금융 부문은 신용등급 BB+ 이상의 법인 및 개인사업자를 대상으로 운전자금 및 "
             "시설자금을 지원하는 범용 대출상품과 신성장산업 기업을 위한 특화 상품을 제공한다.")

    assert direct_support_problem(claim, source, None) == ""


def test_factual_superlative_without_attribution_stays_with_the_existing_review() -> None:
    """귀속 없이 사실처럼 쓴 서열 표현은 이 검사가 «판정하지 않는다».

    빈 결과는 안전하다는 뜻이 아니다 — 그 문장이 맞는지는 기존 의미 검수가
    계속 판정하며, 이 검사는 «회사의 말로 돌린 경우»만 본다는 한계를 못 박는다.
    """

    source = {"1": "회사는 2025년 4월 해당 서비스를 오픈하여 사업에 진출하였습니다."}

    assert superlative_attribution_problem("이 회사는 업계 최초로 진출했다.", source) == ""


# ══════════════════════════════════════════════════════════
# ④ 관계 결속 — 후보가 말한 원인·결과에 매여야 한다
# ══════════════════════════════════════════════════════════

def test_relation_without_declared_cause_and_effect_is_rejected() -> None:
    """원인·결과 칸을 비우면 «어떤 관계»인지 정해지지 않으므로 막는다."""

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, _relation("1", _BOUND_QUOTE)) == (
        CAUSE_PAIR_MISSING
    )


def test_other_causal_sentence_in_the_same_source_cannot_stand_in() -> None:
    """같은 출처의 «다른» 인과 문장으로는 이 후보의 관계를 세울 수 없다."""

    borrowed = _relation("1", _CAUSAL_QUOTE, **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, borrowed) == CAUSE_PAIR_NOT_IN_QUOTE


def test_declared_pair_absent_from_the_candidate_sentence_is_rejected() -> None:
    """후보 문장이 말하지 않은 원인·결과를 선언하면 막는다."""

    mismatched = _relation("1", _BOUND_QUOTE, **{"원인": "환율 변동", "결과": "이익 감소"})

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, mismatched) == CAUSE_PAIR_NOT_IN_CLAIM


def test_quote_that_carries_only_the_cause_or_only_the_effect_is_rejected() -> None:
    """원인 낱말만·결과 낱말만 담은 구절로는 관계가 성립하지 않는다."""

    only_cause = _relation("1", "비용 부담 증가로 인해", **_COST_PAIR)
    only_effect = _relation("1", "로 인해 이익 감소가 발생하였습니다", **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, only_cause) == CAUSE_PAIR_NOT_IN_QUOTE
    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, only_effect) == CAUSE_PAIR_NOT_IN_QUOTE


def test_reversed_role_assignment_in_the_source_is_rejected() -> None:
    """원문의 문법 표지가 반대 배정을 뒷받침하면 방향이 뒤집힌 것이다."""

    source = {"1": "이익 감소로 인해 비용 부담 증가가 뒤따랐습니다."}
    entry = _relation("1", "이익 감소로 인해 비용 부담 증가가 뒤따랐습니다", **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, source, entry) == CAUSE_DIRECTION_REVERSED


def test_effect_first_korean_sentence_is_a_normal_causal_form() -> None:
    """「B는 A가 주요 원인이다」처럼 결과가 먼저 오는 문장도 정상이다(양성 대조).

    ★ 글자 순서로 방향을 정하던 첫 판은 이런 정상 문장을 틀리게 봤다. 역할 표지로
      판정하도록 바꾼 뒤의 회귀 고정이다.
    """

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    source = {"1": "이익 증가는 비용 감소가 주요 원인이다."}
    entry = _relation("1", "이익 증가는 비용 감소가 주요 원인이다",
                      **{"원인": "비용 감소", "결과": "이익 증가"})

    assert causal_relation_problem(claim, source, entry) == ""


def test_labels_swapped_against_the_candidate_sentence_are_rejected() -> None:
    """후보 문장이 배정한 역할과 반대로 라벨하면 후보 쪽에서 먼저 걸린다."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    source = {"1": "이익 증가는 비용 감소가 주요 원인이다."}
    entry = _relation("1", "이익 증가는 비용 감소가 주요 원인이다",
                      **{"원인": "이익 증가", "결과": "비용 감소"})

    assert causal_relation_problem(claim, source, entry) == CAUSE_CLAIM_ROLES_MISMATCH


def test_candidate_roles_opposite_to_the_labels_are_rejected() -> None:
    """후보는 A가 B의 원인이라 말하는데 응답이 반대로 라벨한 실제 반례(R1)."""

    claim = "비용 감소는 이익 증가가 주요 원인이다."
    source = {"1": "비용 감소는 이익 증가의 주요 원인이다."}
    entry = _relation("1", "비용 감소는 이익 증가의 주요 원인이다",
                      **{"원인": "비용 감소", "결과": "이익 증가"})

    assert causal_relation_problem(claim, source, entry) == CAUSE_CLAIM_ROLES_MISMATCH


def test_endpoints_from_different_clauses_cannot_be_paired() -> None:
    """관계 구문과 결과가 서로 다른 문장이면 한 쌍으로 묶지 않는다(R2)."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    source = {"1": "비용 감소가 주요 원인이다. 그러나 이익 증가는 별개다."}
    entry = _relation("1", "비용 감소가 주요 원인이다. 그러나 이익 증가는 별개다",
                      **{"원인": "비용 감소", "결과": "이익 증가"})

    assert causal_relation_problem(claim, source, entry) == CAUSE_ROLES_UNPROVEN


def test_negation_in_a_later_independent_clause_is_preserved() -> None:
    """뒤에 이어지는 «다른» 독립절의 부정은 정상 인과를 지우지 않는다(R3)."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    source = {"1": "비용 감소가 이익 증가의 주요 원인이다; 임금 감소는 없었다."}
    entry = _relation("1", "비용 감소가 이익 증가의 주요 원인이다",
                      **{"원인": "비용 감소", "결과": "이익 증가"})

    assert causal_relation_problem(claim, source, entry) == ""


def test_non_string_relation_fields_are_rejected_without_coercion() -> None:
    """칸이 문자열이 아니면 문자열로 바꾸지 않고 거절한다(R4)."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    source = {"1": "비용 감소가 이익 증가의 주요 원인이다."}
    listed = {"관계": [{"근거": ["1"], "원인": "비용 감소", "결과": "이익 증가",
                      "원문": "비용 감소가 이익 증가의 주요 원인이다", "유형": "인과"}]}
    flagged = {"관계": [{"근거": "1", "원인": True, "결과": "이익 증가",
                       "원문": "비용 감소가 이익 증가의 주요 원인이다", "유형": "인과"}]}

    assert causal_relation_problem(claim, source, listed) == RELATION_FIELD_TYPE_INVALID
    assert causal_relation_problem(claim, source, flagged) == RELATION_FIELD_TYPE_INVALID


def test_duplicate_candidate_numbers_void_that_number_instead_of_last_wins() -> None:
    """같은 번호가 두 번 오면 마지막이 이기지 않고 그 번호 전체가 무효다(R5)."""

    raw = json.dumps({"판정": [
        {"번호": 1, "검증근거": {"관계": [{"근거": "a"}]}},
        {"번호": 1, "검증근거": {"관계": [{"근거": "b"}]}},
        {"번호": 2, "검증근거": {"관계": []}},
    ]}, ensure_ascii=False)

    assert support_entries_by_number(raw) == {1: None, 2: {"관계": []}}


def test_identical_or_overlapping_slots_are_rejected() -> None:
    """원인과 결과가 같거나 한쪽이 다른 쪽의 일부면 관계가 성립하지 않는다."""

    same = _relation("1", _BOUND_QUOTE, **{"원인": "이익 감소", "결과": "이익 감소"})
    shrunk = _relation("1", _BOUND_QUOTE, **{"원인": "이익", "결과": "이익 감소"})

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, same) == CAUSE_PAIR_DEGENERATE
    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, shrunk) == CAUSE_PAIR_DEGENERATE


def test_causal_wording_outside_the_known_forms_is_reported_as_unproven() -> None:
    """틀에 없는 인과 표현은 «통과»가 아니라 «확인하지 못함»으로 남는다."""

    source = {"1": "비용 부담 증가에 기인하여 이익 감소가 나타났습니다."}
    entry = _relation("1", "비용 부담 증가에 기인하여 이익 감소가 나타났습니다", **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, source, entry) == CAUSE_ROLES_UNPROVEN


def test_denial_of_a_different_relation_earlier_does_not_delete_a_sound_cause() -> None:
    """앞 문장이 «다른» 관계를 부정해도 이 후보의 정상 인과는 지우지 않는다."""

    source = {"1": "환율 변동은 이익 감소의 원인이 아닙니다. " + _BOUND_QUOTE + "."}
    entry = _relation("1", _BOUND_QUOTE, **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, source, entry) == ""


def test_source_sentence_that_denies_the_relation_is_rejected() -> None:
    """원문 문장이 그 인과를 부정하면 막는다."""

    entry = _relation("1", _DENIAL_QUOTE, **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, _DENIAL_SOURCE, entry) == CAUSE_NEGATED_IN_SOURCE


def test_truncating_the_quote_before_the_denial_does_not_evade() -> None:
    """부정 직전까지만 잘라 내도 그 구절이 «속한 문장»을 보므로 막힌다."""

    truncated = _relation("1", "비용 부담 증가는 이익 감소의 원인", **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, _DENIAL_SOURCE, truncated) == CAUSE_NEGATED_IN_SOURCE


def test_bound_cause_and_effect_in_one_sentence_is_kept() -> None:
    """원문이 원인→결과를 그대로 적은 중립 문장은 통과시킨다(양성 대조)."""

    entry = _relation("1", _BOUND_QUOTE, **_COST_PAIR)

    assert causal_relation_problem(_CAUSE_CLAIM, _MIXED_SOURCE, entry) == ""


# ══════════════════════════════════════════════════════════
# ⑤ 독립 검토가 새로 확인한 우회 — 실제 반례를 그대로 고정한다
# ══════════════════════════════════════════════════════════

_TWO_CAUSE_CLAIM = "이익 증가는 비용 감소가 주요 원인이고, 수요 확대는 신규 계약이 주요 요인이다."
_TWO_CAUSE_SOURCE = {
    "1": "비용 감소가 이익 증가의 주요 원인이다. 신규 계약이 수요 확대의 주요 요인이다.",
}
_FIRST_ITEM = _item("1", "비용 감소가 이익 증가의 주요 원인이다",
                    **{"원인": "비용 감소", "결과": "이익 증가"})
_SECOND_ITEM = _item("1", "신규 계약이 수요 확대의 주요 요인이다",
                     **{"원인": "신규 계약", "결과": "수요 확대"})


def test_direction_word_only_slots_cannot_bind_a_relation() -> None:
    """원인·결과를 «감소»·«증가»로만 적으면 서로 다른 대상의 증감이 붙는다(exp1).

    후보는 「비용 감소 → 이익 증가」인데 원문은 「수요 감소 → 비용 증가」다. 두 칸이
    후보에도 원문에도 글자로 들어 있어 이전 판에서는 모든 검사를 통과했다.
    """

    claim = "비용 감소가 이익 증가의 주요 원인이다."
    source = {"1": "수요 감소가 비용 증가의 주요 원인이다."}
    entry = _relation("1", "수요 감소가 비용 증가의 주요 원인이다",
                      **{"원인": "감소", "결과": "증가"})

    assert causal_relation_problem(claim, source, entry) == CAUSE_SLOT_DIRECTION_ONLY


def test_direction_word_attached_to_a_subject_is_still_accepted() -> None:
    """«무엇이» 변했는지가 붙은 「비용 감소」·「이익 증가」는 그대로 통과한다(양성 대조)."""

    claim = "비용 감소가 이익 증가의 주요 원인이다."
    source = {"1": "비용 감소가 이익 증가의 주요 원인이다."}
    entry = _relation("1", "비용 감소가 이익 증가의 주요 원인이다",
                      **{"원인": "비용 감소", "결과": "이익 증가"})

    assert causal_relation_problem(claim, source, entry) == ""


def test_second_causal_assertion_without_its_own_relation_is_rejected() -> None:
    """한 후보에 단언이 둘인데 관계가 하나면 나머지 한 자리가 비어 있다(exp2)."""

    entry = _relations(_FIRST_ITEM)

    assert causal_relation_problem(_TWO_CAUSE_CLAIM, _TWO_CAUSE_SOURCE, entry) == (
        CAUSE_CLAIM_UNCOVERED
    )


def test_repeating_the_same_relation_does_not_cover_the_second_assertion() -> None:
    """같은 관계를 되풀이해 항목 수만 늘려도 «다른» 자리는 덮이지 않는다(exp2).

    ★ 중복 자체를 거절하지는 않는다 — 그러면 한 인과를 두 인용으로 입증한 정상
      응답까지 떨어진다. 중복은 «같은 자리»에만 기여하고, 남은 자리는 커버리지가 막는다.
    """

    duplicated = _relations(_FIRST_ITEM, dict(_FIRST_ITEM))

    assert causal_relation_problem(_TWO_CAUSE_CLAIM, _TWO_CAUSE_SOURCE, duplicated) == (
        CAUSE_CLAIM_UNCOVERED
    )


def test_one_relation_proven_by_two_citations_is_kept() -> None:
    """한 인과를 두 인용으로 겹쳐 입증한 정상 응답은 그대로 둔다(양성 대조)."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    quote = "비용 감소가 이익 증가의 주요 원인"
    source = {"1": quote + "이다.", "2": "회사는 " + quote + "이라고 밝혔다."}
    pair = {"원인": "비용 감소", "결과": "이익 증가"}
    entry = _relations(_item("1", quote, **pair), _item("2", quote, **pair))

    assert causal_relation_problem(claim, source, entry) == ""


def test_cause_markers_are_consistent_across_claim_role_and_source() -> None:
    """단언 표지·역할 틀·원문 사전검사가 «같은» 닫힌 낱말을 써야 한다.

    한쪽만 좁으면 정상 문장이 비인과로 떨어진다 — 「가장 큰」은 역할 틀에,
    「배경」·「요인」은 원문 사전검사에 빠져 있었다.
    """

    pair = {"원인": "비용 감소", "결과": "이익 증가"}
    for marker in ("주요 원인", "주요 배경", "주요 요인", "가장 큰 원인"):
        claim = f"이익 증가는 비용 감소가 {marker}이다."
        quote = f"비용 감소가 이익 증가의 {marker}이다"
        entry = _relation("1", quote, **pair)

        assert causal_relation_problem(claim, {"1": quote + "."}, entry) == "", marker


def test_both_assertions_proven_by_their_own_relations_are_kept() -> None:
    """단언마다 제 관계를 대면 그대로 통과한다(양성 대조)."""

    entry = _relations(_FIRST_ITEM, _SECOND_ITEM)

    assert causal_relation_problem(_TWO_CAUSE_CLAIM, _TWO_CAUSE_SOURCE, entry) == ""


def test_hedged_denial_in_the_source_is_not_a_positive_cause() -> None:
    """원문이 「원인이라고 보기(는) 어렵다」로 물린 관계를 인과로 세우면 막는다(exp3b)."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    pair = {"원인": "비용 감소", "결과": "이익 증가"}
    for tail in ("보기는 어렵다", "보기 어렵다", "단정하기는 어렵다"):
        sentence = "비용 감소가 이익 증가의 주요 원인이라고 " + tail
        entry = _relation("1", sentence, **pair)

        assert causal_relation_problem(claim, {"1": sentence + "."}, entry) == (
            CAUSE_HEDGED_IN_SOURCE
        )


def test_ordinary_difficulty_wording_does_not_delete_a_sound_cause() -> None:
    """「업무가 어렵다」·「원인 규명에 어려움」 같은 정상 문장은 지우지 않는다(exp3b)."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    pair = {"원인": "비용 감소", "결과": "이익 증가"}
    same_clause = "비용 감소가 이익 증가의 주요 원인이며 원인 규명에 어려움이 있다"
    later_clause = {"1": "비용 감소가 이익 증가의 주요 원인이다; 후속 업무가 어렵다."}

    assert causal_relation_problem(
        claim, {"1": same_clause + "."}, _relation("1", same_clause, **pair)) == ""
    assert causal_relation_problem(
        claim, later_clause,
        _relation("1", "비용 감소가 이익 증가의 주요 원인이다", **pair)) == ""


def test_empty_evidence_id_is_rejected_even_with_an_empty_key_in_the_source_map() -> None:
    """원문 매핑에 빈 키가 있어도 빈 근거 id는 따로 거절한다(item 4)."""

    claim = "이익 증가는 비용 감소가 주요 원인이다."
    quote = "비용 감소가 이익 증가의 주요 원인이다"
    source = {"": quote + ".", "1": quote + "."}
    pair = {"원인": "비용 감소", "결과": "이익 증가"}

    assert causal_relation_problem(claim, source, _relation("", quote, **pair)) == (
        CAUSE_SOURCE_ID_EMPTY
    )
    assert causal_relation_problem(claim, source, _relation("   ", quote, **pair)) == (
        CAUSE_SOURCE_ID_EMPTY
    )
    assert causal_relation_problem(claim, source, _relation("1", quote, **pair)) == ""


def test_a_denied_cause_is_not_treated_as_a_positive_assertion() -> None:
    """「주요 원인이 아니다」는 정상 부정 진술이지 인과 단언이 아니다(item 5)."""

    for claim in ("환율 변동은 이익 감소의 주요 원인이 아니다.",
                  "환율 변동은 이익 감소의 주요 원인은 아니었다.",
                  "환율 변동을 이익 감소의 주요 원인으로 보기는 어렵다.",
                  "환율 변동이 주요 원인이라고 볼 근거는 없다."):
        assert causal_relation_problem(claim, _MIXED_SOURCE, None) == ""


def test_a_positive_assertion_beside_a_denial_still_needs_its_own_relation() -> None:
    """부정 옆에 붙은 «긍정» 단언은 그대로 관계 근거를 요구한다(item 5)."""

    claim = "환율 변동은 주요 원인이 아니고, 이익 증가는 비용 감소가 주요 원인이다."
    source = {"1": "비용 감소가 이익 증가의 주요 원인이다."}
    entry = _relation("1", "비용 감소가 이익 증가의 주요 원인이다",
                      **{"원인": "비용 감소", "결과": "이익 증가"})

    assert causal_relation_problem(claim, source, None) == CAUSE_RELATION_MISSING
    assert causal_relation_problem(claim, source, entry) == ""


def test_every_reason_code_has_a_korean_description() -> None:
    """코드만 늘고 사람이 읽는 설명이 빠지면 진단이 영문 코드로 새어 나간다."""

    for code in (CAUSE_SLOT_DIRECTION_ONLY, CAUSE_CLAIM_UNCOVERED,
                 CAUSE_SOURCE_ID_EMPTY, CAUSE_HEDGED_IN_SOURCE):
        assert DIRECT_SUPPORT_REASON_TEXTS.get(code)
