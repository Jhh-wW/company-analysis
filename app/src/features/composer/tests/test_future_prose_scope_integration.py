# -*- coding: utf-8 -*-
"""6장 «본문 문장»의 회사 계획 주장은 실제 검수 경로에서 근거를 대야 한다.

전부 `verify_report` 를 지난다 — 평문 본문 검수·묶음 packet 검수·재작성 재검수가
모두 쓰는 실제 진입점이다. 가드 함수를 직접 부르지 않으므로, 가드가 맞아도 배선이
빠지면 이 시험이 깨진다.

확정 반례(F2)의 문장과 원문은 이 폴더의 `fixtures/future_prose_scope_case.json` 에
그대로 담겨 있다. 개인 폴더(`.local-artifacts`)를 찾아 올라가지 않으므로 깨끗한
checkout 에서도 그대로 돌아간다. 대신 시험이 매번 그 문자열의 SHA256 을 직접 재서,
원본 반례 기록에 적힌 값과 같은지 스스로 확인한다.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from src.features.composer.constants import STRATEGY_TABLE_SECTION_ID
from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_KEY,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODE_PLAN,
    FUTURE_QUOTE_KEY,
    FUTURE_REASON_CODES,
    FUTURE_SOURCE_KEY,
    FUTURE_TARGET_KEY,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report

_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_CASE = json.loads(
    (_FIXTURE_DIR / "future_prose_scope_case.json").read_text(encoding="utf-8")
)

ACTUAL_PROSE = _CASE["paragraph"]
ACTUAL_SOURCE = _CASE["source"]
ACTUAL_FRAGMENT = _CASE["fragment_id"]

#: 원문이 실제로 회사의 계획을 밝힌 정상 문장 — 보존돼야 한다.
GOOD_SOURCE = "당사는 향후 팬 플랫폼 사업을 확대할 계획입니다."
GOOD_PROSE = "회사는 팬 플랫폼 사업을 확대할 계획이다."
GOOD_FRAGMENT = "77"
#: 현재 진행 서술에는 미래 근거 배열을 요구하지 않는다. 다만 미래 장에
#: 현재 사실만 싣는 것은 장 범위 검사에서 제외된다.
PRESENT_SOURCE = "당사는 팬 플랫폼 사업을 확대하고 있습니다."
PRESENT_PROSE = "회사는 팬 플랫폼 사업을 확대하고 있다."
PRESENT_FRAGMENT = "78"

#: 같은 대상·활동이 두 자리에 되풀이되는 짝. 원문은 둘 다 회사의 계획을 밝힌다.
REPEAT_SOURCE_A = "당사는 팬 플랫폼 사업을 확대할 계획입니다."
REPEAT_SOURCE_B = "당사는 내년에도 팬 플랫폼 사업을 확대할 계획입니다."
REPEAT_FRAGMENT_A = "79"
REPEAT_FRAGMENT_B = "80"
#: 두 자리 모두 계획 주장이다 — 근거가 둘이면 정상, 하나면 두 번째가 미증명이다.
REPEAT_PROSE = (
    "회사는 팬 플랫폼 사업을 확대할 계획이다."
    " 회사는 내년에도 팬 플랫폼 사업을 확대할 방침이다."
)
#: 앞은 현재 서술(주장 아님), 뒤는 계획 주장. 근거는 뒤 자리에 결속돼야 한다.
PRESENT_THEN_PLAN_PROSE = (
    "회사는 팬 플랫폼 사업을 확대하고 있다."
    " 회사는 팬 플랫폼 사업을 확대할 계획이다."
)


def _future_item(target, activity, quote, fragment):
    return {
        FUTURE_SOURCE_KEY: fragment,
        FUTURE_TARGET_KEY: target,
        FUTURE_ACTIVITY_KEY: activity,
        FUTURE_QUOTE_KEY: quote,
        FUTURE_MODE_KEY: FUTURE_MODE_PLAN,
    }


def _fan_platform(quote, fragment):
    return _future_item("팬 플랫폼 사업", "확대", quote, fragment)


#: 검수 AI가 각 후보에 붙일 미래근거. 문장 원문으로 고른다.
_FUTURE_BY_TEXT = {
    ACTUAL_PROSE: [_future_item(
        "부가사업의 비중", "확대",
        "스트리밍, 영상 콘텐츠, 온라인 공연, 팬 커뮤니티 플랫폼 등 부가사업의 비중도"
        " 지속적으로 확대되고 있습니다",
        ACTUAL_FRAGMENT,
    )],
    GOOD_PROSE: [_fan_platform(GOOD_SOURCE, GOOD_FRAGMENT)],
    #: 되풀이된 두 자리를 각자의 원문으로 증명한다 — 정상 반복.
    REPEAT_PROSE: [_fan_platform(REPEAT_SOURCE_A, REPEAT_FRAGMENT_A),
                   _fan_platform(REPEAT_SOURCE_B, REPEAT_FRAGMENT_B)],
    PRESENT_THEN_PLAN_PROSE: [_fan_platform(REPEAT_SOURCE_A, REPEAT_FRAGMENT_A)],
}

#: 되풀이된 두 자리 중 «앞 하나»만 증명한 나쁜 반복. 정상 반복과 문장이 똑같다.
_REPEAT_ONLY_FIRST = [_fan_platform(REPEAT_SOURCE_A, REPEAT_FRAGMENT_A)]


def _echoed_citations(item):
    """프롬프트에서 읽은 인용 id 를 그대로 되돌린다.

    ⚠️ 인용이 둘 이상인 후보에서 시험용 파서(`review_items`)가 두 번째 id 를
       「조각 79」처럼 앞말과 붙여 흘린다. 그 값을 그대로 되돌리면 소유 인용이
       달라져 판정이 폐기되므로, 여기서 마지막 토막만 취해 되돌린다. 이것은
       «시험용 파서»의 한계를 되돌리는 것이지 검수 규칙을 느슨하게 하는 것이 아니다.
    """

    return [str(citation).split()[-1] for citation in item.citations]


def _reviewer(calls, *, omit_future=False, future_by_text=None):
    """실제 프롬프트를 읽고 판정을 돌려주는 가짜 검수 AI. 유료 호출은 없다."""

    table = _FUTURE_BY_TEXT if future_by_text is None else future_by_text

    def ask(prompt):
        calls.append(prompt)
        rows = []
        for item in review_items(_GRADE_RE.sub("", prompt)):
            entry = {"번호": item.number, "장": item.section,
                     "근거": _echoed_citations(item), "결과": "참"}
            future = None if omit_future else table.get(item.text.strip())
            if future is not None:
                entry["검증근거"] = {FUTURE_KEY: [dict(one) for one in future]}
            rows.append(entry)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    return ask


_ALL_FRAGMENTS = (
    (ACTUAL_FRAGMENT, ACTUAL_SOURCE),
    (GOOD_FRAGMENT, GOOD_SOURCE),
    (PRESENT_FRAGMENT, PRESENT_SOURCE),
    (REPEAT_FRAGMENT_A, REPEAT_SOURCE_A),
    (REPEAT_FRAGMENT_B, REPEAT_SOURCE_B),
)


def _fragments():
    return tuple(CollectedFragment(fid, "공시", text) for fid, text in _ALL_FRAGMENTS)


def _run(section_id, sentences, ask, grouped, diagnostics):
    report = ComposedReport((ComposedSection(section_id, tuple(sentences)),))
    allowed = (
        {section_id: frozenset(fid for fid, _ in _ALL_FRAGMENTS)}
        if grouped else None
    )
    return verify_report(
        report, _fragments(), None, ask,
        allowed_fragment_ids_by_section=allowed, diagnostics=diagnostics,
    )


def _kept(checked):
    return [sentence.text for sentence in checked.sections[0].sentences]


# ── 반례 원문이 실제 기록과 같은지부터 확인한다 ─────────────────────────
def test_the_stored_counterexample_matches_its_recorded_sha256():
    """fixture 를 사람이 손대면 이 시험이 먼저 깨진다."""

    assert hashlib.sha256(ACTUAL_SOURCE.encode("utf-8")).hexdigest() == (
        _CASE["source_sha256"]
    )
    assert hashlib.sha256(ACTUAL_PROSE.encode("utf-8")).hexdigest() == (
        _CASE["paragraph_sha256"]
    )
    assert _CASE["section"] == "future_strategy" and _CASE["kind"] == "body"


# ── 확정 반례, 실제 검수 경로로 ─────────────────────────────────────────
@pytest.mark.parametrize("grouped", (False, True))
def test_actual_industry_present_source_cannot_publish_a_company_plan(grouped):
    """수정 전에는 이 문장이 그대로 실렸다. 이제는 공개에서 빠져야 한다."""

    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(ACTUAL_PROSE, (ACTUAL_FRAGMENT,), "확인"),),
        _reviewer(calls), grouped, diagnostics,
    )
    assert ACTUAL_PROSE not in _kept(checked), (
        "산업의 현재 변화만 담은 자기 원문이 회사 확정 계획으로 그대로 공개됐다"
    )
    reasons = {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}
    assert reasons & set(FUTURE_REASON_CODES), reasons


@pytest.mark.parametrize("grouped", (False, True))
def test_actual_counterexample_without_any_future_evidence_is_dropped(grouped):
    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(ACTUAL_PROSE, (ACTUAL_FRAGMENT,), "확인"),),
        _reviewer(calls, omit_future=True), grouped, diagnostics,
    )
    assert ACTUAL_PROSE not in _kept(checked)


# ── 살아남아야 하는 것 ──────────────────────────────────────────────────
@pytest.mark.parametrize("grouped", (False, True))
def test_a_company_plan_backed_by_its_own_source_is_published(grouped):
    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(GOOD_PROSE, (GOOD_FRAGMENT,), "확인"),),
        _reviewer(calls), grouped, diagnostics,
    )
    assert _kept(checked) == [GOOD_PROSE]


@pytest.mark.parametrize("grouped", (False, True))
def test_present_progress_prose_in_the_same_chapter_is_dropped_by_the_section_contract(grouped):
    """6장 «장 계약» 변경: 앞으로의 이야기가 없는 진행 서술은 이 장에 남지 않는다.

    예전 계약은 「진행 서술이면 미래 근거를 요구하지 않는다」였고 그대로 실렸다.
    실측(새 SM 6장 두 문장)에서 그것이 «분류 누락»으로 드러나 계약을 바꾼다.
    미래 근거를 요구하지 않는다는 점은 그대로다 — 요구하는 것은 «자격»이다.
    """

    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(PRESENT_PROSE, (PRESENT_FRAGMENT,), "확인"),),
        _reviewer(calls, omit_future=True), grouped, diagnostics,
    )
    assert PRESENT_PROSE not in _kept(checked)


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("section_id", ("business_model", "current_challenges"))
def test_other_chapters_are_untouched_by_this_check(section_id, grouped):
    """같은 문장이라도 다른 장이면 이 검사가 아무것도 요구하지 않는다."""

    calls, diagnostics = [], []
    checked = _run(
        section_id,
        (ComposedSentence(GOOD_PROSE, (GOOD_FRAGMENT,), "확인"),),
        _reviewer(calls, omit_future=True), grouped, diagnostics,
    )
    assert _kept(checked) == [GOOD_PROSE]


@pytest.mark.parametrize("grouped", (False, True))
def test_the_bad_sentence_goes_while_the_good_one_stays(grouped):
    """한 장에 둘이 함께 있어도 문장 단위로 갈린다."""

    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(GOOD_PROSE, (GOOD_FRAGMENT,), "확인"),
         ComposedSentence(ACTUAL_PROSE, (ACTUAL_FRAGMENT,), "확인")),
        _reviewer(calls), grouped, diagnostics,
    )
    kept = _kept(checked)
    assert GOOD_PROSE in kept
    assert ACTUAL_PROSE not in kept


# ── 같은 대상·활동이 되풀이될 때: 정상 반복과 나쁜 반복을 짝지어 본다 ───
@pytest.mark.parametrize("grouped", (False, True))
def test_a_repeated_plan_backed_on_both_sides_is_published(grouped):
    """양쪽 자리를 각자의 원문이 뒷받침하면 되풀이돼도 실려야 한다.

    수정 전에는 두 근거가 «첫 자리»에만 결속돼 뒤 자리가 미증명으로 몰렸다.
    """

    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(
            REPEAT_PROSE, (REPEAT_FRAGMENT_A, REPEAT_FRAGMENT_B), "확인"),),
        _reviewer(calls), grouped, diagnostics,
    )
    assert _kept(checked) == [REPEAT_PROSE], (
        "양쪽 원문이 뒷받침하는 정상 반복이 근거 결속 자리 때문에 잘렸다"
    )


@pytest.mark.parametrize("grouped", (False, True))
def test_a_repeated_plan_proven_only_once_is_dropped(grouped):
    """문장은 위와 똑같고 근거만 하나다 — 두 번째 자리는 여전히 미증명이다."""

    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(
            REPEAT_PROSE, (REPEAT_FRAGMENT_A, REPEAT_FRAGMENT_B), "확인"),),
        _reviewer(calls, future_by_text={REPEAT_PROSE: _REPEAT_ONLY_FIRST}),
        grouped, diagnostics,
    )
    assert REPEAT_PROSE not in _kept(checked), (
        "되풀이된 두 자리를 근거 하나로 덮었다"
    )
    reasons = {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}
    assert reasons & set(FUTURE_REASON_CODES), reasons


@pytest.mark.parametrize("grouped", (False, True))
def test_a_plan_after_a_present_sentence_binds_to_the_plan_position(grouped):
    """앞 문장이 같은 대상·활동을 현재로 적어도, 근거는 뒤 계획 자리에 붙는다."""

    calls, diagnostics = [], []
    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(
            PRESENT_THEN_PLAN_PROSE,
            (PRESENT_FRAGMENT, REPEAT_FRAGMENT_A), "확인"),),
        _reviewer(calls), grouped, diagnostics,
    )
    assert _kept(checked) == [PRESENT_THEN_PLAN_PROSE], (
        "앞 현재 서술이 같은 낱말을 써서 정상 계획 근거가 첫 자리에 묶였다"
    )


# ── 프롬프트가 파서와 같은 것을 요구하는지 ──────────────────────────────
@pytest.mark.parametrize("grouped", (False, True))
def test_the_review_prompt_asks_for_body_future_evidence(grouped):
    calls = []
    _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(GOOD_PROSE, (GOOD_FRAGMENT,), "확인"),),
        _reviewer(calls), grouped, [],
    )
    assert calls, "검수 프롬프트가 만들어지지 않았다"
    prompt = calls[0]
    assert FUTURE_KEY in prompt
    # 수정 전 안내는 «본문 문장에는 넣지 말라»고 적혀 있었다. 그 문구가 남아 있으면
    # 파서가 요구하는 것과 프롬프트가 시키는 것이 정면으로 어긋난다.
    assert "이 표가 아닌 장·본문 문장에는 이 배열을 넣지 않는다" not in prompt
    assert "이 장의 «본문 문장»도" in prompt


@pytest.mark.parametrize("grouped", (False, True))
def test_the_prompt_does_not_read_as_table_only(grouped):
    """제목과 «반드시»가 표에만 걸려 있으면 검수 AI가 본문을 건너뛴다.

    본문 요구가 조건절 한 문단에만 있으면 「이 표의 모든 줄은 반드시」라는 앞
    문장만 읽고 본문을 면제로 볼 여지가 있다. 제목과 필수 표현이 본문까지
    덮는지 실제 프롬프트에서 확인한다.
    """

    calls = []
    _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(GOOD_PROSE, (GOOD_FRAGMENT,), "확인"),),
        _reviewer(calls), grouped, [],
    )
    prompt = calls[0]
    heading = next(line for line in prompt.splitlines()
                   if line.startswith("■") and "성장 계획" in line)
    assert "본문 문장" in heading, heading
    prose_paragraph = next(
        line for line in prompt.splitlines() if "이 장의 «본문 문장»도" in line
    )
    assert "«반드시»" in prose_paragraph, prose_paragraph
    # 되풀이 자리마다 항목을 따로 두라는 지시가 파서 규칙과 같아야 한다.
    assert "되풀이" in prose_paragraph, prose_paragraph


# ── 재작성 재검수로도 되돌아오지 못한다 ─────────────────────────────────
def test_a_rewrite_cannot_smuggle_an_unbacked_company_plan_back_in():
    """«거짓» 뒤 재작성으로도 근거 없는 회사 계획은 실리지 않는다."""

    from src.features.composer.verify import REWRITE_PROMPT_HEADER

    calls = []

    def ask(prompt):
        calls.append(prompt)
        if REWRITE_PROMPT_HEADER in prompt:
            # 고쳐 쓴 문장도 여전히 산업 현재 원문을 회사 계획으로 말한다.
            return ACTUAL_PROSE
        rows = []
        for item in review_items(_GRADE_RE.sub("", prompt)):
            verdict = "거짓" if item.text.strip() == PRESENT_PROSE else "참"
            entry = {"번호": item.number, "장": item.section,
                     "근거": _echoed_citations(item), "결과": verdict}
            future = _FUTURE_BY_TEXT.get(item.text.strip())
            if verdict == "참" and future is not None:
                entry["검증근거"] = {FUTURE_KEY: [dict(one) for one in future]}
            rows.append(entry)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    checked = _run(
        STRATEGY_TABLE_SECTION_ID,
        (ComposedSentence(PRESENT_PROSE, (ACTUAL_FRAGMENT,), "확인"),),
        ask, False, [],
    )
    assert any(REWRITE_PROMPT_HEADER in call for call in calls), "재작성 경로를 지나지 않았다"
    assert ACTUAL_PROSE not in _kept(checked)
    assert PRESENT_PROSE not in _kept(checked)
