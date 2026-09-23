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


# ══════════════════════════════════════════════════════════
# FULL 호출 장부 «검수 1회 + 재요청 자리 1» (2026-09-23 개방)
#   재요청 전용 호출자만 두 자리("bundled", "bundled_retry")로 감싼다. 최초 검수·
#   재작성·재검수·일반 검수 호출자는 한 자리 그대로다. 장부는 (회차, 역할)마다 공용
#   계수를 쓰므로 첫 검수 뒤에는 재요청 한 번만 나가고, 재작성·재검수·세 번째 검수자
#   호출은 공급자 «전»에 막힌다.
# ══════════════════════════════════════════════════════════


def _full_reviewer_wraps():
    """pipeline 이 FULL 에서 감싸는 모양 그대로의 검수자 래퍼들과 공급자 호출 기록."""
    from src.features.composer import pipeline
    from src.shared.report_generation.models import ValidationRound

    recorder = pipeline._CallLedgerRecorder()
    provider_calls = []

    def provider(name):
        def ask(prompt):
            provider_calls.append(name)
            return f"{name} 응답"
        return ask

    def wrap(name, section_ids):
        return recorder.wrap(
            provider(name), role="reviewer",
            validation_round=ValidationRound.PRIMARY, section_ids=section_ids,
        )

    return recorder, provider_calls, {
        "initial": wrap("initial", ("bundled",)),
        "retry": wrap("retry", pipeline.PRIMARY_REVIEW_RETRY_SECTION_IDS),
        "rewrite": wrap("rewrite", ("bundled",)),
        "recheck": wrap("recheck", ("bundled",)),
    }


def test_FULL_장부는_재요청_래퍼에만_두번째_검수자리를_열고_세번째는_막는다():
    recorder, provider_calls, wraps = _full_reviewer_wraps()

    wraps["initial"]("첫 검수")
    wraps["retry"]("누락 후속")
    with pytest.raises(RuntimeError):
        wraps["retry"]("세 번째 검수 시도")

    assert provider_calls == ["initial", "retry"]
    reviewers = [record for record in recorder.freeze().records if record.role == "reviewer"]
    assert [(record.section_id, record.role_index, record.outcome) for record in reviewers] == [
        ("bundled", 1, "returned"), ("bundled_retry", 2, "returned"),
    ]


@pytest.mark.parametrize("after_retry", (False, True), ids=("after_first", "after_retry"))
def test_FULL_재작성·재검수_래퍼는_첫_검수_뒤_여전히_막힌다(after_retry):
    _, provider_calls, wraps = _full_reviewer_wraps()
    wraps["initial"]("첫 검수")
    if after_retry:
        wraps["retry"]("형식 재요청")

    for name in ("rewrite", "recheck"):
        with pytest.raises(RuntimeError):
            wraps[name]("다듬기")

    assert provider_calls == (["initial", "retry"] if after_retry else ["initial"])


def test_재요청_래퍼의_자리수는_검수_호출수_정책과_같다():
    from src.features.composer import pipeline
    from src.shared.report_recovery import (
        PRIMARY_REVIEW_CALLS, PRIMARY_REVIEW_RETRY_CALLS,
    )

    assert pipeline.PRIMARY_REVIEW_RETRY_SECTION_IDS == ("bundled", "bundled_retry")
    assert len(pipeline.PRIMARY_REVIEW_RETRY_SECTION_IDS) == (
        PRIMARY_REVIEW_CALLS + PRIMARY_REVIEW_RETRY_CALLS
    )


def test_재요청_자리_수는_verify의_두번째_호출_예산과_같다():
    """2026-09-24 독립 검토 F5 — 두 겹이 따로 적은 «두 번째 검수 호출» 수를 맞댄다.

    verify 는 첫 검수 뒤 형식 재요청·누락 후속을 합쳐 ``PARSE_RETRY_LIMIT`` 회까지
    보낸다(`_ask_grouped_verdicts`). FULL 장부·영수증·회복 정책은 그 자리를
    ``PRIMARY_REVIEW_RETRY_CALLS`` 로 센다. 한쪽만 늘리면 늘어난 호출이 장부에
    막히거나(검수 불능) 영수증 검사에서 멈춘다.
    """
    from src.features.composer.constants import PARSE_RETRY_LIMIT
    from src.shared.report_recovery import PRIMARY_REVIEW_RETRY_CALLS

    assert PRIMARY_REVIEW_RETRY_CALLS == PARSE_RETRY_LIMIT


def test_run_v2는_FULL에서_재요청_호출자만_두_자리로_감싼다(monkeypatch):
    """pipeline 이 실제로 어떤 모양으로 감싸는지 — 위 래퍼 시험의 전제를 못 박는다."""
    from src.features.composer import pipeline
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer, _FakeWriter, _strict_packet_set,
    )
    from src.features.composer.validate import V2ValidationError
    from src.shared.report_evidence.constants import ReleaseMode
    from src.shared.report_generation.models import ValidationRound

    wrapped = []

    class _Spy(pipeline._CallLedgerRecorder):
        def wrap(self, ask, *, role, validation_round, section_ids):
            wrapped.append((ask, role, validation_round, section_ids))
            return super().wrap(
                ask, role=role, validation_round=validation_round,
                section_ids=section_ids,
            )

    monkeypatch.setattr(pipeline, "_CallLedgerRecorder", _Spy)
    reviewer, initial, retry, rewrite, recheck = (_FakeReviewer() for _ in range(5))
    with pytest.raises(V2ValidationError, match="post_validation_safety_blocked"):
        pipeline.run_v2(
            "가나다전자", {}, None,
            writer_ask=_FakeWriter(), reviewer_ask=reviewer,
            initial_reviewer_ask=initial, initial_retry_reviewer_ask=retry,
            rewrite_ask=rewrite, recheck_ask=recheck,
            release_mode=ReleaseMode.FULL, section_evidence_packets=_strict_packet_set(),
            company_id="00123456", build_identity_sha256="b" * 64,
        )

    primary_reviewers = {
        id(ask): section_ids
        for ask, role, validation_round, section_ids in wrapped
        if role == "reviewer" and validation_round is ValidationRound.PRIMARY
    }
    assert primary_reviewers[id(retry)] == ("bundled", "bundled_retry")
    for ask in (reviewer, initial, rewrite, recheck):
        assert primary_reviewers[id(ask)] == ("bundled",)


@pytest.mark.parametrize(
    "case", ("provider_failure", "coordination", "budget_block", "already_degradable"),
)
def test_재요청_자리_래퍼는_공급자_호출_실패만_강등_가능으로_바꾼다(case):
    """2026-09-24 결정 3 개정 — FULL 재요청 전용 래퍼(`_provider_failure_degradable`)의 경계.

    원인이 공급자 호출 실패(ProviderCallFailed)일 때만 ``provider_failure`` 로 다시
    올린다. 조정 오류(전역 취소·epoch 부류)·예산/계정 차단·이미 강등 가능한 예외는
    같은 예외 그대로 재전파한다.
    """
    from src.features.budget.provider_budget import ProviderBudgetUnavailable
    from src.features.composer import pipeline
    from src.features.composer.port import AskFatalError
    from src.features.composer.tests.test_section_public_manifest import (
        _provider_call_failed,
    )
    from src.shared.generation_coordination import GenerationCoordinationError

    original = {
        "provider_failure": lambda: AskFatalError(_provider_call_failed()),
        "coordination": lambda: AskFatalError(GenerationCoordinationError("전역 취소")),
        "budget_block": lambda: AskFatalError(ProviderBudgetUnavailable("미확정 호출 뒤 차단")),
        "already_degradable": lambda: AskFatalError(
            RuntimeError("요청 한도"), call_limit=True,
        ),
    }[case]()

    def ask(_prompt):
        raise original

    assert pipeline._provider_failure_degradable(None) is None
    with pytest.raises(AskFatalError) as raised:
        pipeline._provider_failure_degradable(ask)("재요청")

    if case == "provider_failure":
        assert raised.value is not original
        assert raised.value.cause is original.cause
        assert raised.value.provider_failure and raised.value.degradable
        assert not (raised.value.call_limit or raised.value.request_budget)
        assert raised.value.__cause__ is original
    else:
        assert raised.value is original


def test_운영_FULL의_근거_재작성은_장부자리가_없어_보내지_않고_진단에_한_줄만_남긴다(
    monkeypatch,
):
    """2026-09-24 총괄 결정 4 + 발견 1 확정 (a) — 운영 FULL 은 근거 재작성 스위치가 켜져 있다.

    재작성·재검수 래퍼는 한 자리(``bundled``)라 첫 검수 뒤에는 장부가 반드시 막는다.
    그래서 pipeline 이 «재작성 자리 없음»을 미리 답하고, verify 는 재작성을 «시도하지
    않는다». 공급자 호출도, 장부 래퍼 도달도, 실패 기록도 없다. 진단에는 가짜 «작성형식
    실패·실제전송·읽기실패» 대신 «장부자리없음» 한 줄만 남는다. 두 번째 검수 자리
    (``bundled_retry``)는 재요청 전용 호출자만 쓴다.
    """
    import contextlib

    from src.features.composer import pipeline
    from src.features.composer.tests.test_grounding_rewrite import (
        _PacketWriter, _packet_reviewer_class,
    )
    from src.features.composer.tests.test_pipeline import (
        _strict_fragments, _strict_packet_set,
    )
    from src.features.composer.validate import V2ValidationError
    from src.shared.report_evidence.constants import ReleaseMode

    recorders = []
    attempts = {"rewrite": 0, "recheck": 0}

    class _ProviderSpy:
        def __init__(self):
            self.prompts = []

        def __call__(self, prompt):
            self.prompts.append(prompt)
            raise AssertionError("장부가 막아야 할 호출이 공급자에 닿았다")

    rewrite, recheck = _ProviderSpy(), _ProviderSpy()

    class _Spy(pipeline._CallLedgerRecorder):
        def __init__(self):
            super().__init__()
            recorders.append(self)

        def wrap(self, ask, *, role, validation_round, section_ids):
            tracked = super().wrap(
                ask, role=role, validation_round=validation_round,
                section_ids=section_ids,
            )
            name = "rewrite" if ask is rewrite else "recheck" if ask is recheck else ""
            if not name:
                return tracked

            def counted(prompt):
                attempts[name] += 1  # 장부 래퍼까지 온 «시도» 수
                return tracked(prompt)

            counted.__dict__.update(tracked.__dict__)
            return counted

    monkeypatch.setattr(pipeline, "_CallLedgerRecorder", _Spy)
    reviewer = _packet_reviewer_class()()
    composition: list[dict] = []
    # 이 fixture 는 끝에서 품질 판정으로 막힌다 — 이 시험은 그 «전»의 호출만 본다.
    with contextlib.suppress(V2ValidationError):
        pipeline.run_v2(
            "가나다전자", _strict_fragments(), None,
            writer_ask=_PacketWriter(), reviewer_ask=reviewer,
            rewrite_ask=rewrite, recheck_ask=recheck,
            release_mode=ReleaseMode.FULL, section_evidence_packets=_strict_packet_set(),
            company_id="00123456", build_identity_sha256="b" * 64,
            grounding_rewrite_enabled=True,
            preserve_on_ask_failure=True,
            composition_diagnostics_sink=composition,
        )

    grounding = [record for record in composition if record.get("step") == "8_근거결속_재작성"]
    # 재작성 대상은 있었다(시험 전제) — 그래도 장부 래퍼에조차 가지 않았다.
    assert [record["상태"] for record in grounding] == ["장부자리없음"]
    assert grounding[0]["대상"] >= 1 and "실제전송" not in grounding[0]
    assert attempts == {"rewrite": 0, "recheck": 0}
    assert rewrite.prompts == [] and recheck.prompts == []
    assert reviewer.batch_prompts == []
    recorder, = recorders
    records = recorder.freeze().records
    assert [record for record in records if record.outcome != "returned"] == []
    assert [record.section_id for record in records if record.role == "reviewer"] == [
        "bundled",
    ]
