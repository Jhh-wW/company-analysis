"""검수 응답 JSON이 «한 글자» 깨져도 온전한 판정 행은 살리는 행 단위 구제.

왜 있는가 (2026-09-23 유료 실행 실측)
-------------------------------------
본문 검수 응답은 코드 펜스 안에 ``{"판정": [ …42행… ]}`` 를 전부 냈는데, 14번 행의
``검증근거.추세`` 배열을 닫는 ``]`` 자리에 ``}`` 가 찍혀 문서 전체가 JSON으로
읽히지 않았다. 응답을 한 덩어리로만 읽으면 온전한 41행까지 «판정 없음»으로 버려져
본문 37문장·도식 5행이 모두 빠지고 9장 중 8장이 빈 보고서가 됐다.

무엇을 하는가
-------------
«판정» 배열을 찾아 원소를 ``json.JSONDecoder().raw_decode`` 로 하나씩 읽는다.
원소 하나를 못 읽으면 그 뒤의 다음 «행 시작»(``{"번호"`` · ``{ "번호"`` 같은 공백
변형 포함)으로 건너뛰어 계속 읽는다. 온전히 읽힌 행(객체이고 «번호» 키가 있음)만
모아 ``json.dumps({"판정": rows}, ensure_ascii=False)`` 형태의 «유효 응답 문자열»과
«구문 탈락 행 수»를 돌려준다.

받아들이지 않는 모양 — 모델이 준 적 없는 판정을 만들지 않는다
-------------------------------------------------------------
응답 «어디서든» ``{"번호": …}`` 로 시작하는 객체를 행으로 받으면, 형식 예시를
되풀이한 응답에서 깨진 본 응답 행 대신 예시 행(«참»)이 들어오고, 따옴표가 샌
문자열 속 문구나 배열 뒤 설명·정정 속 객체가 판정이 된다(2026-09-23 적대 검토 두
차례의 재현). 그래서 아래 여섯을 지킨다.

1. «판정» 키가 응답에 두 번 이상 나오면 구제하지 않는다(None). JSON 유니코드
   이스케이프(``\\uXXXX``)로 쓴 키도 풀어서 같은 키로 센다. 어느 배열이 본 응답인지
   알 수 없다 — 부르는 쪽은 예전처럼 «구문 오류»로 닫는다.
2. 재동기화는 «원소 경계»에서만 한다. 행 시작 바로 앞(공백 제외)이 ``,`` 이거나
   «판정 배열 자신의» ``[`` 이고, 배열 시작부터 그 자리까지 이스케이프 안 된 따옴표
   수가 짝수(문자열 밖)여야 한다. 다른 ``[`` 뒤의 행 시작은 남의 배열(판정 배열 뒤
   다른 키의 예시, 문자열에 샌 인용 배열)의 첫 원소다 — 그 배열은 통째로 건너뛰고
   (둘째 원소부터는 ``,`` 뒤라 경계 검사만으로는 못 거른다), 끝을 모르면 재동기화를
   멈춘다.
3. 배열의 진짜 끝 뒤는 읽지 않는다. 배열 수준에서 만난 ``]`` 는 바로 뒤(공백·쉼표
   제외)에 행 시작이 이어질 때만 «짝 잃은 괄호»로 건너뛰고, 아니면 거기서 끝낸다.
   깨진 행을 건너뛰는 동안 만난 문자열 밖 ``]`` 는, 뒤따르는 닫는 괄호·공백을
   지나 쉼표·따옴표·«행 시작»이 아닌 글자가 오면 배열 끝으로 본다(문서 끝·코드
   펜스·설명 글·새 배열의 ``[``·행이 아닌 새 객체의 ``{``). 깨진 행 «안»의
   ``]`` 뒤에는 쉼표·따옴표나 쉼표가 빠진 다음 행이 오므로 구별된다.
4. 같은 번호의 온전한 행이 두 번 읽히면 둘 다 넘긴다 — 판정이 다르면 파서의 번호
   충돌 규칙(ROW_NUMBER_CONFLICT)이 그 번호를 무효로 한다.
5. 문서는 하나여야 한다. «판정» 배열을 감싼 객체의 «앞» 글과, 판정 배열의 끝
   «뒤» 글(감싼 객체의 남은 칸 포함)에 ``{``·``[``·``"`` 가 있으면 구제하지 않는다.
   코드 펜스·공백·설명 글과 닫는 괄호만 허용한다. 앞머리의 형식 예시, 뒤의 정정
   배열·다른 키의 JSON·따옴표 든 설명이 모두 여기서 걸려 «구문 오류» → 형식
   재요청으로 간다. 배열 수준에서 «번호 없는 온전한 객체»를 만나면 판정 배열이
   ``]`` 없이 끝나고 다른 JSON 이 시작된 것으로 보아 거기부터를 «끝 뒤 글»로 본다.
6. 깨진 행과 같은 번호의 행은 받지 않는다. 깨진 행 머리(``{"번호": N``)에서 번호를
   읽어 두고, 끝에서 그 번호의 행을 모두 뺀다. 규칙 4를 깨진 행까지 넓힌 것이다 —
   원래 행이 깨져 버려지면 파서는 가짜 행 하나만 보므로 충돌이 생기지 않는다. 빈
   자리는 누락 후속(모델에게 다시 묻기)이 채운다. 문자열 속 문구·배열 뒤 정정
   행은 채우지 못한다.

하지 않는 것
------------
* 깨진 행을 고치거나 추측해 채우지 않는다. 괄호를 더하거나 오류 지점까지 잘라
  «부분 객체»를 만들지 않는다. 깨진 행은 통째로 버리고 개수만 센다.
* 행이 다 읽힌 뒤 쉼표와 문자열이 이어지면(객체가 도중에 닫히고 나머지 칸이
  뒤따른 모양) 그 행도 온전하지 않은 것으로 보고 버린다.
* 판정값·장 소유권·근거 결속을 검사하지 않는다. 구제한 문자열은 원래 응답과
  «같은» 파서와 근거 결속 검사를 다시 받는다.
* 응답을 언제 구제할지 정하지 않는다. «응답 전체가 JSON으로 못 읽힐 때만» 부르는
  것은 부르는 쪽(verify)의 책임이다. 정상 응답은 이 함수를 지나지 않는다.

알려진 한계
-----------
아래는 행을 «덜» 살리는 쪽이다 — 건지지 못한 번호는 미응답으로 남아 누락 후속
대상이 되고, 그래도 없으면 판정 없이 제거된다(fail-closed). 구제 자체를 거절하면
응답 전체가 «구문 오류»가 되어 형식 재요청으로 간다. FULL 에서는 두 번째 검수
호출이 호출 장부에 막히므로, 거절은 그 검수의 판정 전체를 잃는다는 뜻이다.

* 규칙 6의 대가: 모델이 깨진 행 바로 뒤에 같은 행을 온전히 다시 써도 버리고 다시
  묻는다.
* 규칙 5의 대가: 판정 배열 뒤에 따옴표 든 설명 한 줄만 붙어도 구제하지 않는다.
  깨진 행 안에서 ``]`` 와 닫는 괄호 뒤에 쉼표 없이 행이 아닌 객체가 이어지면
  (``…]} {"표현": …``) 거기를 배열 끝으로 보아, 뒤의 행들이 «끝 뒤 글»이 되어
  구제하지 않는다. 판정 배열 «안»에 번호 없는 온전한 객체가 끼어도(``{"결과":
  "참"}``) 구제를 통째로 거절한다.
* 규칙 2의 대가: 판정 배열 안에 행을 담은 배열이 잘못 끼면(``[{"번호": 4…},
  {"번호": 5…}]``) 그 배열의 행들은 남의 배열로 보고 건너뛴다.
* 재동기화는 «번호»가 첫 키인 행 시작만 찾는다. 검수 지시문과 응답 스키마가
  번호를 첫 칸으로 요구하므로 실측 응답은 모두 이 모양이다. 번호가 첫 키가 아닌
  행이 깨진 행 바로 뒤에 오면 다음 «번호» 첫 키 행까지 함께 건너뛴다. 번호가 첫
  키가 아닌 행이 깨지면 번호를 읽지 못해 규칙 6도 그 번호를 모른다.
* 뒤 행의 여는 ``{`` 가 빠지면(``{…14번…}, "번호": 15, …}, {…16번…}``) 온전한 앞
  행(14번)까지 버린다 — 읽힌 14번 객체 뒤에 쉼표와 문자열이 이어져 «도중에 닫힌
  행»과 구별되지 않는다. 이때 구문 탈락 행 수는 1로, 실제로 잃은 2행보다 적게
  센다(여는 괄호가 없는 15번은 행 시작으로 보이지 않아 세지 않는다).
* 깨진 행 바로 뒤 행이 쉼표 없이 시작하면(``…깨진 행…}`` 다음 줄 ``{"번호": 15``)
  원소 경계가 아니므로 그 행도 건너뛴다. 이 행도 구문 탈락 수에 세지 않는다.
  (온전한 행끼리 쉼표가 빠진 것은 재동기화가 아니라 그대로 읽는다.)
* 문자열 속 따옴표가 이스케이프 없이 «홀수 개» 새면 그 뒤는 문자열 안으로 보여
  더는 재동기화하지 않는다.
* 배열 수준의 짝 잃은 ``]`` 뒤에 행 시작 대신 다른 괄호가 이어지면(``]}, {…``)
  배열 끝으로 보고, 그 뒤가 «끝 뒤 글»이 되어 구제하지 않는다.

아래는 행을 «더» 받을 수 있는 쪽이다. 가짜 행이 깨진 행과 «다른» 번호일 때만
남는다(같은 번호는 규칙 6이 뺀다). 받은 행도 판정값·장 소유권·근거 id·근거 결속
검사를 똑같이 받는다.

* 마지막 행이 깨지고 그 뒤에 닫는 괄호와 «쉼표»를 지나 행 모양 객체가 붙으면
  (``…깨진 6번…]}, {"번호": 5, …}``, 또는 객체 칸 사이의 ``, {"번호": …}`` 처럼
  JSON 문법에 없는 모양) 받는다. 깨진 행 안의 닫는 괄호 묶음 뒤 다음 행
  (``}]}}}}, {"번호": 15``, 실측 14번 모양)과 글자로는 구별되지 않는다.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import Final, Optional

from src.features.composer.grounding_constants import (
    REVIEW_ENTRIES_KEY,
    REVIEW_NUMBER_KEY,
)
from src.features.composer.verdict_number import coerce_verdict_number

#: «판정» 키 — 응답 전체에서 몇 번 나오는지 센다(규칙 1).
_VERDICTS_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r'"' + re.escape(REVIEW_ENTRIES_KEY) + r'"\s*:'
)
#: 응답에 «판정» 키가 이보다 많이 나오면 구제하지 않는다(규칙 1).
_MAX_VERDICTS_KEYS: Final[int] = 1
#: 규칙 1이 셀 때 푸는 JSON 유니코드 이스케이프 — «판정» 을 ``\\ud310\\uc815``
#: 처럼 써도 JSON 으로는 같은 키다.
_UNICODE_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\\u([0-9a-fA-F]{4})")
_HEX_BASE: Final[int] = 16
#: «판정» 배열의 시작 — 키와 여는 대괄호 사이의 공백 변형을 허용한다.
_VERDICTS_ARRAY_RE: Final[re.Pattern[str]] = re.compile(
    r'"' + re.escape(REVIEW_ENTRIES_KEY) + r'"\s*:\s*\['
)
#: 행 시작 — «번호»가 첫 키인 객체의 시작(``{"번호":`` · ``{ "번호" :``).
_ROW_START_PATTERN: Final[str] = (
    r'\{\s*"' + re.escape(REVIEW_NUMBER_KEY) + r'"\s*:'
)
_ROW_START_RE: Final[re.Pattern[str]] = re.compile(_ROW_START_PATTERN)
#: 깨진 행 머리의 번호(따옴표로 싼 숫자 포함, 규칙 6). 자릿수 상한은 번호 보정
#: (``coerce_verdict_number``)이 건다 — 잡은 숫자를 ``int()`` 에 바로 넘기지 않는다.
_BROKEN_ROW_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    _ROW_START_PATTERN + r'\s*"?\s*([0-9]+)'
)
#: 재동기화 중에 보는 두 표지 — 배열을 닫을 수 있는 ``]`` 와 행 시작.
_RESYNC_MARK_RE: Final[re.Pattern[str]] = re.compile(r"\]|" + _ROW_START_PATTERN)
#: JSON 문법이 인정하는 공백 네 글자(RFC 8259 §2).
_JSON_WHITESPACE: Final[str] = " \t\n\r"
_OBJECT_OPEN: Final[str] = "{"
_OBJECT_CLOSE: Final[str] = "}"
_ARRAY_OPEN: Final[str] = "["
_ARRAY_CLOSE: Final[str] = "]"
_MEMBER_SEPARATOR: Final[str] = ","
_STRING_QUOTE: Final[str] = '"'
_STRING_ESCAPE: Final[str] = "\\"
#: 짝을 이루는 글자 수 — 여닫는 따옴표 한 쌍, 서로 이스케이프하는 역슬래시 한 쌍.
_PAIR: Final[int] = 2
#: 쉼표 뒤에 와도 «배열 원소 경계»로 볼 수 있는 글자 — 다음 행의 시작이나 배열 끝.
_ELEMENT_BOUNDARY_AFTER_COMMA: Final[str] = _OBJECT_OPEN + _ARRAY_CLOSE
#: 여는 괄호 두 가지 — 감싼 객체를 찾을 때 쌓는다.
_OPENERS: Final[str] = _OBJECT_OPEN + _ARRAY_OPEN
#: 배열 끝 판정에서 ``]`` 뒤에 이어져도 되는 닫는 괄호.
_CLOSERS: Final[str] = _OBJECT_CLOSE + _ARRAY_CLOSE
#: 닫는 괄호 뒤에 와도 «JSON이 아직 이어지는» 글자(규칙 3) — 다음 원소·칸(쉼표),
#: 쉼표가 빠진 다음 칸(따옴표). 여는 중괄호는 «행 시작»일 때만 이어짐이다(쉼표가
#: 빠진 다음 행). 그 밖(문서 끝·코드 펜스·설명 글·새 배열·행이 아닌 새 객체)이면
#: 배열이 끝난 것이다.
_JSON_CONTINUATION: Final[str] = _MEMBER_SEPARATOR + _STRING_QUOTE
#: 감싼 객체 밖에 있으면 «문서가 하나가 아니다»로 보는 글자(규칙 5).
_OTHER_DOCUMENT_MARKS: Final[str] = _OBJECT_OPEN + _ARRAY_OPEN + _STRING_QUOTE


@dataclass(frozen=True)
class SalvagedVerdicts:
    """행 단위 구제 결과.

    Attributes:
        text: 온전히 읽힌 행만 담은 새 응답 JSON 문자열(``{"판정": [...]}``).
            파서와 근거 결속이 «같은» 이 문자열을 다시 읽는다.
        kept_rows: ``text`` 에 담긴 행 수.
        dropped_rows: 버린 행 수 — (가) 행 시작(``{``)으로 보였지만 온전한 행으로
            읽지 못한 행과 (나) 온전히 읽혔지만 깨진 행과 같은 번호라 뺀 행(규칙 6).
            행 사이에 끼어든 짝 잃은 닫는 괄호 같은 «행이 아닌» 찌꺼기는 세지 않는다.
            재동기화가 건너뛴 행(여는 괄호가 빠진 행·원소 경계가 아닌 자리의 행)도
            세지 않으므로 잃은 행 수의 «하한»이다(모듈 머리말의 알려진 한계).
    """

    text: str
    kept_rows: int
    dropped_rows: int


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in _JSON_WHITESPACE:
        index += 1
    return index


def _skip_separators(text: str, index: int) -> int:
    """원소 사이의 공백과 쉼표를 건너뛴다."""
    while index < len(text) and (
        text[index] in _JSON_WHITESPACE or text[index] == _MEMBER_SEPARATOR
    ):
        index += 1
    return index


def _verdicts_key_count(text: str) -> int:
    """«판정» 키 수 — 유니코드 이스케이프로 쓴 키도 풀어서 같은 키로 센다(규칙 1).

    이스케이프를 넉넉히 푼다(문자열 속 ``\\\\u`` 까지). 더 세는 쪽은 구제를 덜
    하는 쪽이라 안전하다.
    """
    decoded = _UNICODE_ESCAPE_RE.sub(
        lambda match: chr(int(match.group(1), _HEX_BASE)), text,
    )
    return len(_VERDICTS_KEY_RE.findall(decoded))


def _enclosing_object_start(text: str, key_start: int) -> Optional[int]:
    """«판정» 키를 곧바로 품은 객체의 여는 중괄호 위치. 못 찾으면 None.

    글 첫머리부터 키 앞까지 문자열 밖의 여는 괄호를 쌓고 닫는 괄호로 걷어 낸다.
    키 자리에서 맨 위가 ``{`` 여야 키가 그 객체의 칸이다. 키가 문자열 안으로
    보이거나(앞 설명의 짝 없는 따옴표) 배열 원소 자리면 None — 문서 모양을 믿을
    수 없으므로 부르는 쪽은 구제하지 않는다.
    """
    stack: list[tuple[str, int]] = []
    in_string = False
    escaped = False
    for index in range(key_start):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == _STRING_ESCAPE:
                escaped = True
            elif char == _STRING_QUOTE:
                in_string = False
            continue
        if char == _STRING_QUOTE:
            in_string = True
        elif char in _OPENERS:
            stack.append((char, index))
        elif char in _CLOSERS and stack:
            stack.pop()
    if in_string or not stack or stack[-1][0] != _OBJECT_OPEN:
        return None
    return stack[-1][1]


def _has_other_document(segment: str) -> bool:
    """감싼 객체 밖 글에 다른 JSON 의 표지(여는 괄호·따옴표)가 있는지(규칙 5)."""
    return any(char in _OTHER_DOCUMENT_MARKS for char in segment)


def _unescaped_quote_positions(text: str, start: int) -> list[int]:
    """``start`` 부터 이스케이프 안 된 따옴표의 위치(오름차순).

    따옴표 바로 앞에 붙은 역슬래시가 홀수 개면 이스케이프된 따옴표(문자열 속
    글자)이고, 짝수 개면 역슬래시끼리 짝을 이룬 것이라 그 따옴표가 문자열을
    여닫는다.
    """
    positions: list[int] = []
    backslashes = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == _STRING_ESCAPE:
            backslashes += 1
            continue
        if char == _STRING_QUOTE and backslashes % _PAIR == 0:
            positions.append(index)
        backslashes = 0
    return positions


def _outside_string(quote_positions: list[int], index: int) -> bool:
    """``index`` 앞의 이스케이프 안 된 따옴표가 짝수 개면 문자열 밖이다."""
    return bisect_left(quote_positions, index) % _PAIR == 0


def _previous_non_whitespace(text: str, index: int) -> Optional[int]:
    """``index`` 바로 앞(공백 제외) 글자의 위치. 없으면 None."""
    before = index - 1
    while before >= 0 and text[before] in _JSON_WHITESPACE:
        before -= 1
    return before if before >= 0 else None


def _skip_foreign_array(
    decoder: json.JSONDecoder, text: str, open_index: int,
) -> Optional[int]:
    """판정 배열이 아닌 «남의 행 배열»을 통째로 읽어 그 끝 다음 위치를 돌려준다.

    배열이 온전하지 않아 끝을 모르면 None — 부르는 쪽은 재동기화를 멈춘다(그 뒤 어디가
    남의 배열 안인지 알 수 없다).
    """
    try:
        value, end = decoder.raw_decode(text, open_index)
    except (ValueError, RecursionError):
        return None
    return end if isinstance(value, list) else None


def _is_non_row_object(decoder: json.JSONDecoder, text: str, start: int) -> bool:
    """``start`` 의 ``{`` 부터 «번호 없는 온전한 객체»가 읽히는지(규칙 5의 F2)."""
    try:
        value, _ = decoder.raw_decode(text, start)
    except (ValueError, RecursionError):
        return False
    return isinstance(value, dict) and REVIEW_NUMBER_KEY not in value


def _is_stray_array_close(text: str, index: int) -> bool:
    """배열 수준의 ``]`` 가 «짝 잃은 괄호»인지 — 뒤(공백·쉼표 제외)에 행 시작이 온다."""
    return _ROW_START_RE.match(text, _skip_separators(text, index + 1)) is not None


def _ends_json_after(text: str, index: int) -> bool:
    """``index`` 의 ``]`` 뒤로 닫는 괄호·공백을 지나 JSON이 더 이어질 수 없는지."""
    after = index + 1
    while after < len(text) and (
        text[after] in _JSON_WHITESPACE or text[after] in _CLOSERS
    ):
        after += 1
    if after >= len(text):
        return True
    if text[after] == _OBJECT_OPEN:
        return _ROW_START_RE.match(text, after) is None
    return text[after] not in _JSON_CONTINUATION


def _next_row_start(
    text: str, start: int, quote_positions: list[int], *,
    verdicts_open: int, decoder: json.JSONDecoder,
) -> tuple[Optional[int], Optional[int]]:
    """깨진 원소 뒤에서 이어 읽을 다음 행 시작.

    Returns:
        (행 시작, None) — 이어 읽을 자리. (None, ``]`` 위치) — 배열 끝을 만났다.
        (None, None) — 표지가 더 없거나, 끝을 모르는 남의 행 배열을 만났다.

    문자열 안의 표지는 보지 않는다(규칙 2). 문자열 밖 ``]`` 가 배열 끝이면
    (`_ends_json_after`) 거기서 멈춘다(규칙 3). 행 시작은 바로 앞(공백 제외)이
    ``,`` 이거나 «판정 배열 자신의» ``[``(``verdicts_open``)일 때만 받는다(규칙 2).
    다른 ``[`` 뒤의 행 시작은 남의 배열(다른 키의 예시·문자열에 샌 인용)의 첫
    원소다 — 그 배열을 통째로 건너뛴다. 첫 원소만 거르면 둘째 원소부터는 ``,``
    뒤라 원소 경계를 통과하기 때문이다(2026-09-23 재확인 탐침 «여러 행»).
    """
    position = start
    while True:
        mark = _RESYNC_MARK_RE.search(text, position)
        if mark is None:
            return None, None
        index = mark.start()
        position = index + 1
        if not _outside_string(quote_positions, index):
            continue
        if text[index] == _ARRAY_CLOSE:
            if _ends_json_after(text, index):
                return None, index
            continue
        before = _previous_non_whitespace(text, index)
        if before is None:
            continue
        # 판정 배열 자신의 «[» 바로 뒤 원소는 본 흐름이 재동기화 없이 먼저 읽으므로,
        # 지금 흐름에서 실제로 걸리는 쪽은 «,» 다. 규칙의 뜻 그대로 둘 다 적어 둔다.
        if text[before] == _MEMBER_SEPARATOR or before == verdicts_open:
            return index, None
        if text[before] == _ARRAY_OPEN:
            skipped = _skip_foreign_array(decoder, text, before)
            if skipped is None:
                return None, None
            position = skipped


def _continues_after_close(text: str, end: int) -> bool:
    """읽힌 객체 «뒤»에 그 객체의 나머지 칸이 이어지는 모양인지.

    객체가 도중에 닫히고 남은 칸(``, "결과": …``)이 뒤따르면, 읽힌 객체는 행의
    앞부분일 뿐이다. 짝 잃은 닫는 중괄호는 건너뛰고 본다 — 온전한 행 뒤에 ``}`` 가
    하나 더 찍힌 경우는 행 자체가 닫힌 것이므로 살린다.
    """
    index = _skip_whitespace(text, end)
    while index < len(text) and text[index] == _OBJECT_CLOSE:
        index = _skip_whitespace(text, index + 1)
    if index >= len(text):
        return False
    char = text[index]
    if char == _MEMBER_SEPARATOR:
        after = _skip_whitespace(text, index + 1)
        return after < len(text) and text[after] not in _ELEMENT_BOUNDARY_AFTER_COMMA
    return char == _STRING_QUOTE


def _read_row(
    decoder: json.JSONDecoder, text: str, start: int,
) -> tuple[Optional[dict], int]:
    """``start`` 의 ``{`` 부터 행 하나를 읽는다. 온전한 행이 아니면 (None, start)."""
    try:
        value, end = decoder.raw_decode(text, start)
    except (ValueError, RecursionError):
        # ValueError: JSONDecodeError 포함 — 이 자리의 행이 문법상 깨졌다.
        # RecursionError: 비정상적으로 깊은 중첩 — 역시 온전한 행이 아니다.
        return None, start
    if not isinstance(value, dict) or REVIEW_NUMBER_KEY not in value:
        return None, start
    if _continues_after_close(text, end):
        return None, start
    return value, end


def _broken_row_number(text: str, start: int) -> Optional[int]:
    """``start`` 에서 못 읽은 행의 머리 번호(규칙 6). 번호를 못 읽으면 None."""
    match = _BROKEN_ROW_NUMBER_RE.match(text, start)
    return None if match is None else coerce_verdict_number(match.group(1))


def salvage_verdict_rows(raw: Optional[str]) -> Optional[SalvagedVerdicts]:
    """«판정» 배열에서 온전히 읽히는 행만 건진다. 한 행도 못 건지면 None.

    Args:
        raw: 검수 AI 응답 원문. 부르는 쪽이 «응답 전체가 JSON으로 못 읽힌다»고
            이미 확인한 경우에만 넘긴다.

    Returns:
        구제 결과. «판정» 배열이 없거나, «판정» 키가 두 번 이상 나오거나(규칙 1),
        문서가 하나가 아니거나(규칙 5), 남는 온전한 행이 하나도 없으면 None 이다 —
        그때 부르는 쪽은 예전처럼 «구문 오류»로 닫는다.
    """
    text = raw or ""
    # 규칙 1 — 형식 예시를 되풀이했거나 JSON을 두 번 낸 응답은 어느 배열이 본
    # 응답인지 모른다. 예시 행(«참»)이 깨진 본 응답 행을 대신하지 않게 통째로 둔다.
    if _verdicts_key_count(text) > _MAX_VERDICTS_KEYS:
        return None
    array = _VERDICTS_ARRAY_RE.search(text)
    if array is None:
        return None
    # 규칙 5(앞) — 판정 배열을 감싼 객체 앞에는 펜스·공백·설명 글만 온다.
    enclosing = _enclosing_object_start(text, array.start())
    if enclosing is None or _has_other_document(text[:enclosing]):
        return None
    decoder = json.JSONDecoder()
    quote_positions = _unescaped_quote_positions(text, array.end())
    rows: list[dict] = []
    broken_numbers: set[int] = set()
    dropped = 0
    #: 판정 배열이 끝난 «뒤» 글의 시작 — 규칙 5(뒤)가 본다. 끝을 못 찾으면 None.
    rest_start: Optional[int] = None
    position = array.end()
    while True:
        position = _skip_separators(text, position)
        if position >= len(text):
            break
        char = text[position]
        if char == _ARRAY_CLOSE:
            # 규칙 3 — 배열 수준의 ``]``: 뒤에 행 시작이 이어지면 짝 잃은 괄호라
            # 건너뛰고, 아니면 배열의 진짜 끝이다. 그 뒤(설명 문장·두 번째 JSON)는
            # 행으로 읽지 않고 규칙 5가 본다.
            if not _is_stray_array_close(text, position):
                rest_start = position + 1
                break
            position += 1
            continue
        if char == _OBJECT_OPEN:
            if _is_non_row_object(decoder, text, position):
                # 규칙 5(F2) — 배열 수준의 «번호 없는 온전한 객체»는 판정 행이 아니라
                # 다른 JSON 이다(``]`` 없이 끝난 판정 배열 뒤의 ``{"예시": […]}``).
                # 거기부터를 «끝 뒤 글»로 보아 규칙 5가 구제를 거절하게 한다.
                rest_start = position
                break
            row, end = _read_row(decoder, text, position)
            if row is not None:
                rows.append(row)
                position = end
                continue
            dropped += 1
            number = _broken_row_number(text, position)
            if number is not None:
                broken_numbers.add(number)
        # 못 읽은 행이나 행이 아닌 찌꺼기(짝 잃은 ``}`` 등) — 규칙 2·3을 지키는
        # 다음 행 시작에서 이어 읽는다.
        resync, array_end = _next_row_start(
            text, position + 1, quote_positions,
            verdicts_open=array.end() - 1, decoder=decoder,
        )
        if resync is None:
            if array_end is not None:
                rest_start = array_end + 1
            break
        position = resync
    # 규칙 5(뒤) — 판정 배열이 끝난 뒤에 다른 JSON·정정 배열·따옴표 든 글이 있으면
    # 문서가 하나가 아니다.
    if rest_start is not None and _has_other_document(text[rest_start:]):
        return None
    # 규칙 6 — 깨진 행과 같은 번호의 행은 뺀다. 그 번호는 누락 후속이 다시 묻는다.
    kept = [
        row for row in rows
        if coerce_verdict_number(row.get(REVIEW_NUMBER_KEY)) not in broken_numbers
    ]
    dropped += len(rows) - len(kept)
    if not kept:
        return None
    return SalvagedVerdicts(
        text=json.dumps({REVIEW_ENTRIES_KEY: kept}, ensure_ascii=False),
        kept_rows=len(kept),
        dropped_rows=dropped,
    )
