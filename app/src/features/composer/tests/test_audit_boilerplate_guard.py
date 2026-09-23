"""감사보고서 «감사인 표준 문구»가 회사의 과제·대응·차별점으로 둔갑하는 것을 막는 안전망.

★ 2026-09-23 5차 실측(독립 검수 상-3): 「감사인의 책임」 단락을 인용한 5장 산문·도식과
  감사의견 문구를 옮긴 9장 문장을 검수 AI가 전부 «참»으로 통과시켰다.
자료는 감사기준서 예시 문형을 새로 풀어 쓴 익명 글이다(실제 회사 원문을 옮기지 않았다).
"""

from __future__ import annotations

import json

import pytest

from src.features.composer import scope_guard, verify
from src.features.composer.accounting_policy_constants import ACCOUNTING_POLICY_BOILERPLATE
from src.features.composer.audit_boilerplate_guard import (
    RULE_ACT_TRANSFER,
    RULE_CITATION_BOILERPLATE,
    audit_boilerplate_problem,
    audit_boilerplate_rule,
)
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.grounding import grounding_problem
from src.features.composer.grounding_constants import REVIEW_GROUNDING_REJECTED, TABLE_SOURCE_ID
from src.features.composer.grounding_rewrite_constants import GROUNDING_REWRITE_EXCLUDED_REASONS
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.scope_guard import flow_scope_problem, scope_problem
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS

SECTION = "current_challenges"
AUDITOR_RESPONSIBILITY = (
    "- 부정이나 오류로 인한 재무제표의 중요왜곡표시위험을 식별하고 평가하며, 그 위험에 "
    "대응하는 감사절차를 설계하고 수행합니다. 그리고 감사의견의 근거로 충분하고 적합한 "
    "감사증거를 입수합니다. 부정은 공모나 내부통제 무력화가 개입될 수 있어 부정으로 인한 "
    "중요한 왜곡표시를 발견하지 못할 위험이 오류로 인한 위험보다 큽니다.- 상황에 맞는 "
    "감사절차를 설계하기 위하여 감사와 관련된 내부통제를 이해합니다."
)
AUDIT_OPINION = (
    "감사의견 우리는 주식회사 가나다의 재무제표를 감사하였습니다. 우리의 의견으로는 별첨된 "
    "재무제표는 회사의 재무상태와 재무성과를 중요성의 관점에서 공정하게 표시하고 있습니다. "
    "감사의견근거 우리는 대한민국의 회계감사기준에 따라 감사를 수행하였습니다."
)
COMPANY_OVERVIEW = (
    "회사의 개요 주식회사 가나다는 2019년에 설립되어 산업용 센서 제조를 주요 사업으로 "
    "영위하고 있습니다. 회사는 일반기업회계기준을 적용하고 있습니다."
)
COMPANY_ICFR = (
    "회사는 내부회계관리제도를 운영하고 있으며, 평가 결과 중요성의 관점에서 효과적으로 "
    "운영되고 있어 재무제표의 중요한 왜곡표시를 예방하는 합리적인 확신을 제공합니다."
)
KAM_MIXED = (
    "회사의 매출은 다수 고객과 맺은 구독 계약에서 발생하며, 경영진의 성과 목표 때문에 "
    "수익이 과대계상될 위험이 있습니다. 해당 사항에 대응하기 위하여 우리가 수행한 주요 "
    "감사절차는 다음과 같습니다."
)
GOING_CONCERN = (
    "회사는 당기 중 영업손실 12,345백만원이 발생하였고 유동부채가 유동자산을 5,678백만원 "
    "초과하고 있습니다. 이러한 상황은 계속기업으로서의 존속능력에 유의적 의문을 제기할 만한 "
    "중요한 불확실성이 존재함을 나타냅니다."
)
LIQUIDITY_NOTE = "유동성 위험이란 회사가 금융부채 관련 의무를 이행하는 데 어려움을 겪을 위험입니다."
CHALLENGE_CELLS = (
    "지금 겪는 과제: 부정이나 오류로 인한 재무제표의 중요한 왜곡표시 위험",
    "회사가 밝힌 대응: 상황에 적합한 감사절차 설계, 충분하고 적합한 감사증거 입수",
)


# ── ① 인용 상용구 — 인용한 원문이 전부 감사인 표준 문구뿐 ───────────────────

@pytest.mark.parametrize(("text", "sources"), [
    ("회사는 재무제표 작성 과정에서 부정이나 오류로 인한 중요한 왜곡표시 위험에 직면해 있다.",
     {"12": AUDITOR_RESPONSIBILITY}),
    ("특히 부정으로 인한 왜곡표시 위험은 내부통제 무력화가 개입될 수 있어 오류로 인한 위험보다 크다.",
     {"12": AUDITOR_RESPONSIBILITY}),
    ("회사는 계속기업 존속능력과 관련한 중요한 불확실성이 있는지 평가하는 절차를 거치고 있다.",
     {"12": AUDITOR_RESPONSIBILITY}),
    ("회사는 일반기업회계기준에 따라 재무제표를 작성하고 있다.", {"3": AUDIT_OPINION}),
    # 모든 후보에 함께 실리는 실적표 원문은 «인용»으로 세지 않는다.
    ("회사는 재무제표 작성 과정에서 중요한 왜곡표시 위험에 직면해 있다.",
     {"12": AUDITOR_RESPONSIBILITY, TABLE_SOURCE_ID: "매출액 100억원 영업이익 10억원"}),
], ids=["과제_왜곡표시", "과제_부정위험", "대응_계속기업평가", "감사의견_단락만", "실적표_동반"])
def test_감사인_문구만_인용한_후보는_무엇을_썼든_막힌다(text, sources):
    assert audit_boilerplate_rule(text, sources) == RULE_CITATION_BOILERPLATE
    assert audit_boilerplate_problem(text, sources) == ACCOUNTING_POLICY_BOILERPLATE


# ── ② 행위 전가 — 감사인의 감사 행위·감사의견 문형을 감사인 주어 없이 적음 ────

@pytest.mark.parametrize(("text", "sources"), [
    ("이러한 위험에 대응하기 위해 회사는 상황에 적합한 감사절차를 설계하고 감사증거를 입수하는 "
     "방식으로 대처하고 있다.", {"9": KAM_MIXED}),
    ("회사는 재무상태와 재무성과를 중요성의 관점에서 공정하게 표시하고 있다.",
     {"1": COMPANY_OVERVIEW, "3": AUDIT_OPINION}),
    ("회사는 합리적인 확신을 얻기 위해 감사와 관련된 내부통제를 이해하고 있다.", {"9": KAM_MIXED}),
    ("회사가 밝힌 대응: 감사절차 설계, 감사증거 입수", {"9": KAM_MIXED}),
], ids=["대응_감사절차_감사증거", "차별점_감사의견", "합리적확신_내부통제이해", "도식칸_명사형"])
def test_감사인의_행위를_회사의_것으로_옮기면_섞인_인용이어도_막힌다(text, sources):
    assert audit_boilerplate_rule(text, sources) == RULE_ACT_TRANSFER
    assert audit_boilerplate_problem(text, sources) == ACCOUNTING_POLICY_BOILERPLATE


# ── 경계 — 회사가 주어인 서술·회사 고유 사정·감사인 귀속은 막지 않는다 ─────────

@pytest.mark.parametrize(("text", "sources"), [
    ("회사는 내부회계관리제도를 운영하며 재무제표의 중요한 왜곡표시를 예방하는 합리적인 확신을 "
     "제공하고 있다.", {"5": COMPANY_ICFR}),
    ("회사의 매출은 구독 계약에서 발생해 수익이 과대계상될 위험이 있다.", {"9": KAM_MIXED}),
    ("회사는 영업손실과 유동부채 초과로 계속기업 존속능력에 유의적 의문이 제기됐다.",
     {"7": GOING_CONCERN}),
    ("외부감사인은 충분하고 적합한 감사증거를 입수했다고 밝혔다.", {"9": KAM_MIXED}),
    ("회사는 적정 감사의견을 받았다.", {"1": COMPANY_OVERVIEW}),
    ("회사는 내부 감사절차를 설계해 운영한다.", {"5": COMPANY_ICFR}),
    ("매출은 전년 대비 20% 증가했다.", {TABLE_SOURCE_ID: "매출액 120억원 전년 100억원"}),
    ("회사는 유동성 위험에 노출되어 있다.", {"12": AUDITOR_RESPONSIBILITY, "18": LIQUIDITY_NOTE}),
], ids=["회사_내부회계관리제도", "핵심감사사항_회사위험", "계속기업_회사사정", "감사인_귀속",
        "감사의견_유형_사실", "내부_감사절차", "실적표만", "상용구와_회사조각_혼합"])
def test_회사_서술과_감사인_귀속과_혼합_인용은_막지_않는다(text, sources):
    assert audit_boilerplate_rule(text, sources) == ""
    assert audit_boilerplate_problem(text, sources) == ""


# ── 배선 — 이미 모든 검수 후보가 지나는 진입점에서 걸린다(verify.py 무수정) ────

def test_범위_진입점과_도식_칸과_근거_결속이_같은_사유를_낸다():
    sources = {"12": AUDITOR_RESPONSIBILITY}
    text = "회사는 재무제표 작성 과정에서 중요한 왜곡표시 위험에 직면해 있다."
    assert scope_problem(text, sources) == ACCOUNTING_POLICY_BOILERPLATE
    assert flow_scope_problem(CHALLENGE_CELLS, sources) == ACCOUNTING_POLICY_BOILERPLATE
    assert grounding_problem(text, sources, {"결과": "참"}) == ACCOUNTING_POLICY_BOILERPLATE


def test_사유는_공유_닫힌_목록과_재작성_제외_목록에_이미_있다():
    assert REVIEW_SCOPE_ITEMS[ACCOUNTING_POLICY_BOILERPLATE] == "장별 작성범위"
    assert ACCOUNTING_POLICY_BOILERPLATE in GROUNDING_REWRITE_EXCLUDED_REASONS


# ── 검수 통합 — 검수 AI가 «참»이라 해도 공개로 새지 않는다 ─────────────────────

def _true_reviewer(calls, *, grouped=False):
    def ask(prompt):
        calls.append(str(prompt))
        row = {"번호": 1, "근거대조": "원문 대조", "결과": "참"}
        if grouped:
            row.update({"장": SECTION, "근거": ["12"]})
        return json.dumps({"판정": [row]}, ensure_ascii=False)
    return ask


def _fragments(text=AUDITOR_RESPONSIBILITY):
    return {"12": CollectedFragment("12", "공시", text)}


def _flat(text, fragments, *, table_source=""):
    problems: dict[int, str] = {}
    calls: list[str] = []
    item = verify._ReviewItem(1, ComposedSentence(text, ("12",), "확인"), SECTION)
    verdicts = verify._ask_verdicts(
        _true_reviewer(calls), (item,), fragments, "", table_source, grounding_problems=problems,
    )
    return verdicts, problems, calls


def _grouped(text, fragments, *, table_source=""):
    problems: dict[int, str] = {}
    calls: list[str] = []
    item = verify._GroupedReviewItem(
        1, SECTION, "문장", ("12",), sentence=ComposedSentence(text, ("12",), "확인"),
    )
    verdicts = verify._ask_grouped_verdicts(
        _true_reviewer(calls, grouped=True), (item,), fragments, None, grounding_problems=problems,
    )
    return verdicts, problems, calls


RESPONSE_TRANSFER = (
    "이러한 위험에 대응하기 위해 회사는 상황에 적합한 감사절차를 설계하고 감사와 관련된 "
    "내부통제를 이해하며, 충분하고 적합한 감사증거를 입수하는 방식으로 대처하고 있다."
)


#: ① 규칙에만 걸리는 문장 — 행위 전가 표지가 없어 «인용 상용구» 겹만 이 문장을 지킨다.
RISK_RESTATEMENT = "회사는 재무제표 작성 과정에서 부정이나 오류로 인한 중요한 왜곡표시 위험에 직면해 있다."


@pytest.mark.parametrize("review", [_flat, _grouped], ids=["flat", "grouped"])
@pytest.mark.parametrize("text", [RISK_RESTATEMENT, RESPONSE_TRANSFER], ids=["과제_재진술", "대응_전가"])
def test_검수가_참이어도_감사인_책임_단락에_기댄_5장_문장은_탈락한다(review, text):
    verdicts, problems, calls = review(text, _fragments())
    assert verdicts == {1: REVIEW_GROUNDING_REJECTED}
    assert problems == {1: ACCOUNTING_POLICY_BOILERPLATE}
    assert len(calls) == 1  # 추가 AI 호출 없음


def test_실적표_원문이_함께_실려도_평문_검수에서_탈락한다():
    verdicts, problems, _calls = _flat(
        RISK_RESTATEMENT, _fragments(), table_source="매출액 100억원 영업이익 10억원",
    )
    assert verdicts == {1: REVIEW_GROUNDING_REJECTED}
    assert problems == {1: ACCOUNTING_POLICY_BOILERPLATE}


@pytest.mark.parametrize("review", [_flat, _grouped], ids=["flat", "grouped"])
def test_회사_과제_문장은_그대로_통과한다(review):
    company = "원재료 가격 상승이 회사의 당면 과제이며, 회사는 공급처를 다변화하는 대책으로 대응하고 있습니다."
    verdicts, problems, _calls = review("회사는 원재료 가격 상승에 공급처 다변화로 대응하고 있다.",
                                        _fragments(company))
    assert verdicts == {1: "참"} and problems == {}


def test_대조군_가드를_끄면_같은_문장이_참으로_통과한다(monkeypatch):
    """이 파일의 통합 차단 시험이 «이 가드 덕분에» 초록인지 확인하는 대조군(실측 재현)."""

    monkeypatch.setattr(scope_guard, "audit_boilerplate_problem", lambda text, sources: "")
    verdicts, problems, _calls = _flat(RESPONSE_TRANSFER, _fragments())
    assert verdicts == {1: "참"} and problems == {}


def test_도식_검수가_참이어도_감사인_책임_단락에_기댄_과제_대응_행은_빠진다():
    diagnostics: list[dict] = []
    row = FlowRow(CHALLENGE_CELLS, ("12",))
    draft = ComposedReport((ComposedSection(SECTION, (), flow_rows=(row,)),))

    def ask(prompt):
        return json.dumps({"판정": [{"번호": 1, "결과": "참"}]}, ensure_ascii=False)

    result, problems = check_diagrams(
        draft, (CollectedFragment("12", "공시", AUDITOR_RESPONSIBILITY),), ask,
        diagnostics=diagnostics,
    )
    assert result.sections[0].flow_rows == ()
    assert any(ACCOUNTING_POLICY_BOILERPLATE in problem for problem in problems)
    assert diagnostics[0]["reason_code"] == ACCOUNTING_POLICY_BOILERPLATE
    assert diagnostics[0]["verification_items"] == ("장별 작성범위",)


# ══ 2026-09-23 독립 검토 반영 — 인용 원문 구조 표지 관문(F1)·좁힌 판단(F2·F3)·전용 어휘(F6) ══

#: 감사 업종 회사의 자기 소개(익명) — 짧은 감사 표지만 있고 감사보고서 구조 표지는 없다.
AUDIT_INDUSTRY = (
    "당사의 AI 플랫폼은 감사증거 수집과 검토를 자동화합니다. 당사는 연간 300여 개 기업의 "
    "감사보고서를 발행하는 회계법인 고객에게 재무제표감사 일정 관리 기능을 제공합니다."
)
#: 5차 실측 조각 12와 같은 모양 — 머리말은 없고 「감사보고서일까지 입수」만 구조 표지다
#: (R2로 「감사보고서일」 단독 표지를 감사인 문형 셋으로 좁힌 뒤에도 걸리는 꼴).
AUDITOR_CHUNK_WITH_REPORT_DATE = (
    "- 부정이나 오류로 인한 재무제표의 중요왜곡표시위험을 식별하고 평가하며, 그 위험에 대응하는 "
    "감사절차를 설계하고 수행합니다. 우리의 결론은 감사보고서일까지 입수한 감사증거에 기초합니다."
)
#: 머리말도 「감사보고서일」도 없는 감사인 책임 조각 — composer 는 알아보지 못한다(경계).
AUDITOR_CHUNK_WITHOUT_STRUCTURE = (
    "- 부정이나 오류로 인한 재무제표의 중요왜곡표시위험을 식별하고 평가하며, 그 위험에 대응하는 "
    "감사절차를 설계하고 수행합니다. 그리고 감사의견의 근거로 충분하고 적합한 감사증거를 입수합니다."
)
LAWSUIT_NOTE = "보고기간 후 사건으로 감사보고서 발행일 현재 회사가 피소된 소송은 2건입니다."


@pytest.mark.parametrize(("text", "sources"), [
    ("회사는 회계법인에 감사증거 자동화 솔루션을 판매한다.", {"1": AUDIT_INDUSTRY}),
    ("회사는 감사보고서를 발행하는 회계법인 고객에게 소프트웨어를 판매한다.", {"1": AUDIT_INDUSTRY}),
    ("회사는 기업 고객에게 재무제표감사 일정 관리 기능을 제공한다.", {"1": AUDIT_INDUSTRY}),
    ("당사의 AI 플랫폼은 감사증거 수집과 검토를 자동화한다.", {"1": AUDIT_INDUSTRY}),
    ("회사는 고객의 재무제표감사 부담을 줄이는 것을 목적으로 한다.",
     {"1": "우리의 목적은 고객의 재무제표감사 부담을 줄이는 것입니다."}),
], ids=["감사SW_판매", "발행_고객", "재무제표감사_서비스", "플랫폼_자동화", "홈페이지_목적"])
def test_감사_업종_회사의_사업_문장은_감사보고서를_인용하지_않으면_막지_않는다(text, sources):
    assert audit_boilerplate_rule(text, sources) == ""
    assert audit_boilerplate_problem(text, sources) == ""


def test_감사_업종_사업_문장은_검수_경로에서도_참으로_남는다():
    verdicts, problems, _calls = _flat("회사는 회계법인에 감사증거 자동화 솔루션을 판매한다.",
                                       _fragments(AUDIT_INDUSTRY))
    assert verdicts == {1: "참"} and problems == {}


def test_머리말_없이_감사보고서일만_있는_감사인_조각도_막는다():
    """5차 조각 12 모양 — 구조 표지 목록에 긴 문형(「감사보고서일까지 입수」)을 둔 이유."""

    sources = {"12": AUDITOR_CHUNK_WITH_REPORT_DATE}
    assert audit_boilerplate_rule(RISK_RESTATEMENT, sources) == RULE_CITATION_BOILERPLATE


def test_구조_표지가_전혀_없는_감사인_조각은_composer가_알아보지_못한다():
    """경계(알려진 한계) — composer 는 조각 종류를 받지 못한다. 이 조각은 근거 선별이
    문서 종류(감사보고서)로 먼저 뺀다(chapter_evidence 시험이 지킨다)."""

    sources = {"12": AUDITOR_CHUNK_WITHOUT_STRUCTURE}
    assert audit_boilerplate_rule(RISK_RESTATEMENT, sources) == ""


@pytest.mark.parametrize("text", [
    "회사는 재무제표가 공정하게 표시되도록 내부회계관리제도를 운영한다.",
    "내부회계관리제도는 재무제표가 기업회계기준에 따라 작성되고 공정하게 표시되는지에 대한 "
    "합리적인 확신을 제공하기 위해 설계되었다.",
    "회사는 재무보고의 신뢰성에 대한 합리적인 확신을 얻기 위해 내부통제를 운영한다.",
    "회사는 재무제표를 공정하게 표시하고자 내부통제를 운영한다.",
    "재무제표는 기업회계기준에 따라 공정하게 표시되어야 한다.",
    "회사는 재무제표가 공정하게 표시되게 절차를 두고 있다.",
], ids=["표시되도록", "표시되는지", "합리적확신_내부통제운영", "표시하고자", "표시되어야", "표시되게"])
def test_회사가_주어인_내부통제_서술은_감사보고서를_함께_인용해도_막지_않는다(text):
    sources = {"3": AUDIT_OPINION, "5": COMPANY_ICFR}
    assert audit_boilerplate_rule(text, sources) == ""


@pytest.mark.parametrize("text", [
    "회사의 재무상태와 경영성과는 중요성의 관점에서 적정하게 표시되어 있다.",
    "회사의 재무제표는 재무상태를 공정하게 표시한다.",
], ids=["표시되어있다", "표시한다"])
def test_감사의견_판단을_회사_주장으로_옮긴_단정은_여전히_막는다(text):
    sources = {"1": COMPANY_OVERVIEW, "3": AUDIT_OPINION}
    assert audit_boilerplate_rule(text, sources) == RULE_ACT_TRANSFER


def test_감사보고서_발행일_기준_문구는_감사인_행위가_아니다():
    sources = {"3": AUDIT_OPINION, "7": LAWSUIT_NOTE}
    assert audit_boilerplate_rule("감사보고서 발행일 현재 회사는 2건의 소송에 피소되어 있다.", sources) == ""
    assert audit_boilerplate_rule("회사는 감사보고서를 발행하였다.", sources) == RULE_ACT_TRANSFER


@pytest.mark.parametrize(("text", "expected"), [
    ("회사는 전문가적 회의주의를 유지하며 위험에 대응한다.", RULE_ACT_TRANSFER),
    ("회사는 감사범위와 감사시기를 계획해 대응한다.", RULE_ACT_TRANSFER),
    ("회사는 위험에 대응해 주요 감사절차를 수행하고 있다.", RULE_ACT_TRANSFER),
    ("회사는 감사 절차를 계획하고 설계해 위험에 대처한다.", RULE_ACT_TRANSFER),
    ("회사는 감사 절차들을 설계해 위험에 대처한다.", RULE_ACT_TRANSFER),
    ("회사는 협력사 품질 감사절차를 설계해 연 2회 점검한다.", ""),
], ids=["전문가적회의주의", "감사범위와시기", "주요감사절차_수행", "계획하고_설계", "절차들을_설계",
        "품질감사_면제"])
def test_감사인_전용_어휘와_설계_변형은_섞인_인용에서도_막는다(text, expected):
    sources = {"12": AUDITOR_RESPONSIBILITY, "1": COMPANY_OVERVIEW}
    assert audit_boilerplate_rule(text, sources) == expected


# ══ 2026-09-23 B 수정 재검토 반영 — 회사 주석의 «감사보고서일 현재»(R2)·핵심감사사항 머리(R6)·
#    「전문가적 의구심」(R7)·흔한 감사의견 단정 꼴(R4)·설계 표지 사이 글자(R8) ══

#: 소송 주석(익명) — 「감사보고서일 현재」는 회사 주석의 흔한 기준일 문구다(감사인 문형이 아니다).
LITIGATION_NOTE = (
    "보고기간종료일 현재 회사가 피고로 계류중인 소송사건은 2건이며, 감사보고서일 현재 "
    "그 결과를 합리적으로 예측할 수 없습니다."
)
#: 감사보고서 유효 기간 문단(감사기준서 예시 문형을 새로 풀어 씀) — 감사인만 쓰는 문형이다.
AUDITOR_VALIDITY_NOTICE = (
    "이 감사보고서는 감사보고서일 현재로 유효한 것입니다. 따라서 감사보고서일 후 이 보고서를 "
    "열람하는 시점 사이에 재무제표에 중요한 영향을 미칠 수 있는 사건이 발생할 수 있습니다."
)
#: 사업보고서에 옮겨 실린 핵심감사사항 머리 문단(익명 재서술).
KAM_PREAMBLE = (
    "핵심감사사항은 우리의 전문가적 판단에 따라 당기 재무제표감사에서 가장 유의적인 사항들입니다. "
    "해당 사항들은 재무제표 전체에 대한 감사의 관점에서 우리의 의견형성 시 다루어졌으며, "
    "우리는 이런 사항에 대하여 별도의 의견을 제공하지는 않습니다."
)
#: 「전문가적 회의주의」의 실제 번역 변형을 쓴 감사인 문단(익명 재서술).
AUDITOR_SKEPTICISM_VARIANT = (
    "감사기준에 따른 감사의 일부로서 우리는 감사의 전 과정에 걸쳐 전문가적 판단을 수행하고 "
    "전문가적 의구심을 유지하고 있습니다."
)


def test_감사보고서일_현재를_기준일로_쓴_회사_주석은_감사인_문구가_아니다():
    """R2 — 「감사보고서일」 단독 표지는 이 주석을 감사인 문구로 읽어 인용 후보를 지웠다
    (재작성 제외 사유라 곧바로 삭제)."""

    sources = {"7": LITIGATION_NOTE}
    assert audit_boilerplate_rule("회사는 계류 중인 소송 2건의 결과를 예측할 수 없다.", sources) == ""


def test_감사보고서일_현재_주석에_기댄_문장은_검수_경로에서도_참으로_남는다():
    verdicts, problems, _calls = _flat("회사는 계류 중인 소송 2건의 결과를 예측할 수 없다.",
                                       _fragments(LITIGATION_NOTE))
    assert verdicts == {1: "참"} and problems == {}


@pytest.mark.parametrize("text", [AUDITOR_VALIDITY_NOTICE, KAM_PREAMBLE, AUDITOR_SKEPTICISM_VARIANT],
                         ids=["감사보고서일_현재로_유효_후", "핵심감사사항_머리", "전문가적_의구심"])
def test_감사인만_쓰는_문형만_인용한_후보는_막는다(text):
    """R2·R6·R7 — 좁힌 «감사보고서일» 문형과 새 구조 표지로 관문이 열리고 규칙 ①이 막는다."""

    assert audit_boilerplate_rule(RISK_RESTATEMENT, {"9": text}) == RULE_CITATION_BOILERPLATE


def test_전문가적_의구심을_회사의_태도로_옮기면_막는다():
    sources = {"12": AUDITOR_RESPONSIBILITY, "1": COMPANY_OVERVIEW}
    text = "회사는 전문가적 의구심을 유지하며 위험에 대응한다."
    assert audit_boilerplate_rule(text, sources) == RULE_ACT_TRANSFER


@pytest.mark.parametrize("text", [
    "회사의 재무제표는 중요성의 관점에서 공정하게 표시된다.",
    "회사의 재무제표는 중요성의 관점에서 공정하게 표시돼 있다.",
    "회사의 재무상태는 적정하게 표시됐다.",
    "회사는 재무제표를 공정하게 표시했다.",
], ids=["표시된다", "표시돼있다", "표시됐다", "표시했다"])
def test_감사의견_단정의_흔한_어미도_막는다(text):
    """R4 — 단정 어미를 허용 목록으로 적으면 빠지던 꼴."""

    sources = {"1": COMPANY_OVERVIEW, "3": AUDIT_OPINION}
    assert audit_boilerplate_rule(text, sources) == RULE_ACT_TRANSFER


@pytest.mark.parametrize("text", [
    "회사는 재무제표를 공정하게 표시하기 위한 내부통제를 운영한다.",
    "회사는 재무제표가 공정하게 표시될 수 있도록 결산 절차를 점검한다.",
    "회사는 재무제표가 공정하게 표시되지 않을 위험을 관리한다.",
], ids=["표시하기_위한", "표시될_수_있도록", "표시되지_않을"])
def test_목적_가능_부정_꼴은_감사의견_단정이_아니다(text):
    sources = {"1": COMPANY_OVERVIEW, "3": AUDIT_OPINION}
    assert audit_boilerplate_rule(text, sources) == ""


@pytest.mark.parametrize(("text", "expected"), [
    ("회사는 감사 절차와 무관한 설계 변경을 관리한다.", ""),
    ("회사는 감사 절차를 계획 및 설계해 위험에 대처한다.", RULE_ACT_TRANSFER),
    ("회사는 감사절차 설계로 위험에 대응한다.", RULE_ACT_TRANSFER),
], ids=["무관한_설계", "계획_및_설계", "명사형_설계"])
def test_설계_표지의_사이_글자는_조사와_계획만_허용한다(text, expected):
    """R8 — 사이 글자를 아무것이나 6자까지 허용하던 확장은 「감사 절차와 무관한 설계」 같은
    회사 서술까지 감사인 행위로 읽었다."""

    sources = {"12": AUDITOR_RESPONSIBILITY, "1": COMPANY_OVERVIEW}
    assert audit_boilerplate_rule(text, sources) == expected
