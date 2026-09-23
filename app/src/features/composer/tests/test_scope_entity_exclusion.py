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


# ══ 5차 실측 P11(2026-09-23) — 분류 이름을 앞에 둔 보유 단정 ══════════════════
# 「회사는 종속기업 <법인>을 보유하고 있으며 … 100% 지분으로 소유」가, 인용한 조각
# 안에 같은 법인의 「종속기업에서 제외」 각주가 있는데도 단정 목록 밖의 꼴이라 통과했다.
# C 재현(무료)으로 소유·가지다·「회사의 종속기업」·「100% 지분으로 소유」·「자회사」 꼴도
# 같은 구멍임을 확인했다. 지분 단정은 표 행의 제외 조건을 떼어 낸 것이라 막되(총괄 결정),
# 후보가 같은 법인의 제외 사실을 함께 적으면 조건이 살아 있으므로 통과시킨다.

HOLDING_CLAIMS = pytest.mark.parametrize("candidate", [
    # 실측 [30] 모양.
    "회사는 종속기업 가람 Holdings를 보유하고 있으며, 가람 Holdings는 100% 지분으로 소유되고 있다.",
    # ① 분류 이름 + 보유 동사(보유·소유·두다·가지다) — C 재현에서 통과하던 꼴.
    "회사는 종속기업 가람 Holdings를 보유하고 있다.",
    "회사는 종속기업 가람 Holdings를 소유하고 있다.",
    "회사는 종속기업 가람 Holdings를 두고 있다.",
    "회사는 종속기업 가람 Holdings를 가지고 있다.",
    "회사는 종속기업 가람 Holdings 등을 보유하고 있다.",
    # ② 「<법인>은 회사의 종속기업」 서술형.
    "가람 Holdings는 회사의 종속기업입니다.",
    "가람 Holdings는 회사의 종속기업이며 일본 사업을 맡고 있다.",
    "가람 Holdings는 회사가 해외 운영을 맡긴 종속기업입니다.",
    # ③ 지분 보유 단정(각주 제외 조건 없이).
    "가람 Holdings는 100% 지분으로 소유되고 있다.",
    "회사는 가람 Holdings를 100% 지분으로 소유하고 있다.",
    "회사는 가람 Holdings의 지분 100%를 보유하고 있다.",
    "회사는 가람 Holdings 지분을 100% 소유하고 있다.",
    # ④ 「자회사」 — 같은 소속 단정의 일상어.
    "회사는 자회사 가람 Holdings를 두고 있다.",
    "가람 Holdings는 회사의 100% 자회사다.",
    "회사는 가람 Holdings를 자회사로 두고 있다.",
    "자회사인 가람 Holdings는 일본 사업을 맡는다.",
], ids=["실측_보유_100지분", "종속기업_보유", "종속기업_소유", "종속기업_두고", "종속기업_가지고",
        "종속기업_등", "회사의_종속기업입니다", "회사의_종속기업이며", "관형절_종속기업입니다",
        "100지분으로_소유되고", "100지분으로_소유", "지분_100_보유", "지분을_100_소유",
        "자회사_두고", "100_자회사다", "자회사로_두고", "자회사인"])
KEPT_HOLDING = pytest.mark.parametrize("candidate", [
    # 과거 관계와 제외 사실을 함께 정확히 적은 문장 — 지분 단정은 제외 조건과 함께면 통과.
    "가람 Holdings는 경과규정에 따라 종속기업에서 제외되었으며, 회사는 가람 Holdings 지분 100%를 보유하고 있다.",
    "가람 Holdings는 과거 회사의 종속기업이었으나 경과규정에 따라 종속기업에서 제외되었고, "
    "회사는 현재 가람 Holdings를 100% 지분으로 소유하고 있다.",
    "회사는 종속기업에서 제외된 가람 Holdings의 지분 100%를 보유하고 있다.",
    # 현재 대여·거래만 적은 문장(분류 이름만 붙은 거래 서술 포함).
    "회사는 종속기업 가람 Holdings에 운영자금을 대여하였다.",
    "회사는 자회사 가람 Holdings에 운영자금을 대여하였다.",
    "회사는 자회사로부터 배당금을 받았다.",
    # 제외 법인 자신이 주어인 배당 수취 — 「자회사로부터」는 소속 단정이 아니다.
    "가람 Holdings는 자회사로부터 배당금을 받았다.",
    # 대상 자리에 다른 술어가 낀 거래 서술.
    "회사는 종속기업 가람 Holdings에 운영자금을 대여하고 지분을 보유하고 있다.",
    # 같은 술어의 부정·과거 서술.
    "회사는 종속기업 가람 Holdings를 보유하지 않는다.",
    "회사는 과거 종속기업 가람 Holdings를 보유했다.",
    "가람 Holdings는 회사의 종속기업이었다.",
    # 표식이 없는 다른 법인.
    "회사는 종속기업 나래 Studio를 보유하고 있다.",
    "회사는 나래 Studio 지분 30%를 보유하고 있다.",
], ids=["제외와_지분_정확", "과거와_제외와_현재지분", "제외된_법인의_지분", "종속기업_대여만",
        "자회사_대여만", "자회사로부터_배당", "제외법인의_자회사로부터_배당", "대여_거래", "부정",
        "과거", "과거_서술형",
        "다른_법인", "다른_법인_지분"])


@NOTES
@HOLDING_CLAIMS
def test_P11_인용_조각_안의_제외_각주가_보유_단정을_막는다(note, candidate):
    assert scope_problem(candidate, {"6": note}) == UNBOUND
    assert grounding_problem(candidate, {"6": note}, {"결과": "참"}) == UNBOUND


@NOTES
@HOLDING_CLAIMS
def test_P11_같은_공시의_인용_밖_제외_각주도_보유_단정을_막는다(note, candidate):
    context = _context({"related": RELATED}, {"fn": note})
    assert document_entity_scope_problem(candidate, (context,)) == UNBOUND


@NOTES
@KEPT_HOLDING
def test_P11_제외_사실과_지분을_정확히_적은_문장과_거래_서술은_남는다(note, candidate):
    assert scope_problem(candidate, {"6": note}) == ""
    context = _context({"related": RELATED}, {"fn": note})
    assert document_entity_scope_problem(candidate, (context,)) == ""


# ══ 2026-09-23 독립 검토 반영 — P11 부정·과거·계획(F7), 나열·장식(F8), 회사 주어 제외(F12) ══
# 2판은 보유·소속·지분 단정 꼴을 넓히면서, 같은 술어의 부정(「보유하고 있지 않다」)·과거
# (「보유했던」·「보유했었다」·「두었으나」)·계획(「편입할 계획」)까지 현재 단정으로 읽었다.
# 각주 조건을 정확히 지킨 과거 서술도 막혔다. 단정 뒤 «같은 술어의 꼬리»로 가린다.

FOUND_KEPT = pytest.mark.parametrize("candidate", [
    "회사는 종속기업 가람 Holdings를 보유하고 있지 않다.",
    "회사는 종속기업 가람 Holdings를 두었으나, 2025년에 매각했다.",
    "종속기업 가람 Holdings를 보유했던 회사는 지분을 정리했다.",
    "회사는 종속기업 가람 Holdings를 보유했었다.",
    # 정답 과거 서술 — 과거 보유와 제외 사실을 쉼표로 이어 적었다.
    "회사는 종속기업 가람 Holdings를 보유했었으나, 경과규정에 따라 종속기업에서 제외되었다.",
    "회사는 종속기업 가람 Holdings를 보유하고 있었다.",
    "회사는 가람 Holdings 지분 100%를 보유하고 있지 않다.",
    "회사는 가람 Holdings 지분 100%를 보유했었다.",
    "회사는 가람 Holdings를 자회사로 두었으나, 2025년 매각했다.",
    "회사는 가람 Holdings를 자회사로 편입할 계획이다.",
    # 변경 전부터 막히던 기존 약점 — 같은 꼬리 규칙으로 함께 풀린다.
    "회사는 가람 Holdings를 종속기업으로 두고 있지 않다.",
    "회사는 가람 Holdings를 종속기업으로 두었으나, 경과규정에 따라 종속기업에서 제외되었다.",
], ids=["보유하고_있지_않다", "두었으나_쉼표_매각", "보유했던", "보유했었다", "정답_과거_서술",
        "보유하고_있었다", "지분_보유하고_있지_않다", "지분_보유했었다", "자회사로_두었으나_매각",
        "자회사로_편입_계획", "종속기업으로_두고_있지_않다", "종속기업으로_두었으나_제외"])


@NOTES
@FOUND_KEPT
def test_P11_같은_술어의_부정_과거_계획은_현재_단정이_아니다(note, candidate):
    assert scope_problem(candidate, {"6": note}) == ""
    context = _context({"related": RELATED}, {"fn": note})
    assert document_entity_scope_problem(candidate, (context,)) == ""


@NOTES
def test_P11_단순_과거_보유했다는_현재일_수_있어_계속_막는다(note):
    """경계 — 「과거」 같은 표지 없는 「보유했다」는 지금도 보유 중일 수 있다."""

    candidate = "회사는 종속기업 가람 Holdings를 보유했다."
    assert scope_problem(candidate, {"6": note}) == UNBOUND


LISTED_CLAIMS = pytest.mark.parametrize("candidate", [
    "회사는 종속기업 가람 Holdings와 나래 Studio 등 2개사를 보유하고 있다.",
    "회사는 종속기업 나래 Studio와 가람 Holdings를 보유하고 있다.",
    "회사는 종속기업 나래 Studio 및 가람 Holdings를 보유하고 있다.",
    "회사는 종속기업 (주)가람 Holdings를 보유하고 있다.",
    "회사는 종속기업 가람 Holdings(100%)를 보유하고 있다.",
], ids=["나열_앞_등_2개사", "나열_뒤", "및_나열", "주_앞붙음", "지분율_덧붙음"])
# 알려진 한계 — 쉼표 나열(「종속기업 A, B 및 C를 보유」)은 후보를 쉼표에서 절로 먼저
# 자르는 구조(`_claims_bound_to`) 때문에 분류 이름과 술어가 다른 절로 갈려 잡히지 않는다.


@NOTES
@LISTED_CLAIMS
def test_P11_나열하거나_장식을_붙인_보유_단정도_막는다(note, candidate):
    assert scope_problem(candidate, {"6": note}) == UNBOUND
    context = _context({"related": RELATED}, {"fn": note})
    assert document_entity_scope_problem(candidate, (context,)) == UNBOUND


@NOTES
@pytest.mark.parametrize("candidate", [
    "회사는 종속기업 가람 Holdings와 나래 Studio에 대한 채권을 보유하고 있다.",
    "회사는 종속기업 나래 Studio와 다온 Tech 등 2개사를 보유하고 있다.",
], ids=["나열_거래_서술", "제외_법인_없는_나열"])
def test_P11_나열이_거래_서술이거나_제외_법인이_없으면_막지_않는다(note, candidate):
    assert scope_problem(candidate, {"6": note}) == ""


COMPANY_SUBJECT_EXCLUSION = "회사는 당기 중 나래 Studio의 지분을 전량 처분하여 연결범위에서 제외하였습니다."
OWN_SUBJECT_EXCLUSION = "당사는 당기 중 나래 Studio를 청산하여 종속기업에서 제외하였습니다."


@pytest.mark.parametrize(("candidate", "source"), [
    ("회사는 가람 Holdings의 지분 100%를 보유하고 있다.", COMPANY_SUBJECT_EXCLUSION),
    ("회사는 가람 Holdings 지분 전부를 소유한다.", COMPANY_SUBJECT_EXCLUSION),
    ("회사는 다온 Tech의 지분 30%를 보유하고 있다.", COMPANY_SUBJECT_EXCLUSION),
    ("당사는 가람 Holdings 지분 100%를 보유하고 있습니다.", OWN_SUBJECT_EXCLUSION),
], ids=["회사_지분_100", "회사_지분_전부", "회사_다른법인_30", "당사_지분_100"])
def test_P11_회사가_주어인_제외_문장은_회사를_제외_법인으로_만들지_않는다(candidate, source):
    """F12 — 문두 주어가 회사 자신이면 제외된 법인은 목적어다."""

    assert scope_problem(candidate, {"6": source}) == ""


def test_P11_법인이_주어인_제외_문장은_그_법인의_지분_단정을_계속_막는다():
    source = "나래 Studio는 당기 중 청산되어 연결범위에서 제외되었습니다."
    assert scope_problem("회사는 나래 Studio의 지분 100%를 보유하고 있다.", {"6": source}) == UNBOUND


#: 제외 법인 자신이 주어인 서술 경로의 제외 문장.
ENTITY_SUBJECT_EXCLUSION = "가람 Holdings는 경과규정에 따라 종속기업에서 제외되었습니다."
F12_STILL_BLOCKED = pytest.mark.parametrize(("candidate", "exclusion_source"), [
    ("회사는 종속기업 가람 Holdings를 보유하고 있으며, 가람 Holdings는 100% 지분으로 소유되고 있다.",
     FLAT_NOTE),
    ("회사는 종속기업 가람 Holdings를 두고 있다.", FLAT_NOTE),
    ("회사는 종속기업 가람 Holdings 등을 보유하고 있다.", FLAT_NOTE),
    ("가람 Holdings는 회사의 연결재무제표 작성 대상 종속기업이다.", FLAT_NOTE),
    ("회사는 가람 Holdings를 종속기업으로 두고 있다.", FLAT_NOTE),
    ("회사는 가람 Holdings의 지분 100%를 보유하고 있다.", FLAT_NOTE),
    ("가람 Holdings는 회사의 종속기업입니다.", FLAT_NOTE),
    ("가람 Holdings는 회사의 종속기업이다.", ENTITY_SUBJECT_EXCLUSION),
    ("회사는 가람 Holdings를 100% 지분으로 소유하고 있다.", ENTITY_SUBJECT_EXCLUSION),
], ids=["각주_실측_모양", "각주_두고", "각주_등", "각주_연결대상_종속기업이다", "각주_종속기업으로_두고",
        "각주_지분_100", "각주_종속기업입니다", "서술_종속기업이다", "서술_100지분으로_소유"])


@F12_STILL_BLOCKED
def test_P11_회사_주어_제외_문장이_함께_있어도_제외_법인_단정은_막는다(candidate, exclusion_source):
    """F12 수정은 자기 지칭 주어만 건너뛴다 — 진짜 제외 법인(각주 표식·법인 주어 서술)의
    현재 소속·지분 단정은 회사 주어 제외 문장을 함께 인용해도 그대로 막힌다.

    회사 주어 문장을 «먼저» 둔다. 원문은 넣은 순서대로 대조되므로, 그 문장을 만나자마자
    판정을 끝내는 과잉 수정(자기 지칭 주어면 곧바로 통과)도 이 순서여야 드러난다."""

    sources = {"5": COMPANY_SUBJECT_EXCLUSION, "6": exclusion_source}
    assert scope_problem(candidate, sources) == UNBOUND


# ══ 2026-09-23 B 수정 재검토 반영 — 과거 판정 되돌이(R1)·제외 서술의 태(R3) ══
# R1: 과거 판정 정규식의 가운데 과거 어미 묶음이 선택이라, 되돌이가 마지막 「었」 하나로
#     「종속기업으로 두었다」·「편입되었다」·「두었고」까지 대과거로 읽었다(HEAD는 막던 꼴).
#     분류 이름을 앞에 둔 경계 시험(「종속기업 X를 보유했다」)은 꼬리가 「다」만 남아 이 결함을 못 봤다.

SIMPLE_PAST_AFTER_CLASS = pytest.mark.parametrize("candidate", [
    "회사는 가람 Holdings를 종속기업으로 두었다.",
    "회사는 가람 Holdings를 자회사로 두었다.",
    "가람 Holdings는 회사의 종속기업으로 편입되었다.",
    "회사는 가람 Holdings를 종속기업으로 두었고 지금도 지배한다.",
], ids=["종속기업으로_두었다", "자회사로_두었다", "종속기업으로_편입되었다", "두었고_지금도"])


@NOTES
@SIMPLE_PAST_AFTER_CLASS
def test_P11_분류가_뒤에_오는_단순_과거_완료도_현재일_수_있어_막는다(note, candidate):
    assert scope_problem(candidate, {"6": note}) == UNBOUND
    context = _context({"related": RELATED}, {"fn": note})
    assert document_entity_scope_problem(candidate, (context,)) == UNBOUND


@NOTES
@pytest.mark.parametrize("candidate", [
    "종속기업 가람 Holdings를 보유하던 회사는 지분을 정리했다.",
    "회사는 종속기업 가람 Holdings를 두었었다.",
    "회사는 가람 Holdings를 종속기업으로 두었지만 지금은 지분이 없다.",
    "회사는 가람 Holdings를 종속기업으로 두었는데 지금은 지분이 없다.",
], ids=["보유하던", "분류앞_두었었다", "두었지만", "두었는데"])
def test_P11_과거_어미를_거친_회상_대과거_양보는_계속_통과한다(note, candidate):
    assert scope_problem(candidate, {"6": note}) == ""
    context = _context({"related": RELATED}, {"fn": note})
    assert document_entity_scope_problem(candidate, (context,)) == ""


# R3: 능동 제외 서술(「회사는 X를 … 제외하였습니다」)의 주어는 제외한 쪽이다. 자기 지칭 주어만
#     건너뛰면(F12) 정작 제외된 목적어 X의 현재 단정이 통과하고, 목록 밖 주어(연결회사·지배기업·
#     회사 이름)는 다시 제외 법인이 되어 회사의 다른 지분 문장을 막는다. 태로 가른다.

@pytest.mark.parametrize(("candidate", "source"), [
    ("회사는 나래 Studio의 지분 100%를 보유하고 있다.", COMPANY_SUBJECT_EXCLUSION),
    ("회사는 종속기업 나래 Studio를 보유하고 있다.", COMPANY_SUBJECT_EXCLUSION),
    ("나래 Studio는 회사의 종속기업이다.", COMPANY_SUBJECT_EXCLUSION),
    ("나래 Studio는 현재 연결 대상이다.", OWN_SUBJECT_EXCLUSION),
    ("회사는 나래 Studio 지분 30%를 보유하고 있다.",
     "회사는 당기 중 나래 Studio 지분을 전량 처분하여 연결범위에서 제외하였습니다."),
], ids=["회사주어_목적어_지분", "회사주어_목적어_종속기업보유", "회사주어_목적어_종속기업이다",
        "당사주어_목적어_연결대상", "의_없는_지분_목적어"])
def test_P11_능동_제외_문장은_목적어_법인의_현재_단정을_막는다(candidate, source):
    assert scope_problem(candidate, {"6": source}) == UNBOUND


@pytest.mark.parametrize(("candidate", "source"), [
    ("연결회사는 다온 Tech의 지분 30%를 보유하고 있다.",
     "연결회사는 당기 중 나래 Studio의 지분을 전량 처분하여 연결범위에서 제외하였습니다."),
    ("지배기업은 다온 Tech의 지분 30%를 보유하고 있다.",
     "지배기업은 당기 중 나래 Studio의 지분을 전량 처분하여 연결범위에서 제외하였습니다."),
    ("다온전자는 다온 Tech의 지분 30%를 보유하고 있다.",
     "다온전자는 당기 중 나래 Studio의 지분을 전량 처분하여 연결범위에서 제외하였습니다."),
    ("연결회사는 다온 Tech의 지분 30%를 보유하고 있다.",
     "연결회사는 당기 중 보유 주식을 처분하여 연결범위에서 제외하였습니다."),
], ids=["연결회사_주어", "지배기업_주어", "회사이름_주어", "일반_목적어뿐"])
def test_P11_능동_제외_문장의_주어는_어떤_낱말이든_제외_법인이_아니다(candidate, source):
    assert scope_problem(candidate, {"6": source}) == ""


def test_P11_목적어_없는_능동_제외는_주어를_제외_법인으로_본다():
    """문법이 흐트러진 공시(「X는 … 제외하였습니다」)도 수동처럼 주어를 제외 법인으로 읽는다."""

    source = "나래 Studio는 당기 중 종속기업에서 제외하였습니다."
    assert scope_problem("회사는 나래 Studio의 지분 100%를 보유하고 있다.", {"6": source}) == UNBOUND
