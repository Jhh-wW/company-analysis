# -*- coding: utf-8 -*-
"""최초 본문 검수의 «부분 응답»은 빠진 문장만 한 번 더 묻는다 — 2026-09-23 뤼튼 실측 회귀.

★ 왜 필요한가 — 실제 배포(fb6cc61) 실행에서 1차 검수가 50문장을 물었는데 응답은
  20행뿐이었다(정상 종료·형식 정상·응답 5,714자). 미응답 30문장은 재질의 없이
  «검수 미완료»로 제거돼 9장 중 6장이 안내문만 남았다. 파싱 재요청 예산(1회)은
  쓰이지도 않았다 — 응답이 «읽히기는» 했기 때문이다.
★ 지키는 것
  (1) 빠진 번호만 다시 묻는다. 호출은 최대 2회, 세 번째는 없다.
  (2) 빠진 번호를 참으로 간주하지 않는다 — 후속이 못 읽히거나 또 빠지면 예전처럼 제거.
  (3) 첫 응답의 판정·근거는 그대로다. 후속 응답은 후속 대상 번호에만 적용되고
      거짓·애매·요청 밖 번호를 덮어쓰지 못한다.
  (4) 파싱 재요청을 이미 쓴 실행은 후속을 묻지 않는다 — 예산·상한 증액 없음.
  (5) 후속은 initial_retry_ask(없으면 initial_ask)로만 나가고, 후속 검수 호출자
      (ask)는 쓰지 않는다. 일반 경로(재검수·요약)는 예전 그대로 1회다.
"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.features.composer import verify
from src.features.composer.constants import (
    GRADE_CONFIRMED,
    MISSING_VERDICTS_REMINDER,
    PARSE_RETRY_LIMIT,
    RETRY_REMINDER,
)
from src.features.composer.grounding_constants import REVIEW_GROUNDING_REJECTED
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.review_protocol_observation import (
    READ_EMPTY,
    READ_JSON_SYNTAX,
    READ_OK,
)
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA, ReviewPrompt
from src.features.composer.tests import test_initial_review_schema as schema_case
from src.features.composer.tests.review_evidence_fixture import review_items
from src.shared.report_quality.composition_diagnostic_constants import (
    BODY_DISPOSITION_STEP,
)

SECTION = "identity"
#: 서로 다른 낱말 50개 — 실측(요청 50)과 같은 규모. 같은 문장의 반복이 아니다.
WORDS = (
    "가구", "가방", "가전", "교재", "금속", "기계", "김치", "꽃", "녹차", "도서",
    "도자기", "등산복", "라면", "레이저", "마스크", "맥주", "모니터", "목재",
    "바이올린", "배터리", "백신", "보석", "비누", "빵", "사료", "선풍기", "세제",
    "소파", "수영복", "시계", "신발", "안경", "약품", "양말", "연필", "오디오",
    "완구", "우산", "유리", "의자", "자전거", "장난감", "전구", "조명", "주스",
    "치즈", "카메라", "커피", "키보드", "타이어",
)
TOTAL = len(WORDS)
ANSWERED = frozenset(range(1, 21))
MISSING = frozenset(range(21, TOTAL + 1))
INVALID = "형식 오류"


def _text(number: int) -> str:
    return f"회사는 {WORDS[number - 1]} 제품을 만들어 판매한다."


def _sentence(number: int) -> ComposedSentence:
    return ComposedSentence(_text(number), (str(number),), GRADE_CONFIRMED)


FRAGMENTS = {
    str(number): CollectedFragment(str(number), "공시", _text(number))
    for number in range(1, TOTAL + 1)
}
ITEMS = tuple(
    verify._ReviewItem(number, _sentence(number), SECTION)
    for number in range(1, TOTAL + 1)
)


def _never(prompt):
    pytest.fail("최초 검수 경로가 후속 검수 호출자를 사용했습니다")


def _rows(prompt, numbers=None, *, results=None, extra=()):
    """프롬프트에 실린 번호 중 ``numbers`` 만 답한다(None 이면 전부). ``extra`` 는 그대로 덧붙인다."""
    rows = []
    for item in review_items(prompt):
        if numbers is not None and item.number not in numbers:
            continue
        rows.append({
            "번호": item.number, "장": item.section or SECTION,
            "근거": list(item.citations), "근거대조": "원문과 문장이 일치한다.",
            "결과": (results or {}).get(item.number, "참"),
        })
    rows.extend(extra)
    return json.dumps({"판정": rows}, ensure_ascii=False)


class _Caller:
    def __init__(self, name, reply):
        self.name, self.prompts, self._reply = name, [], reply

    def __call__(self, prompt):
        self.prompts.append(prompt)
        reply = self._reply(prompt)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def count(self):
        return len(self.prompts)


def _ask_flat(items, fragments, *, initial, retry=None, ask=None):
    sinks = SimpleNamespace(diagnostics=[], protocol=[], problems={}, moves=[])
    sinks.result = verify._ask_verdicts(
        ask or _never, items, fragments, "",
        initial_ask=initial, initial_retry_ask=retry,
        diagnostics=sinks.diagnostics, protocol_diagnostics=sinks.protocol,
        grounding_problems=sinks.problems, section_moves=sinks.moves,
    )
    return sinks


def _numbers_in(prompt):
    return [item.number for item in review_items(prompt)]


# ── (1) 실측 규모: 요청 50 · 응답 20 → 빠진 30만 한 번 더 묻고 회복한다 ──────


def test_partial_first_answer_asks_only_the_missing_numbers_once_and_recovers_them():
    initial = _Caller("initial", lambda p: _rows(p, ANSWERED))
    retry = _Caller("retry", lambda p: _rows(p))
    run = _ask_flat(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result == {number: "참" for number in range(1, TOTAL + 1)}
    assert initial.count == 1 and retry.count == 1
    assert _numbers_in(initial.prompts[0]) == list(range(1, TOTAL + 1))
    # 후속 프롬프트는 «빠진 30개»만 원래 번호 그대로 싣는다 — 답한 20개는 없다.
    followup = retry.prompts[0]
    assert _numbers_in(followup) == sorted(MISSING)
    assert isinstance(followup, ReviewPrompt) and followup.response_schema is FLAT_REVIEW_SCHEMA
    assert followup.endswith(MISSING_VERDICTS_REMINDER)
    assert RETRY_REMINDER not in followup
    # 관측: 첫 시도(요청 50·미응답 30) → 후속 시도(요청 30·미응답 0). 원문은 없다.
    first, second = run.protocol
    assert (first["시도"], first["요청번호수"], first["응답행수"], first["미응답번호수"]) == (1, TOTAL, 20, 30)
    assert (second["시도"], second["요청번호수"], second["응답행수"], second["미응답번호수"]) == (2, 30, 30, 0)
    assert first["판독"] == second["판독"] == READ_OK
    assert not run.problems and not run.diagnostics


# ── (3) 첫 판정 보존 — 후속 응답은 후속 대상에만 적용된다 ────────────────────


def test_followup_answers_apply_only_to_missing_numbers_and_never_overwrite_first_verdicts():
    first_results = {1: "거짓", 2: "애매"}
    initial = _Caller("initial", lambda p: _rows(p, ANSWERED, results=first_results))
    # 후속 답이 «첫 응답이 이미 판정한» 1·2·3번을 참으로 되돌리고 요청 밖 99번까지 낸다.
    overwrite = [
        {"번호": number, "장": SECTION, "근거": [str(number)], "결과": "참"}
        for number in (1, 2, 3)
    ] + [{"번호": 99, "장": SECTION, "근거": ["1"], "결과": "참"}]
    retry = _Caller("retry", lambda p: _rows(p, extra=overwrite))
    run = _ask_flat(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result[1] == "거짓" and run.result[2] == "애매" and run.result[3] == "참"
    assert 99 not in run.result
    assert all(run.result[number] == "참" for number in MISSING)
    assert len(run.result) == TOTAL
    assert initial.count == 1 and retry.count == 1


# ── (2) 후속이 못 읽히거나 실패하면 예전처럼 «그 번호만» 제거, 세 번째 호출 없음 ──


@pytest.mark.parametrize(
    ("reply", "read_code"),
    ((INVALID, READ_JSON_SYNTAX), ("", READ_EMPTY), (RuntimeError("호출 오류"), READ_EMPTY)),
    ids=("json_syntax", "empty", "call_error"),
)
def test_unreadable_followup_leaves_missing_numbers_removed_without_a_third_call(reply, read_code):
    initial = _Caller("initial", lambda p: _rows(p, ANSWERED))
    retry = _Caller("retry", lambda p: reply)
    run = _ask_flat(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result == {number: "참" for number in ANSWERED}
    assert not (set(run.result) & MISSING)
    assert initial.count == 1 and retry.count == PARSE_RETRY_LIMIT
    assert [record["판독"] for record in run.protocol] == [READ_OK, read_code]
    assert run.protocol[1]["요청번호수"] == 30 and run.protocol[1]["미응답번호수"] == 30


def test_partial_followup_recovers_only_what_it_actually_answered():
    initial = _Caller("initial", lambda p: _rows(p, ANSWERED))
    retry = _Caller("retry", lambda p: _rows(p, frozenset(range(21, 31))))
    run = _ask_flat(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert set(run.result) == set(range(1, 31))
    assert initial.count == 1 and retry.count == 1
    assert run.protocol[1]["요청번호수"] == 30 and run.protocol[1]["미응답번호수"] == 20


def test_fatal_provider_error_on_the_followup_is_not_swallowed():
    error = AskFatalError("제공자 장애")
    initial = _Caller("initial", lambda p: _rows(p, ANSWERED))
    retry = _Caller("retry", lambda p: error)
    with pytest.raises(AskFatalError) as raised:
        _ask_flat(ITEMS, FRAGMENTS, initial=initial, retry=retry)
    assert raised.value is error


# ── (4) 예산: 파싱 재요청을 이미 썼으면 부분 응답이어도 후속이 없다 ───────────


def test_parse_retry_consumes_the_budget_so_a_partial_retry_answer_gets_no_followup():
    initial = _Caller("initial", lambda p: schema_case.BROKEN)
    retry = _Caller("retry", lambda p: _rows(p, ANSWERED))
    run = _ask_flat(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result == {number: "참" for number in ANSWERED}
    assert initial.count == 1 and retry.count == PARSE_RETRY_LIMIT
    assert [record["판독"] for record in run.protocol] == [READ_JSON_SYNTAX, READ_OK]
    # 파싱 재요청은 예전 그대로 «같은 프롬프트 + 형식 상기문»이다.
    assert retry.prompts[0] == str(initial.prompts[0]) + RETRY_REMINDER
    assert run.protocol[1]["요청번호수"] == TOTAL and run.protocol[1]["미응답번호수"] == 30


# ── (5) 호출자 경계 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("separate_retry", (True, False))
def test_followup_goes_to_the_retry_callable_or_the_initial_one_never_the_followup_reviewer(separate_retry):
    calls = []

    def initial_reply(prompt):
        calls.append("initial")
        return _rows(prompt, ANSWERED if len(calls) == 1 else None)

    initial = _Caller("initial", initial_reply)
    retry = _Caller("retry", lambda p: (calls.append("retry"), _rows(p))[1])
    run = _ask_flat(
        ITEMS, FRAGMENTS, initial=initial, retry=retry if separate_retry else None,
    )
    assert len(run.result) == TOTAL
    assert calls == ["initial", "retry" if separate_retry else "initial"]


def test_plain_path_without_the_initial_callable_is_unchanged():
    ask = _Caller("ask", lambda p: _rows(p, ANSWERED))
    sinks = SimpleNamespace(protocol=[])
    result = verify._ask_verdicts(
        ask, ITEMS, FRAGMENTS, "", protocol_diagnostics=sinks.protocol,
    )
    assert result == {number: "참" for number in ANSWERED}
    assert ask.count == 1 and len(sinks.protocol) == 1
    assert sinks.protocol[0]["미응답번호수"] == 30


# ── 첫 응답의 근거 결속은 합친 응답에서도 그대로 검사된다 ──────────────────


@pytest.mark.parametrize("proof_is_valid", (True, False), ids=("valid_proof", "mismatched_proof"))
def test_first_answer_grounding_survives_the_merge(proof_is_valid):
    numeric_item = schema_case.NUMERIC_ITEMS[0]
    items = (numeric_item, verify._ReviewItem(2, schema_case.SENTENCE, SECTION))
    fragments = {**schema_case.NUMERIC_FRAGMENTS, "1": schema_case.FRAGMENTS["1"]}
    entries = (
        schema_case.numeric_fixture._three_year_direct_entries()
        if proof_is_valid else schema_case._mismatched_entries()
    )
    first_reply = schema_case._response(schema_case._numeric_row(deepcopy(entries)))
    schema_case._assert_schema_valid(first_reply)
    initial = _Caller("initial", lambda p: first_reply)
    retry = _Caller("retry", lambda p: schema_case._response(schema_case._row(2)))
    run = _ask_flat(items, fragments, initial=initial, retry=retry)

    assert _numbers_in(retry.prompts[0]) == [2]
    assert run.result[2] == "참"
    if proof_is_valid:
        assert run.result[1] == "참" and not run.problems
    else:
        assert run.result[1] == REVIEW_GROUNDING_REJECTED
        assert run.problems[1] and run.diagnostics


def test_merge_keeps_first_rows_verbatim_and_drops_followup_rows_outside_the_targets():
    first_rows = [
        {"번호": 1, "결과": "거짓", "검증근거": {"수치": [{"표현": "5억원", "원문": "500000000원"}]}},
        {"번호": 2, "결과": "참"},
        {"번号": "깨진행"},
        {"번호": 99, "결과": "참"},
    ]
    first_raw = "```json\n" + json.dumps({"판정": first_rows, "머리말": "x"}, ensure_ascii=False) + "\n```"
    followup_rows = [
        {"번호": 1, "결과": "참"},   # 첫 판정 덮어쓰기 시도 → 버린다
        {"번호": 3, "결과": "참"},   # 후속 대상 → 남긴다
        {"번호": "4", "결과": "애매"},  # 숫자 문자열 번호도 같은 보정 규칙
        {"번호": 5, "결과": "참"},   # 대상이 아닌 번호 → 버린다
    ]
    merged = json.loads(verify._merge_review_payloads(
        first_raw, {1: "거짓", 2: "참", 99: "참"},
        json.dumps({"판정": followup_rows}, ensure_ascii=False), {3: "참", 4: "애매"},
    ))
    assert merged == {"판정": [first_rows[0], first_rows[1], first_rows[3], followup_rows[1], followup_rows[2]]}
    # 후속이 아예 못 읽히면 첫 응답의 행만 남는다.
    only_first = json.loads(verify._merge_review_payloads(first_raw, {1: "거짓"}, INVALID, {3: "참"}))
    assert only_first == {"판정": [first_rows[0]]}


# ── 보고서 수준: 50문장 중 30문장이 «검수 미완료»로 사라지지 않는다 ───────────


def _disposition(protocol):
    [record] = [entry for entry in protocol if entry.get("step") == BODY_DISPOSITION_STEP]
    return record["판정별"]


@pytest.mark.parametrize("followup_works", (True, False), ids=("followup_ok", "followup_unreadable"))
def test_report_level_fifty_sentences_survive_only_when_the_followup_answers(followup_works):
    report = ComposedReport((ComposedSection(SECTION, tuple(_sentence(n) for n in range(1, TOTAL + 1))),))
    initial = _Caller("initial", lambda p: _rows(p, ANSWERED))
    retry = _Caller("retry", (lambda p: _rows(p)) if followup_works else (lambda p: INVALID))
    protocol: list[dict] = []
    verified = verify.verify_report(
        report, tuple(FRAGMENTS.values()), None, _never,
        diagnostics=[], protocol_diagnostics=protocol,
        initial_ask=initial, initial_retry_ask=retry, allow_sentence_rewrite=False,
    )
    survivors = [sentence.text for sentence in verified.sections[0].sentences]
    dispositions = _disposition(protocol)
    assert initial.count == 1 and retry.count == 1
    if followup_works:
        assert survivors == [_text(n) for n in range(1, TOTAL + 1)]
        assert dispositions["참"] == TOTAL and dispositions["번호없음_제거"] == 0
    else:
        assert survivors == [_text(n) for n in sorted(ANSWERED)]
        assert dispositions["참"] == 20 and dispositions["번호없음_제거"] == 30


# ── 무호출 예방: 첫 프롬프트가 «모든 번호»를 요구한다 ───────────────────────


def test_first_prompt_tells_the_reviewer_to_answer_every_number():
    guide = verify.REVIEW_JSON_GUIDE
    assert "번호마다 판정 행을 하나씩 빠짐없이 출력한다" in guide
    assert "번호는 따옴표 없는 정수로 쓴다" in guide
    assert MISSING_VERDICTS_REMINDER != RETRY_REMINDER
    assert "읽을 수 없었다" not in MISSING_VERDICTS_REMINDER
    prompt = verify._build_review_prompt(ITEMS, FRAGMENTS, "")
    assert "빠짐없이 출력한다" in prompt
