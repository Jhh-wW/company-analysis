"""culture 장의 순수 회계 인식·측정 정책 오배치 — 실제 SM 결함 재현과 정상 경계.

실제 실패 원문은 .local-artifacts/resume-20260909-culture-accounting-correction/
00-false-positive-repro-before-correction.txt와 .../prior-fix-folder-snapshot/
00-real-failure-text.txt에 그대로 보존했다. 여기서는 회사명·연도·인용번호를
빼고 일반화한 문장으로 같은 판정을 재확인한다(하드코딩 금지).

이 파일은 총괄이 지적한 결함(재무팀 검토·이사회 승인처럼 실제 조직 절차와
회계처리가 한 문장 안에 공존할 때 잘못 차단하던 문제)을 고친 뒤의 시험이다.
"""

import inspect

from src.features.composer.culture_constants import CULTURE_ACCOUNTING_POLICY_MISPLACED
from src.features.composer.culture_guard import (
    culture_accounting_policy_problem,
    culture_problem,
)

# 실제 SM 결함 문단(회사명·인용번호 제거, 원문 문구는 그대로).
_REAL_FAILURE_TEXT = (
    "회사는 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
    "인식하는 간편법을 적용하며, 신용위험 특성과 연체일을 기준으로 구분하여 "
    "기대신용손실률을 산출한다."
)

# 총괄이 제시한 반례 — 같은 회계 항목을 다루지만 실제 담당·검토·승인 절차가
# 같은 문장 안에 결속돼 있다. 이 문장은 반드시 보존돼야 한다.
_BOUND_PROCEDURE_TEXT = (
    "재무팀이 손실충당금 산출 시 신용위험 특성과 연체일을 기준으로 검토하고 "
    "이사회가 승인한다."
)


def test_existing_culture_problem_misses_the_real_failure_paragraph():
    """참고 경계: 기존 culture_problem은 조직문화 추론 어휘가 없는 이 문단을
    구조적으로 놓친다 — 문제없음("")을 돌려준다. 이것이 이번 별도 함수가
    필요한 이유다(culture_problem 자체는 이번에 수정하지 않았다)."""
    assert culture_problem(_REAL_FAILURE_TEXT, {}) == ""


def test_new_guard_blocks_the_real_failure_paragraph():
    """실제 SM 실패 문단은 여전히 차단된다 — 절차 결속이 전혀 없기 때문이다."""
    assert culture_accounting_policy_problem(_REAL_FAILURE_TEXT) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_new_guard_does_not_need_sources_because_grounding_is_not_the_issue():
    """원문과 정확히 같아도(=근거는 맞아도) 판정은 바뀌지 않는다 — 함수가 sources를
    아예 받지 않는 설계 자체가 그 사실을 보장한다."""
    signature = inspect.signature(culture_accounting_policy_problem)
    assert list(signature.parameters) == ["text"]


# ══════════════════════════════════════════════════════════
# ① 실제 조직 절차 6종 — 회계 인식·측정 어휘 자체가 없어 애초에 대상이 아니다
# ══════════════════════════════════════════════════════════

_PRESERVED_PROCEDURE_PARAGRAPHS = [
    "회사는 이사회를 중심으로 위험관리 체계를 구축하고 감독하며, 재무위험관리 활동은 "
    "주로 지배기업의 재무 담당부서에서 주관하고 전사통합적 관점에서 재무위험 관리정책 "
    "수립 및 식별 활동을 수행한다.",
    "당사는 자본관리의 목표로 계속기업으로서의 존속능력 유지, 자본조달비용 최소화를 "
    "통한 주주이익 극대화, 적정한 자본구조 유지를 설정하고 부채비율 및 유동비율을 "
    "기초로 자본을 관리하고 있다.",
    "회사는 영업활동 현금흐름 창출을 통해 투자 지출 또는 경영 자금 운용에 필요한 현금 "
    "소요량을 충족해 왔으며, 중장기 경영계획 및 단기 경영전략을 통해 현금흐름을 "
    "모니터링하고 일반적인 예상 운영비용을 충당할 수 있는 현금을 보유하고 있다.",
    "회사는 금융시장의 변동성에 초점을 맞춘 전반적인 위험관리정책을 수립하여 재무성과에 "
    "미치는 부정적 영향을 최소화하는 데 중점을 두고 있으며, 특정 위험을 회피하기 위하여 "
    "파생상품을 이용하고 있다.",
    "회사는 국제적 영업활동으로 인해 미국달러화, 일본 엔화, 중국 위안화 관련 환율 변동 "
    "위험에 노출되어 있으며, 이러한 외환위험을 관리하기 위해 파생상품을 활용하고 있다.",
    "에스엠엔터테인먼트는 공식 채용 안내에서 파트타임, 인턴, 프리랜서 경력에 대해 직무 "
    "연관성을 고려하여 판단하며 채용 과정에서 다양한 직무 경험을 참고할 수 있다고 밝혔다.",
]


def test_preserved_organizational_procedure_paragraphs_are_not_blocked():
    """이사회감독·자본관리목표·부채비율관리·현금흐름모니터링·파생상품 위험관리정책·
    채용 안내 등 실제 조직 운영 절차 문단 6종은 재무 단어가 섞여 있어도 차단되지
    않는다 — 회계 인식·측정 어휘(손실충당금/간편법 + 신용위험 특성·연체일)가
    한 절 안에 함께 있지 않기 때문이다."""
    for paragraph in _PRESERVED_PROCEDURE_PARAGRAPHS:
        assert culture_accounting_policy_problem(paragraph) == ""


# ══════════════════════════════════════════════════════════
# ② 새 공존 사례 — 조직 절차와 회계처리가 «같은 문장»에 결속되면 보존한다
# ══════════════════════════════════════════════════════════

def test_bound_review_and_approval_procedure_in_same_sentence_is_not_blocked():
    """총괄이 지적한 반례. 재무팀 검토·이사회 승인이 같은 문장 안에서 그
    회계처리(손실충당금 산출)와 결속돼 있으므로 차단하지 않는다."""
    assert culture_accounting_policy_problem(_BOUND_PROCEDURE_TEXT) == ""


def test_bound_oversight_verb_alone_also_preserves_the_sentence():
    """검토·승인 대신 감독 동사로 결속된 경우도 같은 원리로 보존한다."""
    text = (
        "리스크관리위원회가 손실충당금 산출 방식과 신용위험 특성·연체일 기준 적용을 "
        "정기적으로 감독한다."
    )
    assert culture_accounting_policy_problem(text) == ""


def test_long_same_sentence_gap_between_wordings_still_preserves_bound_procedure():
    """정규식에 글자수 상한(예: .{0,10})을 다시 넣지 않았는지 확인한다 — 같은
    절 안이라면 회계 어휘와 절차 동사 사이 거리가 아무리 멀어도 결속으로
    인정해야 한다."""
    text = (
        "재무팀이 매출채권 및 계약자산과 관련하여 전체기간 기대신용손실을 손실충당금으로 "
        "인식하는 간편법을 적용하는 과정에서, 신용위험 특성별 분류와 연체일 산정 절차를 "
        "포함한 세부 방법론 전반에 대해, 각 항목별 세부 데이터와 과거 손실 실적을 "
        "종합적으로 반영하여 다각도로 면밀히 검토하고, 최종적으로 이사회가 승인한다."
    )
    assert culture_accounting_policy_problem(text) == ""


def test_full_width_and_combining_unicode_variants_still_bind_the_procedure():
    """전각 문자·조합형 자모가 섞여도(NFKC 정규화 대상) 절차 결속 판단이
    깨지지 않는지 확인한다."""
    text = (
        "재무팀이　손실충당금　산출　시　신용위험　특성과　"
        "연체일을　기준으로　검토하고　이사회가　승인한다."
    )
    assert culture_accounting_policy_problem(text) == ""


# ══════════════════════════════════════════════════════════
# ③ 부정 사례 — 관계없는 절이나 부정된 절차로 면제를 만들 수 없다
# ══════════════════════════════════════════════════════════

def test_unrelated_later_sentence_mentioning_board_does_not_rescue_the_earlier_clause():
    """회계 정책 문장 뒤에 이사회를 언급하는 «별개» 문장이 와도, 그 문장이
    회계처리 자체와 결속되지 않았다면 앞 문장은 여전히 차단된다."""
    text = (
        "회사는 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
        "인식하는 간편법을 적용하며, 신용위험 특성과 연체일을 기준으로 구분하여 "
        "기대신용손실률을 산출한다. 이사회는 매년 정기적으로 위험관리 전반을 감독한다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_negated_review_and_approval_does_not_rescue_the_sentence():
    """검토·승인이 부정되면(실제로 수행되지 않았으면) 절차 결속으로 인정하지
    않는다 — 회계 서술만 남으므로 차단한다."""
    text = (
        "재무팀이 손실충당금 산출 시 신용위험 특성과 연체일을 기준으로 검토하지 않으며 "
        "이사회 승인도 받지 않는다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_board_noun_without_a_governance_verb_does_not_rescue_the_sentence():
    """"이사회"·"담당자" 같은 명사가 있다는 것만으로는 면제하지 않는다 —
    검토/승인/감독 동사가 실제로 있어야 한다."""
    text = (
        "이사회 산하 재무팀은 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 "
        "손실충당금으로 인식하는 간편법을 적용하며, 신용위험 특성과 연체일을 기준으로 "
        "구분하여 기대신용손실률을 산출한다."
    )
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


# ══════════════════════════════════════════════════════════
# ④ 어휘 부분집합 경계 — 과도 삭제 방지
# ══════════════════════════════════════════════════════════

def test_recognition_wording_alone_without_measurement_basis_is_not_blocked():
    """손실충당금·간편법 같은 인식 어휘만 있고 측정 기준(신용위험 특성·연체일)이
    없으면 과도 삭제를 피하기 위해 차단하지 않는다."""
    text = "회사는 매출채권에 대해 손실충당금을 계상하는 간편법을 적용하고 있다."
    assert culture_accounting_policy_problem(text) == ""


def test_credit_characteristic_alone_without_overdue_basis_is_not_blocked():
    """신용위험 특성 표현만 있고 연체일 기준·인식 어휘가 없으면 차단하지 않는다."""
    text = "회사는 거래상대방의 신용위험 특성을 참고 자료로만 공시하고 있다."
    assert culture_accounting_policy_problem(text) == ""


def test_overdue_basis_alone_without_recognition_is_not_blocked():
    """연체일 표현만 있고 손실충당금/간편법 등 인식 어휘가 없으면 차단하지 않는다."""
    text = "회사는 연체일을 기준으로 고객 등급을 안내하고 있다."
    assert culture_accounting_policy_problem(text) == ""


def test_generic_operating_expense_reserve_wording_is_not_confused_with_loss_allowance():
    """'충당'이라는 낱말만으로는 걸리지 않는다 — '손실충당금' 전체 문자열이 필요하다
    (실제 보존 대상 문단3의 '운영비용을 충당할 수 있는' 표현과 동일한 함정)."""
    text = "회사는 예상 운영비용을 충당할 수 있는 현금을 보유하고 있다."
    assert culture_accounting_policy_problem(text) == ""


def test_full_period_expected_credit_loss_wording_alone_is_recognized_as_recognition_wording():
    """'전체기간 기대신용손실' 표현 자체만으로도 인식 어휘로 잡히는지 별도 확인한다
    (측정 기준까지 같은 절에 함께 있고 절차 결속이 없을 때만 최종 차단으로
    이어진다)."""
    text = "회사는 전체기간 기대신용손실을 인식하며, 신용위험 특성과 연체일을 기준으로 산출한다."
    assert culture_accounting_policy_problem(text) == CULTURE_ACCOUNTING_POLICY_MISPLACED


def test_scope_does_not_depend_on_company_or_citation_numbers():
    """회사명·연도·인용번호를 붙이거나 빼도 판정이 같다 — 정규식이 그런 값을
    전혀 쓰지 않기 때문이다."""
    with_hardcoded_values = (
        "2025년 새빛산업은 매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 "
        "손실충당금으로 인식하는 간편법을 적용하며, 신용위험 특성과 연체일을 기준으로 "
        "구분하여 기대신용손실률을 산출한다고 밝혔다. [999][1000]"
    )
    assert culture_accounting_policy_problem(with_hardcoded_values) == CULTURE_ACCOUNTING_POLICY_MISPLACED
