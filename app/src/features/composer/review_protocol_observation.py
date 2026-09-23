"""본문 검수 응답 «판독»만 닫힌 필드로 남기는 관측 도우미.

왜 있는가
---------
검수 응답을 읽었는지 못 읽었는지가 지금은 ``logger`` 에만 남아 실행 기록에
실리지 않는다. 그래서 사후에 아래 둘이 구분되지 않는다.

  (A) 정상 판독됐고 문장들이 판정에 따라 걸러졌다
  (B) 응답을 통째로 «읽지 못해» 판정 없이 끝났다

이 모듈은 그 구분에 필요한 최소 정보만 만든다. **판정·재시도·호출 상한·품질
통과선을 바꾸지 않는다** — 파서가 지나간 분기에서 개수만 적는다.

담지 않는 것
------------
문장 본문·요약 본문·원문 조각·프롬프트·응답 전문·오류 문구·API 키·URL,
그리고 **근거 식별자 자체**(개수만 센다).

행 단위 구제 (2026-09-23)
-------------------------
응답 전체가 구문 오류로 못 읽혀도 «판정» 행을 하나씩 건졌으면
``추출방식=row_salvage`` 와 ``구문탈락행수``(버린 행 수)를 적는다. 버린 행에는
못 읽은 행과, 깨진 행과 같은 번호라 뺀 행이 함께 든다. 이때 ``응답행수`` 는
«건진» 행 수이고, 응답에 적힌 행 수는 «적어도» 둘을 더한 값이다(하한 — 재동기화가
건너뛴 행은 세지 않는다. 뒤 행의 여는 ``{`` 가 빠지면 온전한 앞 행까지 버리고
탈락은 1만 센다). ``판독`` 은 건진 행을 검사한 결과(``ok``/``all_rows_invalid``)다.
«판정» 키가 두 번 이상 나오는 응답(유니코드 이스케이프로 쓴 키 포함)과 문서가
하나가 아닌 응답(판정 객체 앞이나 판정 배열 끝 뒤 글에 ``{``·``[``·``"``)은
구제하지 않아 예전처럼 ``json_syntax`` 다.

코드 목록은 ``shared/report_quality/composition_diagnostic_constants`` 가
소유한다. 각 소비자는 해당 상수를 직접 가져온다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from src.shared.report_quality.composition_diagnostic_constants import (
    PROTOCOL_STEP,
    PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD,
    EXTRACT_FAILED,
    EXTRACT_ROW_SALVAGE,
    READ_CALL_LIMIT,
    READ_REQUEST_BUDGET,
    READ_OK,
    READ_EMPTY,
    READ_JSON_SYNTAX,
    READ_NOT_OBJECT,
    READ_ALL_ROWS_INVALID,
    ROW_NOT_MAPPING,
    ROW_NUMBER_NOT_INT,
    ROW_RESULT_INVALID,
    ROW_OWNER_MISMATCH,
    ROW_EVIDENCE_EMPTY,
    ROW_EVIDENCE_DUPLICATE,
    ROW_EVIDENCE_MISMATCH,
    ROW_NUMBER_CONFLICT,
)

#: ``logic.extract_json_payload`` 가 «어떻게 읽었는지» 적는 관측 칸 이름.
#: 이 모듈의 관측 계약과 같은 칸이다(``new_protocol_observation`` 참고).
_EXTRACT_FIELD = "추출방식"

_ROW_REASON_ORDER = (
    ROW_NOT_MAPPING,
    ROW_NUMBER_NOT_INT,
    ROW_RESULT_INVALID,
    ROW_OWNER_MISMATCH,
    ROW_EVIDENCE_EMPTY,
    ROW_EVIDENCE_DUPLICATE,
    ROW_EVIDENCE_MISMATCH,
    ROW_NUMBER_CONFLICT,
)


def new_protocol_observation(
    path: str,
    attempt: int,
    *,
    prompt_chars: int,
    response_chars: int,
    requested_count: int,
) -> dict[str, Any]:
    """실제로 «보낸» 호출 하나에 대한 빈 관측.

    ★ 호출이 일어난 뒤에만 만든다. 도달하지 않은 시도를 «응답 0건»으로 적지 않는다.
    """
    return {
        "step": PROTOCOL_STEP,
        "경로": path,
        "시도": int(attempt),
        "입력문자": int(prompt_chars),
        "응답문자": int(response_chars),
        "요청번호수": int(requested_count),
        _EXTRACT_FIELD: EXTRACT_FAILED,
        "json시작offset": -1,
        "json끝offset": -1,
        "응답행수": 0,
        "유효행수": 0,
        "미응답번호수": int(requested_count),
        "요청밖번호수": 0,
        PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD: 0,
        "행탈락": {},
        "판독": READ_EMPTY,
    }


def extraction_failed(probe: Mapping[str, Any]) -> bool:
    """``extract_json_payload(raw, observe=probe)`` 가 응답을 «통째로» 못 읽었는지.

    빈 dict 를 넘겨 부른 뒤 이 함수로 본다. 읽기에 성공하면 추출방식이
    적히므로(``null``·배열처럼 객체가 아닌 적법한 JSON 포함), 칸이 비어 있거나
    «실패»이면 구문 오류다. ``envelope_code_for_payload`` 와 같은 기준이다.
    """
    return probe.get(_EXTRACT_FIELD, EXTRACT_FAILED) == EXTRACT_FAILED


def note_row_salvage(observe: Optional[dict], dropped_rows: int) -> None:
    """응답 전체는 못 읽었지만 행 단위로 구제했다는 사실과 버린 행 수만 적는다.

    판정·재시도에는 영향이 없다. 응답 본문과 버린 행의 내용은 담지 않는다.
    """
    if observe is None:
        return
    observe[_EXTRACT_FIELD] = EXTRACT_ROW_SALVAGE
    observe[PROTOCOL_SYNTAX_DROPPED_ROWS_FIELD] = int(dropped_rows)


def note_optional_call_aborted(observe: Optional[dict], *, call_limit: bool) -> None:
    """두 번째 호출을 요청 AI 몫 소진으로 포기했음을 닫힌 판독 코드로 적는다.

    ``call_limit`` 이 참이면 호출 «횟수» 상한, 거짓이면 요청 로컬 «예약액» 소진이다
    (AskFatalError 의 두 깃발). 오류 문구·응답은 담지 않는다.
    """
    if observe is None:
        return
    observe["판독"] = READ_CALL_LIMIT if call_limit else READ_REQUEST_BUDGET


def note_row_failure(observe: Optional[dict], code: str) -> None:
    """행 하나가 계약을 어긴 사유를 센다. 판정에는 영향이 없다."""
    if observe is None:
        return
    bucket = observe.setdefault("행탈락", {})
    bucket[code] = bucket.get(code, 0) + 1


def note_envelope(observe: Optional[dict], code: str) -> None:
    """봉투 단계에서 끝난 이유를 적는다 (행 검사에 도달하지 못한 경우)."""
    if observe is None:
        return
    observe["판독"] = code


def envelope_code_for_payload(
    observe: Optional[dict], raw: Optional[str] = None
) -> str:
    """봉투 단계에서 끝난 이유를 세 가지로 가른다.

    · 응답이 없거나 공백뿐 → ``empty_response`` (구문 오류가 아니다)
    · ``null``·배열처럼 **적법하게 파싱됐지만 객체가 아님** → ``not_object``
    · 그 밖에 읽기 자체가 실패 → ``json_syntax``

    파싱 성공 여부는 추출방식으로만 판단한다.
    """
    if raw is None or not raw.strip():
        return READ_EMPTY
    if observe is None:
        return READ_JSON_SYNTAX
    return (
        READ_JSON_SYNTAX
        if observe.get(_EXTRACT_FIELD) == EXTRACT_FAILED
        else READ_NOT_OBJECT
    )


def finish_protocol_observation(
    observe: Optional[dict],
    returned: Mapping[int, str],
    requested: Sequence[int] | set[int],
) -> None:
    """행 검사를 마친 뒤의 개수를 적는다.

    ``유효행수`` 는 **파서가 실제로 돌려주는 dict 의 길이**다.
    ``미응답번호수`` 는 «요청번호 − 돌려준 키».
    ``요청밖번호수`` 는 요청에 없던 번호가 돌려준 dict 에 남은 수 —
    실제 파서가 그것을 받아들이므로 **제외하지 않고 세기만** 한다.
    """
    if observe is None:
        return
    requested_set = {int(number) for number in requested}
    returned_keys = set(returned)
    observe["유효행수"] = len(returned)
    observe["미응답번호수"] = (
        len(requested_set - returned_keys) if requested_set else 0
    )
    observe["요청밖번호수"] = (
        len(returned_keys - requested_set) if requested_set else 0
    )
    observe["행탈락"] = {
        code: observe["행탈락"][code]
        for code in _ROW_REASON_ORDER
        if code in observe["행탈락"]
    }
    observe["판독"] = READ_OK if returned else READ_ALL_ROWS_INVALID
