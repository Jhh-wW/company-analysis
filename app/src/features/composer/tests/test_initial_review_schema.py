"""최초 본문 검수의 «첫 요청»이 구조화 출력 계약을 싣는지 무과금으로 확인한다.

`initial_ask`(최초 본문 검수 전용 호출자)가 있을 때만 첫 프롬프트가
FLAT_REVIEW_SCHEMA 를 싣는다. 전송 문자열 바이트·캐시 경계·재요청 문구·호출자
선택·의미 검증기는 그대로다. 스키마는 응답의 «모양»만 제한하므로 번호 누락·
수치 증명 누락·인용 불일치는 예전처럼 거절돼야 한다(스키마 적합 ≠ 의미 합격).
실제 공급자의 문법 컴파일·지연·절감액은 이 시험의 범위가 아니다.
"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from src.features.composer import verify
from src.features.composer.constants import PARSE_RETRY_LIMIT, RETRY_REMINDER
from src.features.composer.grounding_constants import NUMERIC_KEY, REVIEW_GROUNDING_REJECTED
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.prompt_cache_constants import REVIEW_PROMPT_CACHE_ENV
from src.features.composer.review_protocol_observation import READ_JSON_SYNTAX, READ_OK
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA, ReviewPrompt
from src.features.composer.tests import test_numeric_quote_refs as numeric_fixture


TEXT = "회사는 제품을 판매한다."
SECOND_TEXT = "회사는 고객에게 서비스를 제공한다."
SENTENCE = ComposedSentence(TEXT, ("1",), "확인")
SECOND_SENTENCE = ComposedSentence(SECOND_TEXT, ("2",), "확인")
FRAGMENTS = {
    "1": CollectedFragment("1", "공시", TEXT),
    "2": CollectedFragment("2", "공시", SECOND_TEXT),
}
ITEMS = (verify._ReviewItem(1, SENTENCE, "identity"),)
TWO_ITEMS = (
    verify._ReviewItem(1, SENTENCE, "identity"),
    verify._ReviewItem(2, SECOND_SENTENCE, "identity"),
)
NUMERIC_SENTENCE = ComposedSentence(numeric_fixture._THREE_YEAR_TEXT, ("공시",), "확인")
NUMERIC_FRAGMENTS = {
    "공시": CollectedFragment("공시", "공시", numeric_fixture._THREE_YEAR_QUOTE),
}
NUMERIC_ITEMS = (verify._ReviewItem(1, NUMERIC_SENTENCE, "past_changes"),)
# 단위 환산이 필요한 수치는 원문에 글자 그대로 없어 «수치 증명»이 필수다.
CONVERSION_SENTENCE = ComposedSentence("매출액은 5억원이다.", ("1",), "확인")
CONVERSION_FRAGMENTS = {"1": CollectedFragment("1", "공시", "매출액은 500000000원이다.")}
CONVERSION_ITEMS = (verify._ReviewItem(1, CONVERSION_SENTENCE, "past_changes"),)
INVALID = "형식 오류"
# 실측(2026-09-22 실행 e5f7dcb2)과 같은 모양 — JSON 앞 서두 8자 + 본문 문법 오류.
BROKEN = '```json\n{"판정": [{"번호": 1, "장": "identity", "결과": "참"'
MISMATCHED_QUOTE = "매출액은 2023년 매출액 90억원을 기록했다."


def _never(prompt):
    pytest.fail("최초 검수 경로가 후속 호출자를 사용했습니다")


def _row(number, result="참", *, section="identity", evidence=("1",), **extra):
    row = {
        "번호": number, "장": section, "근거": list(evidence),
        "근거대조": "공시 원문과 문장이 일치한다.", "결과": result,
    }
    row.update(extra)
    return row


def _response(*rows):
    return json.dumps({"판정": list(rows)}, ensure_ascii=False)


def _numeric_row(entries):
    return _row(1, section="past_changes", evidence=("공시",),
                검증근거={NUMERIC_KEY: entries})


def _mismatched_entries():
    entries = numeric_fixture._three_year_direct_entries()
    entries[0]["원문"] = MISMATCHED_QUOTE
    return entries


def _assert_schema_valid(raw):
    Draft202012Validator(FLAT_REVIEW_SCHEMA).validate(json.loads(raw))
    return raw


def _run(items, fragments, replies, *, schema_first):
    """같은 응답 대본을 «최초 검수 경로»와 «일반 경로»에 재생한다."""
    calls, queue = [], list(replies)

    def ask(prompt):
        calls.append(prompt)
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    sinks = SimpleNamespace(
        calls=calls, diagnostics=[], protocol=[], problems={}, moves=[],
    )
    common = dict(
        diagnostics=sinks.diagnostics, protocol_diagnostics=sinks.protocol,
        grounding_problems=sinks.problems, section_moves=sinks.moves,
    )
    if schema_first:
        sinks.result = verify._ask_verdicts(
            _never, items, fragments, "",
            initial_ask=ask, initial_retry_ask=ask, **common,
        )
    else:
        sinks.result = verify._ask_verdicts(ask, items, fragments, "", **common)
    return sinks


# ── 첫 요청 계약 ────────────────────────────────────────────────


@pytest.mark.parametrize("enabled", (False, True), ids=("cache-off", "cache-on"))
def test_initial_review_sends_schema_on_first_request_with_same_bytes(monkeypatch, enabled):
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1" if enabled else "0")
    plain = verify._build_review_prompt(ITEMS, FRAGMENTS, "")
    expected_prefix = getattr(plain, "cache_prefix_chars", 0)
    assert (expected_prefix > 0) is enabled

    run = _run(ITEMS, FRAGMENTS, [_response(_row(1))], schema_first=True)
    assert run.result == {1: "참"}
    [first] = run.calls
    assert isinstance(first, ReviewPrompt)
    assert first.response_schema is FLAT_REVIEW_SCHEMA
    assert first.encode("utf-8") == plain.encode("utf-8")
    assert first.cache_prefix_chars == expected_prefix
    assert first[:expected_prefix] == plain[:expected_prefix]
    assert run.protocol[0]["입력문자"] == len(plain)
    assert run.protocol[0]["판독"] == READ_OK


@pytest.mark.parametrize("separate_retry", (False, True))
@pytest.mark.parametrize("failure", ("invalid", "broken", "error"))
def test_schema_first_failure_still_retries_exactly_once_with_reminder(separate_retry, failure):
    calls = []
    first_reply = {"invalid": INVALID, "broken": BROKEN, "error": RuntimeError("호출 오류")}[failure]

    def initial(prompt):
        calls.append(("initial", prompt))
        if len(calls) == 1:
            if isinstance(first_reply, BaseException):
                raise first_reply
            return first_reply
        return _response(_row(1))

    def retry(prompt):
        calls.append(("retry", prompt))
        return _response(_row(1))

    assert verify._ask_verdicts(
        _never, ITEMS, FRAGMENTS, "",
        initial_ask=initial, initial_retry_ask=retry if separate_retry else None,
    ) == {1: "참"}
    assert [name for name, _prompt in calls] == [
        "initial", "retry" if separate_retry else "initial",
    ]
    assert len(calls) == PARSE_RETRY_LIMIT + 1
    first, second = (prompt for _name, prompt in calls)
    assert isinstance(first, ReviewPrompt) and first.response_schema is FLAT_REVIEW_SCHEMA
    assert second == str(first) + RETRY_REMINDER
    assert second.response_schema is FLAT_REVIEW_SCHEMA
    assert second.cache_prefix_chars == first.cache_prefix_chars


def test_schema_first_retry_exhaustion_returns_none_without_a_third_call():
    run = _run(ITEMS, FRAGMENTS, [BROKEN, INVALID, _response(_row(1))], schema_first=True)
    assert run.result is None
    assert len(run.calls) == PARSE_RETRY_LIMIT + 1
    assert [observation["판독"] for observation in run.protocol] == [READ_JSON_SYNTAX, READ_JSON_SYNTAX]
    assert all(isinstance(prompt, ReviewPrompt) for prompt in run.calls)


@pytest.mark.parametrize("when", ("first", "retry"))
def test_fatal_provider_error_on_schema_request_is_not_swallowed(when):
    error = AskFatalError("제공자 장애")
    replies = [error] if when == "first" else [INVALID, error]
    with pytest.raises(AskFatalError) as raised:
        _run(ITEMS, FRAGMENTS, replies, schema_first=True)
    assert raised.value is error


@pytest.mark.parametrize("enabled", (False, True), ids=("cache-off", "cache-on"))
def test_retry_after_schema_first_keeps_cache_boundary(monkeypatch, enabled):
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1" if enabled else "0")
    plain = verify._build_review_prompt(ITEMS, FRAGMENTS, "")
    expected_prefix = getattr(plain, "cache_prefix_chars", 0)
    run = _run(ITEMS, FRAGMENTS, [INVALID, _response(_row(1))], schema_first=True)
    first, second = run.calls
    assert first.cache_prefix_chars == second.cache_prefix_chars == expected_prefix
    assert second.encode("utf-8") == (str(plain) + RETRY_REMINDER).encode("utf-8")
    assert second[:expected_prefix] == plain[:expected_prefix]


# ── 후속 검수는 예전 계약 그대로 ────────────────────────────────


def test_followup_review_without_initial_caller_keeps_plain_first_request():
    run = _run(ITEMS, FRAGMENTS, [INVALID, _response(_row(1))], schema_first=False)
    assert run.result == {1: "참"}
    first, second = run.calls
    assert type(first) is str
    assert getattr(first, "response_schema", None) is None
    assert second == first + RETRY_REMINDER and second.response_schema is FLAT_REVIEW_SCHEMA


def test_rewritten_sentence_recheck_starts_plain():
    calls = []

    def recheck(prompt):
        calls.append(prompt)
        return _response(_row(1))

    final = {}
    verify._recheck_rewritten(_never, ITEMS, FRAGMENTS, "", "", final, recheck_ask=recheck)
    assert final[1].verification_state == "verified"
    assert len(calls) == 1 and type(calls[0]) is str


# ── 처분 동등성: 같은 대본을 두 경로에 재생 ──────────────────────


SCRIPTS = {
    "정상": (ITEMS, FRAGMENTS, [_response(_row(1))], {1: "참"}),
    "거짓": (ITEMS, FRAGMENTS, [_response(_row(1, "거짓"))], {1: "거짓"}),
    "애매": (ITEMS, FRAGMENTS, [_response(_row(1, "애매"))], {1: "애매"}),
    "번호누락": (TWO_ITEMS, FRAGMENTS, [_response(_row(1))], {1: "참"}),
    "요청밖번호": (ITEMS, FRAGMENTS, [_response(_row(1), _row(99))], {1: "참", 99: "참"}),
    "계약밖판정": (TWO_ITEMS, FRAGMENTS, [_response(_row(1), _row(2, "모름"))], {1: "참"}),
    "모순중복": (TWO_ITEMS, FRAGMENTS, [_response(_row(1), _row(2), _row(2, "거짓"))], {1: "참"}),
    "장오인": (ITEMS, FRAGMENTS, [_response(_row(1, section="culture"))], {1: "참"}),
    "수치증명일치": (
        NUMERIC_ITEMS, NUMERIC_FRAGMENTS,
        [_response(_numeric_row(numeric_fixture._three_year_direct_entries()))], {1: "참"},
    ),
    "수치증명없는참": (
        CONVERSION_ITEMS, CONVERSION_FRAGMENTS,
        [_response(_row(1, section="past_changes"))],
        {1: REVIEW_GROUNDING_REJECTED},
    ),
    "인용불일치": (
        NUMERIC_ITEMS, NUMERIC_FRAGMENTS,
        [_response(_numeric_row(_mismatched_entries()))], {1: REVIEW_GROUNDING_REJECTED},
    ),
    "JSON불량후재요청": (ITEMS, FRAGMENTS, [BROKEN, _response(_row(1))], {1: "참"}),
    "빈응답후재요청": (ITEMS, FRAGMENTS, ["", _response(_row(1))], {1: "참"}),
    "재요청소진": (ITEMS, FRAGMENTS, [BROKEN, INVALID], None),
    "호출오류후재요청": (ITEMS, FRAGMENTS, [RuntimeError("호출 오류"), _response(_row(1))], {1: "참"}),
}


@pytest.mark.parametrize("name", tuple(SCRIPTS), ids=tuple(SCRIPTS))
def test_disposal_matches_plain_path_for_the_same_response_script(name):
    items, fragments, replies, expected = SCRIPTS[name]
    schema = _run(items, fragments, deepcopy(replies), schema_first=True)
    plain = _run(items, fragments, deepcopy(replies), schema_first=False)
    assert schema.result == plain.result == expected
    assert schema.diagnostics == plain.diagnostics
    assert schema.protocol == plain.protocol
    assert schema.problems == plain.problems
    assert schema.moves == plain.moves
    assert len(schema.calls) == len(plain.calls)
    assert [p.encode("utf-8") for p in schema.calls] == [p.encode("utf-8") for p in plain.calls]
    # 두 경로의 차이는 첫 프롬프트의 «표식»뿐이다 — 재요청부터는 같은 ReviewPrompt.
    assert isinstance(schema.calls[0], ReviewPrompt) and type(plain.calls[0]) is str
    assert [type(p) for p in schema.calls[1:]] == [type(p) for p in plain.calls[1:]]


@pytest.mark.parametrize("name", ("번호누락", "수치증명없는참", "인용불일치"))
def test_schema_valid_rows_are_still_rejected_by_semantic_review(name):
    items, fragments, replies, expected = SCRIPTS[name]
    for raw in replies:
        _assert_schema_valid(raw)
    run = _run(items, fragments, deepcopy(replies), schema_first=True)
    assert run.result == expected
    assert len(run.calls) == 1
    if name == "번호누락":
        assert 2 not in run.result
        assert run.protocol[0]["미응답번호수"] == 1
        assert not run.problems
    else:
        assert run.result[1] == REVIEW_GROUNDING_REJECTED
        assert run.problems[1]
        assert run.diagnostics


def test_report_level_disposal_matches_plain_path_when_a_number_is_missing():
    report = ComposedReport((ComposedSection("identity", (SENTENCE, SECOND_SENTENCE)),))

    def run(schema_first):
        calls = []

        def ask(prompt):
            calls.append(prompt)
            return _response(_row(1))

        kwargs = dict(diagnostics=[], protocol_diagnostics=[])
        if schema_first:
            kwargs.update(initial_ask=ask, initial_retry_ask=ask)
            verified = verify.verify_report(report, tuple(FRAGMENTS.values()), None, _never, **kwargs)
        else:
            verified = verify.verify_report(report, tuple(FRAGMENTS.values()), None, ask, **kwargs)
        return calls, verified, kwargs

    schema_calls, schema_report, schema_sinks = run(True)
    plain_calls, plain_report, plain_sinks = run(False)
    assert [s.text for s in schema_report.sections[0].sentences] == [TEXT]
    assert schema_report == plain_report
    assert schema_sinks["diagnostics"] == plain_sinks["diagnostics"]
    assert schema_sinks["protocol_diagnostics"] == plain_sinks["protocol_diagnostics"]
    assert len(schema_calls) == len(plain_calls) == 1
    assert schema_calls[0].encode("utf-8") == plain_calls[0].encode("utf-8")
    assert isinstance(schema_calls[0], ReviewPrompt) and type(plain_calls[0]) is str
