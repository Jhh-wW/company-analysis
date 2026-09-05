"""검증을 마친 근거 재판정 결과의 SQLite 캐시.

캐시 키에는 접수번호 원문을 넣지 않는다. 정렬·중복 제거한 접수번호 묶음을
먼저 해시하고, 그 지문과 프롬프트 버전·모델 id를 다시 해시해 한 개의 불투명
키로 만든다. 이 모듈이 자기 표를 멱등으로 만들므로 기존 저장소 bootstrap을
고치지 않고도 이전 DB에서 바로 사용할 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Final, Iterable, TypeAlias


TABLE_EVIDENCE_RECLASSIFICATION_CACHE: Final[str] = (
    "evidence_reclassification_cache"
)
_RECEIPT_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9]{14}")
_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


CREATE_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_EVIDENCE_RECLASSIFICATION_CACHE} (
    cache_key                   TEXT PRIMARY KEY,
    validated_items_json       TEXT NOT NULL,
    rejection_diagnostics_json TEXT NOT NULL,
    generated_at                TEXT NOT NULL,
    input_paragraph_hash        TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Cached:
    """재판정 재호출 없이 복원할 검증 완료 스냅샷."""

    validated_items: JsonValue
    rejection_diagnostics: JsonValue
    generated_at: str
    input_paragraph_hash: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    """재판정 캐시 표를 멱등으로 만든다."""

    conn.execute(CREATE_SQL)


def _require_nonempty_text(value: str, *, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name}은 비어 있지 않은 문자열이어야 합니다")
    return value


def _receipt_bundle_digest(receipt_numbers: Iterable[str]) -> str:
    clean_numbers: set[str] = set()
    for receipt_number in receipt_numbers:
        if (
            type(receipt_number) is not str
            or _RECEIPT_NUMBER_PATTERN.fullmatch(receipt_number) is None
        ):
            raise ValueError("접수번호는 정확히 14자리 숫자여야 합니다")
        clean_numbers.add(receipt_number)
    if not clean_numbers:
        raise ValueError("캐시 키에는 접수번호가 하나 이상 필요합니다")

    # 고정 길이 값이지만 구분자를 넣어 규칙 변경 때도 경계를 보존한다.
    canonical = "\x1f".join(sorted(clean_numbers)).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def key_for(
    receipt_numbers: Iterable[str],
    prompt_version: str,
    model: str,
) -> str:
    """접수번호 집합·프롬프트 버전·모델로 결정론적 키를 만든다."""

    receipt_digest = _receipt_bundle_digest(receipt_numbers)
    identity = {
        "model": _require_nonempty_text(model, name="모델 id"),
        "prompt_version": _require_nonempty_text(
            prompt_version,
            name="프롬프트 버전",
        ),
        "receipt_numbers_sha256": receipt_digest,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _json_text(value: JsonValue) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("재판정 캐시 값은 JSON으로 저장할 수 있어야 합니다") from exc


def _require_cache_key(key: str) -> str:
    if type(key) is not str or _DIGEST_PATTERN.fullmatch(key) is None:
        raise ValueError("재판정 캐시 키는 소문자 SHA-256이어야 합니다")
    return key


def _require_payload(payload: Cached) -> Cached:
    if type(payload) is not Cached:
        raise TypeError("재판정 캐시 값은 정확한 Cached 형식이어야 합니다")
    _require_nonempty_text(payload.generated_at, name="생성 시각")
    if (
        type(payload.input_paragraph_hash) is not str
        or _DIGEST_PATTERN.fullmatch(payload.input_paragraph_hash) is None
    ):
        raise ValueError("입력 문단 해시는 소문자 SHA-256이어야 합니다")
    return payload


def load(conn: sqlite3.Connection, key: str) -> Cached | None:
    """키가 맞고 두 JSON 값이 온전한 캐시 행만 돌려준다.

    저장 행의 JSON이 손상됐으면 호출 경로를 깨뜨리지 않고 캐시 미스로
    처리한다. 그러면 상위 계층이 재판정을 다시 수행해 정상 값을 덮어쓸 수 있다.
    """

    clean_key = _require_cache_key(key)
    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT validated_items_json, rejection_diagnostics_json,
               generated_at, input_paragraph_hash
          FROM {TABLE_EVIDENCE_RECLASSIFICATION_CACHE}
         WHERE cache_key = ?
        """,
        (clean_key,),
    ).fetchone()
    if row is None:
        return None
    try:
        validated_items = json.loads(str(row[0]))
        rejection_diagnostics = json.loads(str(row[1]))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return Cached(
        validated_items=validated_items,
        rejection_diagnostics=rejection_diagnostics,
        generated_at=str(row[2]),
        input_paragraph_hash=str(row[3]),
    )


def save(conn: sqlite3.Connection, key: str, payload: Cached) -> None:
    """검증 완료 스냅샷을 저장하거나 같은 키의 값을 원자적으로 갱신한다.

    transaction commit은 연결을 연 호출자가 소유한다. 파이프라인이 더 큰 원자
    작업과 함께 묶을 수 있도록 이 함수가 임의로 commit하지 않는다.
    """

    clean_key = _require_cache_key(key)
    clean_payload = _require_payload(payload)
    validated_items_json = _json_text(clean_payload.validated_items)
    rejection_diagnostics_json = _json_text(
        clean_payload.rejection_diagnostics
    )
    ensure_schema(conn)
    conn.execute(
        f"""
        INSERT INTO {TABLE_EVIDENCE_RECLASSIFICATION_CACHE} (
            cache_key, validated_items_json, rejection_diagnostics_json,
            generated_at, input_paragraph_hash
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            validated_items_json = excluded.validated_items_json,
            rejection_diagnostics_json = excluded.rejection_diagnostics_json,
            generated_at = excluded.generated_at,
            input_paragraph_hash = excluded.input_paragraph_hash
        """,
        (
            clean_key,
            validated_items_json,
            rejection_diagnostics_json,
            clean_payload.generated_at,
            clean_payload.input_paragraph_hash,
        ),
    )
