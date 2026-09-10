# -*- coding: utf-8 -*-
"""6장 성장 계획 게이트의 «정확도» 경계 세 가지.

2026-09-10 실제 유료 실행(run 979d5e3f…, 멀티캠퍼스)에서 6장이 통째로 비었다.
원인은 자료 부족이 아니라 게이트 세 자리였다 — 이 파일은 그 세 자리를 각각
«살아야 하는 실측 문장»과 «계속 빠져야 하는 기존 사례»로 함께 못 박는다.

  ① 축약 연도    원문 「'26년」 ↔ 후보 「2026년」이 같은 해로 대응돼야 한다.
  ② 의지 어미    원문이 계획을 「…해 나가겠습니다」로만 적었는데 그 어미가
                 양태 목록에 없어 계획으로 읽히지 않았다.
  ③ 「N년 연속」  같은 주장을 도식은 거절하고 본문은 통과시켰다(두 잣대).

⚠️ 이 파일의 어떤 시험도 «게이트를 열어» 통과시키지 않는다. 기존에 정당하게
   거절되던 사례(SM 실측 현재 서술·완료 서술)는 같은 사유로 계속 빠져야 하며,
   그 사례는 여기서 다시 적지 않고 `test_future_section_contract` 의 정본
   목록을 그대로 가져다 쓴다 — 두 벌로 베끼면 한쪽만 고쳐져 표류한다.
"""

import json
import re

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED, STRATEGY_TABLE_SECTION_ID,
)
from src.features.composer.diagram_check import check_diagram_numbers
from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_KEY,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODE_PLAN,
    FUTURE_QUOTE_KEY,
    FUTURE_SECTION_NO_FORWARD_STATEMENT,
    FUTURE_SOURCE_KEY,
    FUTURE_TARGET_KEY,
)
from src.features.composer.grounding import (
    _stated_continuous_periods,
    grounding_problem,
    grounding_requirements,
)
from src.features.composer.grounding_constants import (
    GROUNDING_INVALID, GROUNDING_KEY, TREND_KEY,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.tests.test_future_section_contract import (
    COMPLETED_PRESENT, SM_PRESENT,
)
from src.features.composer.verify import verify_report

_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")
FRAGMENT = "1"

# ══════════════════════════════════════════════════════════════════════
# 실측 원문 — DART 20260316000476 (멀티캠퍼스 제55기 사업보고서) 그대로
# ══════════════════════════════════════════════════════════════════════

#: 「Ⅱ. 사업의 내용」 교육서비스 실적·계획 문단.
SOURCE_EDUCATION = (
    "□ 교육서비스는 국내 경기위축에 따른 공공기관 및 교육예산 축소 등으로 인하여 "
    "매출액은 전기 대비 35,213백만원 감소한 253,902백만원을 기록하였습니다."
    "'26년은 AI 교육 체계를 고도화하고, 빠르게 변화하는 AI 기술과 산업 수요에 "
    "대응하는 과정을 지속적으로 확충해 나가겠습니다. 또한 진단 기반 리더십 교육을 "
    "강화함으로써 안정적인 매출 기반과 차별화된 수익 모델을 확보해 나가겠습니다."
)
#: 같은 문서의 외국어서비스 문단.
SOURCE_LANGUAGE = (
    "□ 외국어서비스는 국내 외국어 말하기 평가 수요 증가 및 AI 어학교육 수요 "
    "증가에따라 매출액은 전기대비 13,300백만원 증가한 76,925백만원을 "
    "기록하였습니다. '26년은 기존에 운영해 온 합숙형 집중 어학 교육 모델을 "
    "글로벌 사업 추진 기업 대상으로 확대하겠습니다."
)

#: 실행 기록(단계 29)이 남긴 «버려진 경로» 두 줄의 칸 그대로.
DROPPED_FLOW_ROWS = (
    (
        "AI 교육 체계 고도화",
        "2026년",
        "AI 교육 체계를 고도화하고 빠르게 변화하는 AI 기술과 산업 수요에 "
        "대응하는 과정을 지속적으로 확충",
    ),
    (
        "진단 기반 리더십 교육 강화",
        "2026년",
        "진단 기반 리더십 교육을 강화함으로써 안정적인 매출 기반과 차별화된 "
        "수익 모델을 확보",
    ),
)


def _flow_report(cells, section=STRATEGY_TABLE_SECTION_ID):
    return ComposedReport((
        ComposedSection(
            section, (), flow_rows=(FlowRow(tuple(cells), (FRAGMENT,)),)
        ),
    ))


def _fragments(text):
    return (CollectedFragment(FRAGMENT, "사업내용", text),)


# ══════════════════════════════════════════════════════════════════════
# ① 축약 연도 — 「'26년」 원문이 「2026년」 칸을 뒷받침한다
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("cells", DROPPED_FLOW_ROWS, ids=("AI교육", "리더십"))
def test_the_abbreviated_year_in_the_filing_grounds_the_expanded_year_cell(cells):
    """실측: 이 두 줄이 「인용 원문에 없는 수 — 2026」으로 통째로 빠졌다."""

    report, problems = check_diagram_numbers(
        _flow_report(cells), _fragments(SOURCE_EDUCATION)
    )
    assert problems == (), problems
    assert report.sections[0].flow_rows[0].cells == tuple(cells)


def test_the_abbreviated_year_works_in_the_other_direction_too():
    """후보가 축약형으로 적고 원문이 네 자리로 적은 반대 방향도 같은 잣대다."""

    report, problems = check_diagram_numbers(
        _flow_report(("합숙형 어학 모델", "'26년", "글로벌 기업 대상 확대")),
        _fragments("2026년 합숙형 집중 어학 교육 모델을 확대하겠습니다."),
    )
    assert problems == (), problems


def test_a_year_the_filing_never_mentions_is_still_rejected():
    """느슨해지지 않았다 — 근거에 없는 해는 그대로 «없는 수»다."""

    _report, problems = check_diagram_numbers(
        _flow_report(("AI 교육 체계 고도화", "2029년", "과정 확충")),
        _fragments(SOURCE_EDUCATION),
    )
    assert len(problems) == 1, problems
    assert "2029" in problems[0], problems[0]


@pytest.mark.parametrize(
    "source",
    (
        "당사는 설립 이후 26년간 어학 교육을 확대해 왔습니다.",
        "당사는 어학 교육을 확대해 왔다고 밝혔다.' 26년 만에 이룬 성과입니다.",
    ),
    ids=("기간", "닫는인용부호뒤기간"),
)
def test_a_duration_is_not_read_as_a_year(source):
    """「26년간」은 기간이지 연도가 아니다 — 연도 근거로 쓰이면 안 된다."""

    _report, problems = check_diagram_numbers(
        _flow_report(("어학 교육", "2026년", "확대")), _fragments(source)
    )
    assert len(problems) == 1, problems
    assert "2026" in problems[0], problems[0]


#: 독립 검토(P2-1)가 찾은 «여는 인용부호 + 기간». 아포스트로피가 연도 표지가
#: 아니라 구절을 여는 따옴표인데, 공백만 막은 규칙은 이 꼴을 2026년으로 읽었다.
#: 첫 줄이 실측 재현 문장이다(한국어 기사·공시가 구절을 작은따옴표로 묶는 꼴).
OPENING_QUOTE_DURATIONS = (
    "창사 이래 '26년 만의 최대 실적'을 기록했다고 밝혔습니다.",
    "'26년간 이어온 어학 교육 사업입니다.",
    "'26년째 이어온 어학 교육 사업입니다.",
    "'26년 동안 이어온 어학 교육 사업입니다.",
    "'26년 이상 이어온 어학 교육 사업입니다.",
    "'26년 전에 시작한 어학 교육 사업입니다.",
    "'26년차 강사가 어학 교육을 맡고 있습니다.",
)


@pytest.mark.parametrize("source", OPENING_QUOTE_DURATIONS)
def test_an_opening_quote_before_a_duration_is_not_a_year(source):
    """아포스트로피가 «따옴표»일 때까지 연도로 읽으면 없는 해가 만들어진다.

    ★ base(`6040a028`)에는 이 정규식 자체가 없어 전부 거절됐다 — 축약 연도
      규칙이 «완화 방향»으로 새로 연 구멍이라 여기서 닫는다.
    """

    _report, problems = check_diagram_numbers(
        _flow_report(("어학 교육", "2026년", "확대")), _fragments(source)
    )
    assert len(problems) == 1, problems
    assert "2026" in problems[0], problems[0]


#: 기간 꼬리 목록이 «다른 낱말의 첫 글자»까지 삼키면 축약 연도 규칙 자체가
#: 무의미해진다. 「전략」·「후반」·「차별화」는 공시에서 흔한 연도 표현이다.
YEAR_LOOKALIKE_TAILS = (
    "'26년 전략을 제시하며 어학 교육을 확대하겠습니다.",
    "'26년 후반 어학 교육을 확대하겠습니다.",
    "'26년 차별화 전략으로 어학 교육을 확대하겠습니다.",
    "'26년 만족도 조사를 거쳐 어학 교육을 확대하겠습니다.",
    "'26년 간담회를 열어 어학 교육을 확대하겠습니다.",
    "'26년에는 어학 교육을 확대하겠습니다.",
    "'26년 매출 목표에 맞춰 어학 교육을 확대하겠습니다.",
)


@pytest.mark.parametrize("source", YEAR_LOOKALIKE_TAILS)
def test_a_real_abbreviated_year_still_grounds_the_cell(source):
    """꼬리 목록이 정상 연도까지 막지 않는다 — 이 줄들은 계속 통과해야 한다."""

    _report, problems = check_diagram_numbers(
        _flow_report(("어학 교육", "2026년", "확대")), _fragments(source)
    )
    assert problems == (), problems


# ══════════════════════════════════════════════════════════════════════
# ② 의지 어미 「-겠-」 — 실제 verify_report 배선을 지난다
# ══════════════════════════════════════════════════════════════════════

#: 작성기가 실제로 쓰는 문체(본문덤프 실측: 「…추진했다」·「…기록했으며」)로
#: 위 원문의 계획을 옮긴 문장. 뒤의 보고 동사가 «완료»로 읽혀 빠지던 꼴이다.
PROMISSORY_PROSE = (
    (
        "AI교육체계고도화",
        "당사는 2026년 AI 교육 체계를 고도화하겠다는 계획을 제시했다.",
        SOURCE_EDUCATION,
        ("AI 교육 체계", "고도화"),
    ),
    (
        "리더십교육강화",
        "당사는 2026년 진단 기반 리더십 교육을 강화하겠다는 계획을 제시했다.",
        SOURCE_EDUCATION,
        ("진단 기반 리더십 교육", "강화"),
    ),
)


def _future_evidence(target, activity, quote):
    return {FUTURE_KEY: [{
        FUTURE_SOURCE_KEY: FRAGMENT,
        FUTURE_TARGET_KEY: target,
        FUTURE_ACTIVITY_KEY: activity,
        FUTURE_QUOTE_KEY: quote,
        FUTURE_MODE_KEY: FUTURE_MODE_PLAN,
    }]}


def _reviewer(evidence=None):
    """실제 프롬프트를 읽고 «참»과 지정한 검증근거를 돌려주는 가짜 검수 AI."""

    def ask(prompt):
        rows = []
        for item in review_items(_GRADE_RE.sub("", prompt)):
            row = {
                "번호": item.number,
                "장": item.section,
                "근거": [str(c).split()[-1] for c in item.citations],
                "결과": "참",
            }
            if evidence is not None:
                row["검증근거"] = json.loads(json.dumps(evidence, ensure_ascii=False))
            rows.append(row)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    return ask


def _run(text, source, *, evidence=None, diagnostics=None):
    report = ComposedReport((
        ComposedSection(
            STRATEGY_TABLE_SECTION_ID,
            (ComposedSentence(text, (FRAGMENT,), GRADE_CONFIRMED),),
        ),
    ))
    return verify_report(
        report,
        _fragments(source),
        None,
        _reviewer(evidence),
        diagnostics=diagnostics if diagnostics is not None else [],
    )


@pytest.mark.parametrize(
    "name,text,source,slots", PROMISSORY_PROSE,
    ids=[case[0] for case in PROMISSORY_PROSE],
)
def test_a_promissory_plan_sentence_survives_the_future_section(
    name, text, source, slots
):
    """「…하겠다」 계획이 6장에 남는다 — 근거를 제대로 댔을 때만."""

    quote = source[source.index("'26년"):]
    diagnostics: list[dict] = []
    checked = _run(
        text, source,
        evidence=_future_evidence(slots[0], slots[1], quote),
        diagnostics=diagnostics,
    )
    kept = [sentence.text for sentence in checked.sections[0].sentences]
    assert kept == [text], [row["reason_code"]
                            for row in final_review_outcomes(checked, diagnostics)]


@pytest.mark.parametrize(
    "name,text,source,slots", PROMISSORY_PROSE,
    ids=[case[0] for case in PROMISSORY_PROSE],
)
def test_a_promissory_plan_sentence_still_needs_its_future_evidence(
    name, text, source, slots
):
    """장 배치를 통과했다고 근거가 면제되지 않는다 — 근거를 빼면 빠져야 한다.

    ★ 이 시험이 없으면 ①의 수정이 «통과만 시키고 아무것도 묻지 않는» 완화가
      된다. 실제로 수정 전에는 이 문장이 계획 주장으로 읽히지 않아 미래 근거를
      요구받지 않는 자리가 있었다.
    """

    diagnostics: list[dict] = []
    checked = _run(text, source, evidence=None, diagnostics=diagnostics)
    assert [sentence.text for sentence in checked.sections[0].sentences] == []


def test_a_time_topic_is_skipped_but_the_next_subject_is_still_checked():
    """시점 부사를 건너뛰는 것이지 주체 검사를 끄는 것이 아니다.

    ★ 원문의 주어가 «남»이면 시점 부사가 앞에 붙어 있어도 그대로 거절돼야 한다.
      건너뛰기를 «주어 없음»으로 잘못 구현하면 이 줄이 통과한다.
    """

    source = "경쟁사는 올해는 어학 교육 과정을 확대할 계획입니다."
    diagnostics: list[dict] = []
    checked = _run(
        "당사는 2026년 어학 교육 과정을 확대하겠다는 계획을 제시했다.",
        source,
        evidence=_future_evidence("어학 교육 과정", "확대", source),
        diagnostics=diagnostics,
    )
    assert [sentence.text for sentence in checked.sections[0].sentences] == []
    assert {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)} \
        == {"future_plan_subject_mismatch"}


@pytest.mark.parametrize("text,source", SM_PRESENT + COMPLETED_PRESENT)
def test_the_existing_present_and_completed_cases_are_still_dropped(text, source):
    """음성 대조 — 기존에 정당하게 빠지던 여섯 사례가 그대로 빠진다."""

    diagnostics: list[dict] = []
    checked = _run(text, source, diagnostics=diagnostics)
    assert [sentence.text for sentence in checked.sections[0].sentences] == []
    assert {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)} \
        == {FUTURE_SECTION_NO_FORWARD_STATEMENT}


# ══════════════════════════════════════════════════════════════════════
# ③ 「N년 연속」 — 본문도 도식과 같은 잣대를 쓴다
# ══════════════════════════════════════════════════════════════════════

_METRIC = "매출액"
_VALUES = {
    "2022": "3,600억원",
    "2023": "3,586억원",
    "2024": "3,527억원",
    "2025": "3,308억원",
}
_THREE_YEARS = ("2023", "2024", "2025")
_FOUR_YEARS = ("2022", *_THREE_YEARS)
#: 실측 5장 본문. 금액을 적지 않아 종전 규칙의 «금액이 있을 때만» 조건을 비껴갔다.
THREE_YEAR_CLAIM = (
    "교육서비스 부문의 매출액이 3년 연속 감소하면서 전체 매출 규모가 축소되고 있다."
)


def _trend_source(periods):
    return "\n".join(f"{_METRIC} | {year}년 | {_VALUES[year]}" for year in periods)


def _observation(year):
    return {
        "근거": "공시",
        "원문": f"{_METRIC} | {year}년 | {_VALUES[year]}",
        "항목": _METRIC,
        "기간": year,
        "원문항목": _METRIC,
        "원문값": _VALUES[year],
    }


def _trend_evidence(expression, periods):
    return {GROUNDING_KEY: {TREND_KEY: [{
        "표현": expression,
        "항목": _METRIC,
        "방향": "지속감소",
        "관측": [_observation(year) for year in periods],
    }]}}


def test_a_counted_streak_claim_needs_trend_evidence_even_without_amounts():
    """도식이 「없는 수 3」으로 거절한 그 주장을 본문도 검사 대상으로 삼는다."""

    assert TREND_KEY in grounding_requirements(
        THREE_YEAR_CLAIM, (_trend_source(_THREE_YEARS),)
    )


def test_an_uncounted_qualitative_trend_is_still_left_to_the_semantic_review():
    """수를 못 박지 않은 정성 서술까지 일괄로 끌어오지 않는다(기존 설계 유지)."""

    assert grounding_requirements(
        "교육서비스 부문의 매출액이 지속적으로 감소하고 있다.",
        (_trend_source(_THREE_YEARS),),
    ) == ()


def test_three_year_values_cannot_prove_a_three_year_streak():
    """세 해 값의 전년비 변화는 두 번뿐이다 — 「3년 연속」을 증명하지 못한다."""

    assert grounding_problem(
        THREE_YEAR_CLAIM,
        {"공시": _trend_source(_THREE_YEARS)},
        _trend_evidence("3년 연속 감소", _THREE_YEARS),
    ) == GROUNDING_INVALID


def test_four_year_values_do_prove_a_three_year_streak():
    """근거가 충분하면 통과한다 — 주장을 막는 것이 아니라 근거를 요구한다."""

    assert grounding_problem(
        THREE_YEAR_CLAIM,
        {"공시": _trend_source(_FOUR_YEARS)},
        _trend_evidence("3년 연속 감소", _FOUR_YEARS),
    ) == ""


def test_three_year_values_still_prove_a_two_year_streak():
    """N이 작아지면 요구도 같이 작아진다 — 고정된 새 문턱이 아니다."""

    assert grounding_problem(
        "교육서비스 부문의 매출액이 2년 연속 감소했다.",
        {"공시": _trend_source(_THREE_YEARS)},
        _trend_evidence("2년 연속 감소", _THREE_YEARS),
    ) == ""


#: 독립 검토(P2-2)가 찾은 우회로. 하한은 검수 응답이 «표현»으로 무엇을 적었는지에
#: 달려 있었고, 짝을 «글자 포함»으로 맞추면 수를 뺀 채 오른쪽으로 길게 잡는 것만으로
#: 0으로 떨어졌다. 실측 문장이 「3년 연속 감소«하면서» 전체 매출 규모가…」라서
#: 검수 AI가 이 표현을 집을 확률이 낮지 않다.
TRUNCATED_STREAK_EXPRESSIONS = (
    "연속 감소",
    "연속 감소하면서",
    "연속 감소하면서 전체 매출 규모가 축소",
)


@pytest.mark.parametrize("expression", TRUNCATED_STREAK_EXPRESSIONS)
def test_trimming_the_number_out_of_the_expression_does_not_lower_the_floor(
    expression,
):
    """표현에서 수를 잘라내도 문장이 못 박은 「3년 연속」의 하한이 그대로 적용된다."""

    assert grounding_problem(
        THREE_YEAR_CLAIM,
        {"공시": _trend_source(_THREE_YEARS)},
        _trend_evidence(expression, _THREE_YEARS),
    ) == GROUNDING_INVALID


@pytest.mark.parametrize("expression", TRUNCATED_STREAK_EXPRESSIONS)
def test_the_same_trimmed_expression_passes_when_the_evidence_is_enough(
    expression,
):
    """하한을 올린 것이지 표현을 벌한 것이 아니다 — 네 해 값이면 그대로 통과한다."""

    assert grounding_problem(
        THREE_YEAR_CLAIM,
        {"공시": _trend_source(_FOUR_YEARS)},
        _trend_evidence(expression, _FOUR_YEARS),
    ) == ""


def test_only_the_streak_overlapping_this_expression_sets_the_floor():
    """자리가 겹치는 「N년 연속」만 본다 — 다른 절의 수까지 끌어오지 않는다.

    ★ 이 결함을 「문장 전체에서 최대」로 고치면 이 시험이 깨진다. 그 구현은 한
      문장 안의 관계 없는 주장까지 하한으로 묶어 근거가 충분한 추세를 거짓
      차단한다. 「자리 겹침」이 맞는 답이라는 것을 여기서 못 박는다.
    ⚠️ 이 성질은 `grounding_problem` 으로는 가려낼 수 없다 — 두 주장이 든
      문장은 표현이 덮지 못한 나머지 추세 자리 때문에 어차피 거절되기 때문이다.
      그래서 하한을 정하는 함수를 직접 부른다.
    """

    text = (
        "외국어서비스 매출액이 2년 연속 감소했고, "
        "그와 별개로 수강생 수는 4년 연속 증가했다."
    )
    assert _stated_continuous_periods(text, "2년 연속 감소") == 2
    assert _stated_continuous_periods(text, "연속 감소했고") == 2
    assert _stated_continuous_periods(text, "4년 연속 증가") == 4
    # 문장이 못 박은 수를 표현이 한 글자도 덮지 않으면 하한을 새로 만들지 않는다.
    assert _stated_continuous_periods(text, "수강생 수는") == 0
