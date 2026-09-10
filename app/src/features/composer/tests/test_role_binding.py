# -*- coding: utf-8 -*-
"""역할·과금 결속 가드의 단위 회귀.

fixture 는 외부 정보가 없는 작은 문장뿐이다. 실측 보고서로 확인한 결과는
.local-artifacts/resume-20260910-role-binding-guard-v3 에 따로 기록했다.

각 시험은 «막아야 하는 것»과 «살려야 하는 것»을 짝으로 둔다 — 한쪽만 있으면
가드가 전부 막아도 녹색이 되기 때문이다.
"""

import pytest

from src.features.composer.role_binding import claims_role_or_fee, role_binding_problem
from src.features.composer.role_binding_constants import (
    ROLE_BINDING_ACTOR_BOUNDARY,
    ROLE_BINDING_CLAIM_UNCOVERED,
    ROLE_BINDING_CONDITION_DROPPED,
    ROLE_BINDING_DIRECTION_REVERSED,
    ROLE_BINDING_FIELD_TYPE_INVALID,
    ROLE_BINDING_KIND_MISMATCH,
    ROLE_BINDING_MISSING,
    ROLE_BINDING_NEGATED_IN_SOURCE,
    ROLE_BINDING_NOT_OWN_CITE,
    ROLE_BINDING_PAIR_DEGENERATE,
    ROLE_BINDING_PAIR_MISSING,
    ROLE_BINDING_QUOTE_NOT_IN_SOURCE,
    ROLE_BINDING_REASON_TEXTS,
    ROLE_BINDING_ROLE_OUTSIDE_QUOTE,
    ROLE_BINDING_TARGET_NOT_IN_CANDIDATE,
    ROLE_BINDING_UNBOUND_IN_SOURCE,
)
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS


MADE = "가람은 음반을 제작합니다."
SOLD = "나래는 음반을 판매합니다."


def role(target, value, source="made", quote=MADE, kind="역할"):
    return {"유형": kind, "대상": target, "역할값": value,
            "근거": source, "원문": quote}


def bind(*items):
    return {"관계": list(items)}


def guard(text="", sources=None, entries=None, cells=None):
    return role_binding_problem(text, sources or {"made": MADE, "sold": SOLD},
                                entries, cells)


# ── 발동 ────────────────────────────────────────────────────────────
def test_prose_noun_list_is_not_a_role_claim():
    """명사 나열은 단언이 아니다 — 정상 문장을 대량으로 지우지 않는다."""

    assert not claims_role_or_fee(
        "회사의 주요 사업은 공연 기획, 영상 콘텐츠 제작, MD 상품 개발이다.", None)
    assert not claims_role_or_fee("자체 개발 소비자 조사 데이터를 활용한다.", None)
    assert guard("자체 개발 소비자 조사 데이터를 활용한다.") == ""


def test_prose_predicate_use_is_a_role_claim():
    assert claims_role_or_fee("가람이 음반을 제작한다.", None)
    assert claims_role_or_fee("음반은 가람을 통해 기획·제작되며 판매된다.", None)


def test_candidate_that_denies_the_role_needs_no_binding():
    """「제작하지 않는다」는 단언이 아니므로 결속을 요구하지 않는다."""

    assert not claims_role_or_fee("가람은 음반을 제작하지 않는다.", None)
    assert guard("가람은 음반을 제작하지 않는다.") == ""


def test_flow_cell_fires_on_the_bare_word():
    """도식은 칸 하나가 하나의 주장이라 낱말만으로 결속을 요구한다."""

    assert claims_role_or_fee("음반 ; 기획·제작", ["음반", "기획·제작"])
    assert guard(cells=["음반", "기획·제작"]) == ROLE_BINDING_MISSING


def test_ordinary_flow_row_without_role_or_fee_is_untouched():
    assert not claims_role_or_fee("개인 고객 ; 예금 이용", ["개인 고객", "예금 이용"])
    assert guard(cells=["개인 고객", "예금 이용"]) == ""


# ── 원문 결속 ────────────────────────────────────────────────────────
def test_role_is_kept_when_the_source_clause_binds_it():
    assert guard(cells=["음반", "제작"], entries=bind(role("음반", "제작"))) == ""


def test_role_transferred_from_a_sales_only_source_is_rejected():
    """원문이 그 대상에 준 역할이 «판매»뿐이면 «제작»으로 바뀌지 않는다."""

    assert guard(
        cells=["음반", "제작"],
        entries=bind(role("음반", "제작", source="sold", quote=SOLD)),
    ) == ROLE_BINDING_UNBOUND_IN_SOURCE


def test_quote_cut_before_the_negation_does_not_pass():
    """부정 앞에서 구절을 자르는 우회 — 절 전체를 본다."""

    sources = {"made": "가람은 음반을 제작하지 않는다."}
    assert guard(cells=["음반", "제작"], sources=sources,
                 entries=bind(role("음반", "제작", quote="음반을 제작"))
                 ) == ROLE_BINDING_NEGATED_IN_SOURCE


def test_a_candidate_that_denies_the_fee_is_not_a_fee_claim():
    """「수수료 없는 서비스」는 대가를 «받는다»는 단언이 아니라 그 반대다.

    ★ 후보가 표지 바로 뒤에서 스스로 부정하면 발동하지 않는다 — 원비즈 실측 꼴.
    """

    sources = {"made": "수수료 없는 비대면 서비스 등 차별화된 경험을 제공합니다."}
    assert not claims_role_or_fee("원비즈 ; 수수료 없는 비대면 서비스",
                                  ["원비즈", "수수료 없는 비대면 서비스"])
    assert guard(cells=["원비즈", "수수료 없는 비대면 서비스"], sources=sources) == ""


def test_the_same_denial_written_by_the_candidate_is_preserved():
    """원문 절의 부정을 후보도 함께 옮겼으면 어긋남이 아니다."""

    sources = {"made": "가람 서비스는 수수료 청구가 없다."}
    entry = bind(role("가람 서비스", "수수료", quote=sources["made"], kind="과금"))
    assert guard(cells=["가람 서비스", "수수료 청구 없음"], sources=sources,
                 entries=entry) == ""
    # 같은 근거로 «부정을 뺀» 후보를 승인하지는 않는다.
    assert guard(cells=["가람 서비스", "수수료 청구"], sources=sources,
                 entries=entry) == ROLE_BINDING_NEGATED_IN_SOURCE


def test_condition_dropped_from_the_source_clause_is_rejected():
    sources = {"made": "가람은 계약을 체결한 경우에만 음반을 제작합니다."}
    assert guard(cells=["음반", "제작"], sources=sources,
                 entries=bind(role("음반", "제작", quote="음반을 제작합니다"))
                 ) == ROLE_BINDING_CONDITION_DROPPED
    assert guard("음반은 계약을 체결한 경우에만 제작된다.", sources=sources,
                 entries=bind(role("음반", "제작", quote="음반을 제작합니다"))) == ""


def test_role_outside_the_offered_quote_is_rejected():
    sources = {"made": "가람은 음반을 제작합니다. 그리고 음반을 판매합니다."}
    assert guard(cells=["음반", "제작"], sources=sources,
                 entries=bind(role("음반", "제작", quote="그리고 음반을 판매합니다"))
                 ) == ROLE_BINDING_ROLE_OUTSIDE_QUOTE


def test_spacing_only_difference_still_binds_and_still_catches_negation():
    """띄어쓰기 차이는 결속을 면제하지 않는다 — 살릴 때도 막을 때도 자리를 확정한다."""

    assert guard(cells=["음반", "제작"], sources={"made": "가 람 은  음반 을 제 작 합니다."},
                 entries=bind(role("음반", "제작", quote="음반을제작"))) == ""
    assert guard(cells=["음반", "제작"],
                 sources={"made": "가 람 은 음반 을 제 작 하지 않 는다."},
                 entries=bind(role("음반", "제작", quote="음반을제작"))
                 ) == ROLE_BINDING_NEGATED_IN_SOURCE


# ── 주체 경계 ────────────────────────────────────────────────────────
def test_other_actor_cell_is_not_covered_by_another_cells_target():
    """「가람 개발」·「나래 제작」을 가람 근거 둘로 덮지 못한다."""

    sources = {"made": "가람은 개발한다. 가람은 제작한다."}
    assert guard(
        cells=["가람 개발", "나래 제작"], sources=sources,
        entries=bind(role("가람", "개발", quote="가람은 개발한다"),
                     role("가람", "제작", quote="가람은 제작한다")),
    ) == ROLE_BINDING_ACTOR_BOUNDARY
    # 같은 형태라도 그 칸의 주체가 결속 대상이면 통과한다.
    assert guard(
        cells=["가람 개발", "가람 제작"], sources=sources,
        entries=bind(role("가람", "개발", quote="가람은 개발한다"),
                     role("가람", "제작", quote="가람은 제작한다")),
    ) == ""


@pytest.mark.parametrize("source", (
    "가람은 나래가 제작한 제품을 판매한다.",
    "가람의 협력사 나래는 제작한다.",
))
def test_explicit_other_actor_in_the_source_is_not_the_targets_role(source):
    """원문이 «다른 이름»을 그 일의 주체로 밝히면 대상의 역할이 아니다."""

    assert guard(cells=["가람", "제작"], sources={"made": source},
                 entries=bind(role("가람", "제작", quote=source))
                 ) == ROLE_BINDING_UNBOUND_IN_SOURCE


def test_the_target_may_be_the_object_of_that_predicate():
    """「음반을 제작합니다」처럼 대상이 바로 붙은 목적어면 그 대상의 역할이다."""

    assert guard(cells=["음반", "제작"],
                 entries=bind(role("음반", "제작"))) == ""
    assert guard(cells=["가람", "제작"], sources={"made": "가람은 제작한다."},
                 entries=bind(role("가람", "제작", quote="가람은 제작한다"))) == ""


def test_nominal_role_keeps_its_modifier_chain():
    """역할이 명사면 앞의 「-는」은 꾸밈이라 주체 전이가 아니다(우리은행 신탁 실측 꼴)."""

    source = "신탁수익 중 기준수익률에 영향을 받는 변동대가는 뒤에 인식합니다."
    assert guard(cells=["신탁자산", "신탁수익(변동대가)"], sources={"made": source},
                 entries=bind(role("신탁수익", "변동대가", quote=source, kind="과금"))
                 ) == ""


def test_prose_cannot_cross_a_closed_predicate_of_another_subject():
    text = "가람은 판매하고 나래는 제작한다."
    assert guard(text, sources={"made": text},
                 entries=bind(role("가람", "제작", quote=text))
                 ) == ROLE_BINDING_ACTOR_BOUNDARY


def test_bracketed_list_stays_bound_to_its_target():
    """「신탁수익(수익보수, 변동대가)」의 괄호 안 쉼표는 주체를 가르지 않는다."""

    source = "신탁수익 중 수익보수와 같이 기준수익률에 영향을 받는 변동대가는 뒤에 인식합니다."
    assert guard(
        cells=["신탁자산", "신탁수익(수익보수, 변동대가)"], sources={"made": source},
        entries=bind(role("신탁수익", "수익보수", quote=source, kind="과금"),
                     role("신탁수익", "변동대가", quote=source, kind="과금")),
    ) == ""


# ── 자리마다 증명 ────────────────────────────────────────────────────
def test_one_fee_proof_does_not_cover_another_fee_in_the_same_cell():
    sources = {"made": "나래 서비스는 이용 수수료를 수취합니다."}
    one = bind(role("나래 서비스", "수수료", quote=sources["made"], kind="과금"))
    assert guard(cells=["나래 서비스", "수수료, 로열티"], sources=sources,
                 entries=one) == ROLE_BINDING_CLAIM_UNCOVERED
    both = {"made": "나래 서비스는 이용 수수료를 수취합니다. 나래 서비스는 로열티도 받습니다."}
    assert guard(
        cells=["나래 서비스", "수수료, 로열티"], sources=both,
        entries=bind(
            role("나래 서비스", "수수료", quote="나래 서비스는 이용 수수료를 수취합니다",
                 kind="과금"),
            role("나래 서비스", "로열티", quote="나래 서비스는 로열티도 받습니다",
                 kind="과금"),
        ),
    ) == ""


def test_repeat_claim_needs_its_own_proof():
    sources = {"made": "가람은 기존 구매층을 이용합니다."}
    assert guard(cells=["팬덤", "기존 팬층 재구매"], sources=sources,
                 entries=bind(role("기존 팬층", "재구매", quote=sources["made"],
                                   kind="과금"))
                 ) == ROLE_BINDING_UNBOUND_IN_SOURCE
    proven = {"made": "기존 팬층은 신보를 재구매합니다."}
    assert guard(cells=["팬덤", "기존 팬층 재구매"], sources=proven,
                 entries=bind(role("기존 팬층", "재구매", quote=proven["made"],
                                   kind="과금"))) == ""


def test_role_word_alone_does_not_cover_a_second_occurrence():
    sources = {"made": "가람은 음반을 제작합니다."}
    assert guard(cells=["음반 제작", "영상 제작"], sources=sources,
                 entries=bind(role("음반", "제작"))) == ROLE_BINDING_CLAIM_UNCOVERED


# ── 결속 항목의 모양 ─────────────────────────────────────────────────
@pytest.mark.parametrize("entries", (None, {}, [], "관계", {"관계": "x"},
                                     {"관계": ["x"]}, {"관계": [{"유형": "인과"}]}))
def test_malformed_evidence_never_approves(entries):
    assert guard(cells=["음반", "제작"], entries=entries) == ROLE_BINDING_MISSING


def test_non_string_field_is_rejected():
    item = role("음반", "제작")
    item["원문"] = ["가람은 음반을 제작합니다."]
    assert guard(cells=["음반", "제작"],
                 entries=bind(item)) == ROLE_BINDING_FIELD_TYPE_INVALID


def test_missing_or_degenerate_pair_is_rejected():
    assert guard(cells=["음반", "제작"],
                 entries=bind(role("", "제작"))) == ROLE_BINDING_PAIR_MISSING
    assert guard(cells=["음반 제작", "유통"],
                 entries=bind(role("음반 제작", "제작"))) == ROLE_BINDING_PAIR_DEGENERATE


def test_kind_must_answer_the_claim():
    assert guard(cells=["음반", "제작"],
                 entries=bind(role("음반", "제작", kind="과금"))
                 ) == ROLE_BINDING_KIND_MISMATCH


def test_foreign_or_empty_citation_is_rejected():
    assert guard(cells=["음반", "제작"],
                 entries=bind(role("음반", "제작", source="other"))
                 ) == ROLE_BINDING_NOT_OWN_CITE
    assert guard(cells=["음반", "제작"],
                 entries=bind(role("음반", "제작", source=""))
                 ) == ROLE_BINDING_NOT_OWN_CITE


def test_quote_must_be_a_continuous_run_of_that_source():
    assert guard(cells=["음반", "제작"],
                 entries=bind(role("음반", "제작", quote="가람은 제작합니다"))
                 ) == ROLE_BINDING_QUOTE_NOT_IN_SOURCE


def test_target_must_appear_in_the_candidate():
    assert guard(cells=["음반", "제작"],
                 entries=bind(role("가람", "제작"))
                 ) == ROLE_BINDING_TARGET_NOT_IN_CANDIDATE


# ── 대가의 방향 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("claim", (
    "기업금융의 주요 수익원은 수수료",           # 대상 → 주요 수익원 → 대가
    "기업금융 부문은 수수료가 주요 수익원이다.",  # 대가 → 주요 수익원 (실제 저장 어순)
))
def test_fee_direction_reversal_is_rejected(claim):
    """원문은 «그 대가가 어디서 생기나», 후보는 «그 대상의 주요 수익원». 방향이 반대다."""

    source = "수수료는 주로 기업금융 부문에서 발생합니다."
    entry = bind(role("기업금융", "수수료", quote=source, kind="과금"))
    assert guard(cells=[claim], sources={"made": source},
                 entries=entry) == ROLE_BINDING_DIRECTION_REVERSED


def test_the_plain_fee_claim_on_the_same_source_is_kept():
    source = "수수료는 주로 기업금융 부문에서 발생합니다."
    entry = bind(role("기업금융", "수수료", quote=source, kind="과금"))
    assert guard(cells=["기업금융", "수수료"], sources={"made": source},
                 entries=entry) == ""


# ── 낱말 경계를 걸친 우연한 일치 ─────────────────────────────────────
def test_markers_do_not_fire_on_accidental_cross_word_matches():
    """공백을 지운 표면형에서 낱말 경계를 걸쳐 우연히 맞는 자리는 단언이 아니다."""

    # 「정**부과**제」 — 부과가 아니라 정부/과제다(실측 반례).
    assert not claims_role_or_fee(
        "개발 대가와 정부 과제 수입은 해당 솔루션 계약에서 발생한다.", None)
    # 「유지보수」의 보수는 대가가 아니라 정비다.
    assert not claims_role_or_fee("고객사는 결과를 유지보수 판단에 사용한다.", None)
    assert not claims_role_or_fee("설비 ; 유지보수 서비스", ["설비", "유지보수 서비스"])
    # 실제 대가 서술은 그대로 발동한다.
    assert claims_role_or_fee("이 서비스는 이용 수수료를 부과한다.", None)
    assert claims_role_or_fee("가람은 로열티를 지급받는다.", None)


# ── 계약 ────────────────────────────────────────────────────────────
def test_every_reason_code_is_registered_in_the_diagnostic_contract():
    for code in ROLE_BINDING_REASON_TEXTS:
        assert code in REVIEW_SCOPE_ITEMS, code
        assert ROLE_BINDING_REASON_TEXTS[code].strip()
