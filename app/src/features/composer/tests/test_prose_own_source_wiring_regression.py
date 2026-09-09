# -*- coding: utf-8 -*-
"""«확인» 산문 자기 원문 근거 — 배선 회귀와 조사 변형 보존.

전부 `verify_report` 를 지난다 — 평문(flat)·묶음(grouped)·요약·재작성 재검수가
모두 쓰는 실제 진입점이다. 가드 함수를 직접 부르지 않으므로 배선이 빠지면 깨진다.

각 시험은 짝으로 되어 있다 — 막혀야 하는 문장 하나와 살아야 하는 문장 하나.
전부 막는 가드도, 전부 통과시키는 가드도 초록불로 보이기 때문이다.
"""

import json
import re

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    PerformanceTable,
)
from src.features.composer.prose_own_source_constants import (
    PROSE_OWN_SOURCE_REASON_CODES,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report, verify_sentences

_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")

SECTION = "business_model"

#: 자기 원문이 실제로 뒷받침하는 정상 «확인» 산문 — 언제나 살아야 한다.
GOOD_SOURCE = "당사는 팬 플랫폼 사업에서 구독 서비스를 운영하고 있습니다."
GOOD_PROSE = "회사는 팬 플랫폼 사업에서 구독 서비스를 운영한다."
GOOD_FRAGMENT = "11"

#: 자기 원문과 낱말이 거의 겹치지 않는 «확인» 산문 — 계약 최소치(2개)에 못 미친다.
THIN_SOURCE = "당사는 서울특별시에 본점을 두고 있습니다."
THIN_PROSE = "회사는 글로벌 팬덤 커머스에서 압도적 경쟁우위를 보유한다."
THIN_FRAGMENT = "12"

#: 실적표가 있는 보고서인지 여부만 바꾸기 위한 최소 표.
TABLE = PerformanceTable(
    caption="주요 재무",
    headers=("구분", "2024", "2025"),
    rows=(("매출액", "1,000", "1,200"),),
    unit="억원",
)


def _fragments():
    return (
        CollectedFragment(GOOD_FRAGMENT, "공시", GOOD_SOURCE),
        CollectedFragment(THIN_FRAGMENT, "공시", THIN_SOURCE),
    )


def _echoed_citations(item):
    """묶음 프롬프트에서 둘째 인용 id 가 앞말과 붙어 나오는 것을 되돌린다."""

    return [str(citation).split()[-1] for citation in item.citations]


def _reviewer(calls):
    """실제 프롬프트를 읽고 모두 «참»으로 돌려주는 가짜 검수 AI. 유료 호출 없다."""

    def ask(prompt):
        calls.append(prompt)
        rows = [
            {"번호": item.number, "장": item.section,
             "근거": _echoed_citations(item), "결과": "참"}
            for item in review_items(_GRADE_RE.sub("", prompt))
        ]
        return json.dumps({"판정": rows}, ensure_ascii=False)

    return ask


def _sentence(text, fragment, grade=GRADE_CONFIRMED):
    return ComposedSentence(text, (fragment,), grade)


def _run(sentences, *, grouped, table, diagnostics):
    report = ComposedReport((ComposedSection(SECTION, tuple(sentences)),))
    allowed = (
        {SECTION: frozenset((GOOD_FRAGMENT, THIN_FRAGMENT))} if grouped else None
    )
    return verify_report(
        report, _fragments(), table, _reviewer([]),
        allowed_fragment_ids_by_section=allowed, diagnostics=diagnostics,
    )


def _kept(checked):
    return [sentence.text for sentence in checked.sections[0].sentences]


# ── 표가 없을 때: 두 경로 모두 막아야 한다 ──────────────────────────────
@pytest.mark.parametrize("grouped", (False, True))
def test_thin_prose_is_dropped_when_the_report_has_no_table(grouped):
    diagnostics: list[dict] = []
    checked = _run(
        [_sentence(THIN_PROSE, THIN_FRAGMENT)],
        grouped=grouped, table=None, diagnostics=diagnostics,
    )
    assert THIN_PROSE not in _kept(checked)
    reasons = {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}
    assert reasons & set(PROSE_OWN_SOURCE_REASON_CODES), reasons


@pytest.mark.parametrize("grouped", (False, True))
def test_a_prose_backed_by_its_own_source_survives_without_a_table(grouped):
    checked = _run(
        [_sentence(GOOD_PROSE, GOOD_FRAGMENT)],
        grouped=grouped, table=None, diagnostics=[],
    )
    assert _kept(checked) == [GOOD_PROSE]


# ── 표가 있을 때: 여기서 평문(flat) 경로가 갈린다 ───────────────────────
@pytest.mark.parametrize("grouped", (False, True))
def test_thin_prose_is_dropped_even_when_the_report_has_a_table(grouped):
    """실적표가 있다는 이유만으로 이 검사가 꺼지면 안 된다.

    ★ 반례의 핵심 — 이 문장은 실적표를 인용하지 않는다. 자기 인용은 조각
      하나뿐이고 그 원문이 뒷받침하지 않는다. 그런데 평문 경로는 모든 후보에
      실적표 결속 원문을 함께 실어 주므로, 「표가 근거인 문장은 건너뛴다」는
      조건이 «모든» 문장에 걸린다.
    """

    diagnostics: list[dict] = []
    checked = _run(
        [_sentence(THIN_PROSE, THIN_FRAGMENT)],
        grouped=grouped, table=TABLE, diagnostics=diagnostics,
    )
    assert THIN_PROSE not in _kept(checked), (
        "실적표가 있는 보고서에서 자기 원문 근거 검사가 통째로 꺼졌다"
    )


@pytest.mark.parametrize("grouped", (False, True))
def test_a_prose_backed_by_its_own_source_survives_with_a_table(grouped):
    checked = _run(
        [_sentence(GOOD_PROSE, GOOD_FRAGMENT)],
        grouped=grouped, table=TABLE, diagnostics=[],
    )
    assert _kept(checked) == [GOOD_PROSE]


# ── 보존 계약: 해석 등급은 이 검사가 건드리지 않는다 ────────────────────
@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("table", (None, TABLE))
def test_interpreted_grade_prose_is_not_touched_by_this_check(grouped, table):
    """같은 문장이라도 «해석» 등급이면 이 결속 계약의 대상이 아니다."""

    checked = _run(
        [_sentence(THIN_PROSE, THIN_FRAGMENT, grade=GRADE_INTERPRETED)],
        grouped=grouped, table=table, diagnostics=[],
    )
    assert _kept(checked) == [THIN_PROSE]


# ── 요약 경로 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("table", (None, TABLE))
def test_summary_path_applies_the_same_check(table):
    """요약도 같은 판정을 봐야 한다 — 본문에서 빠진 문장이 요약에 남으면 안 된다."""

    kept = verify_sentences(
        (_sentence(THIN_PROSE, THIN_FRAGMENT),), _fragments(), table,
        _reviewer([]), diagnostics=[],
    )
    assert [sentence.text for sentence in kept] == []


@pytest.mark.parametrize("table", (None, TABLE))
def test_summary_path_keeps_a_backed_sentence(table):
    kept = verify_sentences(
        (_sentence(GOOD_PROSE, GOOD_FRAGMENT),), _fragments(), table,
        _reviewer([]), diagnostics=[],
    )
    assert [sentence.text for sentence in kept] == [GOOD_PROSE]


# ── 재작성 재검수 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("table", (None, TABLE))
def test_a_rewrite_cannot_bring_back_an_unbacked_confirmed_prose(table):
    """«거짓» 뒤 재작성으로 돌아온 문장도 같은 결속을 받아야 한다."""

    from src.features.composer.verify import REWRITE_PROMPT_HEADER

    calls: list[str] = []

    def ask(prompt):
        calls.append(prompt)
        if REWRITE_PROMPT_HEADER in prompt:
            # 고쳐 쓴 문장도 자기 원문이 뒷받침하지 않는다.
            return THIN_PROSE
        rows = []
        for item in review_items(_GRADE_RE.sub("", prompt)):
            verdict = "참" if item.text.strip() == THIN_PROSE else "거짓"
            rows.append({"번호": item.number, "장": item.section,
                         "근거": _echoed_citations(item), "결과": verdict})
        return json.dumps({"판정": rows}, ensure_ascii=False)

    report = ComposedReport((ComposedSection(
        SECTION, (_sentence(GOOD_PROSE, THIN_FRAGMENT),)),))
    checked = verify_report(report, _fragments(), table, ask, diagnostics=[])
    assert any(REWRITE_PROMPT_HEADER in call for call in calls), "재작성 경로를 지나지 않았다"
    assert THIN_PROSE not in _kept(checked), (
        "재검수에서 자기 원문 근거 검사가 걸리지 않았다"
    )


# ── 자기 원문 매핑 완전성 ───────────────────────────────────────────────
def test_a_sentence_with_a_broken_citation_never_reaches_this_check():
    """조각에 «없는» 인용이 달린 문장은 검수 전에 이미 빠진다.

    `_grounding_candidate` 는 조각에 없는 인용을 «빈 문자열이 아니라 아예 빼고»
    넘긴다. 그래서 가드의 `_unrecoverable`(빈 문자열이 섞이면 물러난다)은 그
    모양을 못 본다. 다만 `_machine_check` 규칙 ①이 깨진 인용을 가진 문장을
    미리 제거하므로, 가드가 «반쪽 원문»으로 판정하는 일은 실제로 일어나지 않는다.
    이 시험은 그 전제가 유지되는지 지킨다 — 규칙 ①이 느슨해지면 여기서 깨진다.
    """

    diagnostics: list[dict] = []
    sentence = ComposedSentence(GOOD_PROSE, ("99", GOOD_FRAGMENT), GRADE_CONFIRMED)
    report = ComposedReport((ComposedSection(SECTION, (sentence,)),))
    checked = verify_report(
        report, _fragments(), None, _reviewer([]), diagnostics=diagnostics,
    )
    assert _kept(checked) == []
    reasons = {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}
    assert not (reasons & set(PROSE_OWN_SOURCE_REASON_CODES)), (
        "깨진 인용을 자기 원문 근거 부족으로 «단정»했다면 사유가 잘못 붙은 것이다"
    )


def test_an_empty_source_text_makes_the_check_stand_down():
    """원문을 복원하지 못한 조각(빈 글자)이면 판정하지 않고 물러난다."""

    diagnostics: list[dict] = []
    sentence = ComposedSentence(THIN_PROSE, ("13",), GRADE_CONFIRMED)
    report = ComposedReport((ComposedSection(SECTION, (sentence,)),))
    checked = verify_report(
        report, (CollectedFragment("13", "공시", ""),), None,
        _reviewer([]), diagnostics=diagnostics,
    )
    reasons = {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}
    assert not (reasons & set(PROSE_OWN_SOURCE_REASON_CODES)), reasons


# ── 조사·어미만 다른 정상 의역은 살아야 한다 ───────────────────────────
#: 원문과 조사·어미만 다른 정상 문장. 실측으로 사라지던 모양이다.
JOSA_SOURCE = "가람은 음반을 제작합니다."
JOSA_PROSE = "가람이 음반을 제작한다."
JOSA_FRAGMENT = "21"


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("table", (None, TABLE))
def test_a_paraphrase_differing_only_in_particles_survives(grouped, table):
    """「가람은 … 제작합니다」가 뒷받침하는 「가람이 … 제작한다」는 살아야 한다.

    조사·어미만 다른 같은 낱말을 다른 낱말로 세면 근거어가 1개로 떨어져
    정상 의역이 통째로 사라진다.
    """

    def ask(prompt):
        rows = []
        for item in review_items(_GRADE_RE.sub("", prompt)):
            entry = {"번호": item.number, "장": item.section,
                     "근거": _echoed_citations(item), "결과": "참"}
            # 역할 주장이 있는 문장이라 역할 결속 근거를 함께 낸다 — 이 시험이
            # 보려는 것은 «역할 가드»가 아니라 자기 원문 근거어 계산이다.
            entry["검증근거"] = {"관계": [{
                "유형": "역할", "대상": "음반", "역할값": "제작",
                "근거": JOSA_FRAGMENT, "원문": JOSA_SOURCE,
            }]}
            rows.append(entry)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    report = ComposedReport((ComposedSection(
        SECTION, (ComposedSentence(JOSA_PROSE, (JOSA_FRAGMENT,), GRADE_CONFIRMED),)),))
    allowed = {SECTION: frozenset((JOSA_FRAGMENT,))} if grouped else None
    checked = verify_report(
        report, (CollectedFragment(JOSA_FRAGMENT, "제작 설명", JOSA_SOURCE),),
        table, ask,
        allowed_fragment_ids_by_section=allowed, diagnostics=[],
    )
    assert _kept(checked) == [JOSA_PROSE]


def test_the_support_terms_count_the_particle_variants():
    """직접 측정 — 배선이 아니라 «세는 규칙» 자체를 본다."""

    from src.features.composer.prose_own_source import own_source_support_terms

    terms = own_source_support_terms(JOSA_PROSE, [JOSA_SOURCE])
    assert set(terms) >= {"가람", "음반을", "제작"}, terms


def test_a_generic_subject_never_becomes_support_by_stripping_particles():
    """「회사는」을 벗겨 얻은 「회사」는 근거어로 세지 않는다.

    어느 보고서든 후보는 「회사는」으로 시작하고 공시 원문에는 「회사」가 늘
    있으므로, 이 낱말이 겹친다는 것은 아무것도 뒷받침하지 않는다. 실측 반례
    (SM F3)가 이 자리로 통과했다.
    """

    from src.features.composer.prose_own_source import own_source_support_terms

    terms = own_source_support_terms(
        "회사는 국내 광고시장의 침체에 직면해 있다.",
        ["당해 회사가 영위하는 국내 사업의 개요는 다음과 같습니다."],
    )
    assert "회사" not in terms, terms


# ── «애매»는 해석으로 강등된다 — 확인 등급의 낱말 계약을 대지 않는다 ────
@pytest.mark.parametrize("grouped", (False, True))
def test_an_unclear_verdict_is_demoted_not_dropped_by_this_check(grouped):
    """검수가 «애매»를 주면 해석으로 강등된다. 그 앞에서 이 검사가 먼저 지우면 안 된다."""

    def ask(prompt):
        rows = [
            {"번호": item.number, "장": item.section,
             "근거": _echoed_citations(item), "결과": "애매"}
            for item in review_items(_GRADE_RE.sub("", prompt))
        ]
        return json.dumps({"판정": rows}, ensure_ascii=False)

    report = ComposedReport((ComposedSection(
        SECTION, (ComposedSentence(THIN_PROSE, (THIN_FRAGMENT,), GRADE_CONFIRMED),)),))
    allowed = {SECTION: frozenset((THIN_FRAGMENT,))} if grouped else None
    checked = verify_report(
        report, _fragments(), None, ask,
        allowed_fragment_ids_by_section=allowed, diagnostics=[],
    )
    assert _kept(checked) == [THIN_PROSE]
    assert checked.sections[0].sentences[0].grade == GRADE_INTERPRETED
