# -*- coding: utf-8 -*-
"""보고서 기준일이 «실제로» 임원 재직 가드까지 흘러가는지 못 박는다.

독립 검토(P2-3 계열 관찰) — `grounding.constrain_verdicts` 에 `baseline_date`
매개변수는 있는데 **운영 호출자가 0곳**이라 값이 언제나 `None` 이었다. 그러면
`executive_status_guard` 는 날짜 문턱 없이 이탈 «표지» 존재만으로 판정한다.
「기준일 이후에 물러날 예정」인 임원을 현직으로 쓴 정상 문장까지 빠진다.

여기서 지키는 것 — 네 겹을 «각각» 확인한다. 한 겹만 보면 위에서 안 넘겨도,
아래에서 안 받아도 초록불이 된다.
  ① 운영 진입 `run_v2` 가 `as_of_date` 를 검증기·도식검사에 넘긴다.
  ② `verify_report` 가 받은 값을 근거 결속의 임원 가드까지 흘린다.
  ③ `check_diagrams` 도 같은 값을 같은 가드까지 흘린다.
  ④ 요약은 ②를 통과한 본문 문장에서만 온다. 재검증(`verify_sentences`)을
     한 번도 부르지 않고, 고른 «번호»가 가리키는 그 문장이 그대로 실린다.
     (2026-09-11 이전에는 ④가 「요약 재검증도 같은 기준일을 받는가」였다.
      요약을 AI가 새로 쓰던 시절, 한 보고서 안에 잣대가 둘이 되는 것을 막던
      겹이다. 요약이 축자 재사용이 되면서 그 겹이 필요 없어졌고, 대신 위
      두 가지를 지킨다 — 각 시험 docstring에 근거를 적었다.)

⚠️ 시험 안에서 값을 따로 만들어 검사하지 않는다 — 가드가 «실제로 받은» 인자를
   그대로 기록해 단정한다. 그러지 않으면 배선이 끊겨도 초록불이 된다.
"""

from __future__ import annotations

import json
import re

import pytest

from src.features.composer import grounding, pipeline
from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer,
    _FakeWriter,
    _raw_fragments,
    _summary_selection_json,
)
from src.features.composer.render import INTERPRETATION_MARKER
from src.features.composer.verify import verify_report

#: 운영 호출자가 실제로 넘기는 모양 그대로 — `real.py` 는
#: `as_of_date=business_date.isoformat()` 로 ISO 날짜를 넘긴다.
BASELINE = "2026-08-24"

FRAGMENT = "1"
SECTION = "business_model"
SOURCE = "당사는 기업 대상 어학 교육 과정을 운영합니다."
CANDIDATE = "당사는 기업 대상 어학 교육 과정을 운영한다."


def _fragments() -> tuple[CollectedFragment, ...]:
    return (CollectedFragment(FRAGMENT, "사업내용", SOURCE),)


#: 렌더가 붙이는 «표시» 장식 — 인용 번호 `[n]`과 «해석» 표지.
#: 같은 문장이라도 본문과 요약에서 번호 표시 정책이 달라(`_marker_visibility`)
#: 글자가 갈린다. 요약이 «본문 문장 그대로인가»를 보려면 이 장식을 걷어낸다.
_DISPLAY_MARKER_RE = re.compile(r"\s*\[\d+\]")


def _bare(text: str) -> str:
    """표시 장식을 걷어낸 문장 본문만 남긴다."""

    stripped = _DISPLAY_MARKER_RE.sub("", text)
    return stripped.replace(INTERPRETATION_MARKER, "").strip()


def _approving_ask() -> object:
    """번호마다 «참»만 돌려주는 가짜 검수 AI — 기계 가드만 남긴다."""

    def ask(prompt: str) -> str:
        numbers = [
            int(line.split("]")[0][1:])
            for line in prompt.splitlines()
            if line.startswith("[") and "]" in line and line[1:].split("]")[0].isdigit()
        ]
        return json.dumps(
            {"판정": [
                {"번호": number, "장": SECTION, "근거": [FRAGMENT], "결과": "참"}
                for number in numbers
            ]},
            ensure_ascii=False,
        )

    return ask


@pytest.fixture()
def guard_calls(monkeypatch):
    """임원 가드가 «실제로 받은» baseline_date 를 그대로 모은다."""

    seen: list[object] = []
    real = grounding.executive_status_problem

    def spy(text, sources, cells=None, *, baseline_date=None):
        seen.append(baseline_date)
        return real(text, sources, cells, baseline_date=baseline_date)

    monkeypatch.setattr(grounding, "executive_status_problem", spy)
    return seen


# ══════════════════════════════════════════════════════════
# ② verify_report → 근거 결속 → 임원 가드
# ══════════════════════════════════════════════════════════


def test_verify_report_passes_the_baseline_date_to_the_executive_guard(guard_calls):
    report = ComposedReport((
        ComposedSection(
            SECTION, (ComposedSentence(CANDIDATE, (FRAGMENT,), GRADE_CONFIRMED),)
        ),
    ))

    verify_report(
        report, _fragments(), None, _approving_ask(), baseline_date=BASELINE,
    )

    assert guard_calls, "임원 가드가 한 번도 안 불렸다 — 이 시험이 아무것도 못 잰다"
    assert set(guard_calls) == {BASELINE}, guard_calls


def test_verify_report_without_a_baseline_date_still_calls_the_guard(guard_calls):
    """기존 호출 계약은 그대로다 — 안 넘기면 종전처럼 None 으로 판정한다."""

    report = ComposedReport((
        ComposedSection(
            SECTION, (ComposedSentence(CANDIDATE, (FRAGMENT,), GRADE_CONFIRMED),)
        ),
    ))

    verify_report(report, _fragments(), None, _approving_ask())

    assert guard_calls, "임원 가드가 한 번도 안 불렸다"
    assert set(guard_calls) == {None}, guard_calls


# ══════════════════════════════════════════════════════════
# ③ check_diagrams → 근거 결속 → 임원 가드
# ══════════════════════════════════════════════════════════


def test_check_diagrams_passes_the_baseline_date_to_the_executive_guard(guard_calls):
    report = ComposedReport((
        ComposedSection(
            SECTION,
            (),
            flow_rows=(FlowRow(("어학 교육", "기업 대상 운영"), (FRAGMENT,)),),
        ),
    ))

    check_diagrams(report, _fragments(), _approving_ask(), baseline_date=BASELINE)

    assert guard_calls, "임원 가드가 한 번도 안 불렸다"
    assert set(guard_calls) == {BASELINE}, guard_calls


# ══════════════════════════════════════════════════════════
# ① run_v2 → verify_report·check_diagrams
# ══════════════════════════════════════════════════════════


def test_run_v2_hands_its_as_of_date_to_both_verification_stages(monkeypatch):
    """운영 진입이 실제로 넘기는가 — 두 단계가 «받은 인자»를 그대로 기록한다."""

    verify_seen: list[object] = []
    diagram_seen: list[object] = []
    real_verify = pipeline.verify_report
    real_diagrams = pipeline.check_diagrams

    def verify_spy(*args, **kwargs):
        verify_seen.append(kwargs.get("baseline_date"))
        return real_verify(*args, **kwargs)

    def diagram_spy(*args, **kwargs):
        diagram_seen.append(kwargs.get("baseline_date"))
        return real_diagrams(*args, **kwargs)

    monkeypatch.setattr(pipeline, "verify_report", verify_spy)
    monkeypatch.setattr(pipeline, "check_diagrams", diagram_spy)

    pipeline.run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=_FakeWriter(),
        reviewer_ask=_FakeReviewer(),
        corp_type="상장사",
        as_of_date=BASELINE,
    )

    assert verify_seen, "verify_report 가 안 불렸다 — 이 시험이 아무것도 못 잰다"
    assert diagram_seen, "check_diagrams 가 안 불렸다 — 이 시험이 아무것도 못 잰다"
    assert set(verify_seen) == {BASELINE}, verify_seen
    assert set(diagram_seen) == {BASELINE}, diagram_seen


def test_run_v2_summary_comes_from_verified_body_without_a_recheck(monkeypatch):
    """④ 요약은 «검증된 본문 문장»에서만 온다 — 재검증 호출이 아예 없다.

    ★ 이 시험이 대신하는 것 (2026-09-11) — 예전 ④는 「SHADOW 요약 재검증도
      본문과 같은 기준일을 받는다」였다. 그 겹이 필요했던 이유는 요약을 AI가
      «새로 썼기» 때문이다. 한 보고서 안에 잣대가 둘이 되는 것을 막으려고
      요약에도 같은 기준일로 같은 검수를 다시 걸었다.
      이제 요약은 본문 문장을 글자 그대로 고른다. 그 문장은 이미 ②
      `verify_report`가 그 기준일로 판정한 문장이므로 «잣대가 둘이 될 자리»
      자체가 없다. 그래서 지키는 것을 「같은 값을 받는가」에서 「요약 문장이
      본문 문장인가 + 재검증을 안 부르는가」로 바꾼다. 삭제가 아니라 대체다.
    ⚠️ spy 는 정의 모듈(`composer/verify.py`)에 건다. 파이프라인이 이름을 다시
      들여오거나 다른 모듈이 대신 불러도 여기서 잡힌다.
    """

    from src.features.composer import verify as verify_module

    body_seen: list[object] = []
    recheck_calls: list[tuple] = []
    real_verify = pipeline.verify_report
    real_sentences = verify_module.verify_sentences

    def verify_spy(*args, **kwargs):
        body_seen.append(kwargs.get("baseline_date"))
        return real_verify(*args, **kwargs)

    def sentences_spy(*args, **kwargs):
        recheck_calls.append((args, kwargs))
        return real_sentences(*args, **kwargs)

    monkeypatch.setattr(pipeline, "verify_report", verify_spy)
    monkeypatch.setattr(verify_module, "verify_sentences", sentences_spy)

    output = pipeline.run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=_FakeWriter(),
        reviewer_ask=_FakeReviewer(),
        corp_type="상장사",
        as_of_date=BASELINE,
    )

    assert set(body_seen) == {BASELINE}, body_seen
    assert recheck_calls == [], (
        "요약 재검증이 다시 배선됐다 — 같은 문장을 두 번, 다른 잣대로 검수한다"
    )
    summary_texts = [_bare(item.text) for item in output.report.summary_items]
    assert summary_texts, "요약이 비었다 — 이 시험이 아무것도 못 잰다"
    body_texts = {
        _bare(text)
        for section in output.report.sections
        for text, _cite in section.prose_lines
    }
    for text in summary_texts:
        assert text in body_texts, (
            f"요약 문장이 본문에 없다 — 어딘가에서 새 글자가 생겼다: {text}"
        )


#: 고르기 프롬프트의 후보 한 줄 — `logic.build_summary_selection_prompt` 계약.
_CANDIDATE_LINE_RE = re.compile(r"^(\d+)\. \[([^\]]+)\] (.+)$", re.MULTILINE)


def test_run_v2_summary_carries_the_exact_sentence_each_number_points_at():
    """④-b 고른 «번호»가 가리키는 그 문장이 요약에 실린다.

    ★ 이 시험이 대신하는 것 (2026-09-11) — 예전 ④-b는 `verify_sentences`
      진입 함수가 받은 기준일이 임원 가드까지 가는지 보았다. 그 경로는 요약에서
      사라졌다(본문 경로 ②는 바로 위 시험이 그대로 지킨다).
      요약이 축자 재사용이 된 뒤로 「본문 첫 문장과 유사도 1.0」은 결함이 아니라
      설계다. 대신 새로 생긴 위험이 «번호↔문장 대응»이다 — 후보 목록을 1부터
      세는데 코드가 0부터 세면, 요약은 여전히 «본문 문장»이라 어떤 검사도
      안 걸리면서 엉뚱한 문장이 실린다. 그 자리를 여기서 못 박는다.
    ⚠️ 시험 안에서 기대값을 따로 만들지 않는다 — AI가 «실제로 본» 프롬프트의
      후보 줄에서 번호를 되짚어 기대 문장을 만든다.
    """

    writer = _FakeWriter()
    output = pipeline.run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=_FakeReviewer(),
        corp_type="상장사",
        as_of_date=BASELINE,
    )

    고르기_프롬프트 = [p for p in writer.prompts if "핵심 요약" in p]
    assert len(고르기_프롬프트) == 1, "요약 고르기 호출은 정확히 1회다"
    후보 = {
        int(number): (title, text)
        for number, title, text in _CANDIDATE_LINE_RE.findall(고르기_프롬프트[0])
    }
    assert len(후보) >= 3, f"후보가 너무 적다 — 이 시험이 아무것도 못 잰다: {후보}"

    고른번호 = json.loads(_summary_selection_json(고르기_프롬프트[0]))
    assert len(고른번호) == 3, 고른번호
    기대문장 = [후보[number][1] for number in 고른번호]

    assert [_bare(item.text) for item in output.report.summary_items] == 기대문장
    # 후보 줄은 «어느 장의 문장인지»를 함께 실어 준다 — AI가 장을 섞어 고를 수
    # 있게 하는 재료다. 가짜 AI는 그 재료를 써서 장마다 하나씩 골랐다.
    assert len({후보[number][0] for number in 고른번호}) == len(고른번호)


def test_run_v2_without_an_as_of_date_keeps_none(monkeypatch):
    """기준일이 없는 호출은 «빈 문자열»이 아니라 None 으로 내려간다.

    ★ 빈 문자열을 그대로 흘리면 가드가 `date.fromisoformat("")` 로 예외를
      먹고 나서 조용히 표지 판정으로 돌아간다. 값이 없다는 사실을 타입으로
      드러내는 편이 진단에서도 정직하다.
    """

    seen: list[object] = []
    real_verify = pipeline.verify_report

    def verify_spy(*args, **kwargs):
        seen.append(kwargs.get("baseline_date"))
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(pipeline, "verify_report", verify_spy)

    pipeline.run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=_FakeWriter(),
        reviewer_ask=_FakeReviewer(),
        corp_type="상장사",
    )

    assert seen, "verify_report 가 안 불렸다"
    assert set(seen) == {None}, seen
