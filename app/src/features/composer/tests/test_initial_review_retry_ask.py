# -*- coding: utf-8 -*-
"""최초 본문 검수의 «파싱 재요청»만 별도 호출자로 나간다.

★ 왜 나누는가 (2026-09-13 실측) — 1차 검수 답이 정상 종료했는데 JSON 문법
  오류로 68문장 중 0행만 읽혔다. 규칙대로 재요청을 걸었지만 호출 «전» 예약액이
  출력 상한 24,000토큰으로 잡혀 남은 예약액을 넘겼고(ProviderBudgetExceeded),
  1차 검수는 강등 대상이 아니라 조사 전체가 멈췄다. 재요청은 같은 질문을 형식만
  고쳐 다시 받는 것이라 답 길이가 첫 답과 비슷하므로, 재요청 «그 한 번»만
  작은 상한을 가진 호출자로 보내면 예약액이 줄어 들어간다.
★ 어느 호출이 재요청인지는 «부르는 쪽이 인자로» 정한다. 프롬프트 글자
  (RETRY_REMINDER 포함 여부)나 호출 순번으로 짐작하지 않는다 — 이 시험은 서로
  다른 가짜 callable 셋으로 그 분리가 실제로 일어나는지 본다.
"""

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, RETRY_REMINDER
from src.features.composer.port import ComposedSentence
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA
from src.features.composer.tests.test_initial_reviewer_call_separation import (
    BAD,
    FRAGMENT,
    GOOD,
    SECTION,
    _Recorder,
    _fragments,
    _followup,
    _report,
    _verdict_rows,
)
from src.features.composer.verify import REWRITE_PROMPT_HEADER, verify_report

#: 실측 사고와 같은 «정상 종료인데 JSON 문법이 깨진» 응답.
BROKEN_JSON = '{"판정": [{"번호": 1, "결과": "참"'


def _good_sentence():
    return ComposedSentence(GOOD, (FRAGMENT,), GRADE_CONFIRMED)


def _bad_sentence():
    return ComposedSentence(BAD, (FRAGMENT,), GRADE_CONFIRMED)


def _breaks_once(reply):
    """첫 판정 요청만 깨진 JSON으로 돌려주고 그 뒤로는 정상 응답."""

    state = {"판정요청": 0}

    def 답한다(prompt):
        if REWRITE_PROMPT_HEADER in prompt:
            return reply(prompt)
        state["판정요청"] += 1
        if state["판정요청"] == 1:
            return BROKEN_JSON
        return reply(prompt)

    return 답한다


@pytest.mark.parametrize("grouped,packet_schema", (
    (False, False), (True, False), (True, True),
), ids=("flat", "grouped-schema-off", "grouped-schema-on"))
def test_the_first_review_parse_retry_uses_the_retry_callable(
    monkeypatch, grouped, packet_schema,
):
    """1차 검수의 재요청은 재요청 전용 호출자로 나간다 (첫 호출자는 1회로 끝).

    2026-09-23 — packet(grouped) 경로도 같은 계약이다. 예전에는 «검수 1회
    고정»이라 이 재요청 자체가 없었다. packet 의 네이티브 스키마는 스위치
    ``PACKET_REVIEW_SCHEMA_ENABLED``(기본 꺼짐)를 따르고, 평문은 예전 그대로 싣는다.
    """

    if packet_schema:
        monkeypatch.setattr(
            "src.features.composer.verify.PACKET_REVIEW_SCHEMA_ENABLED", True,
        )
    initial = _Recorder("initial", reply=_breaks_once(_verdict_rows))
    retry = _Recorder("retry", reply=_verdict_rows)
    followup = _Recorder("followup", reply=_followup)
    allowed = {SECTION: frozenset((FRAGMENT,))} if grouped else None
    checked = verify_report(
        _report(_good_sentence()), _fragments(), None, followup,
        allowed_fragment_ids_by_section=allowed,
        diagnostics=[], initial_ask=initial, initial_retry_ask=retry,
    )
    assert initial.count == 1, initial.count
    assert retry.count == 1, retry.count
    assert followup.count == 0, followup.prompts
    # 재요청 프롬프트는 첫 프롬프트 + 형식 상기문이다 (내용 요구는 그대로).
    assert RETRY_REMINDER in retry.prompts[0]
    assert retry.prompts[0].startswith(initial.prompts[0])
    expected_schema = FLAT_REVIEW_SCHEMA if (not grouped or packet_schema) else None
    assert getattr(initial.prompts[0], "response_schema", None) is expected_schema
    assert getattr(retry.prompts[0], "response_schema", None) is expected_schema
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]


def test_the_retry_callable_is_untouched_when_the_first_answer_parses():
    """첫 답이 읽히면 재요청 호출자는 한 번도 쓰이지 않는다."""

    initial = _Recorder("initial", reply=_verdict_rows)
    retry = _Recorder("retry", reply=_verdict_rows)
    followup = _Recorder("followup", reply=_followup)
    checked = verify_report(
        _report(_good_sentence()), _fragments(), None, followup,
        diagnostics=[], initial_ask=initial, initial_retry_ask=retry,
    )
    assert initial.count == 1 and retry.count == 0 and followup.count == 0
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]


@pytest.mark.parametrize("grouped", (False, True))
def test_without_the_retry_callable_the_retry_stays_on_the_initial_callable(grouped):
    """재요청 호출자를 안 넘기면 예전 그대로 1차 호출자가 두 번 나간다."""

    initial = _Recorder("initial", reply=_breaks_once(_verdict_rows))
    followup = _Recorder("followup", reply=_followup)
    allowed = {SECTION: frozenset((FRAGMENT,))} if grouped else None
    checked = verify_report(
        _report(_good_sentence()), _fragments(), None, followup,
        allowed_fragment_ids_by_section=allowed,
        diagnostics=[], initial_ask=initial,
    )
    assert initial.count == 2, initial.count
    assert followup.count == 0, followup.prompts
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]


def test_the_recheck_parse_retry_never_uses_the_retry_callable():
    """재작성 뒤 «재검수»의 파싱 재요청은 예전 호출자로만 나간다."""

    initial = _Recorder(
        "initial", reply=lambda prompt: _verdict_rows(prompt, false_for=(BAD,)),
    )
    retry = _Recorder("retry", reply=_verdict_rows)
    followup = _Recorder("followup", reply=_breaks_once(_followup))
    checked = verify_report(
        _report(_bad_sentence()), _fragments(), None, followup,
        diagnostics=[], initial_ask=initial, initial_retry_ask=retry,
    )
    assert initial.count == 1, initial.prompts
    assert retry.count == 0, retry.prompts
    # 재작성 1회 + 재검수 1회(깨짐) + 그 재요청 1회가 모두 기존 호출자로 나간다.
    assert followup.count == 3, followup.count
    assert sum(
        REWRITE_PROMPT_HEADER in prompt for prompt in followup.prompts
    ) == 1
    assert sum(RETRY_REMINDER in prompt for prompt in followup.prompts) == 1
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]


@pytest.mark.parametrize("grouped", (False, True))
def test_the_retry_callable_does_not_change_the_first_review_itself(grouped):
    """재요청 호출자를 넘겨도 «첫» 검수는 예전과 똑같이 1차 호출자로 1회다."""

    initial = _Recorder("initial", reply=_verdict_rows)
    retry = _Recorder("retry", reply=_verdict_rows)
    followup = _Recorder("followup", reply=_followup)
    allowed = {SECTION: frozenset((FRAGMENT,))} if grouped else None
    checked = verify_report(
        _report(_good_sentence()), _fragments(), None, followup,
        allowed_fragment_ids_by_section=allowed,
        diagnostics=[], initial_ask=initial, initial_retry_ask=retry,
    )
    assert initial.count == 1 and followup.count == 0
    # 첫 답이 온전하면 두 경로 모두 재요청도 누락 후속도 없다. (2026-09-23부터
    # packet 경로도 못 읽힐 때·번호가 빠질 때는 이 호출자로 1회 더 묻는다 —
    # 위 test_the_first_review_parse_retry_uses_the_retry_callable[True].)
    assert retry.count == 0, retry.prompts
    assert [s.text for s in checked.sections[0].sentences] == [GOOD]
