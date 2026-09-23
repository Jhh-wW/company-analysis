# -*- coding: utf-8 -*-
"""본문 검수 응답의 JSON 한 글자 오류가 판정 전체를 지우지 않는다 — 2026-09-23 유료 실행 회귀.

★ 왜 필요한가 — 운영 FULL 보고서가 타는 packet(장별 묶음) 검수의 응답(42행)에서
  14번 행 하나가 ``검증근거.추세`` 배열을 닫는 ``]`` 자리에 ``}`` 를 찍었다. 문서
  전체가 JSON으로 읽히지 않아 유효 행 0 · 미응답 42 → 본문 37문장·도식 5행이
  «판정 없음»으로 제거돼 9장 중 8장이 빈 보고서가 됐다. packet 경로는 «reviewer
  1회 고정»이라 스키마·파싱 재요청·누락 후속이 하나도 없었다.
★ 지키는 것
  (1) 행 단위 구제 — 온전한 행은 살리고 깨진 행만 버린다. 깨진 행을 고쳐 «부분
      객체»를 만들지 않는다. 정상 응답은 구제 경로를 타지 않는다.
  (2) 관측 — 구제는 ``추출방식=row_salvage`` · ``구문탈락행수`` 로 남고 실행 기록
      정화기를 통과한다. 응답 본문은 남지 않는다.
  (3) packet 경로도 평문과 같은 계약 — 형식 재요청 «또는» 빠진 번호만 누락 후속 1회,
      첫 판정 불변, 호출 최대 2회, 치명 오류 재전파. 네이티브 스키마는 스위치
      ``PACKET_REVIEW_SCHEMA_ENABLED``(기본 꺼짐)를 따른다 — 꺼짐(기본)이면 세 호출
      모두 스키마 없음, 켜면 세 호출 모두 FLAT_REVIEW_SCHEMA. 두 갈래를 다 본다.
  (4) 근거 결속은 파서가 받아들인 «같은» 구제 행을 본다 — 깨진 원문을 그대로 읽으면
      수치 결속 검사가 구제된 판정을 건너뛰는 fail-open 이 된다.
  (5) FULL 호출 장부는 «검수 1회 + 재요청 자리 1»이다(2026-09-23 개방) — 두 번째
      호출은 ``bundled_retry`` 자리로 기록되고, 세 번째 검수자 호출은 공급자 전에 막힌다.
  (6) 두 번째 호출이 요청 AI 몫 소진에 걸려도 첫 판정을 지킨다.
  (7) 부르는 쪽이 «두 번째 호출 불가»를 미리 알리면 보내지도 관측하지도 않는다.
  (1-가) 구제가 모델이 준 적 없는 판정을 만들지 않는다 — «판정» 키 2회 이상이면
      구제 거부, 재동기화는 원소 경계·문자열 밖에서만, 배열의 진짜 끝 뒤는 읽지
      않음, 같은 번호 두 행은 파서의 번호 충돌 규칙에 맡김(2026-09-23 적대 검토).
  (1-나) 두 번째 적대 검토(N1~N4) 봉합 — 깨진 행과 같은 번호의 행은 빼서 누락 후속이
      다시 묻게 하고(규칙 6), 문서가 하나가 아니면(판정 객체 앞·판정 배열 끝 뒤 글에
      «{»·«[»·«"») 구제하지 않으며(규칙 5), «판정» 키는 유니코드 이스케이프를 풀어 센다.
  (1-다) B 4판 재확인 V1 봉합 — 남의 «[» 뒤 행은 배열째 건너뛰고(F1), 배열 수준의 번호
      없는 온전한 객체부터는 끝 뒤 글로 본다(F2). 모델이 빠뜨린 번호를 남의 자리 행이
      채우지 않는다.
모든 회사·문장·원문은 가짜다.
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
from src.features.composer.logic import extract_json_payload
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.prompt_cache_constants import REVIEW_PROMPT_CACHE_ENV
from src.features.composer.review_row_salvage import salvage_verdict_rows
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA, ReviewPrompt
from src.features.composer.tests import test_initial_review_schema as schema_case
from src.features.composer.tests.review_evidence_fixture import review_items
from src.shared.report_quality.composition_diagnostic_constants import (
    EXTRACT_DIRECT,
    EXTRACT_FAILED,
    EXTRACT_ROW_SALVAGE,
    EXTRACT_SLICED,
    PATH_PACKET,
    PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD,
    READ_ALL_ROWS_INVALID,
    READ_EMPTY,
    READ_GLOBAL_FAILURE,
    READ_JSON_SYNTAX,
    READ_NOT_OBJECT,
    READ_OK,
    ROW_NUMBER_CONFLICT,
)
from src.shared.report_quality.composition_diagnostics import (
    observed_composition_steps,
)

SECTION = "identity"
WORDS = ("가구", "비누", "우산", "양말", "연필", "시계")
TOTAL = len(WORDS)
#: 깨진 행의 번호 — 실측(42행 중 14번)처럼 앞뒤에 온전한 행이 있는 가운데 자리.
BROKEN = 4
INVALID = "형식 오류"
#: 깨진 행 안에만 들어가는 가짜 원문 — 관측·로그로 새지 않는지 볼 표지다.
BROKEN_ROW_QUOTE = "가짜회사 비공개 원문 표지"
#: 올바른 행 끝(관측 객체 → 관측 배열 → 추세 객체 → 추세 배열 → 검증근거 → 행)과
#: 실측에서 찍힌 끝(추세 배열의 ``]`` 자리에 ``}``). 괄호 개수는 같고 종류만 틀렸다.
CORRECT_TREND_TAIL = "}]}]}}"
BROKEN_TREND_TAIL = "}]}}}}"


def _text(number: int) -> str:
    return f"회사는 {WORDS[number - 1]} 제품을 만들어 판매한다."


def _sentence(number: int) -> ComposedSentence:
    return ComposedSentence(_text(number), (str(number),), GRADE_CONFIRMED)


FRAGMENTS = {
    str(number): CollectedFragment(str(number), "공시", _text(number))
    for number in range(1, TOTAL + 1)
}
ITEMS = tuple(
    verify._GroupedReviewItem(
        number, SECTION, verify.REVIEW_KIND_SENTENCE, (str(number),),
        sentence=_sentence(number),
    )
    for number in range(1, TOTAL + 1)
)
FLAT_ITEMS = tuple(
    verify._ReviewItem(number, _sentence(number), SECTION)
    for number in range(1, TOTAL + 1)
)


def _row(number, result="참", *, section=SECTION, evidence=None, **extra):
    row = {
        "번호": number, "장": section,
        "근거": list(evidence if evidence is not None else (str(number),)),
        "근거대조": "원문과 문장이 일치한다.", "결과": result,
    }
    row.update(extra)
    return row


def _broken_trend_row_text(number, *, section=SECTION, evidence=None) -> str:
    """실측 14번형 — 추세 배열의 닫는 ``]`` 가 빠지고 ``}`` 가 하나 더 붙은 행 원문."""
    source_id = (evidence or (str(number),))[0]
    observations = [
        {"근거": source_id, "원문": BROKEN_ROW_QUOTE, "항목": "매출액",
         "기간": period, "원문값": value}
        for period, value in (("2024", "1"), ("2025", "2"))
    ]
    row = _row(number, section=section, evidence=evidence, 검증근거={"추세": [{
        "표현": "증가", "항목": "매출액", "방향": "증가", "관측": observations,
    }]})
    text = json.dumps(row, ensure_ascii=False)
    assert text.endswith(CORRECT_TREND_TAIL)
    return text[: -len(CORRECT_TREND_TAIL)] + BROKEN_TREND_TAIL


def _document(row_texts, *, fenced=True) -> str:
    """행 원문들을 실측과 같은 모양(``, `` 구분·코드 펜스)의 응답으로 묶는다."""
    body = '{"판정": [' + ", ".join(row_texts) + "]}"
    return "```json\n" + body + "\n```" if fenced else body


def _broken_answer(prompt, *, broken=BROKEN, results=None) -> str:
    """프롬프트의 번호 중 ``broken`` 행만 14번형으로 깨고 나머지는 온전히 답한다."""
    texts = []
    for item in review_items(prompt):
        if item.number == broken:
            texts.append(_broken_trend_row_text(
                item.number, section=item.section, evidence=item.citations,
            ))
        else:
            texts.append(json.dumps(_row(
                item.number, (results or {}).get(item.number, "참"),
                section=item.section, evidence=item.citations,
            ), ensure_ascii=False))
    return _document(texts)


def _rows(prompt, numbers=None, *, results=None, extra=()) -> str:
    """프롬프트에 실린 번호 중 ``numbers`` 만 온전히 답한다(None 이면 전부)."""
    rows = [
        _row(item.number, (results or {}).get(item.number, "참"),
             section=item.section, evidence=item.citations)
        for item in review_items(prompt)
        if numbers is None or item.number in numbers
    ]
    rows.extend(extra)
    return json.dumps({"판정": rows}, ensure_ascii=False)


class _Caller:
    """받은 프롬프트를 기록하는 가짜 검수 AI. 예외 객체를 돌려주면 던진다."""

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


def _never(prompt):
    pytest.fail("최초 검수 경로가 후속 검수 호출자(ask)를 사용했습니다")


def _ask_grouped(
    items, fragments, *, initial, retry=None, ask=None, available=None, sinks=None,
):
    # 예외로 끝나는 시험도 관측을 볼 수 있게 바깥에서 받은 칸을 그대로 쓴다.
    sinks = sinks or SimpleNamespace(diagnostics=[], protocol=[], problems={})
    sinks.result = verify._ask_grouped_verdicts(
        ask or _never, items, fragments, None,
        initial_ask=initial, initial_retry_ask=retry,
        diagnostics=sinks.diagnostics, protocol_diagnostics=sinks.protocol,
        grounding_problems=sinks.problems,
        second_review_call_available=available,
    )
    return sinks


def _numbers_in(prompt):
    return [item.number for item in review_items(prompt)]


#: packet 스키마 스위치의 «소비 지점» — verify 가 이름을 가져다 쓰므로 그 자리를 바꾼다
#: (combined_relation_guard 스위치 시험과 같은 방식).
PACKET_SCHEMA_SWITCH = "src.features.composer.verify.PACKET_REVIEW_SCHEMA_ENABLED"


@pytest.fixture(params=(False, True), ids=("schema-off", "schema-on"))
def packet_schema(request, monkeypatch):
    """packet 스키마 두 갈래. 기대하는 ``response_schema`` 값을 돌려준다.

    꺼짐 갈래는 스위치를 건드리지 않는다 — 실제 기본값(꺼짐)이 그대로 쓰인다.
    """
    if request.param:
        monkeypatch.setattr(PACKET_SCHEMA_SWITCH, True)
    return FLAT_REVIEW_SCHEMA if request.param else None


def _schema_of(prompt):
    return getattr(prompt, "response_schema", None)


# ══════════════════════════════════════════════════════════
# (1) 행 단위 구제 — 순수 함수
# ══════════════════════════════════════════════════════════


def test_14번형_깨진_행만_버리고_앞뒤_온전한_행은_그대로_살린다():
    rows = {number: _row(number) for number in range(1, TOTAL + 1)}
    raw = _document([
        _broken_trend_row_text(number) if number == BROKEN
        else json.dumps(rows[number], ensure_ascii=False)
        for number in range(1, TOTAL + 1)
    ])
    # 전제: 문서 전체는 JSON으로 읽히지 않는다(펜스 자르기로도 못 읽는다).
    assert extract_json_payload(raw) is None

    salvaged = salvage_verdict_rows(raw)

    assert salvaged is not None
    assert (salvaged.kept_rows, salvaged.dropped_rows) == (TOTAL - 1, 1)
    kept = json.loads(salvaged.text)["판정"]
    # 온전한 행은 값 그대로(검증근거 포함) — 순서도 원래 순서다.
    assert kept == [rows[number] for number in range(1, TOTAL + 1) if number != BROKEN]


def test_깨진_행에서_부분_객체를_만들지_않는다():
    raw = _document([
        json.dumps(_row(1), ensure_ascii=False),
        _broken_trend_row_text(2),
        json.dumps(_row(3), ensure_ascii=False),
    ])
    kept = json.loads(salvage_verdict_rows(raw).text)["판정"]
    assert [row["번호"] for row in kept] == [1, 3]
    # 버린 행의 어느 조각도(추세·관측·가짜 원문) 구제 결과에 들어오지 않는다.
    assert BROKEN_ROW_QUOTE not in json.dumps(kept, ensure_ascii=False)
    assert all(set(row) == set(_row(1)) for row in kept)


def test_도중에_닫힌_행은_앞부분만_살리지_않고_통째로_버린다():
    """``{"번호": 2, …}, "결과": "참"}`` — 객체가 결과 칸 앞에서 닫혀 버린 모양."""
    truncated = (
        '{"번호": 2, "장": "identity", "근거": ["2"], "근거대조": "일치"}'
        ', "결과": "참"}'
    )
    raw = _document([
        json.dumps(_row(1), ensure_ascii=False), truncated,
        json.dumps(_row(3), ensure_ascii=False),
    ])
    salvaged = salvage_verdict_rows(raw)
    assert [row["번호"] for row in json.loads(salvaged.text)["판정"]] == [1, 3]
    assert salvaged.dropped_rows == 1


def test_온전한_행_뒤에_짝_잃은_괄호가_끼어도_그_행은_버리지_않는다():
    """괄호가 «하나 더» 찍혔을 뿐 행 자체가 닫혔으면 행을 살리고 탈락으로 세지 않는다."""
    raw = _document([
        json.dumps(_row(1), ensure_ascii=False),
        json.dumps(_row(2), ensure_ascii=False) + "}",
        json.dumps(_row(3), ensure_ascii=False) + "]",
        json.dumps(_row(4), ensure_ascii=False),
    ])
    assert extract_json_payload(raw) is None
    salvaged = salvage_verdict_rows(raw)
    assert [row["번호"] for row in json.loads(salvaged.text)["판정"]] == [1, 2, 3, 4]
    assert salvaged.dropped_rows == 0


def test_공백_변형_행_시작으로도_재동기화한다():
    raw = (
        '{ "판정" : [ {"번호": 1, "결과": "참", "검증근거": {"추세": [}},'
        '\n  { "번호" : 2, "결과": "참" } ] }'
    )
    salvaged = salvage_verdict_rows(raw)
    assert json.loads(salvaged.text)["판정"] == [{"번호": 2, "결과": "참"}]
    assert salvaged.dropped_rows == 1


@pytest.mark.parametrize("raw", (
    INVALID,
    '{"판정": [{"번호": 1, "결과": "참"',          # 유일한 행이 끊겼다
    '{"다른키": [{"번호": 1, "결과": "참"}',       # 판정 배열 자체가 없다
    '{"판정": [{"결과": "참"}, {"번호": ',          # 번호 있는 온전한 행이 없다
), ids=("no_json", "only_row_cut", "no_verdicts_key", "no_numbered_row"))
def test_한_행도_못_건지면_구제하지_않는다(raw):
    assert salvage_verdict_rows(raw) is None


# ══════════════════════════════════════════════════════════
# (1)(2) 두 파서의 구제 적용과 관측
# ══════════════════════════════════════════════════════════


def _observe(requested=TOTAL):
    return verify.new_protocol_observation(
        PATH_PACKET, 1, prompt_chars=1, response_chars=1, requested_count=requested,
    )


def _parse(kind, raw, observe):
    if kind == "flat":
        return verify._parse_verdicts(
            raw, observe=observe, requested_numbers=range(1, TOTAL + 1),
        )
    owners = {number: SECTION for number in range(1, TOTAL + 1)}
    evidence = {number: frozenset({str(number)}) for number in range(1, TOTAL + 1)}
    return verify._parse_grouped_verdicts(raw, owners, evidence, observe=observe)


@pytest.mark.parametrize("kind", ("flat", "packet"))
def test_두_파서_모두_구제한_행을_받고_관측에_구제와_탈락_수를_남긴다(kind):
    raw = _broken_answer(verify._build_grouped_review_prompt(ITEMS, FRAGMENTS, None))
    observe = _observe()

    verdicts = _parse(kind, raw, observe)

    assert verdicts == {number: "참" for number in range(1, TOTAL + 1) if number != BROKEN}
    assert observe["추출방식"] == EXTRACT_ROW_SALVAGE
    assert observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 1
    assert observe["판독"] == READ_OK
    assert (observe["응답행수"], observe["유효행수"], observe["미응답번호수"]) == (
        TOTAL - 1, TOTAL - 1, 1,
    )
    # 구제 전 오프셋(펜스 안 JSON 위치)은 그대로 남는다 — 어디서 잘랐는지의 흔적.
    assert observe["json시작offset"] > 0
    assert BROKEN_ROW_QUOTE not in json.dumps(observe, ensure_ascii=False)


@pytest.mark.parametrize("kind", ("flat", "packet"))
def test_구제한_행이_전부_계약_밖이면_판독은_all_rows_invalid다(kind):
    """구제는 «읽기»만 돕는다 — 건진 행도 판정값 검사를 똑같이 받는다."""
    raw = _document([
        json.dumps(_row(1, "계약밖판정"), ensure_ascii=False),
        _broken_trend_row_text(2),
    ])
    observe = _observe()
    assert _parse(kind, raw, observe) is None
    assert observe["추출방식"] == EXTRACT_ROW_SALVAGE
    assert observe["판독"] == READ_ALL_ROWS_INVALID
    assert observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 1
    assert observe["행탈락"] == {"result_not_allowed": 1}


@pytest.mark.parametrize("kind", ("flat", "packet"))
@pytest.mark.parametrize("fenced,expected", ((False, EXTRACT_DIRECT), (True, EXTRACT_SLICED)))
def test_정상_응답은_구제_경로를_타지_않는다(kind, fenced, expected):
    raw = _document(
        [json.dumps(_row(number), ensure_ascii=False) for number in range(1, TOTAL + 1)],
        fenced=fenced,
    )
    observe = _observe()
    assert _parse(kind, raw, observe) == {number: "참" for number in range(1, TOTAL + 1)}
    assert observe["추출방식"] == expected
    assert observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 0
    # 결속 단계가 읽을 문자열도 원문 «그 객체» 그대로다.
    assert verify._review_binding_text(raw) is raw


@pytest.mark.parametrize("kind", ("flat", "packet"))
@pytest.mark.parametrize("raw,expected", (
    (INVALID, READ_JSON_SYNTAX),
    ('{"판정": [{"번호": 1, "결과": "참"', READ_JSON_SYNTAX),
    # 적법한 JSON(최상위 배열) 안에 판정 배열이 있어도 봉투 위반은 구제하지 않는다.
    ('[{"판정": [{"번호": 1, "장": "identity", "근거": ["1"], "결과": "참"}]}]', READ_NOT_OBJECT),
))
def test_못_건지거나_적법한_JSON의_봉투_위반은_예전처럼_닫힌다(kind, raw, expected):
    observe = _observe()
    assert _parse(kind, raw, observe) is None
    assert observe["판독"] == expected
    assert observe["추출방식"] in (EXTRACT_FAILED, EXTRACT_DIRECT)
    assert observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 0
    assert verify._review_binding_text(raw) is raw


# ══════════════════════════════════════════════════════════
# (1-가) 구제가 모델이 준 적 없는 판정을 만들지 않는다 — 2026-09-23 적대 검토(D2)
#   구제가 응답 «어디서든» {"번호": …} 객체를 행으로 받으면 형식 예시 되풀이·
#   문자열 누수·배열 뒤 정정 문장에서 가짜 «참»이 생긴다. 구제 전에는 이런 응답이
#   통째로 못 읽혀 모두 제거(fail-closed)였으므로, 구제가 새로 연 fail-open 이다.
# ══════════════════════════════════════════════════════════

#: 적대 검토 반례의 번호표 — 3번은 다른 장·다른 근거("5")를 가진다. 이 번호표라야
#: 가짜 3번 행이 장 소유권·근거 id 검사를 «통과»하므로, 구제 규칙만이 막는지 본다.
D2_OWNERS = {1: SECTION, 2: SECTION, 3: "business_model"}
D2_EVIDENCE = {1: frozenset({"1"}), 2: frozenset({"1"}), 3: frozenset({"5"})}
#: 반례 P1 — 2번 행의 근거대조에 따옴표가 새어 «3번 행» 문구가 들어 있다. 3번 행은
#: 모델이 준 적이 없다.
D2_P1_LEAKED_ROW = (
    '{"판정": [\n'
    ' {"번호": 1, "장": "identity", "근거": ["1"], "근거대조": "1: 일치", "결과": "참"},\n'
    ' {"번호": 2, "장": "identity", "근거": ["1"], "근거대조": "2: 앞 행 {"번호": 3, '
    '"장": "business_model", "근거": ["5"], "근거대조": "x", "결과": "참"} 참고", '
    '"결과": "거짓"}\n]}'
)
#: 반례 P2 — 형식 예시(1번 «참»)를 앞에 되풀이하고, 본 응답의 1번 행(«거짓»)이 깨졌다.
D2_P2_EXAMPLE = (
    '{"판정": [{"번호": 1, "장": "identity", "근거": ["1"], "근거대조": "1: 주체와 역할 '
    '일치", "결과": "참", "검증근거": {}}]}'
)
D2_P2_ECHOED_EXAMPLE = (
    "형식에 맞춰 답합니다. 예: " + D2_P2_EXAMPLE + '\n```json\n{"판정": [\n'
    ' {"번호": 1, "장": "identity", "근거": ["1"], "근거대조": "1: 설립연도 불일치", '
    '"결과": "거짓", "검증근거": {"시점": [{"항목": "설립"}}}},\n'
    ' {"번호": 2, "장": "identity", "근거": ["1"], "근거대조": "2: 일치", "결과": "참", '
    '"검증근거": {}}\n]}\n```'
)
#: 반례 P3e — 배열이 끝난 뒤 설명 문장 속에 2번 «정정» 행이 있다.
D2_P3E_TRAILING_CORRECTION = (
    '{"판정": [ {"번호": 1, "근거대조": "a", "결과": "참"}, {"번호": 2, "근거대조": "b", '
    '"결과": "거짓", "검증근거": {"추세": [}} ]}\n정정: 2번은 {"번호": 2, "근거대조": '
    '"b 재확인", "결과": "참"} 입니다.'
)


def _parse_d2(kind, raw, observe):
    if kind == "flat":
        return verify._parse_verdicts(
            raw, observe=observe, requested_numbers=sorted(D2_OWNERS),
        )
    return verify._parse_grouped_verdicts(raw, D2_OWNERS, D2_EVIDENCE, observe=observe)


def _salvaged_numbers(raw):
    salvaged = salvage_verdict_rows(raw)
    assert salvaged is not None
    numbers = [row["번호"] for row in json.loads(salvaged.text)["판정"]]
    return numbers, salvaged.dropped_rows


@pytest.mark.parametrize("kind", ("flat", "packet"))
def test_문자열_속에_샌_행_시작은_행으로_받지_않는다(kind):
    """P1 — 가짜 3번은 문자열 안(따옴표 홀수)이고 원소 경계도 아니다."""
    assert _salvaged_numbers(D2_P1_LEAKED_ROW) == ([1], 1)
    observe = _observe(len(D2_OWNERS))
    # 수정 전: {1: 참, 3: 참} — 모델이 준 적 없는 3번 «참».
    assert _parse_d2(kind, D2_P1_LEAKED_ROW, observe) == {1: "참"}
    assert observe["추출방식"] == EXTRACT_ROW_SALVAGE
    assert observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 1


@pytest.mark.parametrize("kind", ("flat", "packet"))
def test_판정_키가_두_번_나오면_형식_예시를_받지_않고_구제하지_않는다(kind):
    """P2 — 어느 배열이 본 응답인지 모른다. 예전처럼 «구문 오류»로 닫는다."""
    assert extract_json_payload(D2_P2_ECHOED_EXAMPLE) is None
    assert salvage_verdict_rows(D2_P2_ECHOED_EXAMPLE) is None
    observe = _observe(len(D2_OWNERS))
    # 수정 전: {1: 참, 2: 참} — 깨진 본 응답 1번(«거짓»)이 예시의 «참»으로 뒤집혔다.
    assert _parse_d2(kind, D2_P2_ECHOED_EXAMPLE, observe) is None
    assert observe["판독"] == READ_JSON_SYNTAX
    assert observe["추출방식"] == EXTRACT_FAILED
    assert observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 0
    assert verify._review_binding_text(D2_P2_ECHOED_EXAMPLE) is D2_P2_ECHOED_EXAMPLE


def test_배열이_끝난_뒤_설명_속_정정_행이_있으면_구제하지_않는다():
    """P3e — 배열 끝 뒤 글에 «{»·«"»가 있어 문서가 하나가 아니다(규칙 5).

    D2 수정 전: {1: 참, 2: 참} — 2번(깨진 «거짓»)이 정정 문장의 «참»으로 채워졌다.
    D2 수정 뒤(규칙 3): [1]만 건졌다. 단일 문서 규칙(2026-09-23 B 검토 N2~N4 봉합)
    뒤에는 구제 자체를 하지 않는다 — 구문 오류 → 형식 재요청으로 간다.
    """
    assert salvage_verdict_rows(D2_P3E_TRAILING_CORRECTION) is None
    observe = _observe(len(D2_OWNERS))
    assert _parse_d2("flat", D2_P3E_TRAILING_CORRECTION, observe) is None
    assert observe["판독"] == READ_JSON_SYNTAX


def _row_text(number, result="참"):
    return json.dumps(_row(number, result), ensure_ascii=False)


#: 깨진 2번 행의 앞부분 — 온전한 칸들 뒤에서 행이 깨진다.
_BROKEN_ROW_2_HEAD = (
    '{"번호": 2, "장": "identity", "근거": ["2"], '
    '"근거대조": "원문과 문장이 일치한다.", "결과": "거짓"'
)
#: 규칙 2의 두 검사마다 «그 검사 하나만» 가짜 행을 막는 응답 — 음성 대조(검사 하나를
#: 끄면 가짜 3번이 들어온다)로 따로 지켜지는지 본다. 가짜 행은 번호표의 장·근거와
#: 맞아 파서 검사를 통과하는 온전한 행이다. 가짜 3번은 깨진 2번과 번호가 달라 규칙
#: 6(같은 번호 빼기)이 대신 막지 못한다.
_ONE_RULE_CASES = {
    # 원소 경계 — 깨진 행 속 문자열 «밖» 설명 뒤의 행 시작(앞 글자가 «,»·«[»가 아니다).
    "element_boundary": (
        _document([
            _row_text(1),
            _BROKEN_ROW_2_HEAD + " 참고 " + _row_text(3) + " }",
            _row_text(4),
        ]),
        [1, 4],
    ),
    # 문자열 밖 — «,» 뒤라 원소 경계처럼 보이지만 따옴표가 홀수(문자열 안)다.
    # («[» 뒤였다면 남의 배열 규칙(F1)도 막는다 — 이 사례는 따옴표 검사 하나만 막게
    # «,» 뒤에 둔다.)
    "outside_string": (
        _document([
            _row_text(1),
            '{"번호": 2, "장": "identity", "근거": ["2"], "근거대조": "목록, '
            + _row_text(3) + ' 참고", "결과": "거짓"}',
            _row_text(4),
        ]),
        [1, 4],
    ),
}


@pytest.mark.parametrize("case", sorted(_ONE_RULE_CASES))
def test_규칙마다_가짜_행_하나를_막는다(case):
    raw, expected = _ONE_RULE_CASES[case]
    assert extract_json_payload(raw) is None
    assert _salvaged_numbers(raw) == (expected, 1)
    for kind in ("flat", "packet"):
        assert _parse(kind, raw, _observe()) == {number: "참" for number in expected}


#: 배열 끝 뒤에 정정 배열이 붙은 응답 — 끝을 찾는 규칙 3이 있어야 규칙 5가 «끝 뒤
#: 글»을 볼 수 있다. D2 수정 뒤에는 [1, 3]·[1]을 건졌고, 단일 문서 규칙 뒤에는 구제
#: 자체를 거절한다. 음성 대조 — 규칙 3의 두 끝 판정 중 하나를 끄면 끝을 못 찾아
#: 정정 행을 읽고(규칙 6이 그 번호를 빼도) 구제가 성공해 빨개진다.
_AFTER_END_CASES = {
    # 배열 수준의 진짜 끝 — 온전한 3번 행 뒤 «]»에서 끝난다(펜스 뒤 정정 배열).
    "array_level_end": (
        _document([_row_text(1), _broken_trend_row_text(2), _row_text(3)])
        + "\n정정: [" + _row_text(2) + "]"
    ),
    # 깨진 행을 건너뛰는 동안 만난 배열 끝 — 깨진 2번이 마지막 행이다.
    "end_inside_resync": (
        _document([_row_text(1), _BROKEN_ROW_2_HEAD + ', "검증근거": {"추세": [}}'])
        + "\n정정: [" + _row_text(2) + "]"
    ),
}


@pytest.mark.parametrize("case", sorted(_AFTER_END_CASES))
def test_배열_끝을_찾아_그_뒤에_정정_배열이_있으면_구제하지_않는다(case):
    raw = _AFTER_END_CASES[case]
    assert extract_json_payload(raw) is None
    assert salvage_verdict_rows(raw) is None
    for kind in ("flat", "packet"):
        observe = _observe()
        assert _parse(kind, raw, observe) is None
        assert observe["판독"] == READ_JSON_SYNTAX


@pytest.mark.parametrize("kind", ("flat", "packet"))
def test_같은_번호가_두_번_읽히면_구제는_고르지_않고_파서가_충돌로_무효화한다(kind):
    raw = _document([
        _row_text(1), _row_text(2), _broken_trend_row_text(3),
        _row_text(2, "거짓"), _row_text(4),
    ])
    assert _salvaged_numbers(raw) == ([1, 2, 2, 4], 1)
    observe = _observe()
    assert _parse(kind, raw, observe) == {1: "참", 4: "참"}
    assert observe["행탈락"] == {ROW_NUMBER_CONFLICT: 1}


# ══════════════════════════════════════════════════════════
# (1-나) 두 번째 적대 검토(2026-09-23 worker-b, N1~N4) — 같은 부류 fail-open 봉합
#   N1 깨진 행 문자열에 따옴표가 «홀수 개» 새면 그 속 가짜 행이 규칙 2를 통과한다.
#   N2 깨진 마지막 행 뒤 다른 키 JSON 속 행이 빈자리를 채운다.
#   N3 배열 수준 «]» 바로 뒤 정정 행이 깨진 행의 판정을 대신한다.
#   N4 «판정» 키를 글자 그대로만 세어, 예시만 «판정»·본 응답은 다른 키나 유니코드
#      이스케이프로 온 응답에서 예시 행만 참으로 읽힌다.
#   봉합: 규칙 6(깨진 행과 같은 번호의 행은 뺀다 — 그 번호는 누락 후속이 다시 묻는다),
#   규칙 5(문서는 하나 — 감싼 객체 앞·판정 배열 끝 뒤 글에 «{»·«[»·«"» 가 있으면 구제
#   거절), 규칙 1 보강(유니코드 이스케이프를 풀어 센다). 사례 이름은 B 탐침 번호 그대로.
# ══════════════════════════════════════════════════════════

#: 역슬래시 한 글자 — 이스케이프된 따옴표(``\"``)와 유니코드 이스케이프를 만든다.
_BACKSLASH = "\\"
_ESCAPED_QUOTE = _BACKSLASH + '"'
#: «판정»(U+D310 U+C815)을 JSON 유니코드 이스케이프로 쓴 키.
_ESCAPED_VERDICTS_KEY = _BACKSLASH + "ud310" + _BACKSLASH + "uc815"
_FAKE_TRUE_2 = _row_text(2, "참")
_REST_3_TO_6 = [_row_text(number) for number in range(3, TOTAL + 1)]
_ROWS_1_TO_5 = [_row_text(number) for number in range(1, TOTAL)]


def _broken_false(number):
    """온전한 칸 뒤 검증근거에서 깨지는 «거짓» 행 — 모델의 실제 판정은 거짓이다."""
    return (
        '{"번호": ' + str(number) + ', "장": "identity", "근거": ["' + str(number)
        + '"], "근거대조": "원문과 문장이 일치한다.", "결과": "거짓", '
        '"검증근거": {"추세": [}}'
    )


def _leaky_row_2(inner):
    """2번 행 — 근거대조 문자열 안에 ``inner`` 를 넣고, 모델의 실제 판정은 «거짓»."""
    return (
        '{"번호": 2, "장": "identity", "근거": ["2"], "근거대조": "' + inner
        + '", "결과": "거짓"}'
    )


def _after_array_end(document, tail):
    """펜스 안 판정 배열의 닫는 «]» 바로 뒤에 ``tail`` 을 끼운다(배열 수준 정정 행)."""
    return document.replace("]}\n```", "]" + tail + "]}\n```")


_OTHER_KEY_JSON_6 = '{"결과": [' + _row_text(TOTAL, "참") + "]}"
#: 사례 → (응답, 구제 기대값, 가짜 «참»이 노리는 번호). 구제 기대값은 (건진 번호, 탈락
#: 수) 또는 None(구제 거절 → 구문 오류 → 형식 재요청).
_SECOND_REVIEW_CASES = {
    # N1 — 누수 따옴표 1개 뒤 문자열 속 가짜 2번(장·근거까지 번호표와 맞춤).
    "A1": (_document([_row_text(1), _leaky_row_2(
        "원문은 " + _ESCAPED_QUOTE + "우산" + _ESCAPED_QUOTE + '을 말하고 "비교 인용, '
        + _FAKE_TRUE_2 + ' 이라는 "인용'), *_REST_3_TO_6]), ([1, 3, 4, 5, 6], 2), 2),
    # N1 — 누수 따옴표 쌍 안의 JSON 배열 인용. 판정 배열이 아닌 «[» 뒤의 가짜 행은
    # 남의 배열로 보고 배열째 건너뛴다(규칙 2) — 가짜만 빠지고 3~6번은 건진다.
    # 4판에서는 가짜 뒤 «]»가 배열 끝으로 보여 구제 자체를 거절했다(FULL 에서는 그
    # 검수 판정 전체를 잃는 결말). 5판(2026-09-23 B 4판 재확인 V1 반영)이 이것을 바꿨다.
    "A2": (_document([_row_text(1), _leaky_row_2('형식 "[' + _FAKE_TRUE_2 + ']" 과 다르다'),
                      *_REST_3_TO_6]), ([1, 3, 4, 5, 6], 1), 2),
    # N1 — 장·근거 없는 최소 가짜 2번(평문 파서만 받던 꼴).
    "A5": (_document([_row_text(1), _leaky_row_2(
        "원문 " + _ESCAPED_QUOTE + "표현" + _ESCAPED_QUOTE + '과 "대조, '
        '{"번호": 2, "결과": "참"} 끝 "확인'), *_REST_3_TO_6]), ([1, 3, 4, 5, 6], 2), 2),
    # N3 — 깨진 2번(거짓) 뒤 «], 정정 2번(참)».
    "B1": (_after_array_end(_document([_row_text(1), _broken_false(2), *_REST_3_TO_6]),
                            ", " + _FAKE_TRUE_2), ([1, 3, 4, 5, 6], 2), 2),
    # N3 — 깨진 마지막 6번(거짓) 뒤 «], 정정 6번(참)».
    "B2": (_after_array_end(_document([*_ROWS_1_TO_5, _broken_false(TOTAL)]),
                            ", " + _row_text(TOTAL, "참")), ([1, 2, 3, 4, 5], 2), TOTAL),
    # N3 — 쉼표 붙은 «],{» 꼴.
    "B4": (_after_array_end(_document([_row_text(1), _broken_false(2), *_REST_3_TO_6]),
                            "," + _FAKE_TRUE_2), ([1, 3, 4, 5, 6], 2), 2),
    # N4 — 다른 키로 쓴 형식 예시가 앞에 오고 본 응답 1번이 깨짐(앞 글에 «{»).
    "C1": ('형식 예시: {"결과": [' + _row_text(1, "참") + "]}\n"
           + _document([_broken_false(1), _row_text(2), *_REST_3_TO_6]), None, 1),
    # N2 — 깨진 마지막 6번 뒤 같은 펜스 안의 다른 키 JSON.
    "C2": ("```json\n" + _document([*_ROWS_1_TO_5, _broken_false(TOTAL)], fenced=False)
           + "\n" + _OTHER_KEY_JSON_6 + "\n```", None, TOTAL),
    # N2 — 위와 같되 펜스 없음.
    "C2c": (_document([*_ROWS_1_TO_5, _broken_false(TOTAL)], fenced=False)
            + "\n\n" + _OTHER_KEY_JSON_6, None, TOTAL),
    # N2 — 깨진 행이 가운데(2번)이고 뒤에 다른 키 JSON.
    "C3": ("```json\n" + _document([_row_text(1), _broken_false(2), *_REST_3_TO_6],
                                   fenced=False)
           + '\n{"결과": [' + _FAKE_TRUE_2 + "]}\n```", None, 2),
    # N4 — 예시가 «판정», 본 응답(온전, 2번 거짓)은 다른 키.
    "C4": ('{"판정": [' + _FAKE_TRUE_2 + "]}\n" + '{"결과": ['
           + ", ".join([_row_text(1), _row_text(2, "거짓"), *_REST_3_TO_6]) + "]}",
           None, 2),
    # N4 — 예시가 «판정», 본 응답 키는 유니코드 이스케이프로 쓴 «판정».
    "C5a": ('{"판정": [' + _FAKE_TRUE_2 + "]}\n" + '{"' + _ESCAPED_VERDICTS_KEY + '": ['
            + ", ".join([_row_text(1), _row_text(2, "거짓"), *_REST_3_TO_6]) + "]}",
            None, 2),
    # N2+N4 — 깨진 마지막 6번 뒤 유니코드 이스케이프 키의 JSON.
    "C5b": (_document([*_ROWS_1_TO_5, _broken_false(TOTAL)], fenced=False)
            + '\n{"' + _ESCAPED_VERDICTS_KEY + '": [' + _row_text(TOTAL, "참") + "]}",
            None, TOTAL),
    # 규칙 1 보강만 막는 꼴(A 추가) — 같은 객체 안, 판정 배열 뒤 이스케이프 키에
    # 깨진 행과 «다른» 번호(5)의 가짜 행. 규칙 5·6으로는 못 막는다.
    "C5c": ('{"판정": [' + ", ".join([*_ROWS_1_TO_5[:4], _broken_false(TOTAL)])
            + '], "' + _ESCAPED_VERDICTS_KEY + '": [' + _row_text(5, "참") + "]}",
            None, 5),
}


@pytest.mark.parametrize("case", sorted(_SECOND_REVIEW_CASES))
def test_두_번째_적대_검토의_가짜_행은_어느_파서에서도_참이_되지_않는다(case):
    raw, expected, attacked = _SECOND_REVIEW_CASES[case]
    assert extract_json_payload(raw) is None
    if expected is None:
        assert salvage_verdict_rows(raw) is None
    else:
        assert _salvaged_numbers(raw) == expected
    for kind in ("flat", "packet"):
        observe = _observe()
        verdicts = _parse(kind, raw, observe)
        assert attacked not in (verdicts or {})
        if expected is None:
            assert (verdicts, observe["판독"]) == (None, READ_JSON_SYNTAX)
        else:
            assert observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == expected[1]


@pytest.mark.parametrize(("prefix", "suffix", "salvaged"), [
    ("다음은 판정입니다.\n", "\n이상입니다.", True),
    ("", "\n위 판정 가운데 참은 다섯 건입니다.", True),
    ('"형식"에 맞춘 답: ', "", False),
    ("", '\n위 "참" 판정은 원문과 같다.', False),
    ("[참고] ", "", False),
], ids=("plain_prose_both", "plain_prose_after", "quote_before", "quote_after",
        "bracket_before"))
def test_문서_밖_설명은_괄호·따옴표가_없을_때만_구제한다(prefix, suffix, salvaged):
    """규칙 5 — 코드 펜스·공백·설명 글은 되고, «{»·«[»·«"» 가 섞이면 구제하지 않는다."""
    body = _document([_row_text(1), _broken_false(2), _row_text(3)])
    result = salvage_verdict_rows(prefix + body + suffix)
    assert (result is not None) is salvaged
    if salvaged:
        assert _salvaged_numbers(prefix + body + suffix) == ([1, 3], 1)


def test_판정이_감싼_객체의_첫_칸이_아니어도_앞_칸은_문서_안이다():
    """감싼 객체는 괄호를 쌓아 찾는다 — 판정 앞의 다른 칸은 «객체 밖»이 아니다."""
    raw = (
        '```json\n{"요약": "여섯 건 중 한 건 형식 오류", "판정": ['
        + ", ".join([_row_text(1), _broken_false(2), _row_text(3)]) + "]}\n```"
    )
    assert _salvaged_numbers(raw) == ([1, 3], 1)


def test_판정_배열이_끝난_뒤_같은_객체의_다른_칸도_끝_뒤_글이다():
    """판정 배열 뒤 칸에 행 모양 객체를 실을 수 있어 «문서 밖»과 같게 본다(보수적)."""
    raw = _document([_row_text(1), _broken_false(2), _row_text(3)]).replace(
        "]}\n```", '], "비고": "없음"}\n```')
    assert salvage_verdict_rows(raw) is None


@pytest.mark.parametrize("kind", ("packet", "flat"))
@pytest.mark.parametrize("case", ("A1", "B1"))
def test_깨진_행과_같은_번호의_가짜_행은_빼고_그_번호를_후속으로_다시_묻는다(kind, case):
    raw, _, attacked = _SECOND_REVIEW_CASES[case]
    initial = _Caller("initial", lambda p: raw)
    retry = _Caller("retry", lambda p: _rows(
        p, results={number: "거짓" for number in _numbers_in(p)}))

    run = _ask_review(kind, initial=initial, retry=retry)

    assert initial.count == 1 and retry.count == 1
    # 후속은 가짜 행이 채우려던 그 번호만 묻는다 — 모델의 실제 판정(거짓)이 돌아온다.
    assert _numbers_in(retry.prompts[0]) == [attacked]
    assert MISSING_VERDICTS_REMINDER in retry.prompts[0]
    assert run.result == {
        number: ("거짓" if number == attacked else "참") for number in range(1, TOTAL + 1)
    }
    assert [record["판독"] for record in run.protocol] == [READ_OK, READ_OK]
    assert run.protocol[0][PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 2


@pytest.mark.parametrize("kind", ("packet", "flat"))
@pytest.mark.parametrize("case", ("C2", "C4"))
def test_문서가_둘인_응답은_구제하지_않고_형식_재요청으로_간다(kind, case):
    raw, _, attacked = _SECOND_REVIEW_CASES[case]
    initial = _Caller("initial", lambda p: raw)
    retry = _Caller("retry", lambda p: _rows(
        p, results={number: "거짓" for number in _numbers_in(p)}))

    run = _ask_review(kind, initial=initial, retry=retry)

    assert initial.count == 1 and retry.count == 1
    assert RETRY_REMINDER in retry.prompts[0]
    assert _numbers_in(retry.prompts[0]) == list(range(1, TOTAL + 1))
    assert run.result == {number: "거짓" for number in range(1, TOTAL + 1)}
    assert run.result[attacked] == "거짓"
    assert [record["판독"] for record in run.protocol] == [READ_JSON_SYNTAX, READ_OK]


# ══════════════════════════════════════════════════════════
# (1-다) B 4판 재확인 V1 — 남의 자리 행이 «모델이 빠뜨린 번호»를 채우지 않는다
#   가짜 행이 깨진 행과 «다른» 번호면 규칙 6이 못 뺀다. 그 번호가 모델이 빠뜨린 요청
#   번호(아래 5번)이고 장·근거가 맞으면 두 파서가 참으로 받아 공개됐다.
#   R1 마지막 행이 깨지고 같은 객체 «예시» 칸 배열에 가짜 행.
#   R2 판정 배열이 «]» 없이 끝나고 다른 JSON 이 이어진다.
#   봉합: 규칙 2(F1) 재동기화는 «,» 뒤나 판정 배열 자신의 «[» 뒤에서만 — 남의 «[»
#   뒤 행이면 그 배열을 통째로 건너뛴다(첫 원소만 거르면 둘째 원소는 «,» 뒤라 통과한다).
#   규칙 5(F2) 배열 수준의 «번호 없는 온전한 객체»부터를 «끝 뒤 글»로 보아 구제 거절.
# ══════════════════════════════════════════════════════════

_OUT_OF_REQUEST_9 = '{"번호": 9, "결과": "참"}'
#: 모델이 빠뜨린 요청 번호 — R1·R2 응답에는 이 번호의 진짜 행이 없다.
_MISSING = 5
#: 가짜 행 꼴(B 탐침의 가·나·다·라 + 여러 행). 모두 판정 배열 «밖» 자리에 놓인다.
_V1_FAKES = {
    "missing_full": _row_text(_MISSING, "참"),              # 가 — 장·근거까지 맞음
    "overlap3": _row_text(3, "참"),                         # 나 — 온전한 3번(거짓)과 겹침
    "out_of_request9": _OUT_OF_REQUEST_9,                   # 다 — 요청 밖 9번
    "missing_two_fields": '{"번호": 5, "결과": "참"}',      # 라 — 번호·결과 두 칸만
    "multi_rows": _OUT_OF_REQUEST_9 + ", " + _row_text(_MISSING, "참"),  # 둘째 원소가 5번
}


def _v1_rows(*, last_broken):
    if last_broken:
        return [_row_text(1), _row_text(2), _row_text(3, "거짓"), _row_text(4),
                _broken_false(TOTAL)]
    return [_row_text(1), _broken_false(2), _row_text(3, "거짓"), _row_text(4),
            _row_text(TOTAL)]


def _v1_r1(fake_rows):
    """R1 — 마지막 6번이 깨지고, 판정 배열 뒤 같은 객체 «예시» 칸 배열에 가짜 행."""
    return ('```json\n{"판정": [' + ", ".join(_v1_rows(last_broken=True))
            + '], "예시": [' + fake_rows + "]}\n```")


def _v1_r2(fake_rows, *, last_broken=False):
    """R2 — 판정 배열이 «]» 없이 끝나고 다른 JSON(«예시» 배열)이 이어진다."""
    return ('```json\n{"판정": [' + ", ".join(_v1_rows(last_broken=last_broken))
            + '\n{"예시": [' + fake_rows + "]}\n```")


#: 사례 → (응답, 구제 기대값). None 은 구제 거절(→ 구문 오류 → 형식 재요청).
_V1_CASES = {
    **{f"R1-{name}": (_v1_r1(fake), ([1, 2, 3, 4], 1)) for name, fake in _V1_FAKES.items()},
    **{f"R2-{name}": (_v1_r2(fake), None) for name, fake in _V1_FAKES.items()},
    # 배열 수준이 아니라 재동기화 중에 남의 JSON 을 지나는 꼴 — F1 이 배열째 건너뛴다.
    "R2-multi_rows-last_broken": (
        _v1_r2(_V1_FAKES["multi_rows"], last_broken=True), ([1, 2, 3, 4], 1)),
    # 남의 배열이 닫히지 않아 끝을 모르는 꼴 — 재동기화를 멈춘다(뒤는 어디가 남의
    # 배열 안인지 알 수 없다).
    "R1-unclosed_foreign_array": (
        '```json\n{"판정": [' + ", ".join(_v1_rows(last_broken=True)) + '], "예시": ['
        + _V1_FAKES["multi_rows"] + "\n```", ([1, 2, 3, 4], 1)),
}


@pytest.mark.parametrize("case", sorted(_V1_CASES))
def test_남의_자리_행은_모델이_빠뜨린_번호를_채우지_않는다(case):
    raw, expected = _V1_CASES[case]
    assert extract_json_payload(raw) is None
    if expected is None:
        assert salvage_verdict_rows(raw) is None
    else:
        assert _salvaged_numbers(raw) == expected
    for kind in ("flat", "packet"):
        verdicts = _parse(kind, raw, _observe()) or {}
        assert _MISSING not in verdicts and 9 not in verdicts
        # 겹친 3번은 모델이 쓴 온전한 행(거짓) 그대로다 — 가짜 «참»이 덮지 않는다.
        assert verdicts.get(3, "거짓") == "거짓"


def test_깨진_행_안의_행_배열은_통째로_건너뛰고_뒤의_진짜_행은_건진다():
    """남의 배열의 첫 원소만 거르면 둘째 원소(가짜 6번)가 «,» 뒤라 들어온다.

    재동기화를 멈추기만 해도 가짜는 막지만 뒤의 진짜 3·4번까지 잃는다 — 배열째
    건너뛰고 이어 읽어야 둘 다 지킨다.
    """
    broken_2 = (
        '{"번호": 2, "장": "identity", "근거": ["2"], "근거대조": "원문과 문장이 일치한다.", '
        '"결과": "거짓", "검증근거": {"관련": [' + _row_text(5, "참") + ", "
        + _row_text(TOTAL, "참") + '], "추세": [}}'
    )
    raw = _document([_row_text(1), broken_2, _row_text(3), _row_text(4)])
    assert _salvaged_numbers(raw) == ([1, 3, 4], 1)


def test_판정_배열_안의_번호_없는_온전한_객체는_구제를_통째로_거절한다():
    """규칙 5(F2)의 대가를 못 박는다 — 4판은 그 객체만 버리고 1·4번을 건졌다."""
    raw = _document([_row_text(1), '{"결과": "참"}', _broken_false(3), _row_text(4)])
    assert salvage_verdict_rows(raw) is None


@pytest.mark.parametrize("kind", ("packet", "flat"))
def test_R1_꼴은_가짜를_건너뛰고_빠진_번호를_후속으로_다시_묻는다(kind):
    raw = _V1_CASES["R1-missing_full"][0]
    initial = _Caller("initial", lambda p: raw)
    retry = _Caller("retry", lambda p: _rows(
        p, results={number: "거짓" for number in _numbers_in(p)}))

    run = _ask_review(kind, initial=initial, retry=retry)

    assert initial.count == 1 and retry.count == 1
    # 모델이 빠뜨린 5번과 깨진 6번을 묻는다 — 가짜 5번 «참»은 쓰이지 않았다.
    assert _numbers_in(retry.prompts[0]) == [_MISSING, TOTAL]
    assert run.result == {1: "참", 2: "참", 3: "거짓", 4: "참", 5: "거짓", 6: "거짓"}
    assert [record["판독"] for record in run.protocol] == [READ_OK, READ_OK]


@pytest.mark.parametrize("kind", ("packet", "flat"))
def test_R2_꼴은_구제하지_않고_형식_재요청으로_간다(kind):
    raw = _V1_CASES["R2-missing_full"][0]
    initial = _Caller("initial", lambda p: raw)
    retry = _Caller("retry", lambda p: _rows(
        p, results={number: "거짓" for number in _numbers_in(p)}))

    run = _ask_review(kind, initial=initial, retry=retry)

    assert initial.count == 1 and retry.count == 1
    assert RETRY_REMINDER in retry.prompts[0]
    assert run.result == {number: "거짓" for number in range(1, TOTAL + 1)}
    assert [record["판독"] for record in run.protocol] == [READ_JSON_SYNTAX, READ_OK]


# ══════════════════════════════════════════════════════════
# (3) packet 경로 — 평문과 같은 재요청·누락 후속 계약
# ══════════════════════════════════════════════════════════


def test_한_행이_깨진_첫_응답은_나머지를_살리고_빠진_번호만_한_번_더_묻는다(packet_schema):
    first_results = {2: "거짓", 5: "애매"}
    initial = _Caller("initial", lambda p: _broken_answer(p, results=first_results))
    # 후속 답이 «첫 응답이 이미 판정한» 2·5번을 참으로 되돌리고 요청 밖 99번까지 낸다.
    overwrite = [_row(2), _row(5), _row(99, evidence=("1",))]
    retry = _Caller("retry", lambda p: _rows(p, extra=overwrite))

    run = _ask_grouped(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result == {1: "참", 2: "거짓", 3: "참", 4: "참", 5: "애매", 6: "참"}
    assert initial.count == 1 and retry.count == 1
    first, followup = initial.prompts[0], retry.prompts[0]
    # 첫 요청: 번호는 전부다. 스키마는 스위치만 따른다(기본 꺼짐 → 없음).
    assert _schema_of(first) is packet_schema
    assert _numbers_in(first) == list(range(1, TOTAL + 1))
    # 후속: 빠진 번호만 · 누락 안내 · 형식 재요청 문구 없음 · 스키마는 스위치대로.
    assert _numbers_in(followup) == [BROKEN]
    assert _schema_of(followup) is packet_schema
    assert followup.endswith(MISSING_VERDICTS_REMINDER)
    assert RETRY_REMINDER not in followup
    # 관측: 첫 시도는 구제(탈락 1) → 후속 시도는 요청 1·미응답 0.
    one, two = run.protocol
    assert (one["시도"], one["경로"], one["추출방식"], one["판독"]) == (
        1, PATH_PACKET, EXTRACT_ROW_SALVAGE, READ_OK,
    )
    assert (one["요청번호수"], one["응답행수"], one["미응답번호수"]) == (TOTAL, TOTAL - 1, 1)
    assert one[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 1
    assert (two["시도"], two["요청번호수"], two["미응답번호수"], two["판독"]) == (2, 1, 0, READ_OK)
    assert two["요청밖번호수"] == 2 and two["행탈락"] == {"section_owner_mismatch": 1}
    assert not run.problems and not run.diagnostics


@pytest.mark.parametrize(
    ("reply", "read_code"),
    ((INVALID, READ_JSON_SYNTAX), ("", READ_EMPTY), (RuntimeError("호출 오류"), READ_EMPTY)),
    ids=("json_syntax", "empty", "call_error"),
)
def test_후속도_못_읽히면_그_번호만_제거하고_세_번째_호출은_없다(reply, read_code):
    initial = _Caller("initial", _broken_answer)
    retry = _Caller("retry", lambda p: reply)

    run = _ask_grouped(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result == {number: "참" for number in range(1, TOTAL + 1) if number != BROKEN}
    assert initial.count == 1 and retry.count == PARSE_RETRY_LIMIT
    assert [record["판독"] for record in run.protocol] == [READ_OK, read_code]
    assert run.protocol[1]["요청번호수"] == 1 and run.protocol[1]["미응답번호수"] == 1


def test_첫_응답이_통째로_못_읽히면_형식_재요청_1회_뒤에도_실패면_fail_closed(packet_schema):
    initial = _Caller("initial", lambda p: INVALID)
    retry = _Caller("retry", lambda p: INVALID)

    run = _ask_grouped(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result is None
    assert initial.count == 1 and retry.count == PARSE_RETRY_LIMIT
    # 재요청은 «같은 프롬프트 + 형식 상기문»이다. 스키마는 스위치만 따른다.
    assert retry.prompts[0] == str(initial.prompts[0]) + RETRY_REMINDER
    assert _schema_of(initial.prompts[0]) is packet_schema
    assert _schema_of(retry.prompts[0]) is packet_schema
    assert [record["판독"] for record in run.protocol] == [READ_JSON_SYNTAX, READ_JSON_SYNTAX]
    assert [record["추출방식"] for record in run.protocol] == [EXTRACT_FAILED, EXTRACT_FAILED]


def test_형식_재요청을_쓴_뒤에는_부분_응답이어도_후속이_없다():
    initial = _Caller("initial", lambda p: INVALID)
    retry = _Caller("retry", lambda p: _rows(p, frozenset(range(1, TOTAL))))

    run = _ask_grouped(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    assert run.result == {number: "참" for number in range(1, TOTAL)}
    assert initial.count == 1 and retry.count == 1
    assert [record["판독"] for record in run.protocol] == [READ_JSON_SYNTAX, READ_OK]


def test_후속의_AskFatalError는_삼키지_않는다():
    error = AskFatalError("제공자 장애")
    initial = _Caller("initial", _broken_answer)
    retry = _Caller("retry", lambda p: error)
    with pytest.raises(AskFatalError) as raised:
        _ask_grouped(ITEMS, FRAGMENTS, initial=initial, retry=retry)
    assert raised.value is error


@pytest.mark.parametrize("separate_retry", (True, False))
def test_재요청_호출자가_없으면_최초_호출자로_나가고_ask는_쓰지_않는다(separate_retry):
    calls = []

    def initial_reply(prompt):
        calls.append("initial")
        return _broken_answer(prompt) if len(calls) == 1 else _rows(prompt)

    initial = _Caller("initial", initial_reply)
    retry = _Caller("retry", lambda p: (calls.append("retry"), _rows(p))[1])
    run = _ask_grouped(
        ITEMS, FRAGMENTS, initial=initial, retry=retry if separate_retry else None,
    )
    assert len(run.result) == TOTAL
    assert calls == ["initial", "retry" if separate_retry else "initial"]


def test_최초_호출자_없이_부르면_첫_요청은_예전처럼_스키마_없는_문자열이다(monkeypatch, packet_schema):
    # 캐시 표식이 붙으면 str 하위형이 된다 — 기본값(끔)으로 고정해 «표식 없음»만 본다.
    monkeypatch.delenv(REVIEW_PROMPT_CACHE_ENV, raising=False)
    ask = _Caller("ask", _broken_answer)
    sinks = SimpleNamespace(protocol=[])
    result = verify._ask_grouped_verdicts(
        ask, ITEMS, FRAGMENTS, None, protocol_diagnostics=sinks.protocol,
    )
    # 첫 요청은 스위치를 켜도 최초 본문 검수(initial_ask)가 아니므로 표식 없는 문자열이다.
    assert type(ask.prompts[0]) is str
    # 누락 후속은 같은 호출자로 1회 — 이번 가짜 AI는 후속에서도 그 행을 또 깬다.
    assert ask.count == 2 and _numbers_in(ask.prompts[1]) == [BROKEN]
    assert _schema_of(ask.prompts[1]) is packet_schema
    assert result == {number: "참" for number in range(1, TOTAL + 1) if number != BROKEN}


def test_빈_묶음은_못_읽혀도_재요청하지_않는다():
    initial = _Caller("initial", lambda p: INVALID)
    retry = _Caller("retry", lambda p: _rows(p))
    run = _ask_grouped((), FRAGMENTS, initial=initial, retry=retry)
    assert run.result is None
    assert initial.count == 1 and retry.count == 0
    assert len(run.protocol) == 1


@pytest.mark.parametrize("enabled", (False, True), ids=("cache-off", "cache-on"))
def test_스키마_스위치와_무관하게_프롬프트_바이트와_캐시_경계는_그대로다(
    monkeypatch, enabled, packet_schema,
):
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1" if enabled else "0")
    plain = verify._build_grouped_review_prompt(ITEMS, FRAGMENTS, None)
    plain_missing = verify._build_grouped_review_prompt(
        tuple(item for item in ITEMS if item.number == BROKEN), FRAGMENTS, None,
    )
    expected_prefix = getattr(plain, "cache_prefix_chars", 0)
    assert (expected_prefix > 0) is enabled

    initial = _Caller("initial", _broken_answer)
    retry = _Caller("retry", lambda p: _rows(p))
    _ask_grouped(ITEMS, FRAGMENTS, initial=initial, retry=retry)

    first, followup = initial.prompts[0], retry.prompts[0]
    assert first.encode("utf-8") == plain.encode("utf-8")
    assert followup.encode("utf-8") == (str(plain_missing) + MISSING_VERDICTS_REMINDER).encode("utf-8")
    # 고정 지침 길이는 후보와 무관하다 — 후속도 같은 캐시 경계를 쓴다. 스키마를
    # 끈 갈래는 캐시 표식이 없으면(캐시 끔) 평범한 str 이라 칸 자체가 없다(0 으로 본다).
    assert (
        getattr(first, "cache_prefix_chars", 0)
        == getattr(followup, "cache_prefix_chars", 0)
        == expected_prefix
    )
    assert _schema_of(first) is packet_schema and _schema_of(followup) is packet_schema
    if packet_schema is None:
        # 꺼짐이면 첫 요청은 조립한 ``prompt`` 객체 «그 자체»의 모양(str 또는 캐시 표식)이다.
        assert type(first) is type(plain) and not isinstance(followup, ReviewPrompt)


def test_세_호출_모두_스키마는_스위치만_따른다(packet_schema):
    """첫 요청·누락 후속(한 실행)과 첫 요청·형식 재요청(다른 실행)의 세 호출 모양."""
    initial = _Caller("initial", _broken_answer)
    followup_caller = _Caller("retry", lambda p: _rows(p))
    _ask_grouped(ITEMS, FRAGMENTS, initial=initial, retry=followup_caller)
    initial_invalid = _Caller("initial", lambda p: INVALID)
    retry_caller = _Caller("retry", lambda p: _rows(p))
    _ask_grouped(ITEMS, FRAGMENTS, initial=initial_invalid, retry=retry_caller)

    sent = {
        "첫 요청": initial.prompts[0],
        "누락 후속": followup_caller.prompts[0],
        "형식 재요청": retry_caller.prompts[0],
    }
    assert {name: _schema_of(prompt) is packet_schema for name, prompt in sent.items()} == {
        name: True for name in sent
    }
    assert sent["누락 후속"].endswith(MISSING_VERDICTS_REMINDER)
    assert sent["형식 재요청"].endswith(RETRY_REMINDER)


# ══════════════════════════════════════════════════════════
# (4) 근거 결속은 파서가 받아들인 «같은» 구제 행을 본다
# ══════════════════════════════════════════════════════════
#
# 수치 문장(1번)의 검증근거가 원문과 어긋나면 결속 단계가 그 판정을
# REVIEW_GROUNDING_REJECTED 로 바꿔야 한다. 응답의 «다른» 행(3번)이 깨져 문서
# 전체가 못 읽혀도 마찬가지다. 결속 단계가 깨진 원문을 그대로 읽으면 1번 행이
# 안 보여 결속 검사를 건너뛰고 «참»이 그대로 공개된다(fail-open) — 이 시험이
# 그 배선을 지킨다. 후속이 성공(합친 응답)·실패(원래 응답) 두 갈래 모두 본다.

NUMERIC_TEXT = schema_case.NUMERIC_SENTENCE.text
PLAIN = {2: "회사는 우산 제품을 만들어 판매한다.", 3: "회사는 연필 제품을 만들어 판매한다."}
BINDING_FRAGMENTS = {
    **schema_case.NUMERIC_FRAGMENTS,
    **{str(number): CollectedFragment(str(number), "공시", text) for number, text in PLAIN.items()},
}


def _binding_items(kind):
    numeric = schema_case.NUMERIC_SENTENCE
    plain = {number: ComposedSentence(text, (str(number),), GRADE_CONFIRMED)
             for number, text in PLAIN.items()}
    if kind == "flat":
        return (
            verify._ReviewItem(1, numeric, "past_changes"),
            *(verify._ReviewItem(number, plain[number], SECTION) for number in PLAIN),
        )
    return (
        verify._GroupedReviewItem(1, "past_changes", verify.REVIEW_KIND_SENTENCE,
                                  numeric.citations, sentence=numeric),
        *(verify._GroupedReviewItem(number, SECTION, verify.REVIEW_KIND_SENTENCE,
                                    (str(number),), sentence=plain[number])
          for number in PLAIN),
    )


def _binding_first_answer(proof_is_valid):
    entries = (
        schema_case.numeric_fixture._three_year_direct_entries()
        if proof_is_valid else schema_case._mismatched_entries()
    )
    return _document([
        json.dumps(schema_case._numeric_row(deepcopy(entries)), ensure_ascii=False),
        json.dumps(_row(2), ensure_ascii=False),
        _broken_trend_row_text(3),
    ])


@pytest.mark.parametrize("kind", ("flat", "packet"))
@pytest.mark.parametrize("followup_ok", (True, False), ids=("merged", "first_only"))
@pytest.mark.parametrize("proof_is_valid", (True, False), ids=("valid_proof", "mismatched_proof"))
def test_결속기는_구제된_행을_보고_어긋난_수치_근거를_탈락시킨다(kind, followup_ok, proof_is_valid):
    first = _binding_first_answer(proof_is_valid)
    assert extract_json_payload(first) is None  # 전제: 문서 전체는 못 읽힌다
    initial = _Caller("initial", lambda p: first)
    retry = _Caller("retry", (lambda p: _rows(p)) if followup_ok else (lambda p: INVALID))
    items = _binding_items(kind)
    problems: dict[int, str] = {}
    diagnostics: list[dict] = []
    if kind == "flat":
        result = verify._ask_verdicts(
            _never, items, BINDING_FRAGMENTS, "", initial_ask=initial,
            initial_retry_ask=retry, grounding_problems=problems, diagnostics=diagnostics,
        )
    else:
        result = verify._ask_grouped_verdicts(
            _never, items, BINDING_FRAGMENTS, None, initial_ask=initial,
            initial_retry_ask=retry, grounding_problems=problems, diagnostics=diagnostics,
        )

    assert _numbers_in(retry.prompts[0]) == [3]
    assert result[2] == "참"
    assert (3 in result) is followup_ok
    if proof_is_valid:
        # 대조군 — 근거가 맞으면 구제된 수치 행도 그대로 «참»이다(구제가 행을 잃지 않는다).
        assert result[1] == "참" and not problems
    else:
        assert result[1] == REVIEW_GROUNDING_REJECTED
        assert problems[1] and diagnostics


# ══════════════════════════════════════════════════════════
# 보고서 수준 — 한 행의 괄호 오류가 장을 비우지 않는다
# ══════════════════════════════════════════════════════════


def _report():
    return ComposedReport((ComposedSection(SECTION, tuple(_sentence(n) for n in range(1, TOTAL + 1))),))


@pytest.mark.parametrize("followup_works", (True, False), ids=("followup_ok", "followup_unreadable"))
def test_packet_보고서는_깨진_한_행_때문에_장을_비우지_않는다(followup_works):
    initial = _Caller("initial", _broken_answer)
    retry = _Caller("retry", (lambda p: _rows(p)) if followup_works else (lambda p: INVALID))
    protocol: list[dict] = []
    verified = verify.verify_report(
        _report(), tuple(FRAGMENTS.values()), None, _never,
        allowed_fragment_ids_by_section={SECTION: frozenset(FRAGMENTS)},
        diagnostics=[], protocol_diagnostics=protocol,
        initial_ask=initial, initial_retry_ask=retry,
    )
    survivors = [sentence.text for sentence in verified.sections[0].sentences]
    assert initial.count == 1 and retry.count == 1
    if followup_works:
        assert survivors == [_text(n) for n in range(1, TOTAL + 1)]
    else:
        assert survivors == [_text(n) for n in range(1, TOTAL + 1) if n != BROKEN]
    # 실행 기록 정화기가 구제 관측을 버리지 않고 두 칸을 그대로 옮긴다.
    parses = [record for record in observed_composition_steps(protocol)
              if record["step"] == protocol[0]["step"]]
    assert len(parses) == 2
    assert parses[0]["추출방식"] == EXTRACT_ROW_SALVAGE
    assert parses[0][PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] == 1
    serialized = json.dumps(parses, ensure_ascii=False)
    assert BROKEN_ROW_QUOTE not in serialized and _text(1) not in serialized


# ══════════════════════════════════════════════════════════
# (5) FULL 호출 장부 — «검수 1회 + 재요청 자리 1» (2026-09-23 개방)
# ══════════════════════════════════════════════════════════


def test_FULL_호출_장부는_재요청_자리로_두_번째_검수를_받고_세_번째는_막는다():
    """pipeline 이 감싸는 모양 그대로 — 최초 검수 한 자리, 재요청 전용 두 자리.

    깨진 한 행의 번호만 누락 후속으로 다시 물어 채우고, 그 호출은 장부의
    ``bundled_retry`` 자리로 기록된다(영수증 검수 2회). pipeline 의 물음 함수는
    두 번째 호출 직전에 «자리 있음»을 답한다. 세 번째 검수자 호출은 장부가 공급자
    «전»에 막는다.
    """
    from src.features.composer.pipeline import (
        PRIMARY_REVIEW_RETRY_SECTION_IDS, _CallLedgerRecorder, _review_slot_question,
    )
    from src.shared.generation_validation_receipt import ValidationRound
    from src.shared.report_recovery import PRIMARY_REVIEW_CALLS, PRIMARY_REVIEW_RETRY_CALLS

    recorder = _CallLedgerRecorder()
    provider_initial = _Caller("provider-initial", _broken_answer)
    provider_retry = _Caller("provider-retry", lambda p: _rows(p))
    initial = recorder.wrap(
        provider_initial, role="reviewer", validation_round=ValidationRound.PRIMARY,
        section_ids=("bundled",),
    )
    retry = recorder.wrap(
        provider_retry, role="reviewer", validation_round=ValidationRound.PRIMARY,
        section_ids=PRIMARY_REVIEW_RETRY_SECTION_IDS,
    )

    run = _ask_grouped(
        ITEMS, FRAGMENTS, initial=initial, retry=retry,
        available=_review_slot_question(
            recorder, ValidationRound.PRIMARY,
            PRIMARY_REVIEW_CALLS + PRIMARY_REVIEW_RETRY_CALLS,
        ),
    )

    assert provider_initial.count == 1 and provider_retry.count == 1
    assert _numbers_in(provider_retry.prompts[0]) == [BROKEN]
    assert run.result == {number: "참" for number in range(1, TOTAL + 1)}
    assert [record["판독"] for record in run.protocol] == [READ_OK, READ_OK]
    reviewers = [record for record in recorder.freeze().records if record.role == "reviewer"]
    assert [(record.section_id, record.role_index) for record in reviewers] == [
        ("bundled", 1), ("bundled_retry", 2),
    ]
    with pytest.raises(RuntimeError):
        retry("세 번째 검수 시도")
    assert provider_retry.count == 1, "세 번째 시도는 공급자에 닿지 않는다"


# ══════════════════════════════════════════════════════════
# (6) 두 번째 호출이 요청 AI 몫 소진에 걸려도 첫 판정을 지킨다 (2026-09-23)
# ══════════════════════════════════════════════════════════
#
# ★ 두 번째 호출(형식 재요청·누락 후속)이 요청의 AI 호출 «횟수» 상한
#   (call_limit)이나 요청 로컬 «예약액» 소진(request_budget)에 걸리면, 예전에는
#   AskFatalError 가 재전파돼 verify_report 전체가 실패했다. 첫 응답으로 이미
#   확보한 판정(예: 구제한 41행)까지 버려 본문이 통째로 안내문이 됐다. 이제는 그
#   호출만 포기하고 첫 판정으로 진행한다. 돈·계정 장애와 첫 호출의 장애는 예전처럼
#   재전파한다. 판독 코드는 글자 그대로 못 박는다(실행 기록 계약값).
#   packet·flat 두 경로 모두 본다.

DEGRADABLE_FLAGS = (
    ("call_limit", "call_limit_reached"),
    ("request_budget", "request_budget_exhausted"),
    # FULL 재요청 자리의 공급자 호출 실패(2026-09-24 결정 3 개정) — 판독 코드를 나눈다.
    ("provider_failure", "provider_failure_degraded"),
)


def _ask_review(kind, *, initial, retry, available=None, sinks=None):
    """packet 이면 묶음 검수, flat 이면 평문 최초 검수를 같은 호출자·관측으로 부른다."""
    if kind == "packet":
        return _ask_grouped(
            ITEMS, FRAGMENTS, initial=initial, retry=retry, available=available,
            sinks=sinks,
        )
    sinks = sinks or SimpleNamespace(diagnostics=[], protocol=[], problems={})
    sinks.result = verify._ask_verdicts(
        _never, FLAT_ITEMS, FRAGMENTS, "",
        initial_ask=initial, initial_retry_ask=retry,
        diagnostics=sinks.diagnostics, protocol_diagnostics=sinks.protocol,
        grounding_problems=sinks.problems,
        second_review_call_available=available,
    )
    return sinks


@pytest.mark.parametrize("kind", ("packet", "flat"))
@pytest.mark.parametrize("flag,read_code", DEGRADABLE_FLAGS, ids=[flag for flag, _ in DEGRADABLE_FLAGS])
def test_누락_후속이_요청_한도에_걸려도_첫_판정을_지키고_예외_없이_끝난다(kind, flag, read_code):
    error = AskFatalError(RuntimeError("요청 한도"), **{flag: True})
    initial = _Caller("initial", _broken_answer)
    retry = _Caller("retry", lambda p: error)

    run = _ask_review(kind, initial=initial, retry=retry)

    assert run.result == {number: "참" for number in range(1, TOTAL + 1) if number != BROKEN}
    # 두 번째 호출은 시도됐다가 포기됐고, 세 번째 호출은 없다.
    assert initial.count == 1 and retry.count == 1
    assert [record["판독"] for record in run.protocol] == [READ_OK, read_code]
    # 실행 기록 정화기가 새 판독 코드를 버리지 않는다.
    assert [record["판독"] for record in observed_composition_steps(run.protocol)] == [READ_OK, read_code]


@pytest.mark.parametrize("kind", ("packet", "flat"))
@pytest.mark.parametrize("flag,read_code", DEGRADABLE_FLAGS, ids=[flag for flag, _ in DEGRADABLE_FLAGS])
def test_형식_재요청이_요청_한도에_걸리면_예전처럼_fail_closed_로_닫힌다(kind, flag, read_code):
    error = AskFatalError(RuntimeError("요청 한도"), **{flag: True})
    initial = _Caller("initial", lambda p: INVALID)
    retry = _Caller("retry", lambda p: error)

    run = _ask_review(kind, initial=initial, retry=retry)

    # 통째로 못 읽은 첫 응답뿐이라 쓸 판정이 없다 — 예외 대신 «검수 불능»(None).
    assert run.result is None
    assert initial.count == 1 and retry.count == 1
    assert [record["판독"] for record in run.protocol] == [READ_JSON_SYNTAX, read_code]


@pytest.mark.parametrize("kind", ("packet", "flat"))
@pytest.mark.parametrize("stage", ("retry", "followup"))
def test_돈_계정_장애는_두_번째_호출에서도_재전파한다(kind, stage):
    error = AskFatalError(RuntimeError("일일 예산 소진"))
    initial = _Caller("initial", (lambda p: INVALID) if stage == "retry" else _broken_answer)
    retry = _Caller("retry", lambda p: error)
    sinks = SimpleNamespace(diagnostics=[], protocol=[], problems={})
    with pytest.raises(AskFatalError) as raised:
        _ask_review(kind, initial=initial, retry=retry, sinks=sinks)
    assert raised.value is error and not raised.value.degradable
    # 2026-09-24 결정 3 — 처분은 그대로(재전파)이고, 그 시도는 진단 한 줄로 남는다.
    # 남는 것은 판독 코드와 원인 «종류»(예외 클래스 이름)뿐이다 — 문구는 싣지 않는다.
    second = sinks.protocol[-1]
    assert (second["시도"], second["판독"], second["원인종류"]) == (
        2, READ_GLOBAL_FAILURE, "RuntimeError",
    )
    assert "일일 예산 소진" not in json.dumps(sinks.protocol, ensure_ascii=False)
    assert observed_composition_steps([second]) == (second,)


@pytest.mark.parametrize("kind", ("packet", "flat"))
def test_첫_호출의_요청_한도_장애는_예전처럼_재전파한다(kind):
    """본문 1차 검수는 우아한 저하 대상이 아니다 — 첫 호출은 바뀌지 않는다."""
    error = AskFatalError(RuntimeError("요청 한도"), call_limit=True)
    initial = _Caller("initial", lambda p: error)
    retry = _Caller("retry", lambda p: _rows(p))
    with pytest.raises(AskFatalError) as raised:
        _ask_review(kind, initial=initial, retry=retry)
    assert raised.value is error
    assert retry.count == 0


def test_보고서_수준_packet_후속이_호출_한도에_걸려도_구제한_문장은_남는다():
    initial = _Caller("initial", _broken_answer)
    retry = _Caller("retry", lambda p: AskFatalError(RuntimeError("호출 수 상한"), call_limit=True))
    verified = verify.verify_report(
        _report(), tuple(FRAGMENTS.values()), None, _never,
        allowed_fragment_ids_by_section={SECTION: frozenset(FRAGMENTS)},
        diagnostics=[], protocol_diagnostics=[],
        initial_ask=initial, initial_retry_ask=retry,
    )
    survivors = [sentence.text for sentence in verified.sections[0].sentences]
    assert survivors == [_text(n) for n in range(1, TOTAL + 1) if n != BROKEN]


def test_최초_호출자가_없는_평문_검수의_재요청은_예전처럼_재전파한다():
    """재검수·빈 장 복구 검수처럼 initial_ask 없이 부르는 평문 검수는 바뀌지 않는다.

    그 단계들은 바깥에서 이미 degradable 을 받아 보고서를 지킨다(재작성·재검수 저하
    갈래, pipeline 의 빈 장 복구 «호출중단» 기록). 빈 장 복구의 검수 호출자는 두 번째
    호출에서 스스로 AskFatalError(call_limit=True)를 던져 «검수 1회»를 지키는데
    (empty_section_recovery.review_once), 그 장치가 이 재전파에 기댄다 —
    test_empty_section_recovery.test_review_parse_failure_never_retries_provider.
    """
    provider_calls = []
    attempts = []

    def review_once(prompt):
        attempts.append(prompt)
        if len(attempts) > 1:
            raise AskFatalError(ValueError("검수는 한 번만"), call_limit=True)
        provider_calls.append(prompt)
        return INVALID

    with pytest.raises(AskFatalError) as raised:
        verify._ask_verdicts(review_once, FLAT_ITEMS, FRAGMENTS, "", protocol_diagnostics=[])
    assert raised.value.call_limit
    assert len(provider_calls) == 1 and len(attempts) == 2


# ══════════════════════════════════════════════════════════
# (7) 부르는 쪽이 «두 번째 호출 불가»를 미리 알리면 보내지도 관측하지도 않는다
#     — 2026-09-23 적대 검토 D3
# ══════════════════════════════════════════════════════════
#
# ★ FULL 의 호출 장부는 두 번째 검수 호출을 공급자 «전»에 RuntimeError 로 막고,
#   `_safe_ask` 가 그것을 일반 호출 실패로 삼킨다. 그러면 보내지도 않은 호출이
#   «시도 2 · 빈 응답» 관측과 «검수 AI 호출이 실패했다» 경고로 남아 공급자 장애처럼
#   보인다((5)의 시험이 그 현재 모습을 못 박는다). 장부 예외를 타입·문구로
#   알아보지 않고, 장부를 가진 부르는 쪽이 ``second_review_call_available`` 로
#   호출 «전»에 답한다. 생략(None)하면 예전과 같다.

#: `_safe_ask` 가 일반 호출 실패를 삼킬 때 남기는 경고 — 보내지 않은 호출에는 없어야 한다.
SWALLOWED_CALL_WARNING = "검수 AI 호출이 실패했다"
VERIFY_LOGGER = "src.features.composer.verify"


class _Availability:
    """두 번째 호출 직전의 물음에 정해진 답을 하고 몇 번 물었는지 센다."""

    def __init__(self, answer):
        self.answer, self.asked = answer, 0

    def __call__(self):
        self.asked += 1
        return self.answer


@pytest.mark.parametrize("kind", ("packet", "flat"))
@pytest.mark.parametrize("stage", ("followup", "retry"))
def test_두_번째_호출이_불가하면_보내지도_관측하지도_않는다(kind, stage, caplog):
    initial = _Caller("initial", _broken_answer if stage == "followup" else (lambda p: INVALID))
    retry = _Caller("retry", lambda p: _rows(p))
    available = _Availability(False)

    with caplog.at_level("INFO", logger=VERIFY_LOGGER):
        run = _ask_review(kind, initial=initial, retry=retry, available=available)

    assert initial.count == 1 and retry.count == 0
    assert available.asked == 1
    if stage == "followup":
        # 첫 응답의 판정은 그대로 — 깨진 한 행만 판정 없이 남는다.
        assert run.result == {n: "참" for n in range(1, TOTAL + 1) if n != BROKEN}
        assert [record["판독"] for record in run.protocol] == [READ_OK]
    else:
        # 통째로 못 읽은 첫 응답뿐 — 예전처럼 «검수 불능»(fail-closed).
        assert run.result is None
        assert [record["판독"] for record in run.protocol] == [READ_JSON_SYNTAX]
    assert SWALLOWED_CALL_WARNING not in caplog.text
    assert "두 번째 호출을 허락하지 않아" in caplog.text


@pytest.mark.parametrize("kind", ("packet", "flat"))
def test_물음이_참이면_예전처럼_보내고_빠진_번호가_없으면_묻지도_않는다(kind):
    initial = _Caller("initial", _broken_answer)
    retry = _Caller("retry", lambda p: _rows(p))
    available = _Availability(True)
    run = _ask_review(kind, initial=initial, retry=retry, available=available)
    assert run.result == {n: "참" for n in range(1, TOTAL + 1)}
    assert (initial.count, retry.count, available.asked) == (1, 1, 1)
    assert [record["판독"] for record in run.protocol] == [READ_OK, READ_OK]

    whole = _Availability(False)
    run = _ask_review(
        kind, initial=_Caller("initial", lambda p: _rows(p)), retry=_never, available=whole,
    )
    assert run.result == {n: "참" for n in range(1, TOTAL + 1)}
    assert whole.asked == 0


def test_FULL_장부의_검수자리가_하나뿐이면_물음이_막아_가짜_빈_응답_관측과_실패_경고가_없다(
        caplog):
    """보충 검수처럼(또는 재요청 전용 호출자가 없어) 장부 자리가 하나뿐인 모습.

    pipeline 의 물음 함수(`_review_slot_question`, 자리 1)가 두 번째 호출 «전»에
    «자리 없음»을 답한다 — 공급자 호출은 첫 한 번뿐이고, 관측은 실제로 보낸 첫
    호출 하나만 남으며, «검수 AI 호출이 실패했다» 경고도 없다.
    """
    from src.features.composer.pipeline import _CallLedgerRecorder, _review_slot_question
    from src.shared.generation_validation_receipt import ValidationRound

    recorder = _CallLedgerRecorder()
    provider_initial = _Caller("provider-initial", _broken_answer)
    provider_retry = _Caller("provider-retry", lambda p: _rows(p))

    def wrap(ask):
        return recorder.wrap(
            ask, role="reviewer", validation_round=ValidationRound.PRIMARY,
            section_ids=("bundled",),
        )

    with caplog.at_level("INFO", logger=VERIFY_LOGGER):
        run = _ask_grouped(
            ITEMS, FRAGMENTS, initial=wrap(provider_initial), retry=wrap(provider_retry),
            available=_review_slot_question(recorder, ValidationRound.PRIMARY, 1),
        )

    assert provider_initial.count == 1 and provider_retry.count == 0
    assert recorder.calls_for(ValidationRound.PRIMARY, role="reviewer") == 1
    assert run.result == {n: "참" for n in range(1, TOTAL + 1) if n != BROKEN}
    assert [record["판독"] for record in run.protocol] == [READ_OK]
    assert SWALLOWED_CALL_WARNING not in caplog.text


def test_보고서_진입에서_물음만_넘겨도_예전_빠른_길로_새지_않는다():
    """물음 하나만 넘긴 평문 호출이 «인자 없는 예전 모양» 분기로 빠져 물음을 잃지 않는다."""
    reviewer = _Caller("reviewer", lambda p: INVALID)
    available = _Availability(False)
    verified = verify.verify_report(
        _report(), tuple(FRAGMENTS.values()), None, reviewer,
        second_review_call_available=available,
    )
    assert reviewer.count == 1 and available.asked == 1
    assert verified.sections[0].sentences == ()
