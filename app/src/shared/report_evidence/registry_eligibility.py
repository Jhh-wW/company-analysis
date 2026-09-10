# -*- coding: utf-8 -*-
"""출처 등록부 줄이 «구조적으로 색인 대상이 아닌가»만 판별하는 순수 술어.

★ 왜 필요한가 — 정본 출처 검증기는 두 가지 아주 다른 상황에 똑같이 ``None``을
  돌려준다:

    ① 자격 없음 — v3 출처표의 필수 신원 필드가 아예 없는 legacy 등록부 줄
       (공시 원문 조각·재무 API 응답). 본문 근거로 «셀 수 없는» 줄일 뿐,
       자료가 깨졌다는 뜻이 아니다.
    ② 검증 실패 — 도장(provenance seal) 깨짐, 등록부 번호·ID 중복, typed
       등록부 계약 위반, 검증 중 예외. 이건 «자료가 깨졌다»는 뜻이다.

  둘을 구분하지 않고 모두 건너뛰면 ②의 fail-closed 알람이 통째로 사라진다
  (실측: 정본 출처 14건을 한 건씩 검증 불가로 만들어도 전부 출고 허용으로
  뒤집혔다). 반대로 둘을 모두 닫으면 legacy 줄이 늘 섞이는 보완조사 경로가
  자료 유무와 무관하게 항상 막힌다.

★ 이 술어는 ①만 참으로 답한다. 판정 재료는 «그 줄이 v3 색인 필수 필드를
  갖고 있는가»뿐이고, 도장·등록부 중복·신원 결속은 보지 않는다 — 그것들은
  검증기의 몫이고 이 술어가 대신 눈감아 주면 안 되기 때문이다.

★ 이 모듈은 기능(feature) 자료형을 import하지 않는다. 등록부 줄의 실제
  자료형은 provenance 기능이 소유하므로 필드만 읽는다. 필드가 없거나 문자열이
  아니면 «모르겠다»가 아니라 «자격 없음이 아니다»(거짓)로 답한다 — 확신할 수
  없는 줄까지 건너뛰면 ②의 구멍이 다시 생긴다.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Final


#: v3 출처표가 색인에 요구하는 «날짜» 후보 필드. 셋 중 하나라도 실제 달력의
#: ISO 날짜면 날짜 요건을 채운 것으로 본다(정본 ``is_canonical_valid``와 같은
#: 순서·같은 판정).
_DATE_FIELDS: Final[tuple[str, ...]] = (
    "published_at",
    "disclosed_at",
    "collected_at",
)

#: 정본 ``is_canonical_valid``가 쓰는 것과 같은 엄격 ISO 꼴. 여기서 더 너그럽게
#: 받으면 «정본 검증은 실패했는데 이 술어만 통과»하는 어긋남이 생긴다.
_ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(r"\d{4}-\d{2}-\d{2}")


def _text(source: object, field: str) -> str | None:
    value = getattr(source, field, None)
    return value.strip() if type(value) is str else None


def _valid_iso_date(value: str) -> bool:
    if _ISO_DATE_RE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def registry_indexing_ineligible(source: object) -> bool:
    """v3 출처표의 색인 필수 필드가 없어 «셀 수 없는» legacy 줄인가.

    참이면 호출자는 그 줄을 색인에서만 빼고 판정을 계속한다(색인에서 빠진
    번호를 쓴 문장·표 행은 그 다음 판정에서 통째로 제외되므로 판정은 좁아질
    뿐 넓어지지 않는다). 거짓이면 그 줄의 검증 실패는 예전처럼 등록부 전체를
    닫는다.

    ★ 실측한 legacy 모양 두 가지 — 어느 쪽도 도장이나 등록부가 깨진 것이
      아니다.
        · 공시 원문 조각: ``source_type``·``fact_status``가 빈 문자열
        · DART 재무 API 응답: 세 날짜 필드가 모두 빈 문자열
    """

    source_type = _text(source, "source_type")
    fact_status = _text(source, "fact_status")
    if source_type is None or fact_status is None:
        return False
    if not source_type or not fact_status:
        return True

    dates: list[str] = []
    for field in _DATE_FIELDS:
        value = _text(source, field)
        if value is None:
            return False
        dates.append(value)
    return not any(_valid_iso_date(value) for value in dates if value)


__all__ = ["registry_indexing_ineligible"]
