"""표 행 각주 표식으로 결속된 «종속기업 제외»와 현재 연결 관계 단정의 대칭 회귀.

공시 주석은 법인 행에 표식(「(*)」)을 붙이고 표 아래 같은 표식의 각주에 회계 범위
제한을 적는다. 각주를 인용하고도 그 조건을 지운 채 «현재 종속기업·연결 대상»이라고
단정하면 막는다. 같은 법인·같은 표식·같은 기간으로 결속될 때만 막고, 과거 관계
설명·다른 법인·실제 거래 사실·조건을 지킨 설명은 남긴다. 법인 이름은 익명이다.
"""

import pytest

from src.features.composer.entity_scope_constraints import EntityScopeContext
from src.features.composer.grounding import grounding_problem
from src.features.composer.scope_guard import document_entity_scope_problem, scope_problem

UNBOUND = "scope_condition_unbound"

#: 칸이 한 줄로 펼쳐진 꼴(상류 발췌가 공백으로 이은 모양).
FLAT_NOTE = (
    "5. 매도가능증권 당기말 현재 매도가능증권의 내역은 다음과 같습니다. "
    "(단위 : 천원) 구분 지분율 취득원가 장부금액 가람 Holdings(*) 100% 40,000 "
    "40,000 나래 Studio 30% 12,000 12,000 (*) 일반기업회계기준 경과규정에 따라 "
    "종속기업에서 제외되었습니다. 또한, 중소기업 회계처리 특례를 적용하여 지분법을 "
    "적용하지 아니하고 취득원가를 장부금액으로 계상하고 있습니다."
)
#: 칸마다 줄이 바뀌는 실제 공시 꼴 — 「제외 되었습니다」처럼 띄어 쓴다.
LINE_NOTE = (
    "5. 매도가능증권\n당기말 현재 매도가능증권의 내역은 다음과 같습니다.\n"
    "(단위 : 천원)\n구분\n당기말\n전기말\n지분률\n취득원가\n장부금액\n"
    "가람 Holdings(*)\n100%\n45,274\n45,274\n"
    "(*) 일반기업회계기준시행일 및 경과규정(2022.12.2.)에 따라 종속기업에서 제외 "
    "되었습니다. 또한, 중소기업 회계처리 특례를 적용하여 지분법을 적용하지 "
    "아니하고 취득원가를 장부금액으로 계상하고 있습니다.\n6. 유형자산"
)
NOTES = pytest.mark.parametrize("note", [FLAT_NOTE, LINE_NOTE], ids=["flat", "lines"])


@NOTES
@pytest.mark.parametrize("candidate", [
    "가람 Holdings는 회사의 연결재무제표 작성 대상 종속기업이다.",
    "가람 Holdings는 현재 연결 대상이다.",
    "회사는 가람 Holdings를 종속기업으로 두고 있다.",
    "회사는 연결 대상인 가람 Holdings에 자금을 대여했다.",
])
def test_current_relation_claim_that_drops_the_footnote_exclusion_is_rejected(note, candidate):
    assert scope_problem(candidate, {"1": note}) == UNBOUND


@NOTES
@pytest.mark.parametrize("candidate", [
    # 각주 조건을 그대로 지킨 설명.
    "회사는 가람 Holdings를 경과규정에 따라 종속기업에서 제외하고 취득원가로 계상하고 있다.",
    # 실제 거래 사실 — 관계자 표의 분류 이름을 붙인 것은 현재 연결 단정이 아니다.
    "회사는 종속기업 가람 Holdings에 운영자금을 대여하였다.",
    # 과거 관계 설명.
    "가람 Holdings는 과거 종속기업이었으나 경과규정에 따라 종속기업에서 제외되었다.",
    # 조건과 일치하는 부정 진술.
    "가람 Holdings는 연결 대상에 포함되지 않는다.",
    # 표식이 없는 다른 법인 행.
    "나래 Studio는 연결 대상 종속기업이다.",
    # 이름이 없는 일반 서술 — 「연결」 낱말 자체는 금칙어가 아니다.
    "회사는 연결재무제표를 작성하지 않는다.",
])
def test_conditions_kept_transactions_history_and_other_entities_survive(note, candidate):
    assert scope_problem(candidate, {"1": note}) == ""


def test_name_tail_of_another_row_does_not_bind():
    note = FLAT_NOTE.replace("가람 Holdings(*)", "다온 가람 Holdings(*)")
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""
    assert scope_problem("다온 가람 Holdings는 현재 종속기업이다.", {"1": note}) == UNBOUND


def test_marker_must_match_the_same_footnote():
    note = (
        "당기말 현재 구분 지분율 장부금액 가람 Holdings(*1) 100% 40,000 나래 Studio(*2) 100% "
        "10,000 (*1) 지분법을 적용하여 평가하고 있습니다. (*2) 일반기업회계기준 "
        "경과규정에 따라 종속기업에서 제외되었습니다."
    )
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""
    assert scope_problem("나래 Studio는 현재 종속기업이다.", {"1": note}) == UNBOUND


def test_prior_period_only_table_exclusion_is_not_attached_to_current_claim():
    note = (
        "(2) 전기말 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (*) 경과규정에 "
        "따라 종속기업에서 제외되었습니다."
    )
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""


@pytest.mark.parametrize("definition", [
    "(*) 다음 회계연도에 종속기업에서 제외될 예정입니다.",
    "(*) 전기 중 종속기업에서 제외되었으나 당기 중 지분을 재취득하여 종속기업으로 "
    "다시 편입되었습니다.",
])
def test_planned_or_reversed_exclusion_does_not_block(definition):
    note = f"당기말 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 {definition}"
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""


def test_exclusion_in_another_fragment_is_not_borrowed():
    # 다른 조각(다른 문서)의 각주는 이 후보가 인용한 원문이 아니다.
    assert scope_problem(
        "가람 Holdings는 현재 종속기업이다.",
        {"1": "회사는 종속기업 가람 Holdings에 운영자금을 대여하였습니다."},
    ) == ""


@pytest.mark.parametrize("verdict", ["참", "애매"])
def test_reviewer_approval_does_not_publish_the_dropped_condition(verdict):
    candidate = "가람 Holdings는 회사의 연결재무제표 작성 대상 종속기업이다."
    assert grounding_problem(candidate, {"1": LINE_NOTE}, {"결과": verdict}) == UNBOUND


# ══ 독립 반례 후속(2026-09-23) — 주체·술어 결속, 각주 경계, 기간·재편입 ══════

@pytest.mark.parametrize("candidate,blocked", [
    # 반례 1: 제외 법인의 앞선 목적격이 다른 법인의 술어를 빌리지 않는다.
    ("회사는 가람 Holdings를 지원하고 나래 Studio를 연결 대상으로 관리한다.", False),
    # 반례 2: 뒤 술어의 무관한 부정이 앞 단정을 지우지 않는다.
    ("회사는 가람 Holdings를 연결 대상으로 관리하며 영업비용은 부담하지 않는다.", True),
    # 같은 술어에 붙은 부정은 단정이 아니다.
    ("가람 Holdings는 연결 대상에 포함되지 않으며 영업비용은 부담한다.", False),
    # 주제는 이어진 술어까지 미친다 — 다른 주제가 끼면 그 주제의 술어다.
    ("가람 Holdings는 해외 영업을 담당하고 연결 대상이다.", True),
    ("가람 Holdings는 해외 영업을 담당하고 나래 Studio는 연결 대상이다.", False),
    # 「…이다」 앞 목적격은 관형절 안의 것이다 — 주제가 대상이다.
    ("가람 Holdings는 회사가 지분 전부를 보유한 종속기업이다.", True),
    # 관형형 어미(「않은」·「보유하는」)는 주제 조사가 아니다 — 앞 주제가 대상이다.
    ("가람 Holdings는 제외되지 않은 현재 종속기업이다.", True),
    ("가람 Holdings는 회사가 지분을 보유하는 연결 대상이다.", True),
    # 타동 「…에 포함하여」는 목적격이 대상이다.
    ("회사는 가람 Holdings를 연결 범위에 포함하여 재무제표를 작성한다.", True),
    ("회사는 나래 Studio를 연결 범위에 포함하여 재무제표를 작성한다.", False),
])
def test_claim_is_bound_to_its_own_argument_and_predicate(candidate, blocked):
    assert bool(scope_problem(candidate, {"1": FLAT_NOTE})) is blocked


def test_next_section_exclusion_is_not_merged_into_previous_footnote():
    # 반례 3: 가람의 정상 각주 뒤 새 구획의 나래 제외 문장을 가람에 붙이지 않는다.
    note = (
        "당기말 현재 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (*) 지분법을 "
        "적용하여 평가하고 있습니다. 2. 연결 범위 나래 Studio는 경과규정에 따라 "
        "종속기업에서 제외되었습니다."
    )
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""


def test_footnote_body_sentence_naming_another_entity_is_not_attributed():
    note = (
        "당기말 현재 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (*) 지분법을 "
        "적용하여 평가하고 있습니다. 나래 Studio는 경과규정에 따라 종속기업에서 "
        "제외되었습니다."
    )
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""
    # 자기 지칭 주어(「회사는」)는 같은 행의 각주 서술로 본다.
    self_note = note.replace(
        "지분법을 적용하여 평가하고 있습니다. 나래 Studio는",
        "회사는 동 법인을",
    ).replace("종속기업에서 제외되었습니다", "종속기업에서 제외하였습니다")
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": self_note}) == UNBOUND


def test_reused_marker_of_another_table_does_not_reach_back():
    # 반례 3b: 앞 표의 각주 정의가 발췌에서 빠지고 뒤 표가 같은 표식을 다시 쓴다.
    note = (
        "당기말 현재 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (2) 기타 투자 "
        "구분 지분율 장부금액 나래 Studio(*) 30% 12,000 (*) 경과규정에 따라 "
        "종속기업에서 제외되었습니다."
    )
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""
    # 같은 표의 행은 그대로 결속된다(기간은 앞선 당기 표지).
    assert scope_problem("나래 Studio는 현재 종속기업이다.", {"1": note}) == UNBOUND


def test_period_unknown_exclusion_is_not_taken_as_current():
    note = (
        "구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (*) 경과규정에 따라 "
        "종속기업에서 제외되었습니다."
    )
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": note}) == ""
    # 각주 본문이 당기를 명시하면 현재 제약이다.
    current = note.replace("(*) 경과규정에", "(*) 당기 중 경과규정에")
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": current}) == UNBOUND


@pytest.mark.parametrize("exclusion", [
    "당기말 현재 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (*) 경과규정에 따라 "
    "종속기업에서 제외되었습니다.",
    "가람 Holdings는 2024년 종속기업에서 제외되었다.",
])
def test_inclusion_event_in_another_cited_fragment_blocks_nothing(exclusion):
    # 반례 4: 제외 조각과 재편입 조각이 함께 인용되면 선후를 문자열로 정하지 않는다.
    sources = {
        "1": exclusion,
        "2": "2026년 중 회사는 지분을 추가 취득하여 가람 Holdings를 종속기업으로 "
             "다시 편입하였습니다.",
    }
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", sources) == ""
    # 재편입 조각 없이 제외만 인용하면 계속 막는다.
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": exclusion}) == UNBOUND


def test_related_party_classification_is_not_an_inclusion_event():
    # 관계자 표의 「종속기업」 분류는 편입 사건이 아니다 — 제외 각주를 풀지 않는다.
    sources = {
        "1": FLAT_NOTE,
        "2": "구분 특수관계자명 종속기업 가람 Holdings. 회사는 가람 Holdings를 "
             "종속기업으로 두고 있습니다.",
    }
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", sources) == UNBOUND


def test_space_separated_completion_in_subject_form_is_also_read():
    source = "가람 Holdings는 경과규정에 따라 종속기업에서 제외 되었다."
    assert scope_problem("가람 Holdings는 현재 종속기업이다.", {"1": source}) == UNBOUND


# ══ 같은 문서 제약 전용 진입점(총괄 설계 채택안) ═══════════════════════════
# context 타입은 배선 도우미(entity_scope_constraints.py)가 소유한다. 문서 신원의
# 정규성 검사는 도우미 몫이라 여기서는 익명 정규 꼴 하나를 쓴다.
RELATED = "당기 중 회사는 종속기업 가람 Holdings에 운영자금을 대여하였습니다."
OWNCITE_DROPPED = "회사는 가람 Holdings를 종속기업으로 두고 있으며 운영자금을 대여했다."
DOCUMENT = "document:dart.fss.or.kr:20000000000001"


def _context(cited, constraints, identity=DOCUMENT):
    return EntityScopeContext(identity, cited, constraints)


def test_same_document_footnote_constrains_a_candidate_that_did_not_cite_it():
    cited = {"related": RELATED}
    context = _context(cited, {"fn": FLAT_NOTE})
    # 자기 인용만으로는(각주 없음) scope_problem 이 통과시키는 사례다.
    assert scope_problem(OWNCITE_DROPPED, cited) == ""
    assert document_entity_scope_problem(OWNCITE_DROPPED, (context,)) == UNBOUND
    # 제약은 판정에만 쓴다 — 긍정 근거는 그대로다.
    assert dict(context.cited_sources) == {"related": RELATED}
    assert dict(context.constraint_sources) == {"fn": FLAT_NOTE}


@pytest.mark.parametrize("candidate", [
    "회사는 종속기업 가람 Holdings에 운영자금을 대여하였다.",
    "회사는 가람 Holdings를 경과규정에 따라 종속기업에서 제외하고 취득원가로 계상하고 있다.",
    "가람 Holdings는 과거 종속기업이었으나 경과규정에 따라 종속기업에서 제외되었다.",
    "나래 Studio는 연결 대상 종속기업이다.",
    "회사는 가람 Holdings를 지원하고 나래 Studio를 연결 대상으로 관리한다.",
])
def test_context_entry_keeps_transactions_exact_exclusions_history_and_other_entities(candidate):
    assert document_entity_scope_problem(
        candidate, (_context({"related": RELATED}, {"fn": FLAT_NOTE}),)) == ""


@pytest.mark.parametrize("constraint", [
    # 전기만 가리키는 표의 제외.
    "(2) 전기말 구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (*) 경과규정에 "
    "따라 종속기업에서 제외되었습니다.",
    # 기간 불명.
    "구분 지분율 장부금액 가람 Holdings(*) 100% 40,000 (*) 경과규정에 따라 "
    "종속기업에서 제외되었습니다.",
    # 다른 표의 같은 표식.
    "1. 당기말 투자 내역\n구분 장부금액 가람 Holdings(*) 100% 40,000\n2. 별도 투자 "
    "내역\n구분 장부금액 나래 Studio(*) 100% 20,000\n(*) 경과규정에 따라 종속기업에서 "
    "제외되었습니다.",
])
def test_context_entry_does_not_borrow_unbound_period_or_table(constraint):
    assert document_entity_scope_problem(
        OWNCITE_DROPPED, (_context({"related": RELATED}, {"fn": constraint}),)) == ""


def test_context_without_document_identity_is_ignored():
    assert document_entity_scope_problem(
        OWNCITE_DROPPED, (_context({"related": RELATED}, {"fn": FLAT_NOTE}, identity=""),),
    ) == ""


def test_inclusion_event_in_any_known_source_lifts_the_constraint():
    reincluded = ("회사는 당기 중 지분을 추가 취득하여 가람 Holdings를 종속기업으로 "
                  "다시 편입하였습니다.")
    assert document_entity_scope_problem(OWNCITE_DROPPED, (
        _context({"related": RELATED}, {"fn": FLAT_NOTE}),
        _context({"later": reincluded}, {}, identity="document:dart.fss.or.kr:20000000000002"),
    )) == ""


def test_no_context_means_no_constraint():
    assert document_entity_scope_problem(OWNCITE_DROPPED, ()) == ""


# 독립 검증 대칭 반례(조사 한 글자) — 뒤 술어 단위의 국소 주격이 앞 단위 주제보다 우선한다.
@pytest.mark.parametrize("candidate,expected", [
    ("가람 Holdings는 해외 영업을 담당하고 나래 Studio가 연결 대상이다.", ""),
    ("나래 Studio는 해외 영업을 담당하고 가람 Holdings가 연결 대상이다.", UNBOUND),
])
def test_local_subject_of_the_claim_unit_outranks_the_earlier_topic(candidate, expected):
    assert scope_problem(candidate, {"fn": FLAT_NOTE}) == expected
    assert document_entity_scope_problem(
        candidate, (_context({"related": RELATED}, {"fn": FLAT_NOTE}),)) == expected


# 총괄 채택 최소 축소 — 국소 주격은 단정에 «직접»(공백만) 붙을 때만 주체다. 사이에
# 다른 내용이 있으면(관형절 주격일 수 있음) 판정을 보류한다. 앞 주제로 되돌아가
# 추측하지 않으므로 두 번째 사례는 막지 못한 채 의미 검수 범위에 남는다(해결 주장 아님).
@pytest.mark.parametrize("candidate", [
    # 관형절 주격이 제외 법인 — 가람의 현재 관계로 확정해 오차단하지 않는다.
    "나래 Studio는 해외 영업을 담당하고 가람 Holdings가 지분을 보유한 종속기업이다.",
    # 관형절 주격이 다른 주체 — 같은 보류로 막지 않는다(미탐, 의미 검수 몫).
    "가람 Holdings는 해외 영업을 담당하고 회사가 지분을 보유한 종속기업이다.",
])
def test_non_adjacent_local_subject_withholds_judgement(candidate):
    assert scope_problem(candidate, {"fn": FLAT_NOTE}) == ""
    assert document_entity_scope_problem(
        candidate, (_context({"related": RELATED}, {"fn": FLAT_NOTE}),)) == ""
