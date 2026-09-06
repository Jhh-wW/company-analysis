"""실행 하나의 단계 기록(steps) 원본을 SQLite에 보관한다.

★ 왜 파일이 아니라 SQLite인가 — 실행 이력의 정본이 이미 SQLite다. 진단만 따로
  파일에 두면 실행은 남았는데 진단은 사라지는(또는 그 반대의) 짝이 생긴다.

★ 로그와 역할이 다르다. 로그에는 개수·코드만 담고, 여기에는 원본을 담는다.
  대신 **로그인한 관리자 화면에서만** 꺼내 본다.

★ 한 번 쓰면 바꾸지 않는다. 같은 실행 번호로 다시 부르면 조용히 무시한다 —
  이력은 「일어난 일」이라 나중에 값을 바꾸면 그 자체가 거짓말이 된다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Final, Mapping, Sequence

from src.features.observability.constants import (
    RUN_STEPS_MAX_BYTES,
    RUN_STEPS_SCHEMA_VERSION,
)

#: 표 이름. 다른 관측 표와 같은 접두사를 쓴다.
TABLE_RUN_STEPS: Final[str] = "observability_run_steps"

#: 저장한 JSON에 「뒤쪽을 덜어냈다」를 남기는 열쇠.
TRUNCATED_KEY: Final[str] = "생략단계"

_CREATE_TABLE: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_RUN_STEPS} (
    run_id         TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK(schema_version = {RUN_STEPS_SCHEMA_VERSION}),
    steps_json     TEXT NOT NULL,
    step_count     INTEGER NOT NULL CHECK(step_count >= 0),
    omitted_count  INTEGER NOT NULL CHECK(omitted_count >= 0),
    recorded_at    TEXT NOT NULL
)
"""


class RunStepsStoreError(RuntimeError):
    """실행 진단 원본 표가 손상됐거나 쓸 수 없다."""


@dataclass(frozen=True)
class PersistedRunSteps:
    """저장해 둔 실행 진단 원본 한 건."""

    run_id: str
    schema_version: int
    steps: list[dict[str, Any]]
    step_count: int
    omitted_count: int
    recorded_at: str

    @property
    def truncated(self) -> bool:
        """상한 때문에 뒤쪽 단계를 덜어냈는가."""
        return self.omitted_count > 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    """표가 없으면 만든다. 있으면 그대로 둔다."""

    conn.execute(_CREATE_TABLE)


def _encode(steps: Sequence[Any]) -> tuple[str, int, int]:
    """단계 목록을 바이트 상한 안에 드는 JSON으로 만든다.

    Returns:
        (JSON 문자열, 저장한 단계 수, 덜어낸 단계 수).

    ★ 상한을 넘으면 «뒤쪽»부터 덜어낸다. 앞쪽 단계가 수집·판정이라 원인 추적에
      더 쓸모 있기 때문이다.
    """

    items = [dict(item) for item in steps if isinstance(item, Mapping)]
    kept = list(items)
    omitted = 0
    while True:
        encoded = json.dumps(kept, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) <= RUN_STEPS_MAX_BYTES or not kept:
            return encoded, len(kept), omitted
        kept = kept[:-1]
        omitted += 1


def record_once(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    steps: Sequence[Any],
    recorded_at: str,
) -> bool:
    """실행 하나의 단계 기록을 정확히 한 번 저장한다.

    Args:
        conn: 열린 SQLite 연결.
        run_id: 실행 번호(관측 이력과 같은 값).
        steps: 파이프라인이 쌓은 단계 기록.
        recorded_at: 기록 시각(ISO 8601).

    Returns:
        이번 호출로 새로 저장했으면 True, 이미 있으면 False.
    """

    clean_run_id = str(run_id or "").strip()
    if not clean_run_id:
        raise ValueError("실행 진단을 저장하려면 실행 번호가 필요합니다")
    clean_recorded_at = str(recorded_at or "").strip()
    if not clean_recorded_at:
        raise ValueError("실행 진단을 저장하려면 기록 시각이 필요합니다")

    ensure_schema(conn)
    encoded, step_count, omitted = _encode(steps)
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_RUN_STEPS} (
            run_id, schema_version, steps_json, step_count, omitted_count, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            clean_run_id,
            RUN_STEPS_SCHEMA_VERSION,
            encoded,
            step_count,
            omitted,
            clean_recorded_at,
        ),
    )
    return cursor.rowcount == 1


def load(conn: sqlite3.Connection, run_id: str) -> PersistedRunSteps | None:
    """실행 하나의 단계 기록을 읽는다. 없으면 None."""

    clean_run_id = str(run_id or "").strip()
    if not clean_run_id:
        return None
    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT run_id, schema_version, steps_json, step_count, omitted_count, recorded_at
          FROM {TABLE_RUN_STEPS}
         WHERE run_id = ?
        """,
        (clean_run_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        decoded = json.loads(row[2])
    except json.JSONDecodeError as exc:
        raise RunStepsStoreError("실행 진단 원본을 읽을 수 없습니다") from exc
    if not isinstance(decoded, list):
        raise RunStepsStoreError("실행 진단 원본의 모양이 목록이 아닙니다")
    return PersistedRunSteps(
        run_id=str(row[0]),
        schema_version=int(row[1]),
        steps=[item for item in decoded if isinstance(item, dict)],
        step_count=int(row[3]),
        omitted_count=int(row[4]),
        recorded_at=str(row[5]),
    )
