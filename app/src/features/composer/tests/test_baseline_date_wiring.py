# -*- coding: utf-8 -*-
"""보고서 기준일이 «실제로» 임원 재직 가드까지 흘러가는지 못 박는다.

독립 검토(P2-3 계열 관찰) — `grounding.constrain_verdicts` 에 `baseline_date`
매개변수는 있는데 **운영 호출자가 0곳**이라 값이 언제나 `None` 이었다. 그러면
`executive_status_guard` 는 날짜 문턱 없이 이탈 «표지» 존재만으로 판정한다.
「기준일 이후에 물러날 예정」인 임원을 현직으로 쓴 정상 문장까지 빠진다.

여기서 지키는 것 — 세 겹을 «각각» 확인한다. 한 겹만 보면 위에서 안 넘겨도,
아래에서 안 받아도 초록불이 된다.
  ① 운영 진입 `run_v2` 가 `as_of_date` 를 검증기·도식검사에 넘긴다.
  ② `verify_report` 가 받은 값을 근거 결속의 임원 가드까지 흘린다.
  ③ `check_diagrams` 도 같은 값을 같은 가드까지 흘린다.

⚠️ 시험 안에서 값을 따로 만들어 검사하지 않는다 — 가드가 «실제로 받은» 인자를
   그대로 기록해 단정한다. 그러지 않으면 배선이 끊겨도 초록불이 된다.
"""

from __future__ import annotations

import json

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
)
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
