"""공식 자료 날짜의 단일 정규화 경계.

새 수집기는 ISO ``YYYY-MM-DD``를 내고 옛 cache에는 ``YYYYMMDD``가 남아 있다.
두 모양만 실제 달력으로 검산해 ISO로 만들고, 존재하지 않는 날짜나 임의
문자열은 출처 장부까지 흘려보내지 않는다.
"""

from __future__ import annotations

import re
from datetime import date


_ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_LEGACY_COMPACT_DATE_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})$")


def normalize_official_source_date(value: object) -> str:
    """실제 달력의 ISO/legacy compact 날짜만 canonical ISO로 돌려준다."""

    if type(value) is not str:
        raise ValueError("공식 자료 날짜는 문자열이어야 합니다")
    raw = value.strip()
    compact = _LEGACY_COMPACT_DATE_RE.fullmatch(raw)
    candidate = "-".join(compact.groups()) if compact is not None else raw
    if _ISO_DATE_RE.fullmatch(candidate) is None:
        raise ValueError("공식 자료 날짜는 YYYY-MM-DD 또는 YYYYMMDD여야 합니다")
    try:
        parsed = date.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError("공식 자료 날짜가 실제 달력에 없는 날입니다") from error
    return parsed.isoformat()
