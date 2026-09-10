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

코드 목록은 ``shared/report_quality/composition_diagnostic_constants`` 가
소유한다. 각 소비자는 해당 상수를 직접 가져온다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from src.shared.report_quality.composition_diagnostic_constants import (
    PROTOCOL_STEP,
    EXTRACT_FAILED,
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
        "추출방식": EXTRACT_FAILED,
        "json시작offset": -1,
        "json끝offset": -1,
        "응답행수": 0,
        "유효행수": 0,
        "미응답번호수": int(requested_count),
        "요청밖번호수": 0,
        "행탈락": {},
        "판독": READ_EMPTY,
    }


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
        if observe.get("추출방식") == EXTRACT_FAILED
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
