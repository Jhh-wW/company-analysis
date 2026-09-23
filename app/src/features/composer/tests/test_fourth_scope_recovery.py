"""4차 실측 후속 — 오귀속·목적 해석 차단과 정상 수익 설명 보존의 대칭 회귀.

실측 표현을 복사하지 않은 익명 반례를 쓴다. 세 결함을 다룬다:
  · L1: 수익인식 절이 없는 인용만으로 완료일 인식이 참으로 승인됨(차단)과,
    올바른 인용·조건의 원칙·특례 설명 보존(대칭).
  · 2장 정상 원칙·1년 특례가 회계정책 상용구로 오제거됨 — 자기 인용 결속
    면제로 보존하되, 순수 상용구·조건 변조·수익원 바꿔치기는 계속 차단.
  · L2: 설립 사실 절에 인용 원문에 없는 목적 해석을 «확인»으로 붙임(차단)과,
    원문이 목적을 실제로 적은 경우의 보존(대칭).
"""

import json
import re

import pytest

from src.features.composer.accounting_policy_constants import (
    REVENUE_STREAM_EXEMPTION_NAME,
)
from src.features.composer.accounting_policy_guard import (
    accounting_policy_exemptions,
    accounting_policy_problem,
    accounting_policy_rules_ignoring_exemptions,
)
from src.features.composer.direct_support import purpose_interpretation_problem
from src.features.composer.direct_support_constants import (
    PURPOSE_INTERPRETATION_UNSUPPORTED,
)
from src.features.composer.grounding import constrain_verdicts
from src.features.composer.scope_guard import scope_problem

# 수익인식 절이 아예 없는 개요형 인용 — «매출»·«인식» 낱말은 있지만 완료·진행
# 기준의 인식 서술이 없다. 낱말 겹침이 술어 근거를 대체하면 안 되는 반례다.
OVERVIEW_SOURCE = (
    "회사는 2020년 1월 설립되어 소프트웨어 개발과 콘텐츠 공급을 주요 사업으로 "
    "영위하고 있으며, 매출 규모가 성장하고 있다. 회사는 금융자산을 최초 "
    "인식시 공정가치로 측정한다."
)

# 수익 구성 나열과 원칙·특례·시점 인식 서술을 함께 담은 자기 인용 —
# 2장 정상 설명이 기대는 원문 꼴이다.
COMPOSITION_SOURCE = (
    "수익인식 회사의 주된 영업수익의 형태는 소프트웨어 개발과 관련된 용역 매출, "
    "콘텐츠 매출 등으로 구성됩니다. 용역 제공으로 인한 수익은 용역제공거래의 "
    "성과를 신뢰성 있게 추정할 수 있을 때 진행기준에 따라 인식합니다. "
    "다만 회사는 회계처리 특례를 적용하여 1년 내의 기간에 완료되는 용역 매출에 "
    "대하여는 용역제공을 완료한 날에 수익으로 인식하고 있습니다. "
    "콘텐츠 매출은 고객이 제공받는 시점에 수익을 인식하고 있으며, 관련 "
    "미수행의무는 선수수익으로 계상하고 있습니다."
)

# 실제 수익원에 결속된 원칙·특례 설명 — 2장에서 보존되어야 하는 정상 후보.
PRINCIPLE_EXCEPTION_TEXT = (
    "용역 매출은 용역제공거래의 성과를 신뢰성 있게 추정할 수 있을 때 진행기준에 "
    "따라 인식하며, 회계처리 특례를 적용하여 1년 내의 기간에 완료되는 용역 "
    "매출에 대하여는 용역제공을 완료한 날에 수익으로 인식한다."
)
CONTENT_RECOGNITION_TEXT = (
    "콘텐츠 매출은 고객이 제공받는 시점에 수익을 인식하며, 관련 미수행의무는 "
    "선수수익으로 계상한다."
)


# ── L1: 인용에 없는 인식 술어는 낱말 겹침으로 승인되지 않는다 ─────────────
@pytest.mark.parametrize("candidate", [
    "회사의 주된 영업수익은 용역 매출과 콘텐츠 매출로 구성되며, 용역 매출은 "
    "용역제공을 완료한 날에 수익으로 인식한다.",
    "용역 매출은 진행기준에 따라 수익을 인식한다.",
])
def test_recognition_basis_without_source_predicate_is_rejected(candidate):
    assert scope_problem(candidate, {"1": OVERVIEW_SOURCE}) == "scope_condition_unbound"


@pytest.mark.parametrize("candidate", [
    PRINCIPLE_EXCEPTION_TEXT,
    CONTENT_RECOGNITION_TEXT,
    # 사업 서술·시점 설명은 기준 표지가 없어 이 검사 대상이 아니다.
    "회사는 소프트웨어 개발 용역과 콘텐츠 공급으로 매출을 올린다.",
])
def test_grounded_recognition_and_business_statements_survive(candidate):
    assert scope_problem(candidate, {"1": COMPOSITION_SOURCE}) == ""


def test_recognition_basis_is_bound_to_the_named_revenue_stream():
    # 독립 검토 확정 반례 — 다른 수익원의 완료 기준을 빌리면 통과하면 안 된다.
    source = (
        "용역 매출은 진행기준으로 인식한다. 콘텐츠 매출은 콘텐츠 제공을 "
        "완료한 날에 수익으로 인식한다."
    )
    borrowed = "용역 매출은 용역 제공을 완료한 날에 수익으로 인식한다."
    assert scope_problem(borrowed, {"1": source}) == "scope_condition_unbound"
    # 같은 원문에서 정당한 콘텐츠 완료 원칙과 용역 진행 원칙은 보존된다.
    assert scope_problem(
        "콘텐츠 매출은 콘텐츠 제공을 완료한 날에 수익으로 인식한다.",
        {"1": source},
    ) == ""
    assert scope_problem("용역 매출은 진행기준으로 인식한다.", {"1": source}) == ""


def test_transaction_facts_without_recognition_markers_are_untouched():
    # 주석에 실제로 있는 대여·비용 거래 서술은 인식 술어 검사와 무관하다.
    source = (
        "회사는 해외 종속기업에 대여금 형태로 자금을 제공하고 있으며, 해당 "
        "종속기업과의 거래에서 영업비용이 발생하였다."
    )
    candidate = (
        "회사는 해외 종속기업에 대여금을 제공하고 있으며, 해당 거래에서 "
        "영업비용이 발생했다."
    )
    assert scope_problem(candidate, {"1": source}) == ""


# ── 2장 정상 원칙·특례 보존 — 자기 인용 결속 면제 ─────────────────────────
def test_principle_and_exception_survive_with_bound_sources():
    # 원문 없이 보면 종전 그대로 상용구다 — 다른 장·요약의 기존 차단은 유지된다.
    assert accounting_policy_problem(PRINCIPLE_EXCEPTION_TEXT) == "accounting_policy_boilerplate"
    # 실제 수익원을 나열한 자기 인용이 함께 오면 2장 설명으로 보존된다.
    assert accounting_policy_problem(
        PRINCIPLE_EXCEPTION_TEXT, {"1": COMPOSITION_SOURCE}
    ) == ""
    assert accounting_policy_problem(
        CONTENT_RECOGNITION_TEXT, {"1": COMPOSITION_SOURCE}
    ) == ""


def test_exemption_actually_did_the_work():
    # 통과가 «규칙 미적중»이 아니라 «면제»였음을 관측값으로 못 박는다.
    rules = accounting_policy_rules_ignoring_exemptions(PRINCIPLE_EXCEPTION_TEXT)
    assert any(rules), rules
    names = accounting_policy_exemptions(
        PRINCIPLE_EXCEPTION_TEXT, {"1": COMPOSITION_SOURCE}
    )
    assert REVENUE_STREAM_EXEMPTION_NAME in names, names


@pytest.mark.parametrize("candidate", [
    # 조건 변조 — 원문의 1년 한정을 2년으로 바꾸면 면제되지 않는다.
    PRINCIPLE_EXCEPTION_TEXT.replace("1년", "2년"),
    # 수익원 바꿔치기 — 다른 수익원의 기준을 옮겨 적으면 면제되지 않는다.
    "콘텐츠 매출은 회계처리 특례를 적용하여 완료한 날에 수익으로 인식한다.",
    # 나열되지 않은 수익원 — 구성 서술에 없는 이름은 면제되지 않는다.
    "임대 매출은 회계처리 특례를 적용하여 1년 내의 기간에 완료되는 경우 "
    "완료한 날에 수익으로 인식한다.",
    # 회계기준 적용 상용구 — 면제 대상 규칙이 아니다.
    "회사는 일반기업회계기준을 적용하는 중소기업으로서 회계처리 특례를 "
    "적용하여 1년 내의 기간에 완료되는 용역 매출에 대해서는 용역제공을 "
    "완료한 날에 수익으로 인식하고 있다.",
    # 이연법인세 상용구 — 수익원 낱말이 있어도 면제 대상 규칙이 아니다.
    "회사는 용역 매출에 관련된 이연법인세를 인식하고 있다.",
])
def test_pure_boilerplate_stays_blocked_even_with_sources(candidate):
    assert accounting_policy_problem(
        candidate, {"1": COMPOSITION_SOURCE}
    ) == "accounting_policy_boilerplate"


def test_company_and_stream_words_alone_do_not_exempt():
    # 「회사는」·「용역」 낱말과 인식 서술이 있어도, 자기 인용에 수익 구성
    # 나열이 없으면 면제되지 않는다.
    recognition_only = (
        "용역 제공으로 인한 수익은 진행기준에 따라 인식합니다. 다만 회사는 "
        "회계처리 특례를 적용하여 1년 내의 기간에 완료되는 용역 매출에 "
        "대하여는 용역제공을 완료한 날에 수익으로 인식하고 있습니다."
    )
    assert accounting_policy_problem(
        PRINCIPLE_EXCEPTION_TEXT, {"1": recognition_only}
    ) == "accounting_policy_boilerplate"


# ── L2: 사실 절에 덧붙인 목적·의미 해석 ──────────────────────────────────
DECISION_ONLY_SOURCE = (
    "회사는 3월 20일 이사회 결의에 따라 해외법인 설립을 결정하였다. 납입 예정 "
    "자본금은 1천 달러이다."
)
PURPOSE_STATED_SOURCE = (
    "회사는 해외 시장 진출을 위해 운영 구조의 확장에 나서 해외법인 설립을 "
    "결정하였다."
)
FACT_WITH_PURPOSE_TEXT = (
    "회사는 3월 20일 이사회 결의에 따라 해외법인 설립을 결정하였으며, 이는 "
    "해외 시장 진출을 위한 운영 구조의 확장을 의미한다."
)


SALE_WITH_PURPOSE_TEXT = (
    "회사는 노후 설비를 매각했으며, 이는 해외 시장 진출을 위한 운영 구조의 "
    "확장을 의미한다."
)


# 각 음성 원문은 양성 원문(PURPOSE_STATED_SOURCE)과 «한 가지»만 다르다 — 그래야
# 무엇이 가드를 잠갔는지가 분명하다.
@pytest.mark.parametrize("text,source", [
    # 인용 원문에는 결정·자본금만 있다 — 목적 해석의 근거가 없다.
    (FACT_WITH_PURPOSE_TEXT, DECISION_ONLY_SOURCE),
    # 같은 행동·같은 주체지만 원문이 그 목적을 «명시 부정»했다.
    (FACT_WITH_PURPOSE_TEXT,
     "회사는 해외법인 설립을 결정하였으나 이는 해외 시장 진출을 위한 운영 "
     "구조의 확장이 아니라고 밝혔다."),
    # 같은 행동·같은 목적이지만 «다른 주체»의 목적이다.
    (FACT_WITH_PURPOSE_TEXT,
     "경쟁 기업은 해외 시장 진출을 위해 운영 구조의 확장에 나서 해외법인 "
     "설립을 결정하였다."),
    # 같은 주체·같은 목적 낱말이지만 «다른 행동»(법인 설립)의 목적이다 —
    # 「회사」 한 낱말 공유로 설비 매각의 목적을 빌릴 수 없다.
    (SALE_WITH_PURPOSE_TEXT,
     "회사는 신규법인 설립을 통해 해외 시장 진출을 위한 운영 구조의 확장을 "
     "추진한다."),
    # 사실 절 없이 지시어로 시작하면 무엇의 목적인지 특정할 수 없다.
    ("이는 해외 시장 진출을 위한 운영 구조의 확장을 의미한다.",
     PURPOSE_STATED_SOURCE),
    # 독립 검증 확정 반례 A — 한 문장의 «다른 절»(다른 행동)의 목적을 빌린다.
    (SALE_WITH_PURPOSE_TEXT,
     "회사는 노후 설비를 매각했으며, 신규법인 설립은 해외 시장 진출을 위한 "
     "운영 구조의 확장을 목적으로 추진했다."),
    # 독립 검증 확정 반례 B — 한 문장의 «다른 절»(다른 주체)의 목적을 빌린다.
    (SALE_WITH_PURPOSE_TEXT,
     "회사는 노후 설비를 매각했으며, 경쟁 기업은 해외 시장 진출을 위한 운영 "
     "구조의 확장을 위해 노후 설비를 매각했다."),
    # 원문 지시어 절이 목적을 «부정»하면 앞 절과 묶어도 근거가 아니다.
    (SALE_WITH_PURPOSE_TEXT,
     "회사는 노후 설비를 매각했으나 이는 해외 시장 진출을 위한 운영 구조의 "
     "확장이 아니라고 밝혔다."),
])
def test_purpose_interpretation_without_real_support_is_rejected(text, source):
    assert purpose_interpretation_problem(
        text, {"1": source}
    ) == PURPOSE_INTERPRETATION_UNSUPPORTED


@pytest.mark.parametrize("text,sources", [
    # 원문이 목적을 실제로 적었으면 굴절(위한/위해)이 달라도 보존된다.
    (FACT_WITH_PURPOSE_TEXT, {"1": PURPOSE_STATED_SOURCE}),
    # 같은 문장 쉼표 뒤 다른 절의 무관한 부정은 정상 목적을 지우지 않는다.
    (FACT_WITH_PURPOSE_TEXT,
     {"1": "회사는 해외 시장 진출을 위해 운영 구조의 확장에 나서 해외법인 "
           "설립을 결정하였으며, 무리한 차입은 없다고 밝혔다."}),
    # 후보가 회사 이름을 써도 공시의 자기 지칭(「회사는」)과 같은 주체다.
    (FACT_WITH_PURPOSE_TEXT.replace("회사는", "가람테크는"),
     {"1": PURPOSE_STATED_SOURCE}),
    # 같은 행동(설비 매각)에 원문이 목적을 직접 적었으면 보존된다.
    (SALE_WITH_PURPOSE_TEXT,
     {"1": "회사는 해외 시장 진출을 위해 운영 구조의 확장에 나서 노후 설비를 "
           "매각했다."}),
    # 원문이 지시어 절로 «바로 앞 절» 행동의 목적을 적은 정상 서술.
    (SALE_WITH_PURPOSE_TEXT,
     {"1": "회사는 노후 설비를 매각했으며, 이는 해외 시장 진출을 위한 운영 "
           "구조의 확장을 목적으로 한다."}),
    # 앞 절이 다른 일이어도, 목적을 적은 절이 같은 주체(물려받음)·같은 행동이면 보존.
    (SALE_WITH_PURPOSE_TEXT,
     {"1": "회사는 신규법인을 설립했고, 해외 시장 진출을 위한 운영 구조의 "
           "확장을 위해 노후 설비를 매각했다."}),
    # 해석 표지가 없는 사실 문장은 판정하지 않는다.
    ("회사는 3월 20일 이사회 결의에 따라 해외법인 설립을 결정하였으며, 납입 "
     "예정 자본금은 1천 달러이다.", {"1": DECISION_ONLY_SOURCE}),
    # 부정된 해석은 단정이 아니다.
    ("이는 해외 시장 진출을 위한 확장을 의미하지 않는다.",
     {"1": DECISION_ONLY_SOURCE}),
])
def test_purpose_interpretation_symmetry_survives(text, sources):
    assert purpose_interpretation_problem(text, sources) == ""


def _constrained(text, source, confirmed):
    raw = json.dumps(
        {"판정": [{"번호": 1, "결과": "참", "근거": ["1"]}]}, ensure_ascii=False
    )
    return constrain_verdicts(
        raw,
        {1: "참"},
        {1: (text, {"1": source})},
        confirmed_prose_numbers=frozenset({1}) if confirmed else frozenset(),
    )


# ── 정성 인식 기준의 정확 인용 요구 — 기존 exact quote 계약의 좁은 확장 ──
_COMPOSITION_SENTENCES = COMPOSITION_SOURCE.split(". ")
PROGRESS_QUOTE = _COMPOSITION_SENTENCES[1]
EXCEPTION_QUOTE = _COMPOSITION_SENTENCES[2]
CONTENT_QUOTE = _COMPOSITION_SENTENCES[3]
RECOGNITION_EVIDENCE = {"인식기준": [
    {"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "1", "원문": PROGRESS_QUOTE},
    {"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "1", "원문": EXCEPTION_QUOTE},
]}


def test_recognition_basis_claims_require_exact_quote_evidence():
    from src.features.composer.grounding import (
        grounding_hint, grounding_problem, grounding_requirements,
    )

    sources = {"1": COMPOSITION_SOURCE}
    assert "인식기준" in grounding_requirements(
        PRINCIPLE_EXCEPTION_TEXT, (COMPOSITION_SOURCE,)
    )
    # 검수 안내에도 같은 요구가 실린다 — 요구만 하고 알려 주지 않으면
    # 모든 인식 문장이 형식 누락으로 떨어진다.
    assert "인식기준" in grounding_hint(PRINCIPLE_EXCEPTION_TEXT, sources)
    # 발동 범위는 기준(완료·진행) 단정뿐이다 — 사업 서술·시점 인식·원문 직송은
    # 요구하지 않는다(무분별 확대 금지).
    assert grounding_requirements(
        "회사는 소프트웨어 개발 용역과 콘텐츠 공급으로 매출을 올린다.",
        (COMPOSITION_SOURCE,),
    ) == ()
    assert grounding_requirements(
        CONTENT_RECOGNITION_TEXT, (COMPOSITION_SOURCE,)
    ) == ()
    assert grounding_requirements(EXCEPTION_QUOTE, (COMPOSITION_SOURCE,)) == ()
    # 근거 미제출 → 누락, 올바른 정확 인용 → 통과.
    assert grounding_problem(
        PRINCIPLE_EXCEPTION_TEXT, sources, {"결과": "참"}
    ) == "semantic_grounding_missing"
    assert grounding_problem(
        PRINCIPLE_EXCEPTION_TEXT, sources, {"검증근거": RECOGNITION_EVIDENCE}
    ) == ""


@pytest.mark.parametrize("entries", [
    # 다른 수익원(콘텐츠)의 인용으로 용역 기준을 결속할 수 없다.
    [{"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "1", "원문": CONTENT_QUOTE}],
    # 기간 조건을 바꾼 인용은 원문에 글자 그대로 없다.
    [{"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "1",
      "원문": EXCEPTION_QUOTE.replace("1년", "2년")}],
    # 진행 원칙 인용만으로 완료 특례 절까지 결속할 수 없다.
    [{"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "1", "원문": PROGRESS_QUOTE}],
])
def test_recognition_quote_must_carry_same_stream_basis_and_condition(entries):
    from src.features.composer.grounding import grounding_problem

    assert grounding_problem(
        PRINCIPLE_EXCEPTION_TEXT, {"1": COMPOSITION_SOURCE},
        {"검증근거": {"인식기준": entries}},
    ) == "semantic_grounding_invalid"


# 공시 원문은 소제목과 본문이 붙어 있을 수 있다(「1) 용역 매출용역 제공으로…」).
HEADED_SOURCE = COMPOSITION_SOURCE.replace(
    "용역 제공으로 인한 수익은", "1) 용역 매출용역 제공으로 인한 수익은"
)


def test_recognition_quote_starting_inside_a_glued_heading_still_binds():
    from src.features.composer.grounding import grounding_problem

    # 모델이 자연스럽게 자르는 인용 — 원문에서는 「매출」에 붙은 낱말 중간 시작이다.
    evidence = {"인식기준": [
        {"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "1", "원문": PROGRESS_QUOTE},
        {"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "1", "원문": EXCEPTION_QUOTE},
    ]}
    assert PROGRESS_QUOTE in HEADED_SOURCE
    assert grounding_problem(
        PRINCIPLE_EXCEPTION_TEXT, {"1": HEADED_SOURCE}, {"검증근거": evidence},
    ) == ""


def test_cut_first_word_cannot_supply_the_revenue_stream():
    from src.features.composer.grounding import grounding_problem

    # 원문은 «비용역» 매출의 완료 기준인데, 앞 글자를 잘라 «용역» 근거로 대는 우회.
    source = (
        "수익인식 회사의 영업수익은 비용역 매출, 용역 매출 등으로 구성됩니다. "
        "비용역 매출은 제공을 완료한 날에 수익으로 인식합니다."
    )
    text = "용역 매출은 제공을 완료한 날에 수익으로 인식한다."
    cut = {"인식기준": [{"표현": text, "근거": "1",
                        "원문": "용역 매출은 제공을 완료한 날에 수익으로 인식합니다"}]}
    assert grounding_problem(text, {"1": source}, {"검증근거": cut}) != ""


# ── verify 배선 — 2장에만 자기 인용이 넘어가고, 다른 장은 종전 그대로다 ────
def _approving_ask(calls, evidence=None):
    from src.features.composer.tests.review_evidence_fixture import review_items

    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 검수 입력에 후보가 없습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section,
             "근거": list(item.citations), "결과": "참",
             **({"검증근거": evidence} if evidence else {})}
            for item in items
        ]}, ensure_ascii=False)
    return ask


@pytest.mark.parametrize("section,survives,reason", [
    ("business_model", True, None),
    ("operations_partners", False, "accounting_policy_boilerplate"),
])
def test_verify_passes_own_sources_only_to_business_model(section, survives, reason):
    from src.features.composer.port import (
        CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    )
    from src.features.composer.verify import verify_report

    report = ComposedReport((ComposedSection(section, (
        ComposedSentence(PRINCIPLE_EXCEPTION_TEXT, ("revrec",), "확인"),
    )),))
    fragments = (CollectedFragment("revrec", "회계주석", COMPOSITION_SOURCE),)
    calls, diagnostics = [], []
    evidence = {"인식기준": [
        {**entry, "근거": "revrec"} for entry in RECOGNITION_EVIDENCE["인식기준"]
    ]}
    checked = verify_report(report, fragments, None,
                            _approving_ask(calls, evidence=evidence),
                            diagnostics=diagnostics)
    texts = [sentence.text for sentence in checked.sections[0].sentences]
    assert len(calls) == 1
    if survives:
        assert texts == [PRINCIPLE_EXCEPTION_TEXT]
        assert diagnostics == []
    else:
        assert texts == []
        assert [item["reason_code"] for item in diagnostics] == [reason]


def test_verify_rejects_completion_claim_not_in_cited_fragment():
    from src.features.composer.port import (
        CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    )
    from src.features.composer.verify import verify_report

    wrong = (
        "회사의 주된 영업수익은 용역 매출과 콘텐츠 매출로 구성되며, 용역 "
        "매출은 용역제공을 완료한 날에 수익으로 인식한다."
    )
    report = ComposedReport((ComposedSection("operations_partners", (
        ComposedSentence(wrong, ("overview",), "확인"),
    )),))
    fragments = (CollectedFragment("overview", "회사개요", OVERVIEW_SOURCE),)
    calls, diagnostics = [], []
    checked = verify_report(report, fragments, None, _approving_ask(calls),
                            diagnostics=diagnostics)
    # 검수 모델이 참이라고 답해도 인용에 없는 인식 술어는 공개되지 않는다.
    assert [sentence.text for sentence in checked.sections[0].sentences] == []
    assert [item["reason_code"] for item in diagnostics] == ["scope_condition_unbound"]


def test_purpose_check_applies_regardless_of_grade_gate():
    """무근거 목적 꼬리는 라벨을 해석으로 낮춰도 공개되지 않는다(우회 금지)."""

    for confirmed in (True, False):
        verdicts, problems = _constrained(
            FACT_WITH_PURPOSE_TEXT, DECISION_ONLY_SOURCE, confirmed=confirmed
        )
        assert verdicts[1] == "근거결속실패", confirmed
        assert problems[1] == PURPOSE_INTERPRETATION_UNSUPPORTED
    # 원문이 같은 사실에 목적을 적은 후보는 등급 게이트와 무관하게 참으로 남는다.
    for confirmed in (True, False):
        verdicts, problems = _constrained(
            FACT_WITH_PURPOSE_TEXT, PURPOSE_STATED_SOURCE, confirmed=confirmed
        )
        assert verdicts[1] == "참" and 1 not in problems, confirmed


# ── 해석 등급의 목적 꼬리도 사실 절로 회복한다 — 등급은 올리지 않는다 ────────
def test_interpreted_purpose_failure_is_a_rewrite_target_without_upgrade():
    from src.features.composer.port import ComposedSentence
    from src.features.composer.verify import _is_grounding_rewrite_target

    interpreted = ComposedSentence(FACT_WITH_PURPOSE_TEXT, ("1",), "해석")
    confirmed = ComposedSentence(FACT_WITH_PURPOSE_TEXT, ("1",), "확인")
    assert _is_grounding_rewrite_target(
        interpreted, "operations_partners", PURPOSE_INTERPRETATION_UNSUPPORTED)
    assert _is_grounding_rewrite_target(
        confirmed, "operations_partners", PURPOSE_INTERPRETATION_UNSUPPORTED)
    # 다른 사유의 해석 문장은 종전 계약대로 고쳐 쓰지 않는다.
    assert not _is_grounding_rewrite_target(
        interpreted, "operations_partners", "semantic_grounding_invalid")
    assert not _is_grounding_rewrite_target(
        interpreted, "summary", PURPOSE_INTERPRETATION_UNSUPPORTED)


# ── 실제 호출 계약: 안내 → 요구 → 네이티브 스키마 → 검증기 → 재작성·재검수 ─────
SETUP_SOURCE = (
    "회사는 이사회 결의에 따라 해외법인 설립을 결정하였다. 납입 예정 자본금은 "
    "1천 달러이다."
)
SETUP_WITH_PURPOSE = (
    "회사는 이사회 결의에 따라 해외법인 설립을 결정하였으며, 이는 해외 시장 "
    "진출을 위한 운영 구조의 확장을 의미한다."
)
SETUP_FACT_ONLY = "회사는 이사회 결의에 따라 해외법인 설립을 결정하였다."


def _scripted_ai(review_evidence_plan, rewrite_text):
    """검수는 계획된 검증근거로 «참», 묶음 재작성은 지정 글로 답하는 가짜 AI."""
    from src.features.composer.grounding_rewrite_constants import (
        GROUNDING_REWRITE_PROMPT_HEADER,
    )
    from src.features.composer.tests.review_evidence_fixture import review_items

    calls = {"review": [], "rewrite": [], "answers": []}

    def ask(prompt):
        if prompt.startswith(GROUNDING_REWRITE_PROMPT_HEADER):
            calls["rewrite"].append(prompt)
            numbers = re.findall(r"(?m)^번호 (\d+) ·", prompt)
            return json.dumps({"문장들": [
                {"번호": int(number), "글": rewrite_text, "포기": False}
                for number in numbers
            ]}, ensure_ascii=False)
        calls["review"].append(prompt)
        evidence = review_evidence_plan[len(calls["review"]) - 1]
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 검수 입력에 후보가 없습니다"
        answer = json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations),
             "근거대조": "인용 원문과 대조", "결과": "참",
             **({"검증근거": evidence} if evidence else {})}
            for item in items
        ]}, ensure_ascii=False)
        calls["answers"].append(answer)
        return answer
    return ask, calls


@pytest.mark.parametrize("grade", ["확인", "해석"])
def test_purpose_tail_is_recovered_to_fact_clause_through_rewrite_and_recheck(grade):
    from src.features.composer.port import (
        CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    )
    from src.features.composer.verify import verify_report

    report = ComposedReport((ComposedSection("operations_partners", (
        ComposedSentence(SETUP_WITH_PURPOSE, ("setup",), grade),
    )),))
    fragments = (CollectedFragment("setup", "주석", SETUP_SOURCE),)
    ask, calls = _scripted_ai([None, None], SETUP_FACT_ONLY)
    diagnostics = []
    checked = verify_report(report, fragments, None, ask, diagnostics=diagnostics,
                            grounding_rewrite_enabled=True)
    sentences = checked.sections[0].sentences
    # 전문장 삭제가 아니라 근거 있는 사실 절만 정상 재검수를 거쳐 남는다.
    assert [sentence.text for sentence in sentences] == [SETUP_FACT_ONLY]
    assert sentences[0].grade == grade  # 해석을 확인으로 올리지 않는다
    assert len(calls["review"]) == 2 and len(calls["rewrite"]) == 1
    assert "목적·의미 해석" in calls["rewrite"][0]
    assert [item["reason_code"] for item in diagnostics][:1] == [
        PURPOSE_INTERPRETATION_UNSUPPORTED
    ]


def test_recognition_evidence_contract_runs_guide_schema_validator_and_recheck():
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    from src.features.composer.grounding_constants import GROUNDING_GUIDE
    from src.features.composer.port import (
        CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    )
    from src.features.composer.review_schema import (
        DIAGRAM_REVIEW_SCHEMA, FLAT_REVIEW_SCHEMA,
    )
    from src.features.composer.verify import verify_report
    from src.shared.report_quality.review_diagnostics import observed_review_outcomes

    evidence = {"인식기준": [
        {**entry, "근거": "revrec"} for entry in RECOGNITION_EVIDENCE["인식기준"]
    ]}
    report = ComposedReport((ComposedSection("business_model", (
        ComposedSentence(PRINCIPLE_EXCEPTION_TEXT, ("revrec",), "확인"),
    )),))
    fragments = (CollectedFragment("revrec", "회계주석", COMPOSITION_SOURCE),)
    # 최초 검수는 인식기준 근거를 내지 않고, 재검수는 정확 인용을 낸다.
    ask, calls = _scripted_ai([None, evidence], PRINCIPLE_EXCEPTION_TEXT)
    diagnostics = []
    checked = verify_report(report, fragments, None, ask, diagnostics=diagnostics,
                            grounding_rewrite_enabled=True)
    assert [s.text for s in checked.sections[0].sentences] == [PRINCIPLE_EXCEPTION_TEXT]
    # 안내·요구가 실제 검수 입력에 실린다.
    assert "인식기준: [{" in GROUNDING_GUIDE
    assert all("추가 검증 필요: 인식기준" in prompt for prompt in calls["review"])
    # 최초 탈락은 누락으로 남고, 닫힌 전송 진단에서도 버려지지 않는다.
    assert diagnostics[0]["reason_code"] == "semantic_grounding_missing"
    assert diagnostics[0]["verification_items"] == ("인식기준",)
    observed = observed_review_outcomes(diagnostics)
    assert observed and observed[0]["verification_items"] == ("인식기준",)
    # 검증근거 객체 자체가 없으면 기존 계약대로 «근거» 단계 누락이다.
    assert observed[0]["grounding_detail"] == {
        "version": "grounding-detail-v1", "check_kind": "근거",
        "stage": "grounding_missing",
    }
    # 재검수 응답(인식기준 근거 포함)이 실제 네이티브 재요청 스키마를 통과한다.
    recheck_answer = json.loads(calls["answers"][1])
    for schema in (FLAT_REVIEW_SCHEMA, DIAGRAM_REVIEW_SCHEMA):
        reason_key = "대조근거" if schema is DIAGRAM_REVIEW_SCHEMA else "근거대조"
        answer = deepcopy(recheck_answer)
        for row in answer["판정"]:
            row[reason_key] = row.pop("근거대조")
        assert not list(Draft202012Validator(schema).iter_errors(answer))
        previous = deepcopy(schema)
        del previous["$defs"]["grounding"]["properties"]["인식기준"]
        # 추가 전 닫힌 스키마는 이 근거를 보낼 수 없었다(additionalProperties).
        assert list(Draft202012Validator(previous).iter_errors(answer))
        broken = deepcopy(answer)
        del broken["판정"][0]["검증근거"]["인식기준"][0]["원문"]
        assert list(Draft202012Validator(schema).iter_errors(broken))


def test_misquoted_recognition_evidence_is_rejected_and_diagnosed_after_recheck():
    from src.features.composer.port import (
        CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    )
    from src.features.composer.verify import verify_report
    from src.shared.report_quality.review_diagnostics import observed_review_outcomes

    wrong = {"인식기준": [
        {"표현": PRINCIPLE_EXCEPTION_TEXT, "근거": "revrec", "원문": CONTENT_QUOTE},
    ]}
    report = ComposedReport((ComposedSection("business_model", (
        ComposedSentence(PRINCIPLE_EXCEPTION_TEXT, ("revrec",), "확인"),
    )),))
    fragments = (CollectedFragment("revrec", "회계주석", COMPOSITION_SOURCE),)
    ask, _calls = _scripted_ai([None, wrong], PRINCIPLE_EXCEPTION_TEXT)
    diagnostics = []
    checked = verify_report(report, fragments, None, ask, diagnostics=diagnostics,
                            grounding_rewrite_enabled=True)
    assert [s.text for s in checked.sections[0].sentences] == []
    stages =[item.get("grounding_detail", {}).get("stage") for item in diagnostics]
    assert "recognition_invalid" in stages
    observed = observed_review_outcomes(diagnostics)
    assert any(item.get("grounding_detail", {}).get("stage") == "recognition_invalid"
               and item["verification_items"] == ("인식기준",) for item in observed)


def test_missing_recognition_array_keeps_its_check_kind_through_transport():
    """검증근거는 있는데 «인식기준» 배열만 빠진 경우 — 새 종류로 진단이 남는다."""
    from hashlib import sha256

    from src.features.composer.grounding import grounding_problem
    from src.shared.report_quality.review_diagnostics import observed_review_outcomes

    detail = {}
    assert grounding_problem(
        PRINCIPLE_EXCEPTION_TEXT, {"1": COMPOSITION_SOURCE},
        {"검증근거": {"시점": []}}, detail=detail,
    ) == "semantic_grounding_missing"
    assert detail == {"version": "grounding-detail-v1", "check_kind": "인식기준",
                      "stage": "grounding_missing"}
    observed = observed_review_outcomes([{
        "section_id": "business_model", "kind": "본문",
        "reason_code": "semantic_grounding_missing",
        "candidate_sha256": sha256(PRINCIPLE_EXCEPTION_TEXT.encode()).hexdigest(),
        "verification_items": ("인식기준",), "grounding_detail": detail,
    }])
    assert observed[0]["grounding_detail"]["check_kind"] == "인식기준"


def test_detail_stage_and_check_kind_contracts_match_the_producer():
    from src.features.composer.grounding_constants import (
        NUMERIC_KEY, RECOGNITION_KEY, TIME_KEY, TREND_KEY,
    )
    from src.features.composer.grounding_detail_constants import GROUNDING_DETAIL_GUIDES
    from src.shared.report_quality.review_diagnostic_constants import (
        GROUNDING_DETAIL_CHECK_KINDS, GROUNDING_DETAIL_STAGES,
    )

    # 재작성 안내가 있는 단계와 전송 계약의 단계가 같아야 새 단계가 버려지지 않는다.
    assert set(GROUNDING_DETAIL_GUIDES) == set(GROUNDING_DETAIL_STAGES)
    assert {NUMERIC_KEY, TREND_KEY, TIME_KEY, RECOGNITION_KEY, "근거"} == set(
        GROUNDING_DETAIL_CHECK_KINDS)
