"""보고서 수명주기 식별자에 쓰는 결정론적 직렬화 도구."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


class CanonicalValueError(ValueError):
    """식별자에 넣을 수 없는 값이 들어왔다."""


def canonical_json_bytes(value: Any) -> bytes:
    """같은 의미의 JSON 값이 항상 같은 바이트가 되게 직렬화한다."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalValueError("지문 값은 유한한 JSON 값이어야 합니다") from exc


def sha256_hex(value: bytes) -> str:
    """바이트의 SHA-256을 소문자 16진수로 돌려준다."""

    return hashlib.sha256(value).hexdigest()


def require_sha256_hex(value: str, *, label: str, allow_empty: bool = False) -> str:
    """저장된 SHA-256 문자열이 정규 소문자 형식인지 확인한다."""

    text = str(value).strip()
    if allow_empty and not text:
        return ""
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{label} SHA-256이 손상됐습니다")
    return text


def canonical_digest(value: Any) -> str:
    """정규 JSON 값의 SHA-256."""

    return sha256_hex(canonical_json_bytes(value))


def normalized_json_value(value: Any, *, unordered_lists: bool = False) -> Any:
    """공백·키 순서를 정규화하되 값의 타입과 뜻은 보존한다.

    ``unordered_lists``는 API가 행 순서를 계약하지 않는 자료에만 쓴다. 일반
    보고서 본문처럼 순서가 의미인 자료에는 켜면 안 된다.
    """

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalValueError("지문에 NaN이나 무한대를 넣을 수 없습니다")
        return value
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return {
            str(key).strip(): normalized_json_value(
                item, unordered_lists=unordered_lists
            )
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        items = [
            normalized_json_value(item, unordered_lists=unordered_lists)
            for item in value
        ]
        if unordered_lists:
            items.sort(key=canonical_json_bytes)
        return items
    raise CanonicalValueError(f"지문에 넣을 수 없는 값입니다: {type(value).__name__}")


def require_aware(value: dt.datetime, *, label: str) -> dt.datetime:
    """시간대가 있는 시각만 UTC로 정규화한다."""

    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ValueError(f"{label}에는 시간대가 있는 시각이 필요합니다")
    if value.utcoffset() is None:
        raise ValueError(f"{label}에는 시간대가 있는 시각이 필요합니다")
    return value.astimezone(dt.timezone.utc)


def utc_text(value: dt.datetime, *, label: str) -> str:
    """SQLite에서 문자열 비교 가능한 고정 UTC 형식."""

    normalized = require_aware(value, label=label)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def datetime_from_utc_text(value: str, *, label: str) -> dt.datetime:
    """``utc_text``로 저장한 값을 시간대 있는 시각으로 읽는다."""

    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 시각이 올바르지 않습니다") from exc
    return require_aware(parsed, label=label)
