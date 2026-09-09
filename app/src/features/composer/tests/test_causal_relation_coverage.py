"""independent 검토(Q1~Q4)에서 확인한 우회를 회귀로 고정하고, 정상 사례가
그 수정으로 함께 깨지지 않는지 지키는 시험.

이 파일은 direct_support_problem/causal_relation_problem/claims_cause라는
기존 공개 함수만 쓴다. direct_support_constants.py의 상수·새 함수는 아무것도
import하지 않는다 — Opus가 진행 중인 패치가 사유 코드나 내부 상수 이름을
바꿔도 이 시험이 깨지지 않게 하기 위해서다. 관계 배열의 필드 키
("관계"/"근거"/"원인"/"결과"/"원문"/"유형")는 검수 응답 JSON 계약 자체이므로
리터럴로 그대로 쓴다.

★ 반환값이 빈 문자열("")이라는 것은 «이 가드가 거절하지 않았다»는 뜻일 뿐,
  그 후보 문장이 전체적으로 의미상 올바르다고 승인됐다는 뜻이 아니다. 다른
  의미 검수(수치·범위·문화 등)는 별도로 계속 판단한다. 이 시험도 causal_
  relation_problem 하나의 계약만 검증하며, 그 밖의 검증기의 통과 여부는
  다루지 않는다.

빨간(red) 시험은 실제 반례를 그대로 회귀로 옮긴 것이며, 지금 이 소스
(SHA 상단 인계 문서 참고)에서는 실패해야 정상이다 — 기대를 반대로 바꿔
억지로 초록으로 만들지 않았다. 초록(green) 시험은 정상 문장이 지금도
막히지 않는다는 것을 지키는 안전장치다.
"""

from src.features.composer.direct_support import (
    causal_relation_problem,
    claims_cause,
    direct_support_problem,
)


def _relation(source_id, cause, effect, quote, kind="인과"):
    return {"근거": source_id, "원인": cause, "결과": effect, "원문": quote, "유형": kind}


# ══════════════════════════════════════════════════════════
# 빨강 — 실제 반례를 그대로 회귀로 옮김. 지금 소스에서는 실패해야 정상이다.
# ══════════════════════════════════════════════════════════

def test_bare_direction_words_do_not_bypass_subject_mismatch():
    """Q1: 원인·결과 칸에 방향 낱말만("감소"/"증가") 넣으면, 후보의 실제
    주체(비용/이익)와 원문의 실제 주체(수요/비용)가 완전히 달라도 통과해서는
    안 된다."""

    candidate = "비용 감소가 이익 증가의 주요 원인이다"
    source = "수요 감소가 비용 증가의 주요 원인이다"
    entries = {"관계": [_relation("s", "감소", "증가", source)]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result != "", "방향 낱말만으로는 다른 주체의 인과를 빌려오면 안 된다"


def test_second_causal_claim_without_relation_is_not_silently_approved():
    """Q2: 후보 문장에 인과 단언이 두 개 있는데 관계 항목을 하나만(첫 번째
    주장만) 내면, 근거 없는 두 번째 주장까지 통째로 승인되면 안 된다."""

    candidate = (
        "매출 확대가 이익 증가의 주요 원인이다. "
        "또한 비용 절감이 손실 감소의 핵심 요인이다."
    )
    source = "매출 확대가 이익 증가의 주요 원인이다."
    entries = {"관계": [_relation("s", "매출 확대", "이익 증가", source)]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result != "", "두 번째 인과 주장이 근거 없이 함께 승인되면 안 된다"


def test_duplicate_relation_does_not_cover_second_claim():
    """Q2 변형: 첫 번째 관계 항목을 그대로 복제해 두 번 내도(둘 다 첫 번째
    주장만 뒷받침), 두 번째 주장은 여전히 근거가 없다 — 항목 개수가 늘었다고
    승인되면 안 된다."""

    candidate = (
        "매출 확대가 이익 증가의 주요 원인이다. "
        "또한 비용 절감이 손실 감소의 핵심 요인이다."
    )
    source = "매출 확대가 이익 증가의 주요 원인이다."
    entries = {"관계": [
        _relation("s", "매출 확대", "이익 증가", source),
        _relation("s", "매출 확대", "이익 증가", source),
    ]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result != "", "같은 관계를 중복 제출해도 두 번째 주장을 대신 증명하지 못한다"


def test_hedge_negation_source_is_recognized_as_denial():
    """Q3-C/Q3b: 원문이 «~라고 보기는 어렵다»처럼 완곡하게 부정하면, 후보가
    긍정 필드만 냈다고 통과시키면 안 된다(명시적 «아니다» 부정과 같은 취급이
    필요하다)."""

    candidate = "비용 감소가 이익 증가의 주요 원인이다"
    source = "비용 감소가 이익 증가의 원인이라고 보기는 어렵다."
    entries = {"관계": [_relation("s", "비용 감소", "이익 증가", source)]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result != "", "완곡 부정(«보기는 어렵다»)도 명시 부정과 같이 거절해야 한다"


def test_empty_source_id_is_not_accepted_as_valid_grounding():
    """Q4: 근거 id가 빈 문자열이고 sources_mapping에 우연히 빈 문자열 키가
    있어도, 그것을 «실제 근거를 댔다»로 인정하면 안 된다."""

    candidate = "비용 감소가 이익 증가의 주요 원인이다"
    source = "비용 감소가 이익 증가의 주요 원인이다"
    entries = {"관계": [_relation("", "비용 감소", "이익 증가", source)]}

    result = causal_relation_problem(candidate, {"": source}, entries)

    assert result != "", "빈 근거 id는 sources에 빈 키가 있어도 유효한 근거로 인정하면 안 된다"


def test_negated_causal_claim_is_not_gated_as_positive_assertion():
    """추가 반례: 「~가 ~의 주요 원인이 «아니다»」는 인과를 «부정»하는
    문장이지 «단언»이 아니다. 그런데 claims_cause()의 어휘 표지는 극성을
    구분하지 않아 이런 부정 문장까지 관계 근거를 요구해 버린다. 부정 후보는
    긍정 인과 단언 가드의 대상이 아니어야 한다 — 관계 항목이 없다는 이유로
    거절되면 안 된다."""

    candidate = "비용 감소가 이익 증가의 주요 원인이 아니다"

    # 요청 안내에서도 긍정 인과 단언으로 분류하지 않아야 한다. 수정 전의
    # True를 기대값으로 고정하면 알려진 분류 오류를 시험이 요구하게 된다.
    assert claims_cause(candidate) is False

    result = causal_relation_problem(candidate, {"s": "아무 상관 없는 문장이다."}, {"관계": []})

    assert result == "", "부정 인과 후보는 관계 근거가 없다는 이유로 거절되면 안 된다"


# ══════════════════════════════════════════════════════════
# 초록 — 정상 문장은 지금도 막히지 않아야 한다(안전장치).
# ══════════════════════════════════════════════════════════

def test_full_phrase_grounding_with_matching_subject_passes():
    """정상 완전한 원인·결과: 주어를 포함한 전체 구절이 실제로 원문과
    일치하면 direct_support_problem(공개 진입점)이 통과시켜야 한다."""

    candidate = "비용 감소가 이익 증가의 주요 원인이다"
    source = "비용 감소가 이익 증가의 주요 원인이다"
    entries = {"관계": [_relation("s", "비용 감소", "이익 증가", source)]}

    result = direct_support_problem(candidate, {"s": source}, entries)

    assert result == ""


def test_result_before_cause_word_order_passes():
    """결과가 먼저 오는 정상 어순(«이익 증가는 비용 감소가 주요 원인이다»)도
    문법 표지로 방향을 잡으므로 통과해야 한다."""

    candidate = "이익 증가는 비용 감소가 주요 원인이다"
    source = "이익 증가는 비용 감소가 주요 원인이다"
    entries = {"관계": [_relation("s", "비용 감소", "이익 증가", source)]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result == ""


def test_company_attributed_causal_claim_passes():
    """«회사는 ~라고 밝혔다» 식으로 귀속한 인과 단언도, 근거가 실제로
    일치하면 통과해야 한다."""

    candidate = "회사는 비용 감소가 이익 증가의 주요 원인이라고 밝혔다"
    source = "비용 감소가 이익 증가의 주요 원인이다"
    entries = {"관계": [_relation("s", "비용 감소", "이익 증가", source)]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result == ""


def test_two_causal_claims_each_with_own_grounding_pass():
    """정상 대조: 인과 단언이 두 개여도, «각각» 자기 근거를 제대로 대면
    (Q2/중복 회귀와 달리) 전부 통과해야 한다."""

    candidate = (
        "매출 확대가 이익 증가의 주요 원인이다. "
        "또한 비용 절감이 손실 감소의 주요 원인이다."
    )
    source_1 = "매출 확대가 이익 증가의 주요 원인이다."
    source_2 = "비용 절감이 손실 감소의 주요 원인이다."
    entries = {"관계": [
        _relation("s1", "매출 확대", "이익 증가", source_1),
        _relation("s2", "비용 절감", "손실 감소", source_2),
    ]}

    result = causal_relation_problem(candidate, {"s1": source_1, "s2": source_2}, entries)

    assert result == ""


def test_negation_in_unrelated_independent_clause_does_not_block():
    """같은 원문 문장 안에 «다른 독립절»의 부정이 있어도, 실제로 결속된
    인과 절 자체가 부정되지 않았다면 정상 인과는 지워지면 안 된다."""

    candidate = "비용 감소가 이익 증가의 주요 원인이다"
    source = "비용 감소가 이익 증가의 주요 원인이다; 매출 하락은 없었다."
    entries = {"관계": [
        _relation("s", "비용 감소", "이익 증가", "비용 감소가 이익 증가의 주요 원인이다"),
    ]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result == ""


def test_general_difficulty_as_genuine_cause_passes():
    """"어려움"이 헤지 부정이 아니라 «실제 원인 내용어»로 쓰인 정상 문장을
    지키는 안전장치다 — 다음에 완곡 부정(§ 위 test_hedge_negation_...) 어휘를
    보강할 때, "어렵다"류를 통째로 부정 표지에 넣으면 이 정상 문장까지
    거절될 위험이 있다. 이 시험이 그 과잉 수정을 잡아야 한다."""

    candidate = "원자재 조달의 어려움이 비용 증가의 주요 원인이다"
    source = "원자재 조달의 어려움이 비용 증가의 주요 원인이다"
    entries = {"관계": [_relation("s", "원자재 조달의 어려움", "비용 증가", source)]}

    result = causal_relation_problem(candidate, {"s": source}, entries)

    assert result == ""
