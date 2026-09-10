# -*- coding: utf-8 -*-
"""최초 본문 검수만 «다른 호출자»로 나가고 나머지는 예전 호출자를 그대로 쓴다.

★ 왜 나누는가 — 최초 본문 검수 한 번은 후보 전부의 판정과 각 후보가 요구받은
  근거 배열을 한 응답에 담아야 해서 뒤따르는 재작성·재검수·요약 검수보다 훨씬
  큰 출력이 필요하다. 그 한 번에만 큰 출력 여유를 주고, 작은 후속 호출은
  예전 호출자를 그대로 써야 예약이 단계 예산을 밀어내지 않는다.
★ 어느 검수인지는 «부르는 쪽이 인자로» 정한다. 프롬프트 글자·입력 크기·호출
  순번으로 짐작하지 않는다 — 이 시험은 서로 다른 가짜 callable 두 개로 그
  분리가 실제로 일어나는지 본다.
"""

import json
import re

import pytest

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import (
    REWRITE_PROMPT_HEADER, verify_report, verify_sentences,
)

_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")

SECTION = "identity"
SOURCE = "당사는 음악 콘텐츠 기획·제작·유통을 영위하고 있습니다."
GOOD = "회사는 음악 콘텐츠 기획·제작·유통을 영위한다."
#: 어느 가드에도 걸리지 않고 «검수만» 거짓이라고 부르는 문장.
BAD = "회사는 음악 콘텐츠 기획을 영위한다."
FRAGMENT = "1"


def _fragments():
    return (CollectedFragment(FRAGMENT, "사업내용", SOURCE),)


def _verdict_rows(prompt, *, false_for=()):
    rows = []
    for item in review_items(_GRADE_RE.sub("", prompt)):
        text = item.text.strip()
        rows.append({
            "번호": item.number, "장": item.section,
            "근거": [str(c).split()[-1] for c in item.citations],
            "결과": "거짓" if text in false_for else "참",
        })
    return json.dumps({"판정": rows}, ensure_ascii=False)


class _Recorder:
    """어떤 호출자가 어떤 프롬프트를 받았는지 기록하는 가짜 검수 AI."""

    def __init__(self, name, *, reply):
        self.name = name
        self.prompts: list[str] = []
        self._reply = reply

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self._reply(prompt)

    @property
    def count(self):
        return len(self.prompts)


def _followup(prompt):
    if REWRITE_PROMPT_HEADER in prompt:
        return GOOD
    return _verdict_rows(prompt)


def _report(*sentences):
    return ComposedReport((ComposedSection(SECTION, tuple(sentences)),))


@pytest.mark.parametrize("grouped", (False, True))
def test_the_first_body_review_uses_the_initial_callable(grouped):
    """최초 본문 검수는 initial 호출자로 나가고 기존 호출자는 쓰이지 않는다."""

    initial = _Recorder("initial", reply=_verdict_rows)
    followup = _Recorder("followup", reply=_followup)
    allowed = {SECTION: frozenset((FRAGMENT,))} if grouped else None
    checked = verify_report(
        _report(ComposedSentence(GOOD, (FRAGMENT,), GRADE_CONFIRMED)),
        _fragments(), None, followup,
        allowed_fragment_ids_by_section=allowed,
        diagnostics=[], initial_ask=initial,
    )
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]
    assert initial.count == 1, initial.count
    assert followup.count == 0, followup.prompts


def test_the_rewrite_and_recheck_use_the_existing_callable():
    """«거짓» 뒤 재작성·재검수는 예전 호출자로만 나간다 — 큰 상한을 쓰지 않는다."""

    initial = _Recorder(
        "initial", reply=lambda prompt: _verdict_rows(prompt, false_for=(BAD,)))
    followup = _Recorder("followup", reply=_followup)
    checked = verify_report(
        _report(ComposedSentence(BAD, (FRAGMENT,), GRADE_CONFIRMED)),
        _fragments(), None, followup, diagnostics=[], initial_ask=initial,
    )
    assert initial.count == 1, initial.prompts
    # 재작성 1회 + 재검수 1회가 모두 기존 호출자로 나간다.
    assert followup.count == 2, followup.count
    assert any(REWRITE_PROMPT_HEADER in prompt for prompt in followup.prompts)
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]


def test_the_parse_retry_of_the_first_review_stays_on_the_initial_callable():
    """최초 검수의 파싱 재요청도 initial 호출자로 나간다."""

    state = {"asked": 0}

    def flaky(prompt):
        state["asked"] += 1
        if state["asked"] == 1:
            return "형식이 아닌 응답"
        return _verdict_rows(prompt)

    initial = _Recorder("initial", reply=flaky)
    followup = _Recorder("followup", reply=_followup)
    checked = verify_report(
        _report(ComposedSentence(GOOD, (FRAGMENT,), GRADE_CONFIRMED)),
        _fragments(), None, followup, diagnostics=[], initial_ask=initial,
    )
    assert initial.count == 2, initial.count
    assert followup.count == 0, followup.prompts
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]


def test_the_summary_review_never_uses_the_initial_callable():
    """요약 검수는 initial 인자를 받지 않으므로 예전 호출자 그대로다."""

    followup = _Recorder("followup", reply=_followup)
    kept = verify_sentences(
        (ComposedSentence(GOOD, (FRAGMENT,), GRADE_CONFIRMED),),
        _fragments(), None, followup, diagnostics=[],
    )
    assert [s.text for s in kept] == [GOOD]
    assert followup.count == 1


@pytest.mark.parametrize("grouped", (False, True))
def test_without_the_initial_callable_the_old_contract_is_unchanged(grouped):
    """initial 을 넘기지 않으면 예전과 똑같이 호출자 하나가 전부 처리한다."""

    followup = _Recorder("followup", reply=_followup)
    allowed = {SECTION: frozenset((FRAGMENT,))} if grouped else None
    checked = verify_report(
        _report(ComposedSentence(GOOD, (FRAGMENT,), GRADE_CONFIRMED)),
        _fragments(), None, followup,
        allowed_fragment_ids_by_section=allowed, diagnostics=[],
    )
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]
    assert followup.count == 1


def test_the_legacy_positional_call_shape_still_works():
    """인자 없이 부르던 예전 모양(진단·허용근거·initial 전부 없음)도 그대로다."""

    followup = _Recorder("followup", reply=_followup)
    checked = verify_report(
        _report(ComposedSentence(GOOD, (FRAGMENT,), GRADE_CONFIRMED)),
        _fragments(), None, followup,
    )
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]
    assert followup.count == 1



def test_full_initial_review_records_the_actual_request_and_response(monkeypatch):
    """공개 품질 차단 전에 실행된 최초 검수도 FULL 호출 장부에서 빠지지 않는다."""
    from hashlib import sha256
    from src.features.composer import pipeline
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer, _FakeWriter, _strict_packet_set,
    )
    from src.features.composer.validate import V2ValidationError
    from src.shared.report_evidence.constants import ReleaseMode
    from src.shared.report_generation.models import ValidationRound

    recorder = pipeline._CallLedgerRecorder()
    monkeypatch.setattr(pipeline, "_CallLedgerRecorder", lambda: recorder)
    writer, initial, followup = _FakeWriter(), _FakeReviewer(), _FakeReviewer()
    with pytest.raises(V2ValidationError, match="post_validation_safety_blocked"):
        pipeline.run_v2(
            "가나다전자", {}, None,
            writer_ask=writer, reviewer_ask=followup, initial_reviewer_ask=initial,
            release_mode=ReleaseMode.FULL, section_evidence_packets=_strict_packet_set(),
            company_id="00123456", build_identity_sha256="b" * 64,
        )
    records = recorder.freeze().records
    assert writer.section_calls == 9
    assert len(initial.prompts) == 1 and followup.prompts == []
    assert recorder.calls_for(ValidationRound.PRIMARY, role="writer") == 9
    assert recorder.calls_for(ValidationRound.PRIMARY, role="reviewer") == 1
    review, = [record for record in records if record.role == "reviewer"]
    prompt = initial.prompts[0]
    assert review.sequence == 10 and review.role_index == 1
    assert review.section_id == "bundled" and review.outcome == "returned"
    assert review.prompt_sha256 == sha256(prompt.encode("utf-8")).hexdigest()
    assert review.response_sha256 == sha256(_FakeReviewer()(prompt).encode("utf-8")).hexdigest()
