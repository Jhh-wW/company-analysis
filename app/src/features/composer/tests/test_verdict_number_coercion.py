"""검수·도식 «번호» 필드의 좁은 보정을 못 박는다 — 재요청 비용 낭비 방지.

★ 왜 — AI가 JSON 정수 대신 순수 숫자 문자열("3")을 «번호»에 내면 그 행이
  통째로 탈락하고, 응답의 모든 행이 이렇게 나오면 검수·도식 판정 전체가
  버려져 같은 호출을 다시 보낸다(PARSE_RETRY_LIMIT=1, 회사당 약 20원
  낭비 — retry-cause 조사 결과).

이 파일이 못 박는 것:
  (a) 공용 보정 함수 ``coerce_verdict_number`` 자체 — 허용/거부 경계값.
  (b) 실제 검수 파서(``verify._parse_verdicts``)가 문자열 번호와 정수
      번호에서 같은 판정을 낸다. bool·비숫자 문자열은 여전히 버린다.
  (c) 실제 도식 파서(``diagram_check._parse_verdicts``)도 (b)와 같다.
  (d) 검수 진입점(``verify_report`` → ``_ask_verdicts``)이 문자열 번호
      응답에서 재시도 없이 1회 호출로 끝난다.
  (e) 묶음(packet) 검수 파서(``verify._parse_grouped_verdicts``)도 (b)와
      같다. 단, 이 경로는 ``PARSE_RETRY_LIMIT`` 재시도가 **아예 없다**
      (호출 계약이 «1회 고정, 실패 시 즉시 None» — ``_ask_grouped_verdicts``
      docstring 및 ``test_body_review_comparison.py``의 기존
      ``assert len(calls) == 1`` 로 이미 확인된 사실). 그래서 이 경로의
      수정 전/후 차이는 «호출 2회」가 아니라 「판정이 사는지」다.
  (f) 근거 결속(``grounding.constrain_verdicts``)이 파서와 별도로 raw를
      다시 읽어 번호를 찾는 자리도 같은 규칙을 쓴다 — 파서를 고쳐도 이
      자리는 저절로 안 따라온다(별도 재현으로 확인).
  (g) 관계 근거 조회(``direct_support.support_entries_by_number``)도
      (f)와 같은 이유로 별도 수정·별도 시험이 필요했다.
  (h) 독립 검토(P2-1) — 9자리까지는 보정되고 10자리부터는 거부되는 경계,
      5,000자 숫자 문자열이 ``coerce_verdict_number``·flat/도식 파서 양쪽
      모두에서 예외 없이 ``None``/빈 판정을 낸다. 파이썬 3.13의 문자열→
      정수 변환 4,300자리 상한(``sys.get_int_max_str_digits``)에 부딪히기
      전에 자릿수 상한으로 먼저 거른다.
  (i) 독립 검토(P2-2) — 실제로 재요청 비용이 새던 flat 경로의 출력 형식
      안내(``REVIEW_JSON_GUIDE``)에도 "번호는 따옴표 없는 정수로 쓴다"
      구절이 있다. 묶음·도식 안내문은 1·2차에서 이미 넣었다.

  (d)(e)(f)(g)의 「수정 전에는 이렇게 실패했다」는 음성 대조는 이 파일로
  자동화하지 않았다 — 수정 전 코드가 지금 이 커밋에는 존재하지 않기
  때문이다. ``git stash`` 로 파일별로 따로 되돌려 확인했다(보고서 참고).
"""

from __future__ import annotations

import json

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.diagram_check import (
    VERDICT_TRUE as DIAGRAM_VERDICT_TRUE,
    _parse_verdicts as _diagram_parse_verdicts,
    check_diagrams,
)
from src.features.composer.direct_support import support_entries_by_number
from src.features.composer.grounding import constrain_verdicts
from src.features.composer.grounding_constants import (
    GROUNDING_KEY,
    GROUNDING_MISSING,
    REVIEW_GROUNDING_REJECTED,
    REVIEW_NUMBER_KEY,
    REVIEW_RESULT_KEY,
)
from src.features.composer.verdict_number import coerce_verdict_number
from src.features.composer.verify import (
    REVIEW_EVIDENCE_IDS_KEY,
    REVIEW_JSON_GUIDE,
    REVIEW_SECTION_KEY,
    REVIEW_VERDICTS_KEY,
    VERDICT_TRUE,
    _parse_grouped_verdicts as _verify_parse_grouped_verdicts,
    _parse_verdicts as _verify_parse_verdicts,
    verify_report,
)
from src.features.composer.tests.test_verify import (
    _FakeVerifier,
    _all_true,
    _raw_fragments,
    _report,
    _sentence,
    _table,
    _verdict_json,
)
from src.features.composer.tests.test_diagram_check import (
    _검수,
    _fragments as _diagram_fragments,
    _report as _diagram_report,
    _운영장,
    _요약된_경로,
)


# ══════════════════════════════════════════════════════════
# 시험 재료 — test_verify._verdict_json 의 출력을 「변형」만 한다.
# JSON을 새로 만드는 규칙은 원본 생산자 한 곳에만 둔다(드리프트 방지).
# ══════════════════════════════════════════════════════════


def _stringify_verdict_numbers(verdict_json: str) -> str:
    """검수 응답 JSON의 «번호» 값을 문자열로 바꾼다(문자열 번호 응답 흉내)."""
    payload = json.loads(verdict_json)
    for entry in payload[REVIEW_VERDICTS_KEY]:
        entry[REVIEW_NUMBER_KEY] = str(entry[REVIEW_NUMBER_KEY])
    return json.dumps(payload, ensure_ascii=False)


def _diagram_verdict_json(results: dict[int, str], *, as_string: bool = False) -> str:
    """도식 검수 응답 JSON(«판정» 배열)을 만든다. verify 쪽과 키 이름은 같다."""
    entries = [
        {"번호": str(number) if as_string else number, "결과": result}
        for number, result in results.items()
    ]
    return json.dumps({"판정": entries}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
# (a) 공용 보정 함수
# ══════════════════════════════════════════════════════════


def test_정수와_순수_숫자_문자열은_보정된다():
    assert coerce_verdict_number(3) == 3
    assert coerce_verdict_number("3") == 3
    assert coerce_verdict_number(" 3 ") == 3
    assert coerce_verdict_number("007") == 7  # 앞자리 0도 순수 숫자다


def test_bool과_비숫자_문자열과_빈값은_거부된다():
    assert coerce_verdict_number(True) is None
    assert coerce_verdict_number(False) is None
    assert coerce_verdict_number("3번") is None
    assert coerce_verdict_number("-1") is None
    assert coerce_verdict_number("1.0") is None
    assert coerce_verdict_number("") is None
    assert coerce_verdict_number(None) is None


def test_그_외_타입도_거부된다():
    assert coerce_verdict_number(3.0) is None
    assert coerce_verdict_number([3]) is None
    assert coerce_verdict_number({"번호": 3}) is None


def test_9자리_숫자_문자열까지는_보정되고_10자리부터는_거부된다():
    """자릿수 상한의 경계값. 상수를 import하지 않고 리터럴로 고정한다 —
    상한이 조용히 내려가도 이 시험이 잡도록.
    """
    assert coerce_verdict_number("999999999") == 999999999  # 9자리
    assert coerce_verdict_number("1000000000") is None  # 10자리


def test_5000자_숫자_문자열은_예외_없이_거부된다():
    """적대 검토(P2-1) 실측 회귀 — 파이썬 3.13은 문자열→정수 변환에 기본
    4,300자리 상한(``sys.get_int_max_str_digits``)이 있다. 자릿수 상한을
    넣기 전에는 이 입력이 ``int()``에서 ``ValueError``로 그대로 터졌다
    (수정 전에는 ``int()`` 호출 자체가 없어 문제가 없었는데, 보정을 넣으며
    새로 생긴 예외 경로였다). 지금은 조용히 ``None``이어야 한다.
    """
    assert coerce_verdict_number("9" * 5000) is None


def test_flat_검수_출력_형식_안내는_번호가_정수임을_명시한다():
    """적대 검토(P2-2) 실측 회귀 — 재시도 비용이 실제로 새던 flat 경로

    (``verify._build_review_prompt`` → ``REVIEW_JSON_GUIDE``)의 안내문에만
    이 구절이 빠져 있었다. 묶음·도식 안내문에는 이미 있다(각각 별도 코드
    경로 — 셋 다 따로 지켜야 한다).
    """
    assert "번호는 따옴표 없는 정수로 쓴다" in REVIEW_JSON_GUIDE


# ══════════════════════════════════════════════════════════
# (b) 실제 검수 파서 — 문자열 번호와 정수 번호가 같은 판정을 낸다
# ══════════════════════════════════════════════════════════


def test_검수_파서는_문자열_번호와_정수_번호에서_같은_판정을_낸다():
    int_raw = _verdict_json({1: VERDICT_TRUE, 2: VERDICT_TRUE})
    str_raw = _stringify_verdict_numbers(int_raw)

    int_result = _verify_parse_verdicts(int_raw, requested_numbers=[1, 2])
    str_result = _verify_parse_verdicts(str_raw, requested_numbers=[1, 2])

    assert int_result == {1: VERDICT_TRUE, 2: VERDICT_TRUE}
    assert str_result == int_result


def test_검수_파서는_여전히_bool과_비숫자_문자열_번호를_버린다():
    raw = json.dumps(
        {
            REVIEW_VERDICTS_KEY: [
                {REVIEW_NUMBER_KEY: True, REVIEW_RESULT_KEY: VERDICT_TRUE},
                {REVIEW_NUMBER_KEY: "1번", REVIEW_RESULT_KEY: VERDICT_TRUE},
            ]
        },
        ensure_ascii=False,
    )
    # 두 행 모두 번호 판독에서 탈락 → 유효 판정 0개 → 통째로 None(재요청 대상).
    assert _verify_parse_verdicts(raw, requested_numbers=[1]) is None


def test_검수_파서는_5000자_숫자_문자열_번호에서_예외_없이_None을_낸다():
    """적대 검토(P2-1) 실측 회귀 — 이 파서를 직접 부르면 ValueError로 터졌다."""
    raw = json.dumps(
        {REVIEW_VERDICTS_KEY: [{REVIEW_NUMBER_KEY: "9" * 5000, REVIEW_RESULT_KEY: VERDICT_TRUE}]},
        ensure_ascii=False,
    )
    assert _verify_parse_verdicts(raw, requested_numbers=[1]) is None


# ══════════════════════════════════════════════════════════
# (c) 실제 도식 파서 — 문자열 번호와 정수 번호가 같은 판정을 낸다
# ══════════════════════════════════════════════════════════


def test_도식_파서는_문자열_번호와_정수_번호에서_같은_판정을_낸다():
    results = {1: DIAGRAM_VERDICT_TRUE, 2: DIAGRAM_VERDICT_TRUE}
    int_raw = _diagram_verdict_json(results)
    str_raw = _diagram_verdict_json(results, as_string=True)

    int_result = _diagram_parse_verdicts(int_raw)
    str_result = _diagram_parse_verdicts(str_raw)

    assert int_result == results
    assert str_result == int_result


def test_도식_파서는_여전히_bool_번호를_버린다():
    raw = json.dumps(
        {"판정": [{"번호": True, "결과": DIAGRAM_VERDICT_TRUE}]}, ensure_ascii=False
    )
    assert _diagram_parse_verdicts(raw) == {}


def test_도식_파서는_5000자_숫자_문자열_번호에서_예외_없이_빈_사전을_낸다():
    """적대 검토(P2-1) 실측 회귀 — 이 파서도 직접 부르면 ValueError로 터졌다."""
    raw = json.dumps(
        {"판정": [{"번호": "9" * 5000, "결과": DIAGRAM_VERDICT_TRUE}]}, ensure_ascii=False
    )
    assert _diagram_parse_verdicts(raw) == {}


# ══════════════════════════════════════════════════════════
# (d) 검수 진입점 — 문자열 번호 응답에서 재시도 없이 1회로 끝난다
# ══════════════════════════════════════════════════════════


def test_검수_진입점은_문자열_번호_응답에서_재시도_없이_끝난다():
    report = _report(
        (_sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),)
    )
    raw = _stringify_verdict_numbers(_all_true(1))
    ask = _FakeVerifier([raw])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    # 재시도 없음 = 첫 응답에서 이미 판정이 살았다(응답 전체가 버려지지 않았다).
    assert len(ask.review_prompts) == 1
    assert verified.sections[0].sentences[0].grade == GRADE_CONFIRMED


def test_도식_검수_진입점도_문자열_번호_응답에서_재시도_없이_끝난다():
    """검수(verify.py)와 같은 재시도 방어가 도식(diagram_check.py)에도 있다.

    두 파일의 재시도 반복문은 서로 다른 복사본이다 — 한쪽만 고쳤다고
    다른 쪽도 고쳐졌다고 가정하지 않는다(겹마다 따로 대조).
    """
    ask = _검수({1: VERDICT_TRUE, 2: VERDICT_TRUE}, 번호를_문자열로=True)

    report, _problems = check_diagrams(
        _diagram_report(_요약된_경로), _diagram_fragments(), ask,
    )

    assert len(ask.기록) == 1, "번호가 문자열이라는 이유만으로 재시도했습니다"
    assert _운영장(report).flow_rows == _요약된_경로


# ══════════════════════════════════════════════════════════
# (e) 묶음(packet) 검수 파서 — 문자열 번호와 정수 번호가 같은 판정을 낸다
#
# ``_parse_grouped_verdicts``는 verify.py의 «다른» 검수 파서다(``_parse_
# verdicts``와 별개 복사본). ``_ask_grouped_verdicts``는 PARSE_RETRY_LIMIT을
# 전혀 쓰지 않는다 — 실패하면 재시도 없이 즉시 None이다(그 docstring:
# "엄격 packet의 호출 계약은 reviewer 1회 고정이다"). 그래서 이 구역은
# 「호출 2회」가 아니라 「판정이 사는지」로 대조한다.
# ══════════════════════════════════════════════════════════


def _grouped_verdict_json(
    entries: list[tuple[int, str, tuple[str, ...], str]], *, as_string: bool = False,
) -> str:
    """(번호, 장, 근거id들, 결과) 목록을 묶음 검수 응답 JSON으로 만든다."""
    payload = [
        {
            REVIEW_NUMBER_KEY: str(number) if as_string else number,
            REVIEW_SECTION_KEY: section,
            REVIEW_EVIDENCE_IDS_KEY: list(evidence_ids),
            REVIEW_RESULT_KEY: result,
        }
        for number, section, evidence_ids, result in entries
    ]
    return json.dumps({REVIEW_VERDICTS_KEY: payload}, ensure_ascii=False)


def test_묶음_파서는_문자열_번호와_정수_번호에서_같은_판정을_낸다():
    owners = {1: "identity", 2: "identity"}
    evidence_ids_by_number = {1: frozenset({"1"}), 2: frozenset({"1"})}
    entries = [
        (1, "identity", ("1",), VERDICT_TRUE),
        (2, "identity", ("1",), VERDICT_TRUE),
    ]

    int_raw = _grouped_verdict_json(entries)
    str_raw = _grouped_verdict_json(entries, as_string=True)

    int_result = _verify_parse_grouped_verdicts(int_raw, owners, evidence_ids_by_number)
    str_result = _verify_parse_grouped_verdicts(str_raw, owners, evidence_ids_by_number)

    assert int_result == {1: VERDICT_TRUE, 2: VERDICT_TRUE}
    assert str_result == int_result


def test_묶음_파서는_여전히_bool과_비숫자_문자열_번호를_버린다():
    owners = {1: "identity"}
    evidence_ids_by_number = {1: frozenset({"1"})}
    raw = json.dumps(
        {
            REVIEW_VERDICTS_KEY: [
                {
                    REVIEW_NUMBER_KEY: True,
                    REVIEW_SECTION_KEY: "identity",
                    REVIEW_EVIDENCE_IDS_KEY: ["1"],
                    REVIEW_RESULT_KEY: VERDICT_TRUE,
                },
                {
                    REVIEW_NUMBER_KEY: "1번",
                    REVIEW_SECTION_KEY: "identity",
                    REVIEW_EVIDENCE_IDS_KEY: ["1"],
                    REVIEW_RESULT_KEY: VERDICT_TRUE,
                },
            ]
        },
        ensure_ascii=False,
    )
    assert _verify_parse_grouped_verdicts(raw, owners, evidence_ids_by_number) is None


def test_묶음_검수_진입점은_문자열_번호_응답에서_판정을_잃지_않는다():
    """수정 전에는 «재시도 2회»가 아니라 «1회 만에 판정 전체가 None」이었다.

    (e) 절의 설명대로 이 경로는 재시도가 없으므로 호출 수는 고치기 전후로
    똑같이 1회다 — 달라지는 것은 판정이 살아남는지뿐이다.
    """
    report = _report(
        (_sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),)
    )
    raw = _grouped_verdict_json(
        [(1, "identity", ("1",), VERDICT_TRUE)], as_string=True,
    )
    ask = _FakeVerifier([raw])

    verified = verify_report(
        report, _raw_fragments(), _table(), ask,
        allowed_fragment_ids_by_section={"identity": frozenset({"1"})},
    )

    assert len(ask.review_prompts) == 1  # 이 경로는 원래도 항상 1회다
    assert verified.sections[0].sentences[0].verification_state == "verified"


# ══════════════════════════════════════════════════════════
# (f) 근거 결속(grounding.constrain_verdicts) — raw를 파서와 별도로
#     다시 읽는 자리도 같은 규칙을 쓴다
# ══════════════════════════════════════════════════════════


def test_근거_결속은_문자열_번호와_정수_번호에서_같은_결과를_낸다():
    text = "가나다전자는 반도체 검사 장비 전문기업이다."
    sources = {"1": _raw_fragments()[1]["원문"]}
    candidates = {1: (text, sources)}
    verdicts = {1: VERDICT_TRUE}

    def _raw(as_string: bool) -> str:
        entry = {
            REVIEW_NUMBER_KEY: "1" if as_string else 1,
            REVIEW_RESULT_KEY: VERDICT_TRUE,
            # 고의로 객체가 아닌 값을 줘 grounding_problem이 항상
            # GROUNDING_MISSING을 내게 한다 — 대조 대상은 «번호 판독»이지
            # grounding_problem의 판정 로직 자체가 아니다.
            GROUNDING_KEY: "객체가 아닌 값",
        }
        return json.dumps({REVIEW_VERDICTS_KEY: [entry]}, ensure_ascii=False)

    int_constrained, int_problems = constrain_verdicts(_raw(False), verdicts, candidates)
    str_constrained, str_problems = constrain_verdicts(_raw(True), verdicts, candidates)

    assert int_constrained == {1: REVIEW_GROUNDING_REJECTED}
    assert int_problems == {1: GROUNDING_MISSING}
    assert str_constrained == int_constrained
    assert str_problems == int_problems


# ══════════════════════════════════════════════════════════
# (g) 관계 근거 조회(direct_support.support_entries_by_number) — raw를
#     독자적으로 다시 읽는 자리도 같은 규칙을 쓴다
# ══════════════════════════════════════════════════════════


def test_관계_근거_조회는_문자열_번호와_정수_번호에서_같은_결과를_낸다():
    grounding_payload = {"관계": [{"유형": "역할", "근거": "1", "원문": "원문 인용"}]}

    def _raw(as_string: bool) -> str:
        entry = {
            REVIEW_NUMBER_KEY: "1" if as_string else 1,
            REVIEW_RESULT_KEY: VERDICT_TRUE,
            GROUNDING_KEY: grounding_payload,
        }
        return json.dumps({REVIEW_VERDICTS_KEY: [entry]}, ensure_ascii=False)

    int_result = support_entries_by_number(_raw(False))
    str_result = support_entries_by_number(_raw(True))

    assert int_result == {1: grounding_payload}
    assert str_result == int_result
