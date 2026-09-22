"""회계정책 주석 상용구가 8장 밖의 본문도 채우지 못하게 막는지 확인한다.

여기 실린 «차단»·«통과» 문장은 모두 실제 산출 PDF 2건
(주식회사 뤼튼테크놀로지스 2026-09-22, (주)메디라인액티브코리아 2026-09-18)에
인쇄된 글자 그대로다. 재구성본이 아니다 — 재구성본으로 맞춘 규칙은 실제
조각에서 안 걸린다.

시험이 지키는 것:
  ① 실제 차단 대상 12문장이 전부 걸린다(각각 «의도한 규칙»으로).
  ② 실제 정상 문장 13개가 전부 통과한다.
  ③ 경계 사례(「… 이용하는 시점에 인식된다」)는 통과한다 — 수익원 설명이다.
  ④ 혼합 항목은 통과하되 혼합으로 관측된다.
  ⑤ 실제 `verify_report` 경로에서 9·5·1·2장 본문이 실제로 빠지고 사유가 남는다.
  ⑥ 8장(culture)은 기존 경로·기존 사유 코드를 그대로 쓴다.
  ⑦ 화폐 금액·회사 사건이 든 «회사 고유» 문장 5개는 면제로 통과한다 —
     면제가 없으면 막히던 문장이라는 것까지 함께 단정한다.
"""

from __future__ import annotations

from hashlib import sha256
import json
import re

import pytest

from src.features.composer.accounting_policy_guard import (
    accounting_policy_exemptions,
    accounting_policy_matched_rules,
    accounting_policy_mixed,
    accounting_policy_problem,
    accounting_policy_rules_ignoring_exemptions,
)
from src.features.composer.culture_guard import culture_accounting_policy_problem
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


# ★ 기준값은 리터럴이다 — 생산 상수를 import 해 비교하면 값이 조용히 바뀌어도
#   시험이 초록인 순환 검증이 된다.
EXPECTED_REASON = "accounting_policy_boilerplate"

# ── 실제 PDF에 인쇄된 «차단 대상» 문장과 걸려야 할 규칙 범주 ──────────────
BLOCKED_CASES: tuple[tuple[str, str], ...] = (
    (
        "회사는 금융자산이나 금융부채를 최초 인식시 공정가치로 측정하며, 최초 "
        "인식 후에는 상각후원가로 측정하고 있고, 상각후원가로 측정하는 금융자산의 "
        "손상 발생에 대한 객관적인 증거가 있는 경우 손상차손을 인식하며 유가증권을 "
        "제외한 회수가 불확실한 금융자산은 합리적이고 객관적인 기준에 따라 산출한 "
        "대손추산액을 대손충당금으로 설정한다고 밝혔다.",
        "금융상품측정",
    ),
    (
        "회사는 수익관련보조금을 받는 경우 당기의 손익에 반영하되, 수익관련보조금을 "
        "사용하기 위해 특정의 조건을 충족해야 하는 경우에는 그 조건을 충족하기 전에 "
        "받은 수익관련보조금은 선수금으로 계상하며, 특정의 비용을 보전할 목적으로 "
        "지급되는 경우에는 해당 특정비용과 상계처리하고 대응되는 비용이 없는 경우 "
        "회사의 주된 영업활동과 직접적인 관련성이 있다면 영업수익으로, 그렇지 "
        "않다면 영업외수익으로 인식한다고 밝혔다.",
        "정부보조금",
    ),
    (
        "회사는 현금및현금성자산을 큰 거래비용 없이 현금으로 전환이 용이하고 "
        "이자율 변동에 따른 가치변동이 중요하지 않은 유가증권 및 단기금융상품으로서 "
        "취득당시 만기일이 3개월 이내에 도래하는 것으로 분류하고 있다고 공시했다.",
        "현금성자산정의",
    ),
    (
        "회사는 회수가 불확실한 매출채권등에 대하여 합리적이고 객관적인 기준에 따라 "
        "산정한 대손추산액을 대손충당금으로 설정하며, 상거래에서 발생한 채권에 대한 "
        "대손상각비는 영업비용으로, 기타 채권에 대한 대손상각비는 영업외비용으로 "
        "계상하고 있다고 밝혔다.",
        "대손충당금",
    ),
    (
        "회사는 중소기업 분류에 따라 이연법인세 회계처리를 적용하지 않으며 "
        "법인세법에 의한 납부액만 계상하고 있는데, 당기말 현재 납부할 법인세가 없는 "
        "상태이다.",
        "이연법인세",
    ),
    (
        "회사는 일반기업회계기준을 적용하여 재무제표를 작성하고 있으며, 2025년 1월 "
        "1일로 개시하는 회계기간부터 신규로 적용한 제정 또는 개정 기준서는 없다.",
        "회계기준적용",
    ),
    (
        "회사의 재무제표는 일반기업회계기준에 따라 중요성의 관점에서 공정하게 "
        "표시되고 있으며, 2026년 3월 31일자 주주총회에서 최종 승인될 예정이다.",
        "회계기준적용",
    ),
    (
        "회사의 수익은 재화의 판매, 용역의 제공 및 이자수익 등으로 구성되어 있으며, "
        "재화의 소유에 따른 위험과 보상이 구매자에게 이전되고 수익금액을 신뢰성 있게 "
        "측정할 수 있을 때 수익으로 인식한다.",
        "수익인식기준",
    ),
    (
        "회사의 재고자산은 제품, 상품, 원재료로 구성되며, 총평균법을 적용하여 "
        "취득원가로 계상하고 매 기말에 실지재고조사를 통해 기록을 조정하고 있다.",
        "재고자산평가",
    ),
    (
        "당사는 부채상환을 포함하여 합리적으로 예상되는 영업자금수요를 충당할 수 "
        "있는 유동성을 예측하고 관리하고 있습니다.",
        "유동성관리",
    ),
    (
        "콘텐츠 매출은 사용자가 인공지능 콘텐츠를 제공받는 시점에 인식되며, "
        "미수행의무는 선수수익으로 계상된다.",
        "수익인식기준",
    ),
    # 기존 8장 가드가 이미 알던 «순수 회계 측정» 절 — 규칙을 두 벌로 만들지 않고
    # 그 함수를 그대로 호출해 재사용한다. 장이 8장이 아니어도 막혀야 한다.
    (
        "매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
        "인식하는 간편법을 적용하며, 신용위험 특성과 연체일을 기준으로 구분하여 "
        "기대신용손실률을 산출한다.",
        "순수회계측정",
    ),
)

# ── 실제 PDF에 인쇄된 «통과해야 할» 문장 ──────────────────────────────────
PASSING_TEXTS: tuple[str, ...] = (
    # 경계 사례 — 「완료하는 방식으로 발생」·「이용하는 시점에 인식」은 수익원
    # 설명이기도 하다. 인식 «요건» 표지가 없으므로 막지 않는다.
    "용역 매출은 고객이 요청한 인공지능 소프트웨어 개발 프로젝트를 완료하는 "
    "방식으로 발생하며, 콘텐츠 매출은 사용자가 인공지능 콘텐츠를 이용하는 시점에 "
    "인식된다.",
    "회사의 주된 영업수익은 인공지능 소프트웨어 개발과 관련된 용역 매출과 "
    "인공지능 콘텐츠 매출로 구성된다.",
    "주식회사 뤼튼테크놀로지스는 2021년 4월 22일 설립되어 서울특별시 서초구에 "
    "본점을 두고 있으며, 인공지능 소프트웨어와 인공지능 콘텐츠 개발 및 공급을 "
    "주요 사업으로 영위하고 있다. 회사는 설립 이후 수 차례의 유상증자를 통해 "
    "자본금을 확충했으며, 당기말 현재 자본금은 15백만원이고 최대주주는 이세영이다.",
    "회사는 일본 법인인 Wrtn Technologies Japan을 종속기업으로 두고 있으며, "
    "당기 중 해당 종속기업에 대여금 형태로 자금을 지원했다.",
    "2026년 3월 20일 이사회 결의에 따라 미국법인 New AI Entertainment Inc.의 "
    "설립을 결정했으며, 납입 예정 자본금은 1 USD이다.",
    "회사는 금융기관과 일반대출 약정을 체결했으며, 신용보증기금과 서울보증보험으로부터 "
    "차입금 지급보증과 지급보증보험 등의 보증을 제공받고 있다.",
    "메디라인액티브코리아는 의료기기 제조 및 판매업을 영위하며, 제품 매출이 전체 "
    "수익의 대부분을 차지하고 상품 매출이 부수적 역할을 한다.",
    # 「보조금」이 있어도 «회계처리 표지»가 없으면 사실 서술이다.
    "회사는 2025년 3월 31일에 중소벤처기업부 산하 스마트제조혁신추진단과 "
    "'2025년도 정부일반형 스마트공장 구축사업' 협약을 체결하여 정부보조금을 "
    "수령하고 관련 시스템 구축을 진행 중이다.",
    "회사는 수입신용장 발행을 통해 해외로부터 원재료를 조달하고 있으며, 기업은행, "
    "신한은행, 하나은행 등 금융기관과 수입신용장 약정을 체결하고 있다.",
    # 「인식하고 있다」가 있어도 인식 «요건»이 없으면 사실 서술이다.
    "회사는 영업외수익으로 이자수익과 임대료수입을 인식하고 있다.",
    "재무 담당부서가 위험관리 정책을 수립하고 이사회가 감독한다.",
    # 8장 보존 문장(회귀) — 새 가드는 이 문장을 건드리지 않는다.
    "통합 준법경영시스템을 운영하여 리스크 평가 및 통제·관리방법 교육을 실시하고 "
    "부서별 리스크 평가를 수행한다.",
    # ★ 저장소가 「반드시 보존」으로 못 박아 둔 문장
    #   (`test_culture_compensation_accounting.PRESERVED_PROCEDURE_TEXT`).
    #   회계 어휘가 있어도 «보상위원회가 검토하고 승인한다»는 실제 절차 서술이다.
    #   새 가드가 이 문장을 막으면 8장이 지켜 온 경계가 다른 장에서 무너진다.
    "은행은 성과연동형 주식보상을 현금결제방식으로 회계처리하며 부채의 공정가치를 "
    "재측정하여 보상원가에 반영하는 방식을 보상위원회가 매 결산기마다 검토하고 "
    "승인한다.",
)

# ── 금액·사건이 든 «회사 고유» 문장 (면제 대상) ──────────────────────────
# ⚠️ 출처를 정직하게 적는다 — 아래 5문장은 PDF에 인쇄된 글자 «그대로»가 아니라,
#    독립 검토(2026-09-22 D2)가 실제 가드 함수에 직접 먹여 과차단을 실측한
#    문장이다. 5장 「당면 과제」의 유동성위험 노출액 문장이 실제로 이 모양이다.
#    형식은 (문장, 면제 이름, 면제가 «없었다면» 걸렸을 규칙)이다 — 마지막 값이
#    있어야 「원래 막히던 것이 풀렸다」를 단정할 수 있다.
EXEMPT_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "당기말 현재 회사가 유동성위험에 노출된 금융부채의 잔액은 1,234백만원이며 "
        "1년 이내 만기가 도래한다.",
        "화폐금액",
        "유동성관리",
    ),
    (
        "회사는 당기말 이연법인세자산 3,059백만원을 인식하고 있다.",
        "화폐금액",
        "이연법인세",
    ),
    (
        "회사는 정부보조금 15억원을 영업외수익으로 인식했다.",
        "화폐금액",
        "정부보조금",
    ),
    (
        "당기말 현금및현금성자산은 3,120백만원으로 3개월 이내 만기 예금을 "
        "포함한다.",
        "화폐금액",
        "현금성자산정의",
    ),
    # 금액이 없어도 «변경했다»는 그 회사에 실제로 일어난 일이다.
    (
        "회사는 2025년부터 재고자산 평가 회계정책을 총평균법에서 선입선출법으로 "
        "변경했다.",
        "회사사건",
        "재고자산평가",
    ),
)

# ── 숫자가 있어도 화폐가 아닌 절 (면제 사유가 아니다) ────────────────────
NON_MONETARY_NUMBER_CLAUSES: tuple[str, ...] = (
    "취득당시 만기일이 3개월 이내에 도래하는 것으로 분류하고 있다",
    "일반기업회계기준 제31장에 따른 중소기업 회계처리 특례를 적용하고 있다",
    "1년 내에 완료되는 용역에 대해 제공을 완료한 날에 수익으로 인식한다",
    "2025년 1월 1일로 개시하는 회계기간부터 신규로 적용한 기준서는 없다",
    # 「원」으로 시작하는 다른 낱말(원칙·원재료·원가)은 화폐 단위가 아니다.
    # 특히 «원가»·«원재료»는 회계 주석에 흔해서 반드시 걸러져야 한다.
    "제1원칙에 따라 5원재료를 3원가로 계상한다",
    # 낱말만으로는 «사건»이 아니다 — 「전환이 용이하다」는 현금성자산 정의다.
    "현금으로 전환이 용이하고 이자율 변동에 따른 가치변동이 중요하지 않다",
)

MIXED_TEXT = (
    "회사의 주된 영업수익은 인공지능 소프트웨어 개발과 관련된 용역 매출과 "
    "인공지능 콘텐츠 매출로 구성된다. 용역 매출은 중소기업 회계처리 특례에 따라 "
    "1년 내에 완료되는 용역에 대해 제공을 완료한 날에 수익으로 인식하며, 콘텐츠 "
    "매출은 고객이 인공지능 콘텐츠를 제공받는 시점에 수익을 인식한다. 회사는 "
    "중소기업기본법상의 중소기업으로 분류되어 일반기업회계기준 제31장에 따른 "
    "중소기업 회계처리 특례를 적용하고 있다."
)

CULTURE_KEEP_TEXT = (
    "통합 준법경영시스템을 운영하여 리스크 평가 및 통제·관리방법 교육을 실시하고 "
    "부서별 리스크 평가를 수행한다."
)


# ══════════════════════════════════════════════════════════
# ① 단위 판정
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("text,rule", BLOCKED_CASES, ids=range(len(BLOCKED_CASES)))
def test_실제_회계정책_문장은_사유코드로_차단된다(text: str, rule: str):
    assert accounting_policy_problem(text) == EXPECTED_REASON
    # «걸리기만» 하면 안 된다 — 의도한 규칙이 걸렸는지까지 본다. 안 그러면
    # 엉뚱한 규칙이 우연히 잡아 준 것을 «지켜 준다»고 착각하게 된다.
    assert accounting_policy_matched_rules(text) == (rule,)
    # 전부 상용구인 항목은 «혼합»이 아니다 — 두 신호가 동시에 켜지면 안 된다.
    assert accounting_policy_mixed(text) is False


@pytest.mark.parametrize("text", PASSING_TEXTS, ids=range(len(PASSING_TEXTS)))
def test_실제_사업_사실_문장은_통과한다(text: str):
    assert accounting_policy_problem(text) == ""
    assert accounting_policy_mixed(text) is False


@pytest.mark.parametrize(
    "text,exemption,blocked_rule", EXEMPT_CASES, ids=range(len(EXEMPT_CASES))
)
def test_회사_고유_금액이_든_문장은_면제로_통과한다(
    text: str, exemption: str, blocked_rule: str
):
    """금액·사건이 있는 절은 상용구로 세지 않는다.

    ★ 「통과한다」만 단정하면 규칙이 애초에 안 걸려서 통과한 것과 구분이
      안 된다. 그래서 «면제가 없었다면 걸렸을 규칙»까지 함께 단정한다 —
      이 값이 빈칸이 되는 날 면제는 아무것도 지켜 주지 않는 장식이 된다.
    """

    assert accounting_policy_rules_ignoring_exemptions(text) == (blocked_rule,)
    assert accounting_policy_exemptions(text) == (exemption,)
    assert accounting_policy_problem(text) == ""
    # 모든 절이 면제라 «혼합»도 아니다 — 두 신호가 동시에 켜지면 안 된다.
    assert accounting_policy_mixed(text) is False
    assert accounting_policy_matched_rules(text) == ("",)


@pytest.mark.parametrize("text", NON_MONETARY_NUMBER_CLAUSES)
def test_비화폐_숫자는_면제_사유가_아니다(text: str):
    """「3개월」·「제31장」·「1년 내」·「원가」는 화폐 금액이 아니다.

    숫자만 보고 면제하면 실측 차단 12문장 중 여럿이 그대로 되살아난다.
    """

    assert accounting_policy_exemptions(text) == ("",)


@pytest.mark.parametrize("text,rule", BLOCKED_CASES, ids=range(len(BLOCKED_CASES)))
def test_기존_차단_문장에는_면제가_하나도_생기지_않는다(text: str, rule: str):
    """면제를 들인 뒤에도 실측 차단 12문장의 어느 절도 면제되지 않는다."""

    assert set(accounting_policy_exemptions(text)) == {""}, rule
    # 면제가 안 생겼으니 판정도 그대로여야 한다 — 이 두 줄이 함께 있어야
    # 「면제는 안 생겼는데 차단이 풀렸다」는 다른 회귀까지 잡는다.
    assert accounting_policy_problem(text) == EXPECTED_REASON


def test_혼합_항목은_통과하되_혼합으로_관측된다():
    # 회사 고유 사실이 같은 항목에 섞여 있으면 통째로 지우지 않는다.
    assert accounting_policy_problem(MIXED_TEXT) == ""
    assert accounting_policy_mixed(MIXED_TEXT) is True
    assert accounting_policy_matched_rules(MIXED_TEXT) == (
        "", "회계처리방법", "회계기준적용",
    )


@pytest.mark.parametrize("text", ("", "   ", "\n\n"))
def test_빈_항목은_차단하지_않는다(text: str):
    # all(()) 은 참이다 — 절이 하나도 없는 항목이 «전부 상용구»로 읽히면 안 된다.
    assert accounting_policy_problem(text) == ""
    assert accounting_policy_mixed(text) is False


def test_한_절짜리_순수회계측정은_두_가드가_같은_답을_낸다():
    """쌍둥이 규칙 대조 — 규칙을 복사하지 않고 «함께 불러» 비교한다.

    culture 가드가 «절 하나라도» 순수 회계 측정이면 막는 문장 중 절이 하나뿐인
    것은, 새 전 장 공통 가드에서도 반드시 막혀야 한다. 한쪽만 고쳐져 두 잣대가
    되는 것을 이 대조가 잡는다.
    """

    단절_문장 = tuple(
        text for text, _rule in BLOCKED_CASES
        if len(accounting_policy_matched_rules(text)) == 1
    )
    assert 단절_문장, "한 절짜리 차단 표본이 없으면 이 대조는 아무것도 증명하지 않는다"
    for text in 단절_문장:
        if culture_accounting_policy_problem(text):
            assert accounting_policy_problem(text) == EXPECTED_REASON, text


def test_실제_승인절차_문장은_두_가드_모두_통과시킨다():
    """반대 방향 대조 — 넓은 쪽이 좁은 쪽의 보존 경계를 깨뜨리지 않는다.

    8장 가드는 「보상위원회가 검토하고 승인한다」처럼 절차가 결속된 절을 일부러
    살린다. 새 가드가 그 문장을 막으면 8장이 지켜 온 경계가 다른 장에서 무너진다.
    """

    from src.features.composer.tests.test_culture_compensation_accounting import (
        PRESERVED_PROCEDURE_TEXT,
    )

    assert culture_accounting_policy_problem(PRESERVED_PROCEDURE_TEXT) == ""
    assert accounting_policy_problem(PRESERVED_PROCEDURE_TEXT) == ""
    assert PRESERVED_PROCEDURE_TEXT in PASSING_TEXTS, (
        "저장소 원문이 바뀌면 이 시험의 표본도 함께 바뀌어야 한다"
    )


# ══════════════════════════════════════════════════════════
# ② 실제 verify_report 경로 통합 (추가 AI 호출 0)
# ══════════════════════════════════════════════════════════


SECTION_BLOCKED_TEXTS = {
    # 9장 「회사가 밝힌 차별점」이 실제로 이 4문장으로 채워졌다.
    "competitive_position": BLOCKED_CASES[0][0],
    # 5장 「당면 과제」가 실제로 이 문장으로 채워졌다.
    "current_challenges": BLOCKED_CASES[4][0],
    # 1장이 실제로 수익인식 기준 문장으로 채워졌다.
    "identity": BLOCKED_CASES[7][0],
    # 2장이 실제로 재고자산 평가방법 문장으로 채워졌다.
    "business_model": BLOCKED_CASES[8][0],
}
KEEP_TEXT = (
    "회사의 주된 영업수익은 인공지능 소프트웨어 개발과 관련된 용역 매출과 "
    "인공지능 콘텐츠 매출로 구성된다."
)


def _approval(calls: list):
    """프롬프트의 장·인용을 그대로 반사하는 가짜 검수자 — AI 호출 0회."""

    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 검수 입력에 후보가 없습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section,
             "근거": list(item.citations), "결과": "참"}
            for item in items
        ]}, ensure_ascii=False)

    return ask


@pytest.mark.parametrize("section_id", tuple(SECTION_BLOCKED_TEXTS))
def test_verify_report가_모든_장_본문에서_회계정책_상용구를_뺀다(section_id: str):
    blocked = SECTION_BLOCKED_TEXTS[section_id]
    report = ComposedReport((ComposedSection(section_id, (
        ComposedSentence(blocked, ("policy",), "확인"),
        ComposedSentence(KEEP_TEXT, ("fact",), "확인"),
    )),))
    fragments = (
        CollectedFragment("policy", "회계정책 주석", blocked),
        CollectedFragment("fact", "사업의 개요", KEEP_TEXT),
    )
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
    )
    assert [s.text for s in checked.sections[0].sentences] == [KEEP_TEXT]
    assert len(calls) == 1, "추가 AI 호출이 생기면 안 된다"
    # 사유가 남아야 «왜» 빠졌는지 되짚을 수 있다. 원문은 남지 않는다.
    assert [d["reason_code"] for d in diagnostics] == [EXPECTED_REASON]
    assert diagnostics[0]["section_id"] == section_id
    assert diagnostics[0]["kind"] == "본문"
    assert diagnostics[0]["candidate_sha256"] == sha256(blocked.encode()).hexdigest()
    assert blocked not in repr(diagnostics)


def test_금액이_든_회사_고유_문장은_실제_경로에서도_살아남는다():
    """5장 「당면 과제」의 유동성위험 «노출액» 문장이 실제로 이 모양이다.

    단위 판정만 초록이고 운영 경로에서 그대로 빠지면 아무것도 고쳐지지 않는다.
    """

    text = EXEMPT_CASES[0][0]
    report = ComposedReport((ComposedSection("current_challenges", (
        ComposedSentence(text, ("liquidity",), "확인"),
    )),))
    fragments = (CollectedFragment("liquidity", "재무위험관리", text),)
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
    )
    assert [s.text for s in checked.sections[0].sentences] == [text]
    assert [d["reason_code"] for d in diagnostics] == []


def test_8장은_기존_경로와_기존_사유코드를_그대로_쓴다():
    """새 가드는 culture 장을 건드리지 않는다 — 사유 코드가 섞이면 안 된다."""

    accounting = BLOCKED_CASES[11][0]
    report = ComposedReport((ComposedSection("culture", (
        ComposedSentence(accounting, ("policy",), "확인"),
        ComposedSentence(CULTURE_KEEP_TEXT, ("compliance",), "확인"),
    )),))
    fragments = (
        CollectedFragment("policy", "회계정책 주석", accounting),
        CollectedFragment("compliance", "공식 운영절차", CULTURE_KEEP_TEXT),
    )
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
    )
    assert [s.text for s in checked.sections[0].sentences] == [CULTURE_KEEP_TEXT]
    assert [d["reason_code"] for d in diagnostics] == [
        "culture_accounting_policy_misplaced"
    ]


def test_혼합_항목은_실제_경로에서도_살아남는다():
    """일부 절만 상용구인 항목을 통째로 지우면 회사 고유 사실까지 사라진다."""

    report = ComposedReport((ComposedSection("identity", (
        ComposedSentence(MIXED_TEXT, ("mixed",), "확인"),
    )),))
    fragments = (CollectedFragment("mixed", "사업의 개요", MIXED_TEXT),)
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
    )
    assert [s.text for s in checked.sections[0].sentences] == [MIXED_TEXT]
    assert [d["reason_code"] for d in diagnostics] == []


def test_요약_문맥에는_걸지_않는다():
    """요약은 본문 문장을 다시 쓰는 자리다 — 본문에서 걸리면 충분하다.

    여기서 걸면 «본문에서는 살아남았는데 요약에서만 빠지는» 두 잣대가 생긴다.
    """

    from src.features.composer.verify import verify_sentences

    accounting = BLOCKED_CASES[7][0]
    sentences = (ComposedSentence(accounting, ("policy",), "확인"),)
    fragments = (CollectedFragment("policy", "회계정책 주석", accounting),)
    calls = []
    checked = verify_sentences(sentences, fragments, None, _approval(calls))
    assert [s.text for s in checked] == [accounting]
